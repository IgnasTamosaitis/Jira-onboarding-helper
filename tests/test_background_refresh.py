import unittest
import tkinter as tk
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ui import MainWindow
from storage import TaskStorage, TASK_SCHEMA_VERSION
from view_updates import update_listbox


class BackgroundRefreshTests(unittest.TestCase):
    def test_ad_poll_does_not_rebuild_detail_and_reschedules(self):
        window = Mock(_selected_ticket_id="123", _ad_status_job="old")
        MainWindow._refresh_selected_ad(window, {"id": "123"})
        window._ensure_ad_status_check.assert_called_once_with({"id": "123"}, force=True)
        window._show_detail.assert_not_called()
        window.after.assert_called_once()
        self.assertEqual(window.after.call_args.args[0], 60000)

    def test_unchanged_assets_keep_existing_widgets(self):
        state = dict(message="", assets=[{"name": "Laptop"}], error=False)
        frame = Mock(_display_state=state)
        window = SimpleNamespace(_joiner_snipe_frame=frame)
        MainWindow._draw_joiner_snipe_state(window, **state)
        frame.winfo_children.assert_not_called()

    def test_list_refresh_selection_does_not_rebuild_current_ticket(self):
        window = Mock(_selected_ticket_id="123", tickets=[{"id": "123"}])
        window._listbox.curselection.return_value = (0,)
        MainWindow._on_select(window)
        window._show_detail.assert_not_called()


class LiveRefreshTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        for name, values in (("storage._load", {"return_value": {"__task_schema_version": TASK_SCHEMA_VERSION}}),
                             ("storage._save", {}), ("ui.threading.Thread", {})):
            self.enterContext(patch(name, **values))
        self.ticket = {"id": "123", "key": "TEST-123", "name": "Test Employee",
                       "start_date": None, "url": "", "ad_joiner_scenario": "new_joiner"}
        self.window = MainWindow(self.root, [deepcopy(self.ticket)], TaskStorage(), Mock(), Mock())
        self.window._listbox.selection_set(0)
        self.window._on_select()
        self.root.update()

    def test_unchanged_jira_refresh_keeps_notes_focus_and_scroll(self):
        box = self.window._notes_box
        box.insert("1.0", "Draft notes")
        box.mark_set("insert", "1.4")
        box.focus_force()
        self.window._detail_canvas.yview_moveto(.35)
        self.root.update()
        position = self.window._detail_canvas.yview()
        with patch.object(self.window, "_show_detail", wraps=self.window._show_detail) as render:
            self.window.update_tickets([deepcopy(self.ticket)])
            self.root.update()
            render.assert_not_called()
        self.assertIs(self.window._notes_box, box)
        self.assertEqual(box.get("1.0", "end-1c"), "Draft notes")
        self.assertEqual(box.index("insert"), "1.4")
        self.assertIs(self.window.focus_get(), box)
        self.assertEqual(self.window._detail_canvas.yview(), position)

    def test_jira_refresh_preserves_local_joiner_scenario(self):
        refreshed = deepcopy(self.ticket)
        refreshed.pop("ad_joiner_scenario")  # Jira doesn't supply this local value.
        with patch.object(self.window, "_show_detail") as render:
            self.window.update_tickets([refreshed])
            render.assert_not_called()
        self.assertEqual(refreshed["ad_joiner_scenario"], "new_joiner")

    def test_changed_jira_fields_are_displayed(self):
        refreshed = {**self.ticket, "position": "Updated position"}
        with patch.object(self.window, "_show_detail", wraps=self.window._show_detail) as render:
            self.window.update_tickets([refreshed])
            render.assert_called_once_with(refreshed)

    def test_joiner_type_check_updates_badge_without_rebuilding_form(self):
        ticket = self.window.tickets[0]
        ticket.pop("ad_joiner_scenario")
        ticket.update(first_name="Test", last_name="Employee")
        notes = self.window._notes_box
        with patch("ui.find_user_accounts", return_value=[]), \
             patch("ui.classify_scenario", return_value="rejoiner_single"), \
             patch("ui.threading.Thread", side_effect=lambda target, **kw: SimpleNamespace(start=target)), \
             patch.object(self.window, "_show_detail") as render:
            self.window._ensure_ad_joiner_type_check(ticket)
            self.root.update()
            render.assert_not_called()
        self.assertIs(self.window._notes_box, notes)
        self.assertEqual(self.window._joiner_badge.cget("text"), "Rejoiner")

    def test_repeated_buddy_and_comments_results_keep_widgets_and_selection(self):
        buddy = {"name": "buddy", "author": "manual", "disabled": False}
        self.window._refresh_buddy_box("123", buddy)
        children = self.window._buddy_box_frame.winfo_children()
        self.window._refresh_buddy_box("123", deepcopy(buddy))
        self.assertEqual(self.window._buddy_box_frame.winfo_children(), children)
        box = self.window._comments_box
        self.window._set_comments_text(box, "Comment text")
        box.tag_add("sel", "1.0", "1.7")
        self.window._set_comments_text(box, "Comment text")
        self.assertEqual(tuple(map(str, box.tag_ranges("sel"))), ("1.0", "1.7"))

    def test_snipe_metadata_and_order_changes_do_not_redraw_assets(self):
        self.window._joiner_snipe_frame = tk.Frame(self.window)
        assets = [{"name": "Laptop", "id": 1}, {"name": "Monitor", "id": 2}]
        self.window._draw_joiner_snipe_state(assets=assets)
        children = self.window._joiner_snipe_frame.winfo_children()
        refreshed = [{**asset, "updated_at": "new timestamp"} for asset in reversed(assets)]
        self.window._draw_joiner_snipe_state(assets=refreshed)
        self.assertEqual(self.window._joiner_snipe_frame.winfo_children(), children)
        refreshed[0]["name"] = "Replacement monitor"
        self.window._draw_joiner_snipe_state(assets=refreshed)
        self.assertNotEqual(self.window._joiner_snipe_frame.winfo_children(), children)

    def test_repeated_missing_buddy_keeps_manual_entry_draft(self):
        self.window._refresh_buddy_box("123", None)
        frame = self.window._buddy_box_frame
        children = frame.winfo_children()
        self.window._refresh_buddy_box("123", None)
        self.assertEqual(frame.winfo_children(), children)

    def test_unchanged_list_does_not_delete_rows(self):
        with patch.object(self.window._listbox, "delete") as delete:
            self.window._refresh_list()
            delete.assert_not_called()

    def test_updated_list_preserves_viewport_and_selection(self):
        box = tk.Listbox(self.window, height=3)
        box.pack()
        rows = [(f"Ticket {i}", "black") for i in range(40)]
        update_listbox(box, rows)
        self.root.update()
        box.selection_set(18)
        box.yview_moveto(.4)
        position = box.yview()
        rows[18] = ("Updated ticket", "green")
        update_listbox(box, rows)
        self.assertEqual(box.curselection(), (18,))
        self.assertEqual(box.yview(), position)

    def test_mover_refresh_keeps_notes_widget(self):
        panel = self.window._movers_panel
        mover = {"id": "1", "key": "MOVE-1", "name": "Mover", "effective_date": None}
        panel.update_movers([mover])
        panel._listbox.selection_set(0)
        panel._on_select()
        notes = panel._notes_box
        notes.insert("1.0", "Mover draft")
        panel.update_movers([deepcopy(mover)])
        self.assertIs(panel._notes_box, notes)
        self.assertEqual(notes.get("1.0", "end-1c"), "Mover draft")
