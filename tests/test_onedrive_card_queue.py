from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from access_card_registry import (
    AccessCardConfigurationError, AccessCardPending, AccessCardServiceError,
    queue_request_key, reservation_request_from_ticket,
)
from onedrive_card_queue import MAX_QUEUE_FILE_BYTES, QUEUE_FOLDERS, OneDriveAccessCardClient, validate_sync_folder


TICKET = {"id": "10001", "key": "GSD-123", "name": "Aistė Žukaitė", "rejoiner": "No"}


class OneDriveQueueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        for name in QUEUE_FOLDERS:
            (self.root / name).mkdir()
        self.client = OneDriveAccessCardClient(str(self.root))

    def expected(self, ticket=None):
        request = reservation_request_from_ticket(ticket or TICKET)
        return {"requestKey": queue_request_key(request), **request}

    def path(self, folder, ticket=None):
        return self.root / folder / (self.expected(ticket)["requestKey"] + ".json")

    def queue(self, ticket=None, client=None):
        with self.assertRaises(AccessCardPending):
            (client or self.client).reserve_for_ticket(ticket or TICKET)

    def result(self, ticket=None, **changes):
        expected = self.expected(ticket)
        result = {"status": "reserved", "jiraKey": expected["jiraKey"], "fullName": expected["fullName"],
                  "registryName": expected["fullName"], "cardId": "LT5053", "numericPart": 5053,
                  "message": "Confirmed", "workbookChanged": True, **changes}
        envelope = {"request": expected, "result": result}
        self.path("Results", ticket).write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        return envelope

    def test_request_is_published_complete_and_reused_after_restart(self):
        self.queue()
        request_file = self.path("Requests")
        original = request_file.read_bytes()
        self.assertEqual(json.loads(original), self.expected())
        self.assertEqual(list((self.root / "Staging").iterdir()), [])
        with patch("onedrive_card_queue.tempfile.NamedTemporaryFile") as create:
            self.queue(client=OneDriveAccessCardClient(str(self.root)))
        create.assert_not_called()
        self.assertEqual(request_file.read_bytes(), original)

    def test_result_returns_verified_card_without_network_or_excel_mutations(self):
        self.result()
        with patch("requests.Session.request", side_effect=AssertionError("No API access")):
            result = self.client.reserve_for_ticket(TICKET, interactive=True)
        self.assertEqual(result.card_id, "LT5053")
        self.assertEqual(result.print_name, "Aistė Žukaitė")
        self.assertEqual(list((self.root / "Requests").iterdir()), [])

    def test_archive_arriving_before_result_does_not_resubmit(self):
        self.queue()
        self.path("Requests").rename(self.path("Processed"))
        self.queue(client=OneDriveAccessCardClient(str(self.root)))
        self.assertFalse(self.path("Requests").exists())
        self.result(status="existing", workbookChanged=False)
        self.assertEqual(self.client.reserve_for_ticket(TICKET).status, "existing")

    def test_partial_json_or_unavailable_file_keeps_export_pending(self):
        path = self.path("Results")
        for content in (b'{"result":', b'\xff', b''):
            path.write_bytes(content)
            self.queue()
            self.assertFalse(self.path("Requests").exists())
        with patch.object(Path, "open", side_effect=PermissionError("Sync file unavailable")):
            self.queue()
        self.result()
        self.assertTrue(self.client.reserve_for_ticket(TICKET).is_confirmed)

    def test_wrong_request_or_invalid_result_never_creates_or_prints_a_card(self):
        for value in (None, [], {"request": self.expected(), "result": None},
                      {"request": {**self.expected(), "joinerType": "rejoiner"}, "result": {}}):
            self.path("Results").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(AccessCardConfigurationError):
                self.client.reserve_for_ticket(TICKET)
            self.assertFalse(self.path("Requests").exists())
        for changes in ({"numericPart": 5054}, {"jiraKey": "GSD-124"}, {"registryName": "Wrong Name"}):
            self.result(**changes)
            with self.assertRaises(AccessCardConfigurationError):
                self.client.reserve_for_ticket(TICKET)

    def test_conflicting_request_is_not_overwritten(self):
        path = self.path("Requests")
        altered = {**self.expected(), "fullName": "Another Employee"}
        path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket(TICKET)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), altered)

    def test_two_clients_publish_one_request_and_do_not_leave_temporary_files(self):
        def submit():
            client = OneDriveAccessCardClient(str(self.root))
            try:
                client.reserve_for_ticket(TICKET)
            except AccessCardPending:
                return "pending"
        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(list(workers.map(lambda _: submit(), range(2))), ["pending", "pending"])
        self.assertEqual(len(list((self.root / "Requests").iterdir())), 1)
        self.assertEqual(json.loads(self.path("Requests").read_bytes()), self.expected())
        self.assertEqual(list((self.root / "Staging").iterdir()), [])

    def test_changed_jira_identity_has_its_own_request(self):
        self.result()
        for changes in ({"key": "GSD-124"}, {"name": "Changed Name"}, {"rejoiner": "Yes"}):
            self.queue({**TICKET, **changes})
        self.assertEqual(len(list((self.root / "Requests").iterdir())), 3)

    def test_rejoiner_preserves_existing_spelling_and_rejects_allocation(self):
        ticket = {**TICKET, "name": "Aiste Zukaite", "rejoiner": "Yes"}
        self.result(ticket, status="reused", registryName="Aistė Žukaitė", workbookChanged=False)
        result = self.client.reserve_for_ticket(ticket)
        self.assertEqual(result.print_name, "Aistė Žukaitė")
        self.assertFalse(result.workbook_changed)
        self.result(ticket)
        with self.assertRaisesRegex(AccessCardConfigurationError, "rejoiner"):
            self.client.reserve_for_ticket(ticket)

    def test_manual_review_result_can_be_replaced_after_flow_retry(self):
        self.result(status="manual_review", cardId=None, numericPart=None, workbookChanged=False)
        self.assertFalse(self.client.reserve_for_ticket(TICKET).is_confirmed)
        self.result()
        self.assertTrue(self.client.reserve_for_ticket(TICKET).is_confirmed)

    def test_oversized_result_is_rejected(self):
        self.path("Results").write_bytes(b' ' * (MAX_QUEUE_FILE_BYTES + 1))
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket(TICKET)

    def test_publish_failure_cleans_staging_and_does_not_claim_success(self):
        operation = "os.rename" if os.name == "nt" else "os.link"
        with patch("onedrive_card_queue." + operation, side_effect=OSError("Disk full")):
            with self.assertRaises(AccessCardServiceError):
                self.client.reserve_for_ticket(TICKET)
        self.assertEqual(list((self.root / "Staging").iterdir()), [])
        self.assertFalse(self.path("Requests").exists())

    def test_missing_queue_folder_and_movers_never_create_requests(self):
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket({**TICKET, "kind": "mover"})
        (self.root / "Processed").rmdir()
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket(TICKET)
        self.assertEqual(list((self.root / "Requests").iterdir()), [])
        with self.assertRaises(AccessCardConfigurationError):
            validate_sync_folder("relative/AccessCardQueue")

    def test_utf8_bom_from_cloud_is_supported(self):
        envelope = self.result()
        self.path("Results").write_text(json.dumps(envelope), encoding="utf-8-sig")
        self.assertTrue(self.client.reserve_for_ticket(TICKET).is_confirmed)


if __name__ == "__main__":
    unittest.main()
