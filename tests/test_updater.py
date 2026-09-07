import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import updater


class InstalledUpdaterTests(unittest.TestCase):
    def _latest_response(self, tag="v1.3.1"):
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {
            "Location": (
                "https://github.com/IgnasTamosaitis/Jira-onboarding-helper/"
                f"releases/tag/{tag}"
            )
        }
        return response

    def test_installed_build_selects_msi_release_asset(self):
        with (
            patch.object(updater, "IS_FROZEN", True),
            patch.object(updater, "current_version", return_value="1.3.0"),
            patch.object(
                updater.requests,
                "get",
                return_value=self._latest_response(),
            ),
        ):
            release = updater.check_for_update()

        self.assertEqual(release["installer_name"], "Jira-Reminders-1.3.1.msi")
        self.assertEqual(
            release["installer_url"],
            "https://github.com/IgnasTamosaitis/Jira-onboarding-helper/"
            "releases/download/v1.3.1/Jira-Reminders-1.3.1.msi",
        )
        self.assertNotIn("zipball_url", release)

    def test_current_installed_build_is_up_to_date(self):
        with (
            patch.object(updater, "IS_FROZEN", True),
            patch.object(updater, "current_version", return_value="1.3.1"),
            patch.object(
                updater.requests,
                "get",
                return_value=self._latest_response(),
            ),
        ):
            self.assertIsNone(updater.check_for_update())

    def test_source_build_keeps_zip_update_path(self):
        with (
            patch.object(updater, "IS_FROZEN", False),
            patch.object(updater, "current_version", return_value="1.3.0"),
            patch.object(
                updater.requests,
                "get",
                return_value=self._latest_response(),
            ),
        ):
            release = updater.check_for_update()

        self.assertEqual(
            release["zipball_url"],
            "https://github.com/IgnasTamosaitis/Jira-onboarding-helper/"
            "archive/refs/tags/v1.3.1.zip",
        )
        self.assertNotIn("installer_url", release)

    def test_network_failure_is_not_reported_as_up_to_date(self):
        with patch.object(
            updater.requests,
            "get",
            side_effect=requests.ConnectionError("offline"),
        ):
            with self.assertRaisesRegex(updater.UpdateCheckError, "could not be reached"):
                updater.check_for_update()

    def test_unexpected_redirect_is_not_reported_as_up_to_date(self):
        response = self._latest_response()
        response.headers = {}
        with patch.object(updater.requests, "get", return_value=response):
            with self.assertRaisesRegex(updater.UpdateCheckError, "unexpected"):
                updater.check_for_update()

    def test_msi_restart_uses_a_fresh_pyinstaller_runtime(self):
        # The old app's bootloader state survives through WScript/PowerShell,
        # even though its extracted runtime is deleted before the restart.
        inherited_env = {
            "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI110722",
            "_PYI_ARCHIVE_FILE": r"C:\Apps\JiraReminders.exe",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "SYSTEMROOT": r"C:\Windows",
            "TEMP": r"C:\Temp",
        }
        for reset_value in (None, "0", "1"):
            with self.subTest(reset_value=reset_value):
                parent_env = dict(inherited_env)
                if reset_value is not None:
                    parent_env["PYINSTALLER_RESET_ENVIRONMENT"] = reset_value
                with (
                    tempfile.TemporaryDirectory() as temp_dir,
                    patch.dict(os.environ, parent_env, clear=True),
                    patch.object(updater, "IS_FROZEN", True),
                    patch.object(updater, "_STAGING_DIR", Path(temp_dir)),
                    patch.object(updater.subprocess, "Popen") as popen,
                ):
                    updater._launch_msi_update(Path(temp_dir) / "update.msi")

                    popen.assert_called_once()
                    child_env = popen.call_args.kwargs["env"]
                    self.assertEqual(child_env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
                    for name, value in inherited_env.items():
                        self.assertEqual(child_env[name], value)
                    self.assertEqual(dict(os.environ), parent_env)


if __name__ == "__main__":
    unittest.main()
