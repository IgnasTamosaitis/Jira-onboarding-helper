"""Exchange card requests through the user's already-signed-in OneDrive sync.

No web authentication or workbook writes happen here. One serialized cloud
flow reads Requests, runs the workbook script, publishes Results, and archives
requests to Processed. Stable keys and Jira idempotency handle repeat delivery.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from access_card_registry import (
    AccessCardConfigurationError, AccessCardPending, AccessCardServiceError,
    queue_request_key, reservation_from_response, reservation_request_from_ticket,
)


QUEUE_FOLDERS = ("Requests", "Results", "Processed", "Staging")
MAX_QUEUE_FILE_BYTES = 64 * 1024


def validate_sync_folder(folder: str) -> Path:
    """Require the dedicated, existing folder structure; never guess a path."""
    path = Path(folder).expanduser()
    if not folder.strip() or not path.is_absolute():
        raise AccessCardConfigurationError("Choose the full local path to the synced AccessCardQueue folder.")
    try:
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise OSError("Not a folder")
        for name in QUEUE_FOLDERS:
            child = (root / name).resolve(strict=True)
            if not child.is_dir() or child.parent != root:
                raise OSError("Missing or redirected queue subfolder")
    except (OSError, RuntimeError) as exc:
        raise AccessCardConfigurationError(
            "The queue folder must contain Requests, Results, Processed and Staging. Check OneDrive is synced."
        ) from exc
    return root


class OneDriveAccessCardClient:
    def __init__(self, folder: str):
        self.root = validate_sync_folder(folder)

    def reserve_for_ticket(self, ticket: Mapping[str, Any], *, interactive: bool = False):
        request = reservation_request_from_ticket(ticket)
        key = queue_request_key(request)
        expected = {"requestKey": key, **request}
        # Validate again: an unavailable sync root must not become a fresh queue.
        validate_sync_folder(str(self.root))
        result_path = self._path("Results", key)
        try:
            result = self._read_json(result_path)
        except FileNotFoundError:
            pass
        else:
            if not isinstance(result, dict) or result.get("request") != expected:
                raise AccessCardConfigurationError("The synced card result belongs to a different request. Do not print it.")
            return reservation_from_response(result.get("result"), request)

        # A moved request may sync before its result. Keep waiting in that case.
        for folder in ("Requests", "Processed"):
            path = self._path(folder, key)
            try:
                existing = self._read_json(path)
            except FileNotFoundError:
                continue
            if existing != expected:
                raise AccessCardConfigurationError("The existing queue file does not match this employee request.")
            raise AccessCardPending("Waiting for Excel and OneDrive sync. If this persists, check the flow's run history.")

        self._publish_request(self._path("Requests", key), expected)
        raise AccessCardPending("Request saved to OneDrive. Waiting for the workbook flow and return sync…")

    def _path(self, folder: str, key: str) -> Path:
        path = self.root / folder / (key + ".json")
        if path.is_symlink() or path.resolve().parent != self.root / folder:
            raise AccessCardConfigurationError("A queue file points outside the configured folder.")
        return path

    @staticmethod
    def _read_json(path: Path):
        try:
            with path.open("rb") as stream:
                content = stream.read(MAX_QUEUE_FILE_BYTES + 1)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise AccessCardPending("OneDrive has not made the queue file available yet. Check sync is running.") from exc
        if len(content) > MAX_QUEUE_FILE_BYTES:
            raise AccessCardConfigurationError("The synced queue file is larger than the supported result size.")
        try:
            return json.loads(content.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            # A sync download or file replacement can be observed mid-write.
            # Never turn partial JSON into a printable card or a new request.
            raise AccessCardPending("Waiting for a complete JSON file from OneDrive. If this persists, check the flow output.") from exc

    def _publish_request(self, destination: Path, request: dict) -> None:
        temporary = None
        try:
            # Publish a closed, flushed file. The flow scans Requests only.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root / "Staging",
                                             prefix="request-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(request, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "nt":
                # Windows rename is atomic and refuses to replace an existing file.
                os.rename(temporary, destination)
            else:
                # Preserve the same no-replace guarantee for local non-Windows tests.
                os.link(temporary, destination)
            temporary.unlink(missing_ok=True)
            temporary = None
        except FileExistsError:
            if self._read_json(destination) != request:
                raise AccessCardConfigurationError("Another queue file conflicts with this request.")
        except OSError as exc:
            raise AccessCardServiceError("Could not save the card request. Check OneDrive folder access and disk space.") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
