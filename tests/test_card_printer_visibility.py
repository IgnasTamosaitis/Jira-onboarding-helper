import json
import queue
import subprocess
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import app
from ad_automation import get_current_user_site
from ui import MainWindow


class OperatorOfficeTests(unittest.TestCase):
    def test_operator_office_uses_existing_site_mappings(self):
        cases = {
            "Vilnius": "vilnius", "Girteka Park": "vilnius",
            "  VILNIUS  ": "vilnius", "Laisvės pr. 36": "vilnius",
            "GBS": "georgia", "Girteka HUB (GBS)": "georgia", "Tbilisi": "georgia",
            "Poznan Campus": "poland", "Poznań": "poland", "Poznańska 4, Sady": "poland",
            "Šiauliai": "siauliai", "Siauliai Campus": "siauliai",
            "": "", "Unknown Office": "", "Lithuania": "",
            "Vilnius / GBS": "georgia", "Vilnius / Poznan": "poland",
        }
        for office, expected in cases.items():
            with self.subTest(office=office):
                with patch("ad_automation.run_ps", return_value=(json.dumps({"office": office}), "", 0)):
                    self.assertEqual(get_current_user_site(), expected)

    def test_failed_or_invalid_ad_response_does_not_assume_vilnius(self):
        for stdout, code in (
            ('{"office": "Vilnius"}', 1), ("", 0), ("not JSON", 0),
            ('{"office": null}', 0), ('{"office": 42}', 0),
            ('[{"office": "Vilnius"}]', 0),
            ('{"office": "", "company": "Girteka"}', 0),
        ):
            with self.subTest(stdout=stdout, code=code):
                with patch("ad_automation.run_ps", return_value=(stdout, "", code)):
                    self.assertEqual(get_current_user_site(), "")
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("powershell", 10)):
            with self.subTest(error=error):
                with patch("ad_automation.run_ps", side_effect=error):
                    self.assertEqual(get_current_user_site(), "")


class OperatorOfficeAppTests(unittest.TestCase):
    def setUp(self):
        self.target = app.App.__new__(app.App)
        self.target._root = Mock()
        self.target._window = Mock()
        self.target._ui_queue = queue.Queue()
        self.target._operator_office_check_lock = threading.Lock()
        self.target._card_printer_enabled = False
        self.target._tickets = []
        self.target._tickets_lock = threading.Lock()
        self.target._access_card_client = None
        self.target._start_access_card_reservations = Mock()

    def finish_lookup(self, site):
        self.target._operator_office_check_lock.acquire()
        with patch("app.get_current_user_site", return_value=site):
            self.target._check_operator_office()
        self.assertFalse(self.target._operator_office_check_lock.locked())
        self.target._drain_ui_queue()

    def test_only_vilnius_enables_tab_and_later_failure_hides_it(self):
        for site in ("vilnius", "georgia", "poland", "siauliai", "", "vilnius", ""):
            with self.subTest(site=site):
                self.finish_lookup(site)
                self.assertEqual(self.target._card_printer_enabled, site == "vilnius")
                self.target._window.set_card_printer_visible.assert_called_with(site == "vilnius")

    def test_worker_failure_is_queued_for_ui_and_allows_retry(self):
        self.target._operator_office_check_lock.acquire()
        with patch("app.get_current_user_site", side_effect=RuntimeError("Offline")):
            self.target._check_operator_office()
        self.target._window.set_card_printer_visible.assert_not_called()
        self.assertEqual(self.target._ui_queue.get_nowait(), ("operator_office", ""))
        self.assertFalse(self.target._operator_office_check_lock.locked())
        self.finish_lookup("vilnius")
        self.assertTrue(self.target._card_printer_enabled)

    def test_concurrent_refreshes_do_not_start_duplicate_lookups(self):
        with patch("app.threading.Thread") as thread:
            self.target._start_operator_office_check()
            self.target._start_operator_office_check()
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    def test_lookup_before_window_creation_is_applied_when_opened(self):
        self.target._window = None
        self.target._tickets = []
        self.target._movers = []
        self.target._storage = Mock()
        self.target._jira = Mock()
        self.target._snipeit = None
        self.target._pending_update = None
        self.finish_lookup("vilnius")
        with patch("app.MainWindow") as window:
            self.target._show_window()
        self.assertIs(window.call_args.kwargs["card_printer_enabled"], True)


class CardPrinterVisibilityTests(unittest.TestCase):
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

    def test_default_window_does_not_load_printer_even_for_vilnius_tickets(self):
        tickets = [{"id": "1", "key": "TEST-1", "name": "Test User",
                    "start_date": None, "office": "Girteka Park"}]
        with patch("ui.PrinterPanel") as printer:
            storage = Mock()
            storage.get.return_value = [False] * 5
            storage.ad_setup_done.return_value = False
            window = MainWindow(self.root, tickets, storage, Mock(), Mock())
        try:
            self.assertEqual(len(window._notebook.tabs()), 2)
            printer.assert_not_called()
        finally:
            window.destroy()

    def test_visibility_changes_preserve_ticket_views_and_card_input(self):
        window = MainWindow(self.root, [], Mock(), Mock(), Mock())
        try:
            joiner_list = window._listbox
            movers = window._movers_panel
            window.set_card_printer_visible(True)
            panel = window._printer_tab.winfo_children()[0]
            panel.full_name.set("Test User")
            panel.number.set("1234")
            window._notebook.select(window._printer_tab)
            window.set_card_printer_visible(False)
            self.assertEqual(len(window._notebook.tabs()), 2)
            self.assertNotEqual(window._notebook.select(), str(window._printer_tab))
            window.set_card_printer_visible(True)
            window.set_card_printer_visible(True)
            self.assertEqual(len(window._notebook.tabs()), 3)
            self.assertEqual(panel.full_name.get(), "Test User")
            self.assertEqual(panel.number.get(), "1234")
            self.assertIs(window._listbox, joiner_list)
            self.assertIs(window._movers_panel, movers)
        finally:
            window.destroy()


if __name__ == "__main__":
    unittest.main()
