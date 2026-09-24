import copy
import json
import unittest
from unittest.mock import Mock

import requests

from access_card_registry import (
    AccessCardAuthenticationRequired, AccessCardConfigurationError,
    AccessCardPending, AccessCardServiceError, reservation_request_from_ticket,
)
from sharepoint_card_queue import (
    GRAPH_ROOT, QUEUE_FIELDS, SharePointAccessCardClient,
    queue_request_key, validate_list_location,
)


SITE = "https://example-my.sharepoint.com/personal/operator_example_com"
LIST_ID = "4e3b4aff-1c0a-43f6-ba8c-12a7be6e651e"
TICKET = {"id": "10001", "key": "GSD-123", "name": "Test Employee", "rejoiner": "No"}


def result_payload(**changes):
    return dict({"status": "reserved", "jiraKey": "GSD-123", "fullName": "Test Employee",
                 "registryName": "Test Employee", "cardId": "LT5053", "numericPart": 5053,
                 "message": "Confirmed", "workbookChanged": True}, **changes)


class Response:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class QueueService:
    """Small Graph service double with persisted requests and ambiguous writes."""
    def __init__(self):
        self.items = []
        self.calls = []
        self.posts = 0
        self.post_outcome = None
        self.denied = None
        self.next_link = None
        self.columns = [{"name": name, "text": {"allowMultipleLines": name in {"ResultJson", "ErrorMessage"}}}
                        for name in QUEUE_FIELDS]
        self.columns[0].update(enforceUniqueValues=True, indexed=True)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.denied:
            return Response(status=self.denied)
        if method == "GET" and ":/personal/" in url:
            return Response({"id": "example-my.sharepoint.com,site-guid,web-guid"})
        if method == "GET" and url.endswith("/columns"):
            payload = {"value": self.columns}
            if self.next_link:
                payload["@odata.nextLink"] = self.next_link
            return Response(payload)
        if method == "GET" and url.endswith("/items"):
            key = kwargs["params"]["$filter"].split("'")[1]
            return Response({"value": [copy.deepcopy(item) for item in self.items if item["fields"]["Title"] == key]})
        if method == "POST" and url.endswith("/items"):
            self.posts += 1
            fields = copy.deepcopy(kwargs["json"]["fields"])
            item = {"id": str(len(self.items) + 1), "fields": fields}
            self.items.append(item)
            if self.post_outcome == "timeout_after_commit":
                raise requests.Timeout("write result lost")
            if self.post_outcome == "conflict":
                return Response(status=400)
            return Response(copy.deepcopy(item), status=201)
        raise AssertionError(f"Unexpected request: {method} {url}")

    def complete(self, payload=None):
        self.items[-1]["fields"].update(QueueStatus="Completed", ResultJson=json.dumps(payload or result_payload()))


