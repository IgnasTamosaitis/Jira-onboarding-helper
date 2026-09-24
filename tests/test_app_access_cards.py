import queue
import threading
import unittest
from unittest.mock import Mock, patch

import app
from access_card_registry import AccessCardAuthenticationRequired, AccessCardPending, AccessCardReservation
from sharepoint_card_queue import SharePointAccessCardClient
from onedrive_card_queue import OneDriveAccessCardClient


class _Storage:
    def __init__(self, cached=None):
        self.cached = dict(cached or {})
        self.saved = []

    def get_access_card(self, ticket_id):
        return dict(self.cached.get(ticket_id, {}))

    def mark_access_card(self, ticket_id, result):
        self.cached[ticket_id] = dict(result)
        self.saved.append((ticket_id, dict(result)))


def _reservation(status="reserved"):
    return AccessCardReservation(
        status=status,
        jira_key="GSD-123",
        full_name="Aistė Žukaitė",
        card_id="LT5053",
        numeric_part=5053,
        message="Confirmed",
        workbook_changed=status == "reserved",
    )


class AutomaticAccessCardReservationTests(unittest.TestCase):
    def _app(self, storage=None):
        target = app.App.__new__(app.App)
        target._storage = storage or _Storage()
        target._access_card_client = Mock()
        target._card_printer_enabled = True
        target._access_card_worker_lock = threading.Lock()
        target._tickets_lock = threading.Lock()
        target._ui_queue = queue.Queue()
        target._tickets = [
            {"id": "10001", "key": "GSD-123", "name": "Aistė Žukaitė"}
        ]
        return target

    def test_server_result_is_cached_and_attached_to_current_ticket(self):
        target = self._app()
        target._access_card_client.reserve_for_ticket.return_value = _reservation()
        target._access_card_worker_lock.acquire()

        target._reserve_access_cards([dict(target._tickets[0])])

        self.assertEqual(target._storage.saved[0][1]["card_id"], "LT5053")
        self.assertEqual(target._tickets[0]["access_card"]["card_id"], "LT5053")
        command, tickets = target._ui_queue.get_nowait()
        self.assertEqual(command, "update_tickets")
        self.assertEqual(tickets[0]["access_card"]["status"], "reserved")

    def test_confirmed_local_cache_avoids_repeated_workbook_calls(self):
        cached = {**_reservation(status="existing").as_dict(), "joiner_type": "new_joiner"}
        storage = _Storage({"10001": cached})
        target = self._app(storage)
        target._access_card_worker_lock.acquire()

        target._reserve_access_cards([dict(target._tickets[0])])

        target._access_card_client.reserve_for_ticket.assert_not_called()
        self.assertEqual(storage.saved, [])

    def test_cached_result_is_attached_only_to_same_jira_ticket_and_name(self):
        cached = {**_reservation(status="existing").as_dict(), "joiner_type": "new_joiner"}
        target = self._app(_Storage({"10001": cached}))
        matching = dict(target._tickets[0])
        corrected = dict(target._tickets[0], name="Aistė Jonaitė")

        target._attach_cached_access_cards([matching, corrected])

        self.assertIn("access_card", matching)
        self.assertNotIn("access_card", corrected)

    def test_movers_and_non_vilnius_operators_do_not_start_reservations(self):
        target = self._app()
        with patch("app.threading.Thread") as worker:
            target._start_access_card_reservations([{**target._tickets[0], "kind": "mover"}])
            target._card_printer_enabled = False
            target._start_access_card_reservations(target._tickets)
        worker.assert_not_called()

    def test_changed_ticket_does_not_receive_an_inflight_result(self):
        target = self._app()
        snapshot = [dict(target._tickets[0])]
        def reserve(_ticket, **_kwargs):
            target._tickets[0]["name"] = "Corrected Employee"
            return _reservation()
        target._access_card_client.reserve_for_ticket.side_effect = reserve
        target._access_card_worker_lock.acquire()
        target._reserve_access_cards(snapshot)
        self.assertNotIn("access_card", target._tickets[0])

    def test_authentication_failure_reaches_card_panel_and_allows_retry(self):
        target = self._app()
        target._access_card_client.reserve_for_ticket.side_effect = AccessCardAuthenticationRequired("Login")
        target._access_card_worker_lock.acquire()
        target._reserve_access_cards([dict(target._tickets[0])])
        self.assertIn("Sign in", target._tickets[0]["access_card_error"])
        self.assertFalse(target._access_card_worker_lock.locked())
        self.assertEqual(target._storage.saved, [])

    def test_rejoiner_flag_change_rechecks_the_workbook(self):
        cached = {**_reservation().as_dict(), "joiner_type": "new_joiner"}
        target = self._app(_Storage({"10001": cached}))
        target._tickets[0]["rejoiner"] = "Yes"
        target._access_card_client.reserve_for_ticket.return_value = _reservation("reused")
        target._access_card_worker_lock.acquire()
        target._reserve_access_cards([dict(target._tickets[0])])
        target._access_card_client.reserve_for_ticket.assert_called_once()
        self.assertEqual(target._tickets[0]["access_card"]["joiner_type"], "rejoiner")

    def test_pending_request_updates_ui_then_is_replaced_by_confirmation(self):
        target = self._app()
        target._access_card_client.reserve_for_ticket.side_effect = [AccessCardPending("Waiting for Excel"), _reservation()]
        target._access_card_worker_lock.acquire()
        target._reserve_access_cards([dict(target._tickets[0])])
        self.assertTrue(target._tickets[0]["access_card_pending"])
        self.assertNotIn("access_card", target._tickets[0])
        target._access_card_worker_lock.acquire()
        target._reserve_access_cards([dict(target._tickets[0])])
        self.assertNotIn("access_card_pending", target._tickets[0])
        self.assertNotIn("access_card_error", target._tickets[0])
        self.assertEqual(target._tickets[0]["access_card"]["card_id"], "LT5053")

    def test_pending_poll_only_checks_queue_requests_and_reschedules(self):
        target = self._app()
        target._root = Mock()
        target._access_card_client = Mock(spec=SharePointAccessCardClient)
        target._tickets.append({"id": "10002", "key": "GSD-124", "access_card_pending": True})
        with patch.object(target, "_start_access_card_reservations") as start:
            target._poll_pending_access_cards()
        self.assertEqual([ticket["id"] for ticket in start.call_args.args[0]], ["10002"])
        target._root.after.assert_called_once_with(15000, target._poll_pending_access_cards)

    def test_folder_queue_is_polled_without_an_authentication_client(self):
        target = self._app()
        target._root = Mock()
        target._access_card_client = Mock(spec=OneDriveAccessCardClient)
        target._tickets[0]["access_card_pending"] = True
        with patch.object(target, "_start_access_card_reservations") as start:
            target._poll_pending_access_cards()
        start.assert_called_once_with(target._tickets)

    def test_registry_change_invalidates_cache_and_discards_inflight_result(self):
        cached = {**_reservation().as_dict(), "joiner_type": "new_joiner", "registry_source": "old"}
        target = self._app(_Storage({"10001": cached}))
        target._access_card_source = "new"
        target._attach_cached_access_cards(target._tickets)
        self.assertNotIn("access_card", target._tickets[0])
        def reserve(*_args, **_kwargs):
            target._access_card_source = "changed-again"
            return _reservation()
        target._access_card_client.reserve_for_ticket.side_effect = reserve
        target._access_card_worker_lock.acquire()
        target._reserve_access_cards([dict(target._tickets[0])])
        self.assertEqual(target._storage.saved, [])
        self.assertNotIn("access_card", target._tickets[0])


if __name__ == "__main__":
    unittest.main()
