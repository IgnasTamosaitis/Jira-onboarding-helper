import unittest
from unittest.mock import Mock

import requests

from access_card_registry import (
    AccessCardAuthenticationRequired,
    AccessCardConfigurationError,
    AccessCardReservation,
    PowerAutomateAccessCardClient,
    reservation_request_from_ticket,
)


FLOW_URL = "https://prod-00.westeurope.logic.azure.com/workflows/example/triggers/manual/paths/invoke"


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _confirmed_payload(**overrides):
    payload = {
        "status": "reserved",
        "jiraKey": "GSD-123",
        "fullName": "Aistė Žukaitė",
        "cardId": "LT5053",
        "numericPart": 5053,
        "message": "A new access-card ID was reserved in Excel.",
        "workbookChanged": True,
    }
    payload.update(overrides)
    return payload


class ReservationContractTests(unittest.TestCase):
    def test_confirmed_result_is_printable(self):
        result = AccessCardReservation.from_payload(_confirmed_payload())

        self.assertTrue(result.is_confirmed)
        self.assertEqual(result.card_id, "LT5053")
        self.assertEqual(result.numeric_part, 5053)

    def test_reused_result_does_not_claim_workbook_was_changed(self):
        result = AccessCardReservation.from_payload(
            _confirmed_payload(status="reused", workbookChanged=False)
        )

        self.assertTrue(result.is_confirmed)
        self.assertFalse(result.workbook_changed)

    def test_manual_review_cannot_leak_a_printable_card_number(self):
        with self.assertRaises(AccessCardConfigurationError):
            AccessCardReservation.from_payload(
                _confirmed_payload(status="manual_review")
            )

    def test_rejects_mismatched_numeric_part(self):
        with self.assertRaises(AccessCardConfigurationError):
            AccessCardReservation.from_payload(
                _confirmed_payload(numericPart=5054)
            )

    def test_rejects_leading_zero_or_out_of_range_card_ids(self):
        for card_id in ("LT050", "LT0", "LT10000", "5053"):
            with self.subTest(card_id=card_id):
                with self.assertRaises(AccessCardConfigurationError):
                    AccessCardReservation.from_payload(
                        _confirmed_payload(cardId=card_id)
                    )

    def test_unwraps_office_script_result_string(self):
        result = AccessCardReservation.from_payload(
            {"result": '{"status":"existing","jiraKey":"GSD-123",'
             '"fullName":"Aistė Žukaitė","cardId":"LT5053",'
             '"numericPart":5053,"message":"Already reserved",'
             '"workbookChanged":false}'}
        )

        self.assertEqual(result.status, "existing")
        self.assertFalse(result.workbook_changed)

    def test_requires_explicit_workbook_change_confirmation(self):
        payload = _confirmed_payload()
        payload.pop("workbookChanged")

        with self.assertRaises(AccessCardConfigurationError):
            AccessCardReservation.from_payload(payload)


class JiraRequestTests(unittest.TestCase):
    def test_builds_new_joiner_request(self):
        request = reservation_request_from_ticket(
            {"key": "gsd-123", "name": "  Aistė Žukaitė  ", "rejoiner": "No"}
        )

        self.assertEqual(
            request,
            {
                "jiraKey": "GSD-123",
                "fullName": "Aistė Žukaitė",
                "joinerType": "new_joiner",
            },
        )

    def test_builds_rejoiner_request_from_separate_name_fields(self):
        request = reservation_request_from_ticket(
            {
                "key": "GSD-124",
                "first_name": "Jonas",
                "last_name": "Jonaitis",
                "rejoiner": "YES",
            }
        )

        self.assertEqual(request["fullName"], "Jonas Jonaitis")
        self.assertEqual(request["joinerType"], "rejoiner")

    def test_rejects_excel_formula_injection_in_name(self):
        with self.assertRaises(AccessCardConfigurationError):
            reservation_request_from_ticket(
                {"key": "GSD-125", "name": "=HYPERLINK(...)"}
            )


