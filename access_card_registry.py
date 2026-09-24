"""Client-side contract for access-card reservations.

Card numbers are allocated only by the serialized Power Automate flow.  This
module deliberately contains no "next number" calculation: doing that on a
workstation would reintroduce the duplicate-card race the flow prevents.
"""

from __future__ import annotations

import json
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

import requests


_CARD_ID_RE = re.compile(r"^LT([1-9][0-9]{0,3})$")
_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_CONFIRMED_STATUSES = frozenset({"reserved", "existing", "reused"})
_ALLOWED_STATUSES = _CONFIRMED_STATUSES | frozenset({"manual_review", "blocked"})
_TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})


class AccessCardError(RuntimeError):
    """Base error for the access-card reservation service."""


class AccessCardConfigurationError(AccessCardError):
    """The local service configuration or response contract is invalid."""


class AccessCardAuthenticationRequired(AccessCardError):
    """The user must sign in interactively before background calls can run."""


class AccessCardServiceError(AccessCardError):
    """The Power Automate reservation service could not complete the call."""


class AccessCardPending(AccessCardError):
    """A queued request is awaiting the serialized workbook flow."""


class AccessTokenProvider(Protocol):
    def get_access_token(self, *, interactive: bool = False) -> str:
        """Return an Entra access token for the configured service."""


