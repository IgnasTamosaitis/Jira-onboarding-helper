"""Non-blocking card requests through a SharePoint list and Microsoft Graph.

The desktop app only creates request items and reads results. A single Power
Automate flow runs the Office Script and writes the result back to the item.
Title is a deterministic, unique request key, including across PCs and restarts.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import quote, unquote, urlparse
from uuid import UUID

import requests

from access_card_registry import (
    AccessCardAuthenticationRequired, AccessCardConfigurationError, AccessCardError,
    AccessCardPending, AccessCardServiceError, AccessTokenProvider,
    reservation_from_response, reservation_request_from_ticket,
    queue_request_key,
)


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
QUEUE_FIELDS = ("Title", "JiraKey", "FullName", "JoinerType", "QueueStatus", "ResultJson", "ErrorMessage")


def validate_list_location(site_url: str, list_id: str) -> tuple[str, str]:
    """Accept a SharePoint Online site root and the real list GUID."""
    parsed = urlparse(site_url.strip().rstrip("/"))
    if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.sharepoint\.com", parsed.netloc)
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or not re.fullmatch(r"/(?:sites|teams|personal)/[A-Za-z0-9_.%\-]+", parsed.path)
            or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))):
        raise AccessCardConfigurationError("Enter the SharePoint site address, ending before /Lists/ or /_layouts/.")
    try:
        clean_id = str(UUID(list_id.strip().strip("{}")))
    except (ValueError, AttributeError) as exc:
        raise AccessCardConfigurationError("The SharePoint list ID must be its GUID from List settings.") from exc
    return parsed.geturl(), clean_id


class SharePointAccessCardClient:
    def __init__(self, site_url: str, list_id: str, token_provider: AccessTokenProvider,
                 *, session: requests.Session | None = None, timeout_seconds: float = 20):
        self.site_url, self.list_id = validate_list_location(site_url, list_id)
        if timeout_seconds <= 0:
            raise AccessCardConfigurationError("The SharePoint timeout must be positive.")
        self._token_provider = token_provider
        self._session = session or requests.Session()
        self._timeout = timeout_seconds
        self._list_path: str | None = None

    def reserve_for_ticket(self, ticket: Mapping[str, Any], *, interactive: bool = False):
        request = reservation_request_from_ticket(ticket)
        try:
            token = self._token_provider.get_access_token(interactive=interactive)
        except AccessCardError:
            raise
        except Exception as exc:
            raise AccessCardAuthenticationRequired("Sign in to the access-card service in Settings.") from exc
        if not token:
            raise AccessCardAuthenticationRequired("Sign in to the access-card service in Settings.")
        self._resolve_list(token)
        key = queue_request_key(request)
        item = self._find_request(token, key)
        if item is None:
            fields = {"Title": key, "JiraKey": request["jiraKey"], "FullName": request["fullName"],
                      "JoinerType": request["joinerType"], "QueueStatus": "Pending"}
            try:
                response = self._send("POST", self._list_path + "/items", token, json={"fields": fields})
            except AccessCardServiceError as exc:
                # The write may have succeeded. The next poll looks up the same
                # unique Title; it never blindly retries a possibly committed POST.
                raise AccessCardPending("Checking whether SharePoint received the request…") from exc
            if response.status_code in (400, 409):
                # Concurrent creation with unique Title can return either code.
                item = self._find_request(token, key)
                if item is None:
                    raise AccessCardConfigurationError("SharePoint rejected the request. Check the list column setup.")
            else:
                self._check_response(response)
                raise AccessCardPending("Request queued. Waiting for Excel confirmation…")
        return self._read_result(item, request, key)

    def _resolve_list(self, token: str) -> None:
        if self._list_path:
            return
        parsed = urlparse(self.site_url)
        site = self._get(f"/sites/{parsed.netloc}:{parsed.path}", token, params={"$select": "id"})
        site_id = site.get("id")
        if not isinstance(site_id, str) or not site_id:
            raise AccessCardConfigurationError("Microsoft Graph did not return a SharePoint site ID.")
        list_path = f"/sites/{quote(site_id, safe=',')}/lists/{self.list_id}"
        columns = list(self._collection(list_path + "/columns", token))
        by_name = {column.get("name"): column for column in columns}
        for name in QUEUE_FIELDS:
            column = by_name.get(name, {})
            definition = column.get("text")
            if not isinstance(definition, dict) or column.get("readOnly"):
                raise AccessCardConfigurationError(f"Create the {name} text column in the request list as described in the guide.")
            multiline = name in {"ResultJson", "ErrorMessage"}
            if bool(definition.get("allowMultipleLines")) != multiline:
                raise AccessCardConfigurationError(f"Check the single/multiple-lines setting for {name} in the request list.")
            if multiline and (definition.get("textType", "plain") != "plain" or definition.get("appendChangesToExistingText")):
                raise AccessCardConfigurationError(f"Set {name} to plain text with append changes disabled.")
        title = by_name["Title"]
        if title.get("enforceUniqueValues") is not True or title.get("indexed") is not True:
            raise AccessCardConfigurationError("Enable unique values on the request list's Title column before connecting the app.")
        self._list_path = list_path

    def _find_request(self, token: str, key: str) -> dict | None:
        payload = self._get(self._list_path + "/items", token, params={
            "$filter": f"fields/Title eq '{key}'", "$expand": "fields", "$top": "2",
        })
        items = payload.get("value")
        if not isinstance(items, list) or len(items) > 1 or payload.get("@odata.nextLink"):
            raise AccessCardConfigurationError("The request list returned an ambiguous or invalid request lookup.")
        if items and not isinstance(items[0], dict):
            raise AccessCardConfigurationError("The request list returned an invalid item.")
        return items[0] if items else None

    def _read_result(self, item: dict, request: Mapping[str, str], key: str):
        fields = item.get("fields")
        if not isinstance(fields, dict) or any(fields.get(name) != value for name, value in {
                "Title": key, "JiraKey": request["jiraKey"], "FullName": request["fullName"],
                "JoinerType": request["joinerType"],
        }.items()):
            raise AccessCardConfigurationError("The queued request belongs to a different ticket or employee.")
        status = fields.get("QueueStatus")
        if status in {"Pending", "Processing"}:
            raise AccessCardPending("Request queued. Waiting for Excel confirmation…")
        if status == "Failed":
            raise AccessCardServiceError("The workbook flow failed. Check its run history, resubmit the run, then refresh card details.")
        if status != "Completed" or not isinstance(fields.get("ResultJson"), str) or not fields["ResultJson"]:
            raise AccessCardConfigurationError("The request has no valid completed result. Check the flow's Update item action.")
        return reservation_from_response(fields["ResultJson"], request)

    def _collection(self, path: str, token: str):
        # Metadata pagination is bounded and must stay under the same Graph path.
        url = path
        for _ in range(20):
            payload = self._get(url, token)
            values = payload.get("value")
            if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
                raise AccessCardConfigurationError("SharePoint returned invalid list metadata.")
            yield from values
            next_url = payload.get("@odata.nextLink")
            if not next_url:
                return
            if not isinstance(next_url, str) or not next_url.startswith(GRAPH_ROOT + path + "?"):
                raise AccessCardConfigurationError("SharePoint returned an unexpected metadata continuation URL.")
            url = next_url
        raise AccessCardConfigurationError("SharePoint list metadata pagination exceeded the supported limit.")

    def _get(self, path: str, token: str, **kwargs) -> dict:
        response = self._send("GET", path, token, **kwargs)
        self._check_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AccessCardConfigurationError("SharePoint returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise AccessCardConfigurationError("SharePoint returned an invalid response.")
        return payload

    def _send(self, method: str, path: str, token: str, **kwargs):
        url = path if path.startswith(GRAPH_ROOT + "/") else GRAPH_ROOT + path
        try:
            return self._session.request(method, url, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/json",
                "Content-Type": "application/json",
            }, timeout=self._timeout, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise AccessCardServiceError("The SharePoint request queue could not be reached. Refresh to retry.") from exc

    @staticmethod
    def _check_response(response) -> None:
        if response.status_code == 401:
            raise AccessCardAuthenticationRequired("Microsoft sign-in has expired. Sign in again in Settings.")
        if response.status_code == 403:
            raise AccessCardConfigurationError("SharePoint denied access. Check the user's list access and the Entra app's selected-site grant.")
        if response.status_code == 404:
            raise AccessCardConfigurationError("SharePoint could not find the configured site or request list.")
        if not 200 <= response.status_code < 300:
            raise AccessCardServiceError(f"The SharePoint request queue returned HTTP {response.status_code}. Refresh to retry.")
