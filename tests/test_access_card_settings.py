import queue
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import App
from ui import SetupDialog
from onedrive_card_queue import QUEUE_FOLDERS, OneDriveAccessCardClient


SITE = "https://example-my.sharepoint.com/personal/operator_example_com"
LIST_ID = "4e3b4aff-1c0a-43f6-ba8c-12a7be6e651e"
TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"


class CardSettingsTests(unittest.TestCase):
    def collect(self, **changes):
        values = {**SetupDialog.DEFAULTS, "email": "test@example.com", "api_token": "test-token", **changes}
        variables = {key: Mock(get=Mock(return_value=str(value))) for key, value in values.items()}
        return SetupDialog._collect(SimpleNamespace(_vars=variables))

    def list_config(self, **changes):
        return self.collect(**{ "access_card_site_url": SITE, "access_card_list_id": LIST_ID,
            "access_card_tenant_id": TENANT, "access_card_client_id": CLIENT, **changes})

    def test_list_connection_does_not_require_http_url(self):
        result = self.list_config(access_card_list_id="{" + LIST_ID + "}", access_card_site_url=SITE + "/")
        self.assertEqual(result["access_card_site_url"], SITE)
        self.assertEqual(result["access_card_list_id"], LIST_ID)
        self.assertEqual(result["access_card_flow_url"], "")

    def test_incomplete_or_ambiguous_connection_cannot_be_saved(self):
        for changes in ({"access_card_list_id": ""}, {"access_card_tenant_id": ""},
                        {"access_card_client_id": ""}, {"access_card_flow_url": "https://example.test/flow"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.list_config(**changes)

    def test_existing_http_settings_and_disabled_integration_still_work(self):
        self.assertEqual(self.collect()["access_card_site_url"], "")
        result = self.collect(access_card_flow_url="https://example.test/flow",
                              access_card_tenant_id=TENANT, access_card_client_id=CLIENT)
        self.assertEqual(result["access_card_flow_url"], "https://example.test/flow")

    def test_invalid_queue_location_shows_error_and_keeps_settings_open(self):
        for changes in ({"access_card_sync_folder": "relative-folder"},
                        {"access_card_site_url": "https://example.com/sites/cards",
                         "access_card_list_id": LIST_ID,
                         "access_card_tenant_id": TENANT, "access_card_client_id": CLIENT}):
            with self.subTest(changes=changes):
                dialog = Mock(result=None)
                dialog._collect.side_effect = lambda: self.collect(**changes)
                with patch("ui.messagebox.showerror") as show_error:
                    SetupDialog._save(dialog)
                show_error.assert_called_once()
                dialog.destroy.assert_not_called()
                self.assertIsNone(dialog.result)

    def test_app_creates_graph_client_and_clears_previous_registry_display(self):
        target = App.__new__(App)
        target._access_card_source = "previous"
        target._tickets_lock = threading.Lock()
        target._ui_queue = queue.Queue()
        target._tickets = [{"id": "1", "access_card": {"card_id": "LT5053"}, "access_card_pending": True}]
        target._window = Mock()
        with patch("app.PowerAutomateAuthenticator") as auth, patch("app.SharePointAccessCardClient") as client:
            target._apply_config(self.list_config())
        auth.assert_called_once_with(TENANT, CLIENT, service="sharepoint")
        client.assert_called_once_with(SITE, LIST_ID, auth.return_value)
        self.assertEqual(target._tickets, [{"id": "1"}])
        self.assertEqual(target._ui_queue.get_nowait()[0], "update_tickets")

    def test_folder_connection_needs_no_credentials_and_clears_old_api_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in QUEUE_FOLDERS:
                (Path(folder) / name).mkdir()
            cfg = self.collect(access_card_sync_folder=folder, access_card_site_url=SITE,
                               access_card_list_id=LIST_ID)
            for name in ("access_card_site_url", "access_card_list_id", "access_card_tenant_id",
                         "access_card_client_id", "access_card_flow_url"):
                self.assertEqual(cfg[name], "")
            target = App.__new__(App)
            target._access_card_source = ""
            target._tickets_lock = threading.Lock()
            target._tickets = []
            target._ui_queue = queue.Queue()
            target._window = None
            with patch("app.PowerAutomateAuthenticator") as authenticator:
                target._apply_config(cfg)
            authenticator.assert_not_called()
            self.assertIsInstance(target._access_card_client, OneDriveAccessCardClient)


if __name__ == "__main__":
    unittest.main()
