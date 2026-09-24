from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops

from access_card_registry import (
    AccessCardConfigurationError, AccessCardReservation, cached_reservation_for_ticket,
    reservation_request_from_ticket,
)
from printer_ui import PrinterPanel, render_card


def ticket_with_card(key="GSD-123", name="Test Employee", number=5053, rejoiner=False, registry_name=None):
    ticket = {"id": key, "key": key, "kind": "joiner", "name": name,
              "rejoiner": "Yes" if rejoiner else "No"}
    result = AccessCardReservation(
        status="reused" if rejoiner else "reserved", jira_key=key, full_name=name,
        card_id=f"LT{number}", numeric_part=number, message="Confirmed in Excel",
        workbook_changed=not rejoiner, registry_name=registry_name,
    ).as_dict()
    result["joiner_type"] = "rejoiner" if rejoiner else "new_joiner"
    ticket["access_card"] = result
    return ticket


class CardRequestIdentityTests(unittest.TestCase):
    def test_uses_first_and_last_name_instead_of_summary(self):
        request = reservation_request_from_ticket({"key": "GSD-123", "name": "Onboarding ticket summary",
            "first_name": "  Test ", "last_name": " Employee ", "rejoiner": "No"})
        self.assertEqual(request["fullName"], "Test Employee")

    def test_missing_name_parts_unknown_joiner_type_and_movers_are_blocked(self):
        for overrides in ({"first_name": "Test", "last_name": ""}, {"kind": "mover"}, {"rejoiner": ""}):
            with self.subTest(overrides=overrides), self.assertRaises(AccessCardConfigurationError):
                reservation_request_from_ticket({"key": "GSD-123", "name": "Employee", **overrides})

    def test_ad_rejoiner_classification_overrides_jira_no(self):
        for scenario in ("rejoiner_dual", "rejoiner_single"):
            request = reservation_request_from_ticket({"key": "GSD-123", "name": "Test Employee",
                "rejoiner": "No", "ad_joiner_scenario": scenario})
            self.assertEqual(request["joinerType"], "rejoiner")

    def test_changed_name_or_joiner_type_invalidates_cached_card(self):
        ticket = ticket_with_card()
        self.assertIsNotNone(cached_reservation_for_ticket(ticket, ticket["access_card"]))
        for overrides in ({"name": "Another Employee"}, {"rejoiner": "Yes"}, {"kind": "mover"}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(cached_reservation_for_ticket({**ticket, **overrides}, ticket["access_card"]))

    def test_confirmed_flag_cannot_make_an_invalid_card_printable(self):
        ticket = ticket_with_card()
        for overrides in ({"numeric_part": 5054}, {"numeric_part": 5053.0},
                          {"numeric_part": True}, {"registry_name": "Another Employee"},
                          {"workbook_changed": False}, {"is_confirmed": False}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(cached_reservation_for_ticket(ticket, {**ticket["access_card"], **overrides}))


class CardRegistryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(str(error))
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def panel(self, tickets, **kwargs):
        panel = PrinterPanel(self.root, tickets=tickets, **kwargs)
        self.addCleanup(panel.destroy)
        return panel

    def test_new_joiner_populates_verified_number_without_lt_prefix_and_exports_png(self):
        panel = self.panel([ticket_with_card()])
        self.assertEqual(panel.full_name.get(), "Test Employee")
        self.assertEqual(panel.number.get(), "5053")
        self.assertEqual(str(panel._export_button["state"]), "normal")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "employee.png"
            with patch("printer_ui.filedialog.asksaveasfilename", return_value=str(output)):
                panel._submit()
            with Image.open(output) as exported:
                expected = render_card(panel._background, "Test Employee", "5053")
                self.assertIsNone(ImageChops.difference(exported, expected).getbbox())

    def test_rejoiner_uses_excel_spelling_and_existing_number(self):
        panel = self.panel([ticket_with_card(name="Azuolas Zemaitis", number=40, rejoiner=True,
                                            registry_name="Ąžuolas Žemaitis")])
        self.assertEqual(panel.full_name.get(), "Ąžuolas Žemaitis")
        self.assertEqual(panel.number.get(), "40")
        self.assertTrue(panel._has_verified_card())

    def test_switching_to_pending_ticket_clears_old_id_and_blocks_export(self):
        ready = ticket_with_card()
        pending = {"id": "2", "key": "GSD-124", "name": "Another Employee", "rejoiner": "No"}
        panel = self.panel([ready, pending])
        panel.select_ticket(ready["id"])
        self.assertTrue(panel._has_verified_card())
        panel.select_ticket("2")
        self.assertEqual(panel.full_name.get(), "Another Employee")
        self.assertEqual(panel.number.get(), "")
        with patch("printer_ui.filedialog.asksaveasfilename") as dialog:
            panel._submit()
        dialog.assert_not_called()

    def test_manual_review_blocks_export_and_displays_reason(self):
        ticket = ticket_with_card()
        ticket["access_card"].update(status="manual_review", card_id=None, numeric_part=None,
            workbook_changed=False, is_confirmed=False, message="Multiple matching names in Excel.")
        panel = self.panel([ticket])
        self.assertEqual(panel.number.get(), "")
        self.assertEqual(str(panel._export_button["state"]), "disabled")
        self.assertIn("Multiple matching names", panel._status.get())

    def test_refresh_preserves_selection_and_removes_stale_card_when_ticket_disappears(self):
        first, second = ticket_with_card(), ticket_with_card("GSD-124", "Another Employee", 5054)
        panel = self.panel([first, second])
        panel.select_ticket(second["id"])
        panel.update_tickets([second, first])
        self.assertEqual(panel.number.get(), "5054")
        panel.update_tickets([])
        self.assertEqual(panel.full_name.get(), "")
        self.assertEqual(panel.number.get(), "")
        self.assertFalse(panel._has_verified_card())

    def test_movers_are_not_listed_and_forged_manual_number_cannot_export(self):
        joiner = ticket_with_card()
        panel = self.panel([joiner, {**ticket_with_card("GSD-125"), "kind": "mover"}])
        self.assertEqual(len(panel._tickets), 1)
        panel.number.set("9999")
        with patch("printer_ui.filedialog.asksaveasfilename") as dialog:
            panel._submit()
        dialog.assert_not_called()

    def test_missing_configuration_and_service_errors_are_visible(self):
        pending = {"id": "1", "key": "GSD-123", "name": "Test Employee", "rejoiner": "No"}
        panel = self.panel([pending], registry_message="Connect Excel in Settings.")
        self.assertIn("Connect Excel", panel._status.get())
        panel.update_tickets([{**pending, "access_card_error": "Sign in to Microsoft."}])
        self.assertIn("Sign in", panel._status.get())
        self.assertEqual(str(panel._export_button["state"]), "disabled")