class SharePointQueueTests(unittest.TestCase):
    def setUp(self):
        self.service = QueueService()
        self.auth = Mock()
        self.auth.get_access_token.return_value = "graph-token"
        self.client = SharePointAccessCardClient(SITE, LIST_ID, self.auth, session=self.service)

    def queue(self, ticket=None):
        with self.assertRaises(AccessCardPending):
            self.client.reserve_for_ticket(ticket or TICKET)

    def test_submits_request_then_reads_verified_result_without_updating_list_or_excel(self):
        self.queue()
        fields = self.service.items[0]["fields"]
        self.assertEqual(fields["QueueStatus"], "Pending")
        self.assertEqual(fields["JiraKey"], "GSD-123")
        self.assertNotIn("ResultJson", fields)
        self.service.complete()
        result = self.client.reserve_for_ticket(TICKET)
        self.assertEqual(result.card_id, "LT5053")
        self.assertEqual(self.service.posts, 1)
        for method, url, options in self.service.calls:
            self.assertIn(method, {"GET", "POST"})
            self.assertTrue(url.startswith(GRAPH_ROOT + "/sites/"))
            self.assertNotIn("/workbook", url)
            self.assertFalse(options["allow_redirects"])
            self.assertEqual(options["headers"]["Authorization"], "Bearer graph-token")

    def test_pending_poll_and_app_restart_reuse_request(self):
        self.queue()
        self.queue()
        second_client = SharePointAccessCardClient(SITE, LIST_ID, self.auth, session=self.service)
        with self.assertRaises(AccessCardPending):
            second_client.reserve_for_ticket(TICKET)
        self.assertEqual(self.service.posts, 1)

    def test_write_timeout_after_commit_is_recovered_without_duplicate(self):
        self.service.post_outcome = "timeout_after_commit"
        self.queue()
        self.service.complete()
        self.assertEqual(self.client.reserve_for_ticket(TICKET).card_id, "LT5053")
        self.assertEqual(self.service.posts, 1)

    def test_concurrent_unique_title_conflict_reuses_winning_item(self):
        self.service.post_outcome = "conflict"
        self.queue()
        self.service.complete()
        self.assertEqual(self.client.reserve_for_ticket(TICKET).card_id, "LT5053")
        self.assertEqual(len(self.service.items), 1)

    def test_missing_unique_index_stops_before_any_submission(self):
        for property_name in ("enforceUniqueValues", "indexed"):
            with self.subTest(property_name=property_name):
                self.service.columns[0][property_name] = False
                with self.assertRaisesRegex(AccessCardConfigurationError, "unique"):
                    self.client.reserve_for_ticket(TICKET)
                self.service.columns[0][property_name] = True
        self.assertEqual(self.service.posts, 0)

    def test_missing_or_wrong_column_types_stop_before_submission(self):
        original = copy.deepcopy(self.service.columns)
        for mutate in (
            lambda cols: cols.pop(),
            lambda cols: cols[2].update(text={"allowMultipleLines": True}),
            lambda cols: cols[-2].update(text={"allowMultipleLines": True, "textType": "richText"}),
            lambda cols: cols[-1].update(text={"allowMultipleLines": True, "appendChangesToExistingText": True}),
        ):
            self.service.columns = copy.deepcopy(original)
            mutate(self.service.columns)
            with self.assertRaises(AccessCardConfigurationError):
                self.client.reserve_for_ticket(TICKET)
        self.assertEqual(self.service.posts, 0)

    def test_different_ticket_identity_never_consumes_old_result(self):
        self.queue()
        self.service.complete()
        for changes in ({"key": "GSD-124"}, {"name": "Another Employee"}, {"rejoiner": "Yes"}):
            self.queue({**TICKET, **changes})
        self.assertEqual(len(self.service.items), 4)
        self.assertEqual(len({item["fields"]["Title"] for item in self.service.items}), 4)

    def test_duplicate_matches_and_tampered_request_are_not_printable(self):
        self.queue()
        self.service.complete()
        self.service.items.append(copy.deepcopy(self.service.items[0]))
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket(TICKET)
        self.service.items.pop()
        self.service.items[0]["fields"]["FullName"] = "Changed Name"
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket(TICKET)

    def test_completed_requires_valid_bound_result(self):
        self.queue()
        for payload in (
            result_payload(jiraKey="GSD-999"), result_payload(fullName="Other Employee"),
            result_payload(numericPart=5054), result_payload(workbookChanged=False),
        ):
            with self.subTest(payload=payload):
                self.service.complete(payload)
                with self.assertRaises(AccessCardConfigurationError):
                    self.client.reserve_for_ticket(TICKET)
        for result in ("", "not JSON", "null", 5053):
            self.service.items[0]["fields"]["ResultJson"] = result
            with self.assertRaises(AccessCardConfigurationError):
                self.client.reserve_for_ticket(TICKET)

    def test_pending_ignores_even_a_printable_result_until_flow_completes(self):
        self.queue()
        self.service.items[0]["fields"]["ResultJson"] = json.dumps(result_payload())
        self.queue()

    def test_failed_flow_returns_actionable_error_without_recreating_request(self):
        self.queue()
        self.service.items[0]["fields"].update(QueueStatus="Failed", ErrorMessage="sensitive remote detail")
        with self.assertRaises(AccessCardServiceError) as raised:
            self.client.reserve_for_ticket(TICKET)
        self.assertIn("resubmit", str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(self.service.posts, 1)

    def test_rejoiner_preserves_workbook_name_and_rejects_allocation(self):
        ticket = {**TICKET, "name": "Aiste Zukaite", "rejoiner": "Yes"}
        self.queue(ticket)
        self.service.complete(result_payload(fullName="Aiste Zukaite", registryName="Aistė Žukaitė",
                                             status="reused", workbookChanged=False))
        self.assertEqual(self.client.reserve_for_ticket(ticket).print_name, "Aistė Žukaitė")
        self.service.complete(result_payload(fullName="Aiste Zukaite", registryName="Aistė Žukaitė"))
        with self.assertRaisesRegex(AccessCardConfigurationError, "rejoiner"):
            self.client.reserve_for_ticket(ticket)

    def test_manual_review_can_be_updated_by_resubmitting_original_flow(self):
        self.queue()
        self.service.complete(result_payload(status="manual_review", cardId=None, numericPart=None, workbookChanged=False))
        self.assertFalse(self.client.reserve_for_ticket(TICKET).is_confirmed)
        self.service.complete()
        self.assertTrue(self.client.reserve_for_ticket(TICKET).is_confirmed)
        self.assertEqual(self.service.posts, 1)

    def test_permissions_and_auth_errors_create_no_items(self):
        for status, exception in ((401, AccessCardAuthenticationRequired), (403, AccessCardConfigurationError),
                                  (404, AccessCardConfigurationError), (429, AccessCardServiceError)):
            with self.subTest(status=status):
                self.service.denied = status
                with self.assertRaises(exception):
                    self.client.reserve_for_ticket(TICKET)
        self.assertEqual(self.service.posts, 0)

    def test_movers_are_rejected_before_sign_in_or_network(self):
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket({**TICKET, "kind": "mover"})
        self.auth.get_access_token.assert_not_called()
        self.assertEqual(self.service.calls, [])

    def test_metadata_pagination_cannot_forward_token_to_another_host(self):
        self.service.next_link = "https://example.invalid/steal"
        with self.assertRaises(AccessCardConfigurationError):
            self.client.reserve_for_ticket(TICKET)
        self.assertFalse(any("example.invalid" in call[1] for call in self.service.calls))

    def test_site_input_rejects_malformed_or_non_sharepoint_addresses(self):
        for site in ("http://example.sharepoint.com/sites/Test", "https://example.invalid/sites/Test",
                     "https://example.sharepoint.compersonal/operator", SITE + "/Lists/Test",
                     SITE + "?x=y", "https://example.sharepoint.com/sites/%2E%2E"):
            with self.subTest(site=site), self.assertRaises(AccessCardConfigurationError):
                validate_list_location(site, LIST_ID)
        self.assertEqual(validate_list_location(SITE + "/", "{" + LIST_ID + "}"), (SITE, LIST_ID))

    def test_request_key_is_stable_after_whitespace_and_key_normalization(self):
        first = reservation_request_from_ticket(TICKET)
        second = reservation_request_from_ticket({**TICKET, "key": " gsd-123 ", "name": "  Test   Employee  "})
        self.assertEqual(queue_request_key(first), queue_request_key(second))


if __name__ == "__main__":
    unittest.main()
