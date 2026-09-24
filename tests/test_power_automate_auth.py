import unittest

from access_card_registry import (
    AccessCardAuthenticationRequired,
    AccessCardConfigurationError,
)
from power_automate_auth import FLOW_SCOPES, SHAREPOINT_SCOPES, PowerAutomateAuthenticator


TENANT_ID = "11111111-1111-4111-8111-111111111111"
CLIENT_ID = "22222222-2222-4222-8222-222222222222"


class _FakeApp:
    def __init__(self, *, silent_result=None, interactive_result=None):
        self.silent_result = silent_result
        self.interactive_result = interactive_result
        self.interactive_calls = []
        self.removed_accounts = []

    def get_accounts(self):
        return [{"home_account_id": "test-account"}]

    def acquire_token_silent(self, scopes, account):
        self.silent_scopes = scopes
        self.silent_account = account
        return self.silent_result

    def acquire_token_interactive(self, **kwargs):
        self.interactive_calls.append(kwargs)
        return self.interactive_result

    def remove_account(self, account):
        self.removed_accounts.append(account)


class PowerAutomateAuthenticatorTests(unittest.TestCase):
    def _auth_with_app(self, app):
        auth = PowerAutomateAuthenticator(TENANT_ID, CLIENT_ID)
        auth._app = app
        return auth

    def test_silent_cache_is_checked_before_interactive_login(self):
        app = _FakeApp(silent_result={"access_token": "cached-token"})
        auth = self._auth_with_app(app)

        token = auth.get_access_token(interactive=True)

        self.assertEqual(token, "cached-token")
        self.assertEqual(app.silent_scopes, FLOW_SCOPES)
        self.assertEqual(app.interactive_calls, [])

    def test_background_call_never_opens_browser(self):
        app = _FakeApp(silent_result=None)
        auth = self._auth_with_app(app)

        with self.assertRaises(AccessCardAuthenticationRequired):
            auth.get_access_token(interactive=False)

        self.assertEqual(app.interactive_calls, [])

    def test_explicit_sign_in_uses_flow_scope(self):
        app = _FakeApp(
            silent_result=None,
            interactive_result={"access_token": "interactive-token"},
        )
        auth = self._auth_with_app(app)

        token = auth.get_access_token(interactive=True)

        self.assertEqual(token, "interactive-token")
        self.assertEqual(app.interactive_calls, [{"scopes": FLOW_SCOPES}])

    def test_rejects_non_guid_tenant_and_client_ids(self):
        for tenant_id, client_id in (
            ("organizations", CLIENT_ID),
            (TENANT_ID, "not-a-client-id"),
        ):
            with self.subTest(tenant_id=tenant_id, client_id=client_id):
                with self.assertRaises(AccessCardConfigurationError):
                    PowerAutomateAuthenticator(tenant_id, client_id)

    def test_sharepoint_uses_graph_scope_and_a_separate_encrypted_cache(self):
        fake = _FakeApp(interactive_result={"access_token": "graph-token"})
        auth = PowerAutomateAuthenticator(TENANT_ID, CLIENT_ID, service="sharepoint")
        auth._app = fake
        self.assertEqual(auth.get_access_token(interactive=True), "graph-token")
        self.assertEqual(fake.interactive_calls, [{"scopes": SHAREPOINT_SCOPES}])
        self.assertEqual(fake.silent_scopes, SHAREPOINT_SCOPES)
        flow_auth = PowerAutomateAuthenticator(TENANT_ID, CLIENT_ID)
        self.assertNotEqual(auth.cache_path, flow_auth.cache_path)


if __name__ == "__main__":
    unittest.main()
