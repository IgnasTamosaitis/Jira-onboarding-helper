import unittest
from unittest.mock import patch

from storage import DEFAULT_TASK_COUNT, TASK_SCHEMA_VERSION, TaskStorage
from ui import TASKS


class ChecklistDefinitionTests(unittest.TestCase):
    def test_current_checklist_contains_only_the_five_requested_tasks(self):
        self.assertEqual(len(TASKS), DEFAULT_TASK_COUNT)
        self.assertEqual(
            TASKS,
            [
                "Active Directory account setup",
                "Axapta account import/creation",
                "AX user relations assignment",
                "Assign hardware & licenses in Snipe-IT",
                "Physical access card creation",
            ],
        )


class TaskStorageMigrationTests(unittest.TestCase):
    def _load(self, data: dict) -> tuple[TaskStorage, object]:
        with patch("storage._load", return_value=data), patch("storage._save") as save:
            task_storage = TaskStorage()
        return task_storage, save

    def test_six_item_state_keeps_only_still_relevant_completion(self):
        task_storage, save = self._load(
            {
                "ticket-1": [True, False, True, True, True, False],
                "__notes_ticket-1": "Keep this note",
            }
        )

        self.assertEqual(
            task_storage.get("ticket-1"),
            [True, False, False, True, False],
        )
        self.assertEqual(task_storage.get_notes("ticket-1"), "Keep this note")
        self.assertEqual(
            task_storage._data["__task_schema_version"], TASK_SCHEMA_VERSION
        )
        save.assert_called_once()

    def test_older_five_item_state_preserves_snipeit_and_physical_access(self):
        task_storage, _save = self._load(
            {"ticket-2": [False, True, True, False, True]}
        )

        self.assertEqual(
            task_storage.get("ticket-2"),
            [False, True, False, False, True],
        )

    def test_current_schema_is_not_migrated_again(self):
        current = [True, True, True, False, True]
        task_storage, save = self._load(
            {
                "__task_schema_version": TASK_SCHEMA_VERSION,
                "ticket-3": current,
            }
        )

        self.assertEqual(task_storage.get("ticket-3"), current)
        save.assert_not_called()

    def test_new_ad_password_is_kept_out_of_task_json(self):
        with (
            patch(
                "storage._load",
                return_value={"__task_schema_version": TASK_SCHEMA_VERSION},
            ),
            patch("storage._save") as save,
            patch("storage._save_ad_password") as save_password,
        ):
            task_storage = TaskStorage()
            task_storage.mark_ad_setup(
                "ticket-4",
                {
                    "completed_at": "2026-09-04 12:00",
                    "account": "TESTUSER",
                    "password": "Temporary#42",
                },
            )

        saved_record = task_storage._data["__ad_setup_ticket-4"]
        self.assertNotIn("password", saved_record)
        self.assertEqual(
            task_storage.get_ad_setup("ticket-4")["password"], "Temporary#42"
        )
        save_password.assert_called_once_with("ticket-4", "Temporary#42")
        self.assertNotIn("password", save.call_args.args[1]["__ad_setup_ticket-4"])

    def test_legacy_plaintext_ad_password_is_scrubbed(self):
        with (
            patch(
                "storage._load",
                return_value={
                    "__task_schema_version": TASK_SCHEMA_VERSION,
                    "__ad_setup_ticket-5": {
                        "completed_at": "2026-09-04 12:00",
                        "password": "Legacy#42",
                    },
                },
            ),
            patch("storage._save") as save,
            patch("storage._save_ad_password") as save_password,
        ):
            task_storage = TaskStorage()

        self.assertNotIn("password", task_storage._data["__ad_setup_ticket-5"])
        self.assertEqual(
            task_storage.get_ad_setup("ticket-5")["password"], "Legacy#42"
        )
        save_password.assert_called_once_with("ticket-5", "Legacy#42")
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