class FlowClientTests(unittest.TestCase):
    def test_posts_authenticated_request_and_uses_only_server_card_id(self):
        session = Mock()
        session.post.return_value = _Response(payload=_confirmed_payload())
        token_provider = Mock()
        token_provider.get_access_token.return_value = "access-token"
        client = PowerAutomateAccessCardClient(
            FLOW_URL, token_provider, session=session
        )

        result = client.reserve_for_ticket(
            {"key": "GSD-123", "name": "Aistė Žukaitė", "rejoiner": "No"}
        )

        self.assertEqual(result.card_id, "LT5053")
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["jiraKey"], "GSD-123")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer access-token")

    def test_retry_reuses_identical_idempotent_jira_request(self):
        session = Mock()
        session.post.side_effect = [
            _Response(status_code=503, headers={"Retry-After": "0"}),
            _Response(payload=_confirmed_payload(status="existing", workbookChanged=False)),
        ]
        client = PowerAutomateAccessCardClient(
            FLOW_URL,
            lambda **_: "token",
            session=session,
            sleeper=lambda _: None,
        )

        result = client.reserve_for_ticket(
            {"key": "GSD-123", "name": "Aistė Žukaitė"}
        )

        self.assertEqual(result.status, "existing")
        first_body = session.post.call_args_list[0].kwargs["json"]
        second_body = session.post.call_args_list[1].kwargs["json"]
        self.assertEqual(first_body, second_body)

    def test_rejects_result_for_different_employee(self):
        session = Mock()
        session.post.return_value = _Response(
            payload=_confirmed_payload(fullName="Kitas Žmogus")
        )
        client = PowerAutomateAccessCardClient(
            FLOW_URL, lambda **_: "token", session=session
        )

        with self.assertRaises(AccessCardConfigurationError):
            client.reserve_for_ticket(
                {"key": "GSD-123", "name": "Aistė Žukaitė"}
            )

    def test_does_not_invent_an_id_when_service_is_unavailable(self):
        session = Mock()
        session.post.side_effect = requests.ConnectionError("offline")
        client = PowerAutomateAccessCardClient(
            FLOW_URL,
            lambda **_: "token",
            session=session,
            sleeper=lambda _: None,
        )

        with self.assertRaisesRegex(Exception, "No local card ID was created"):
            client.reserve_for_ticket({"key": "GSD-123", "name": "Aistė Žukaitė"})

    def test_requires_https_flow_endpoint(self):
        with self.assertRaises(AccessCardConfigurationError):
            PowerAutomateAccessCardClient(
                "http://example.test/flow", lambda **_: "token"
            )

    def test_rejoiner_rejects_any_response_that_allocated_a_new_card(self):
        session = Mock()
        session.post.return_value = _Response(payload=_confirmed_payload())
        client = PowerAutomateAccessCardClient(FLOW_URL, lambda **_: "token", session=session)
        with self.assertRaisesRegex(AccessCardConfigurationError, "rejoiner"):
            client.reserve_for_ticket({"key": "GSD-123", "name": "Aistė Žukaitė", "rejoiner": "Yes"})

    def test_rejoiner_accepts_normalized_match_and_uses_workbook_spelling(self):
        session = Mock()
        session.post.return_value = _Response(payload=_confirmed_payload(
            status="reused", fullName="Aiste Zukaite", registryName="Aistė Žukaitė", workbookChanged=False,
        ))
        client = PowerAutomateAccessCardClient(FLOW_URL, lambda **_: "token", session=session)
        result = client.reserve_for_ticket({"key": "GSD-123", "name": "Aiste Zukaite", "rejoiner": "Yes"})
        self.assertEqual(result.print_name, "Aistė Žukaitė")
        self.assertFalse(result.workbook_changed)

    def test_maps_unauthorized_response_to_sign_in_error(self):
        session = Mock()
        session.post.return_value = _Response(status_code=401)
        client = PowerAutomateAccessCardClient(
            FLOW_URL, lambda **_: "token", session=session
        )

        with self.assertRaises(AccessCardAuthenticationRequired):
            client.reserve_for_ticket({"key": "GSD-123", "name": "Aistė Žukaitė"})


if __name__ == "__main__":
    unittest.main()
