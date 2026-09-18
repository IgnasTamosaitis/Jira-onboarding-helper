import inspect
import re
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from ad_automation import (
    build_new_joiner_script,
    build_rejoiner_dual_script,
    build_rejoiner_single_script,
)
from ad_ui import ADSetupWindow
from snipeit_client import SnipeITClient


class PasswordSafetyTests(unittest.TestCase):
    def test_all_ad_setup_scenarios_reset_and_validate_the_required_password(self):
        sf_account = {"username": "TESTSF"}
        old_account = {"username": "TESTOLD"}
        args = ("OU=Test,DC=example,DC=com", "test@example.com", [])
        cases = (
            (build_new_joiner_script, ({}, sf_account, *args), "TESTSF"),
            (build_rejoiner_dual_script, ({}, sf_account, old_account, *args), "TESTOLD"),
            (build_rejoiner_single_script, ({}, old_account, *args), "TESTOLD"),
        )
        for builder, arguments, username in cases:
            with self.subTest(scenario=builder.__name__):
                script = builder(*arguments)
                self.assertEqual(
                    re.findall(r"(?m)^\$plainPassword = '([^']*)'$", script),
                    ["Welcome123"],
                )
                self.assertIn(
                    "$securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force",
                    script,
                )
                self.assertIn(
                    f"Set-ADAccountPassword -Identity '{username}' -Reset -NewPassword $securePassword",
                    script,
                )
                self.assertIn(f"$ctx.ValidateCredentials('{username}', $plainPassword)", script)


class ADPasswordWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk display unavailable: {error}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.window = ADSetupWindow(self.root, {"id": "test-ticket"}, storage=Mock())
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.window._sf_account = {"username": "TESTSF"}
        self.window._old_account = {"username": "TESTOLD"}
        self.window._email_var.set("test@example.com")
        self.window._ou_var.set("OU=Test,DC=example,DC=com")

    def test_password_stays_fixed_when_editing_or_toggling_visibility(self):
        window = self.window
        self.assertEqual(window._pwd_var.get(), "Welcome123")
        window._pwd_entry.delete(0, "end")
        window._pwd_entry.insert(0, "AnotherPassword#42")
        self.assertEqual(window._pwd_var.get(), "Welcome123")
        for expected_mask in ("", "*"):
            window._toggle_pwd_visibility()
            self.assertEqual(window._pwd_entry.cget("show"), expected_mask)
            self.assertEqual(window._pwd_entry.cget("state"), "readonly")
            self.assertEqual(window._pwd_var.get(), "Welcome123")

    def test_review_copy_execution_and_handoff_keep_the_required_password(self):
        window = self.window
        for scenario in ("new_joiner", "rejoiner_dual", "rejoiner_single"):
            with self.subTest(scenario=scenario):
                window._scenario = scenario
                window._generate_preview()
                script = window._script_box.get("1.0", "end-1c")
                self.assertIn("$plainPassword = 'Welcome123'", script)
                window._script_box.delete("1.0", "end")
                window._script_box.insert("1.0", "$plainPassword = 'AnotherPassword#42'")
                self.assertEqual(window._script_box.get("1.0", "end-1c"), script)
                with patch.object(window, "_copy_sensitive") as copy:
                    window._copy_script()
                copy.assert_called_once_with(script.strip())
                with (
                    patch("ad_ui.messagebox.askyesno", return_value=True),
                    patch("ad_ui.threading.Thread") as thread,
                    patch("ad_ui.run_ps", return_value=("OK", "", 0)) as run,
                    patch.object(window, "_show_busy"),
                    patch.object(window, "after"),
                ):
                    window._run_script()
                    thread.call_args.kwargs["target"]()
                run.assert_called_once_with(script.strip(), timeout=120)
                window._mark_setup_completed("Completed")
                self.assertEqual(
                    window.storage.mark_ad_setup.call_args.args[1]["password"],
                    "Welcome123",
                )


class SnipeITReadOnlyTests(unittest.TestCase):
    def test_client_has_no_inventory_mutation_request(self):
        source = inspect.getsource(SnipeITClient)

        self.assertIsNone(
            re.search(r"\.\s*(post|put|patch|delete)\s*\(", source, re.IGNORECASE)
        )


if __name__ == "__main__":
    unittest.main()
