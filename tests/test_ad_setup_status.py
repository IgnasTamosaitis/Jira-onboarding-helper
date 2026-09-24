import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import tkinter as tk
from unittest.mock import patch

from ad_automation import build_new_joiner_script
from ad_setup_status import build_status_script, check_setup, readback_from_output, recover_setup
from storage import TaskStorage, TASK_SCHEMA_VERSION
from ui import MainWindow


def baseline():
    return {"version": 1, "complete": True, "account": "SF", "object_guid": "SF",
            "attributes": {"Title": "SF title", "Department": "SF department"},
            "groups": ["CN=Normal Group,DC=example,DC=com"],
            "direct_reports": ["CN=REPORT,OU=Source,DC=example,DC=com"],
            "target_ou": "OU=Target,DC=example,DC=com", "email": "target@example.com"}


class HistoryTests(unittest.TestCase):
    def recover(self, text, saved=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.log"
            path.write_text(text, encoding="utf-8")
            return recover_setup({"key": "PNC-123"}, saved or {}, path)

    def entry(self, key="PNC-123", account="SF", output="", status="Completed"):
        return ("\n" + "=" * 60 + "\nTimestamp : 2026-09-24 12:00\n"
                f"Ticket    : {key} Test Person\nAccount   : {account}\n"
                "Scenario  : new_joiner\nEmail     : target@example.com\n"
                f"OU        : OU=Target,DC=example,DC=com\nStatus    : {status}\nOutput:\n{output}\n")

    def test_recovers_exact_ticket_and_failed_groups_without_claiming_full_baseline(self):
        text = self.entry(output="OK  Added group: Normal Group\nWARNING: Group Denied Group: Access denied")
        text += self.entry(key="PNC-1234", account="OTHER")
        result = self.recover(text)
        self.assertEqual(result["account"], "SF")
        self.assertEqual(result["baseline"]["groups"], ["Normal Group", "Denied Group"])
        self.assertFalse(result["baseline"]["complete"])
        self.assertNotIn("completed_at", result)

    def test_latest_failed_attempt_supersedes_older_success(self):
        result = self.recover(self.entry(account="OLD") + self.entry(account="NEW", status="Not completed"))
        self.assertEqual(result["account"], "NEW")
        self.assertFalse(result["baseline"]["complete"])

    def test_legacy_values_are_recovered_without_inventing_missing_fields(self):
        result = self.recover(self.entry(output="OK  EmployeeID copied: 123\n"
            "OK  extensionAttribute10 set to manager@example.com\nDepartment: Finance"))
        self.assertEqual(result["baseline"]["attributes"], {
            "EmployeeID": "123", "extensionAttribute10": "manager@example.com", "Department": "Finance"})

    def test_structured_baseline_is_recovered_even_if_local_summary_lost(self):
        report = {"account": "SF", "baseline": baseline()}
        result = self.recover(self.entry(output="AD_SETUP_READBACK:" + json.dumps(report)))
        self.assertEqual(result["baseline"], baseline())

    def test_failed_execution_cannot_supply_a_complete_baseline(self):
        report = {"account": "SF", "baseline": baseline(), "failures": []}
        result = self.recover(self.entry(output="AD_SETUP_READBACK:" + json.dumps(report), status="Not completed"))
        self.assertFalse(result["baseline"]["complete"])

    def test_wrong_account_readback_and_unrelated_ticket_are_rejected(self):
        self.assertEqual(readback_from_output('AD_SETUP_READBACK:{"account":"OTHER"}', "SF"), {})
        self.assertEqual(self.recover(self.entry(key="PNC-999")), {})


class StatusTests(unittest.TestCase):
    def test_error_partial_drift_and_success_are_distinct(self):
        for complete, failures, code, state in [
            (True, [], 0, "verified"), (False, [], 0, "partial"),
            (True, ["Account is disabled", "Missing group: Test"], 1, "drift"),
            (True, [], 1, "unavailable"),
        ]:
            with self.subTest(state=state):
                evidence = dict(baseline(), complete=complete)
                report = {"account": "SF", "failures": failures, "server": "dc", "current": {"enabled": not failures}}
                with patch("ad_setup_status.run_ps", return_value=("AD_SETUP_READBACK:" + json.dumps(report), "", code)):
                    result = check_setup({"account": "SF", "baseline": evidence})
                self.assertEqual(result["state"], state)
                self.assertEqual(result["verified"], state == "verified")

    def test_connection_failure_does_not_claim_account_is_disabled(self):
        with patch("ad_setup_status.run_ps", side_effect=OSError("offline")):
            result = check_setup({"account": "SF", "baseline": baseline()})
        self.assertEqual(result["state"], "unavailable")
        self.assertNotIn("actual", result)

    def test_historical_completion_and_group_warnings_do_not_require_a_full_baseline(self):
        for historical, disabled, expected in [(True, False, True), (False, False, False), (True, True, False)]:
            with self.subTest(historical=historical, disabled=disabled):
                report = {"account": "SF", "failures": ["Account is disabled"] if disabled else [],
                          "group_warnings": ["Missing group: Restricted"], "current": {"enabled": not disabled}}
                with patch("ad_setup_status.run_ps", return_value=("AD_SETUP_READBACK:" + json.dumps(report), "", 1 if disabled else 0)):
                    result = check_setup({"account": "SF", "baseline": dict(baseline(), complete=False),
                                          "history_completed": historical})
                self.assertEqual(result["completed"], expected)
                self.assertFalse(result["verified"])
                self.assertEqual(result["group_warnings"], report["group_warnings"])

    def test_full_baseline_with_group_advisories_completes(self):
        report = {"account": "SF", "failures": [], "group_warnings": ["Missing group: Restricted"]}
        with patch("ad_setup_status.run_ps", return_value=("AD_SETUP_READBACK:" + json.dumps(report), "", 0)):
            result = check_setup({"account": "SF", "baseline": baseline()})
        self.assertTrue(result["completed"])
        self.assertTrue(result["verified"])

    def test_storage_allows_historical_completion_without_claiming_full_verification(self):
        with patch("storage._load", return_value={"__task_schema_version": TASK_SCHEMA_VERSION}), patch("storage._save"):
            storage = TaskStorage()
            storage.update_ad_verification("123", {"account": "SF"}, {"completed": True, "verified": False})
            self.assertTrue(storage.ad_setup_done("123"))
            self.assertTrue(storage.get("123")[0])
            self.assertFalse(storage._data["__ad_setup_123"]["verified"])

    def test_storage_invalidates_cached_completion_and_preserves_history(self):
        info = {"completed_at": "original date", "account": "SF", "baseline": baseline()}
        with patch("storage._load", return_value={"__task_schema_version": TASK_SCHEMA_VERSION,
                   "__ad_setup_123": info, "123": [True, True, False, True, False]}), patch("storage._save"):
            storage = TaskStorage()
            self.assertFalse(storage.ad_setup_done("123"))
            with patch("storage.time.monotonic", return_value=100):
                storage.update_ad_verification("123", info, {"verified": True})
                self.assertTrue(storage.ad_setup_done("123"))
            with patch("storage.time.monotonic", return_value=221):
                self.assertFalse(storage.ad_setup_done("123"))
            storage.update_ad_verification("123", info, {"verified": False, "state": "drift"})
            self.assertFalse(storage.ad_setup_done("123"))
            self.assertEqual(storage.get("123"), [False, True, False, True, False])
            self.assertEqual(storage._data["__ad_setup_123"]["completed_at"], "original date")

    def test_failed_retry_invalidates_previous_baseline(self):
        info = {"completed_at": "old", "account": "SF", "baseline": baseline()}
        with patch("storage._load", return_value={"__task_schema_version": TASK_SCHEMA_VERSION,
                   "__ad_setup_123": info}), patch("storage._save"):
            storage = TaskStorage()
            storage.mark_ad_setup_incomplete("123", "Not completed")
        self.assertNotIn("baseline", storage._data["__ad_setup_123"])
        self.assertEqual(storage._data["__ad_setup_123"]["previous_baseline"], baseline())


class SummaryTests(unittest.TestCase):
    def test_disabled_account_is_visible_without_completed_badge_or_password_handoff(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        try:
            with patch("storage._load", return_value={"__task_schema_version": TASK_SCHEMA_VERSION}), patch("storage._save"):
                storage = TaskStorage()
                storage.update_ad_verification("123", {"account": "SF", "baseline": baseline(),
                    "completed_at": "old", "sms_template": "secret handoff"},
                    {"state": "drift", "verified": False, "actual": {"enabled": False},
                     "issues": ["Account is disabled", "Missing group: Required"]})
                window = MainWindow(root, [], storage, None, lambda: None)
                try:
                    window._show_ad_setup_summary({"id": "123"})
                    widgets = []
                    def visit(parent):
                        for child in parent.winfo_children():
                            widgets.append(child)
                            visit(child)
                    visit(window._detail)
                    labels = [w.cget("text") for w in widgets if isinstance(w, (tk.Label, tk.Button))]
                    self.assertIn("AD setup needs attention", labels)
                    self.assertIn("Check AD", labels)
                    self.assertNotIn("Copy message", labels)
                    self.assertFalse(storage.ad_setup_done("123"))
                finally:
                    window.destroy()
        finally:
            root.destroy()


@unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell required")
class ReadOnlyDirectoryTests(unittest.TestCase):
    def test_real_generated_verifier_detects_drift_and_never_writes(self):
        setup = build_new_joiner_script(
            {"company_name": "Example", "office": "Vilnius"}, {"username": "SF"},
            "OU=Target,DC=example,DC=com", "target@example.com", ["Normal Group"], buddy_sam="BUDDY")
        harness = Path(__file__).with_name("ad_setup_fake_directory.ps1").read_text(encoding="utf-8")
        cases = [
            ("", []),
            ("$script:users.SF.Enabled=$false", ["Account is disabled"]),
            ("$script:users.SF.MemberOf=@()", []),
            ("$script:users.SF.Title='Changed'; $script:users.SF.LockedOut=$true", ["Attribute mismatch: Title", "Account is locked"]),
            ("$script:users.SF.targetAddress='wrong'; $script:users.SF.proxyAddresses=@()", ["Email or targetAddress mismatch", "Primary SMTP mismatch"]),
            ("$script:users.REPORT.Manager=$null", ["Missing direct report:"]),
            ("$script:users.SF.ObjectGUID='replacement'", ["Attribute mismatch: ObjectGUID"]),
            ("$script:users.SF.pwdLastSet=0", ["Password state is incomplete"]),
            ("function Get-ADGroup { throw 'Access denied' }", []),
        ]
        script = harness + "\n" + setup
        for index, (change, _) in enumerate(cases):
            # Reset to the setup snapshot before each independent drift case.
            if index == 0:
                script += "\n$clean=$script:users.SF.Clone(); $reportManager=$script:users.REPORT.Manager\n"
            script += "\n$script:users.SF=$clean.Clone(); $script:users.REPORT.Manager=$reportManager\n"
            script += change + "\n$before=$script:mutations\ntry {\n"
            expected_baseline = baseline()
            if index == 8:
                expected_baseline["groups"] = ["Unreadable Group"]
            script += build_status_script({"account": "SF", "baseline": expected_baseline})
            script += f"\n}} catch {{ Write-Host ('CAUGHT:' + $_.Exception.Message) }}\nWrite-Host ('WRITES{index}:' + ($script:mutations-$before))\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.ps1"
            path.write_text(script, encoding="utf-8-sig")
            result = subprocess.run(["powershell", "-NoProfile", "-File", str(path)], capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = [json.loads(line.split(":", 1)[1]) for line in result.stdout.splitlines() if line.startswith("AD_SETUP_READBACK:")]
        self.assertEqual(len(reports), len(cases) + 1, result.stdout + result.stderr)
        setup_baseline = reports[0]["baseline"]
        self.assertTrue(setup_baseline["complete"])
        self.assertEqual(setup_baseline["groups"], baseline()["groups"])
        for index, (_, expected) in enumerate(cases):
            with self.subTest(case=index):
                failures = reports[index + 1]["failures"]
                if index == 2:
                    self.assertTrue(any("Missing group:" in warning for warning in reports[index + 1]["group_warnings"]))
                if index == 8:
                    self.assertIn("Cannot read expected group: Unreadable Group", reports[index + 1]["group_warnings"])
                self.assertEqual(len(failures), len(expected), failures)
                for message in expected:
                    self.assertTrue(any(message in failure for failure in failures), failures)
                self.assertIn(f"WRITES{index}:0", result.stdout)
