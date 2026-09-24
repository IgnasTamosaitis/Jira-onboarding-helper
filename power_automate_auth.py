"""Microsoft Entra authentication for the access-card Power Automate flow."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from access_card_registry import (
    AccessCardAuthenticationRequired,
    AccessCardConfigurationError,
)


FLOW_SCOPES = ["https://service.flow.microsoft.com//.default"]
SHAREPOINT_SCOPES = ["https://graph.microsoft.com/Sites.Selected"]
_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class PowerAutomateAuthenticator:
    """Acquire Flow tokens and keep them in a Windows DPAPI-protected cache."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        *,
        cache_path: Path | None = None,
        service: str = "flow",
    ):
        self.tenant_id = tenant_id.strip()
        self.client_id = client_id.strip()
        if not _GUID_RE.fullmatch(self.tenant_id):
            raise AccessCardConfigurationError(
                "The Microsoft tenant ID must be a GUID."
            )
        if not _GUID_RE.fullmatch(self.client_id):
            raise AccessCardConfigurationError(
                "The Microsoft desktop-app client ID must be a GUID."
            )
        if os.name != "nt":
            raise AccessCardConfigurationError(
                "The encrypted Power Automate token cache currently requires Windows."
            )

        if service not in {"flow", "sharepoint"}:
            raise AccessCardConfigurationError("Unknown access-card authentication service.")
        self.scopes = SHAREPOINT_SCOPES if service == "sharepoint" else FLOW_SCOPES
        cache_name = "sharepoint-token-cache.bin" if service == "sharepoint" else "power-automate-token-cache.bin"

        self.cache_path = cache_path or (
            Path.home() / ".jira-reminders" / cache_name
        )
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._app: Any | None = None

    def get_access_token(self, *, interactive: bool = False) -> str:
        app = self._get_app()
        for account in app.get_accounts():
            result = app.acquire_token_silent(self.scopes, account=account)
            token = _token_from_result(result)
            if token:
                return token

        if not interactive:
            raise AccessCardAuthenticationRequired(
                "No cached Microsoft sign-in is available. Sign in once before background reservations run."
            )

        result = app.acquire_token_interactive(
            scopes=self.scopes,
        )
        token = _token_from_result(result)
        if token:
            return token
        detail = _safe_auth_error(result)
        raise AccessCardAuthenticationRequired(
            f"Microsoft sign-in did not complete{detail}."
        )

    def sign_out(self) -> None:
        app = self._get_app()
        for account in app.get_accounts():
            app.remove_account(account)

    def _get_app(self) -> Any:
        if self._app is not None:
            return self._app
        try:
            import msal
            from msal_extensions import (
                FilePersistenceWithDataProtection,
                PersistedTokenCache,
            )
        except ImportError as exc:
            raise AccessCardConfigurationError(
                "Microsoft authentication dependencies are not installed."
            ) from exc

        try:
            persistence = FilePersistenceWithDataProtection(str(self.cache_path))
            cache = PersistedTokenCache(persistence)
            self._app = msal.PublicClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                token_cache=cache,
            )
        except Exception as exc:
            raise AccessCardConfigurationError(
                "The encrypted Microsoft sign-in cache could not be initialized."
            ) from exc
        return self._app


def _token_from_result(result: Any) -> str:
    if isinstance(result, dict):
        token = result.get("access_token")
        if isinstance(token, str):
            return token
    return ""


def _safe_auth_error(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    code = str(result.get("error", "")).strip()
    correlation_id = str(result.get("correlation_id", "")).strip()
    parts = []
    if code:
        parts.append(code)
    if correlation_id:
        parts.append(f"correlation {correlation_id}")
    return f" ({', '.join(parts)})" if parts else ""