@dataclass(frozen=True)
class AccessCardReservation:
    status: str
    jira_key: str
    full_name: str
    card_id: str | None
    numeric_part: int | None
    message: str
    workbook_changed: bool
    registry_name: str | None = None

    @property
    def print_name(self) -> str:
        return self.registry_name or self.full_name

    @property
    def is_confirmed(self) -> bool:
        """Only confirmed results are safe to send to the card-printing UI."""
        return self.status in _CONFIRMED_STATUSES

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe result for task storage and the future card UI."""
        return {
            "status": self.status,
            "jira_key": self.jira_key,
            "full_name": self.full_name,
            "card_id": self.card_id,
            "numeric_part": self.numeric_part,
            "message": self.message,
            "workbook_changed": self.workbook_changed,
            "is_confirmed": self.is_confirmed,
            "registry_name": self.print_name,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "AccessCardReservation":
        data = _unwrap_response_payload(payload)
        if not isinstance(data, Mapping):
            raise AccessCardConfigurationError(
                "The access-card flow returned an invalid response object."
            )

        status = str(data.get("status", "")).strip().casefold()
        if status not in _ALLOWED_STATUSES:
            raise AccessCardConfigurationError(
                f"The access-card flow returned an unknown status: {status or '(empty)'}."
            )

        jira_key = str(data.get("jiraKey", "")).strip().upper()
        full_name = " ".join(str(data.get("fullName", "")).split())
        registry_name = " ".join(str(data.get("registryName") or full_name).split())
        message = str(data.get("message", "")).strip()
        card_id_value = data.get("cardId")
        card_id = str(card_id_value).strip().upper() if card_id_value else None
        numeric_value = data.get("numericPart")
        numeric_part: int | None
        if numeric_value in (None, ""):
            numeric_part = None
        else:
            try:
                if isinstance(numeric_value, bool) or not re.fullmatch(r"[0-9]+", str(numeric_value)):
                    raise ValueError("Not an integer card number")
                numeric_part = int(numeric_value)
            except (TypeError, ValueError) as exc:
                raise AccessCardConfigurationError(
                    "The access-card flow returned a non-numeric card number."
                ) from exc

        if not jira_key or not _JIRA_KEY_RE.fullmatch(jira_key):
            raise AccessCardConfigurationError(
                "The access-card flow returned an invalid Jira key."
            )
        if not full_name:
            raise AccessCardConfigurationError(
                "The access-card flow returned an empty employee name."
            )

        if status in _CONFIRMED_STATUSES:
            if normalize_employee_name(registry_name) != normalize_employee_name(full_name):
                raise AccessCardConfigurationError("The registry name does not match the employee.")
            match = _CARD_ID_RE.fullmatch(card_id or "")
            if not match:
                raise AccessCardConfigurationError(
                    "The flow confirmed a reservation without a valid LT1-LT9999 card ID."
                )
            parsed_number = int(match.group(1))
            if numeric_part != parsed_number:
                raise AccessCardConfigurationError(
                    "The flow returned inconsistent card ID and numeric-part values."
                )
        elif card_id is not None or numeric_part is not None:
            raise AccessCardConfigurationError(
                "An unconfirmed access-card result must not contain a printable card ID."
            )

        if "workbookChanged" not in data:
            raise AccessCardConfigurationError(
                "The flow response is missing workbookChanged."
            )
        changed_value = data.get("workbookChanged")
        if not isinstance(changed_value, bool):
            raise AccessCardConfigurationError(
                "The flow returned an invalid workbookChanged value."
            )
        if status in {"existing", "reused", "manual_review"} and changed_value:
            raise AccessCardConfigurationError("An existing or unconfirmed card must not change Excel.")
        if status == "reserved" and not changed_value:
            raise AccessCardConfigurationError("A new card must have a verified workbook write.")

        return cls(
            status=status,
            jira_key=jira_key,
            full_name=full_name,
            card_id=card_id,
            numeric_part=numeric_part,
            message=message,
            workbook_changed=changed_value,
            registry_name=registry_name,
        )


def reservation_request_from_ticket(ticket: Mapping[str, Any]) -> dict[str, str]:
    """Build the flow request from a Jira new-joiner ticket."""
    if ticket.get("kind", "joiner") != "joiner":
        raise AccessCardConfigurationError("Access cards are only prepared for joiners and rejoiners.")
    jira_key = str(ticket.get("key") or "").strip().upper()
    full_name = " ".join(str(ticket.get("name") or "").split())
    if "first_name" in ticket or "last_name" in ticket:
        if not str(ticket.get("first_name") or "").strip() or not str(ticket.get("last_name") or "").strip():
            raise AccessCardConfigurationError("Both the joiner's first and last name are required in Jira.")
        full_name = " ".join(
            part.strip()
            for part in (
                str(ticket.get("first_name") or ""),
                str(ticket.get("last_name") or ""),
            )
            if part.strip()
        )
        full_name = " ".join(full_name.split())

    if not _JIRA_KEY_RE.fullmatch(jira_key):
        raise AccessCardConfigurationError(
            "A valid Jira key is required before reserving an access card."
        )
    if not full_name or len(full_name) > 200:
        raise AccessCardConfigurationError(
            "A non-empty employee name of at most 200 characters is required."
        )
    if full_name[0] in "=+-@":
        raise AccessCardConfigurationError(
            "The employee name starts with a character that is unsafe for Excel."
        )

    rejoiner_value = str(ticket.get("rejoiner") or "").strip().casefold()
    scenario = str(ticket.get("ad_joiner_scenario") or "")
    if rejoiner_value in {"yes", "true", "1", "rejoiner"} or scenario in {"rejoiner_dual", "rejoiner_single"}:
        joiner_type = "rejoiner"
    elif rejoiner_value in {"no", "false", "0", "new_joiner"} or scenario == "new_joiner" or "rejoiner" not in ticket:
        joiner_type = "new_joiner"
    else:
        raise AccessCardConfigurationError("Confirm whether this employee is a new joiner or rejoiner in Jira.")
    return {
        "jiraKey": jira_key,
        "fullName": full_name,
        "joinerType": joiner_type,
    }


def normalize_employee_name(value: str) -> str:
    value = unicodedata.normalize("NFD", " ".join(value.split()))
    value = re.sub(r"[\u0300-\u036f]", "", value).translate(str.maketrans("łŁ", "ll"))
    value = re.sub(r"[’'`.,]", "", value).replace("-", " ")
    return " ".join(value.lower().split())


def queue_request_key(request: Mapping[str, str]) -> str:
    """Stable identity for queued requests across machines and retries."""
    encoded = json.dumps(dict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "card-v1-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cached_reservation_for_ticket(ticket: Mapping[str, Any], cached: Any) -> AccessCardReservation | None:
    """Validate the complete cached response and its binding to the current Jira identity."""
    if not isinstance(cached, Mapping):
        return None
    try:
        request = reservation_request_from_ticket(ticket)
        if (cached.get("jira_key") != request["jiraKey"]
                or cached.get("full_name") != request["fullName"]
                or cached.get("joiner_type") != request["joinerType"]):
            return None
        result = AccessCardReservation.from_payload({
            "status": cached.get("status"), "jiraKey": cached.get("jira_key"),
            "fullName": cached.get("full_name"), "registryName": cached.get("registry_name"),
            "cardId": cached.get("card_id"), "numericPart": cached.get("numeric_part"),
            "message": cached.get("message"), "workbookChanged": cached.get("workbook_changed"),
        })
        if cached.get("is_confirmed") is not result.is_confirmed:
            return None
        if request["joinerType"] == "rejoiner" and (result.status == "reserved" or result.workbook_changed):
            return None
        if request["joinerType"] == "new_joiner" and result.status == "reused":
            return None
        return result
    except AccessCardError:
        return None


class PowerAutomateAccessCardClient:
    """Call the tenant-authenticated access-card reservation flow."""

    def __init__(
        self,
        flow_url: str,
        token_provider: AccessTokenProvider | Callable[..., str],
        *,
        timeout_seconds: float = 45,
        max_attempts: int = 2,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        parsed = urlparse(flow_url.strip())
        if parsed.scheme.casefold() != "https" or not parsed.netloc:
            raise AccessCardConfigurationError(
                "The Power Automate flow URL must be a valid HTTPS address."
            )
        if timeout_seconds <= 0:
            raise AccessCardConfigurationError("The flow timeout must be positive.")
        if max_attempts not in (1, 2, 3):
            raise AccessCardConfigurationError("max_attempts must be between 1 and 3.")

        self._flow_url = flow_url.strip()
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._session = session or requests.Session()
        self._sleeper = sleeper

    def reserve_for_ticket(
        self, ticket: Mapping[str, Any], *, interactive: bool = False
    ) -> AccessCardReservation:
        request_body = reservation_request_from_ticket(ticket)
        token = self._get_token(interactive=interactive)
        if not token:
            raise AccessCardAuthenticationRequired(
                "Sign in to Microsoft before reserving access cards."
            )

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.post(
                    self._flow_url,
                    json=request_body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt < self._max_attempts:
                    self._sleeper(1.0)
                    continue
                raise AccessCardServiceError(
                    "The access-card service could not be reached. No local card ID was created."
                ) from exc

            if response.status_code in (401, 403):
                raise AccessCardAuthenticationRequired(
                    "Microsoft sign-in is missing, expired, or not allowed for this flow."
                )
            if response.status_code in _TRANSIENT_STATUS_CODES and attempt < self._max_attempts:
                self._sleeper(_retry_delay(response))
                continue
            if not 200 <= response.status_code < 300:
                raise AccessCardServiceError(
                    f"The access-card service returned HTTP {response.status_code}. "
                    "No local card ID was created."
                )

            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise AccessCardConfigurationError(
                    "The access-card flow did not return valid JSON."
                ) from exc
            return reservation_from_response(payload, request_body)

        raise AccessCardServiceError(
            "The access-card service did not complete the reservation."
        )

    def _get_token(self, *, interactive: bool) -> str:
        provider = self._token_provider
        try:
            if hasattr(provider, "get_access_token"):
                return provider.get_access_token(interactive=interactive)  # type: ignore[union-attr]
            return provider(interactive=interactive)  # type: ignore[operator]
        except AccessCardError:
            raise
        except Exception as exc:
            raise AccessCardAuthenticationRequired(
                "Microsoft authentication did not provide an access token."
            ) from exc


def _unwrap_response_payload(payload: Any) -> Any:
    """Accept the direct response and common Power Automate result wrappers."""
    current = payload
    for _ in range(3):
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except json.JSONDecodeError as exc:
                raise AccessCardConfigurationError(
                    "The access-card flow returned malformed JSON."
                ) from exc
            continue
        if isinstance(current, Mapping) and set(current) in ({"result"}, {"body"}):
            current = current.get("result", current.get("body"))
            continue
        break
    return current


def reservation_from_response(payload: Any, request: Mapping[str, str]) -> AccessCardReservation:
    """Validate a result against the same identity rules for every transport."""
    result = AccessCardReservation.from_payload(payload)
    if result.jira_key != request["jiraKey"]:
        raise AccessCardConfigurationError("The access-card response belongs to a different Jira ticket.")
    if result.full_name != request["fullName"]:
        raise AccessCardConfigurationError("The access-card response belongs to a different employee name.")
    if request["joinerType"] == "rejoiner" and (result.status == "reserved" or result.workbook_changed):
        raise AccessCardConfigurationError("A rejoiner lookup must never allocate a new card or change Excel.")
    if request["joinerType"] == "new_joiner" and result.status == "reused":
        raise AccessCardConfigurationError("A new joiner must not reuse another employee's historical card.")
    return result


def _retry_delay(response: requests.Response) -> float:
    try:
        return max(0.0, min(float(response.headers.get("Retry-After", "1")), 5.0))
    except (TypeError, ValueError):
        return 1.0
