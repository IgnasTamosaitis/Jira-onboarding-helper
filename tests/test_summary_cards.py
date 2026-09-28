import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from ad_ui import ADSetupWindow
from ui import MainWindow


class SummaryCardTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.info = {
            "account": "TESTUSER", "password": "Example123",
            "phone": "+370 +37060000000", "groups_count": 20,
            "target_ou": "OU=Old,DC=example,DC=com", "email": "unused@example.com",
            "recovered_from": "Audit log", "sms_template": "Old message with an old username",
            "live_check": {"state": "verified", "checked_at": "2026-09-25 14:25:36",
                "server": "internal-server", "actual": {"groups": ["A", "B"],
                    "ou": "OU=Current,DC=example,DC=com", "enabled": True}},
        }
        self.storage = Mock()
        self.storage.get_ad_setup.return_value = self.info
        self.storage.ad_setup_done.return_value = True
        self.window = MainWindow(self.root, [], self.storage, Mock(), Mock())
        self.window.withdraw()

    def render(self):
        self.window._show_ad_setup_summary({"id": "test"})
        widgets = []
        def visit(parent):
            for child in parent.winfo_children():
                widgets.append(child)
                visit(child)
        visit(self.window._ad_summary_box)
        texts = [str(widget.cget("text")) for widget in widgets if isinstance(widget, tk.Label)]
        texts += [widget.get("1.0", "end-1c") for widget in widgets if isinstance(widget, tk.Text)]
        buttons = {widget.cget("text"): widget for widget in widgets if isinstance(widget, tk.Button)}
        return texts, buttons

    def test_verified_card_contains_handoff_fields_and_current_directory_values(self):
        texts, buttons = self.render()
        for value in ("AD setup verified", "Username", "TESTUSER", "Password", "Example123",
                      "Groups", "2", "Current OU", "OU=Current,DC=example,DC=com", "+37060000000"):
            self.assertIn(value, texts)
        for unwanted in ("unused@example.com", "internal-server", "Audit log", "Last checked", "OU=Old"):
            self.assertNotIn(unwanted, "\n".join(texts))
        self.assertIn("Copy message", buttons)

    def test_copy_actions_use_the_correct_values_and_current_username_template(self):
        _, buttons = self.render()
        with patch.object(self.window, "_copy_to_clipboard") as copy:
            buttons["Copy username"].invoke()
            copy.assert_called_with("TESTUSER")
            buttons["Copy phone"].invoke()
            copy.assert_called_with("+37060000000")
        with patch.object(self.window, "_copy_sensitive_to_clipboard") as copy:
            buttons["Copy password"].invoke()
            copy.assert_called_with("Example123")
            buttons["Copy message"].invoke()
            copy.assert_called_with(ADSetupWindow._sms_template("TESTUSER"))
            self.assertNotIn("Example123", copy.call_args.args[0])

    def test_username_message_is_available_without_a_locally_stored_password(self):
        self.info.pop("password")
        texts, buttons = self.render()
        self.assertIn("Not stored on this computer", texts)
        self.assertNotIn("Copy password", buttons)
        self.assertIn("Copy message", buttons)

    def test_failed_verification_keeps_issues_visible_without_password_handoff(self):
        self.storage.ad_setup_done.return_value = False
        self.info["live_check"].update(state="drift", issues=["Account is disabled"])
        texts, buttons = self.render()
        self.assertIn("AD setup needs attention", texts)
        self.assertIn("Account is disabled", texts)
        self.assertNotIn("Example123", texts)
        self.assertNotIn("Copy password", buttons)
        self.assertNotIn("Copy message", buttons)


if __name__ == "__main__":
    unittest.main()
