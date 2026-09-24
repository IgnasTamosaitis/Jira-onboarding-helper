"""
Jira New Joiner Reminders
Runs in the system tray, polls Jira, and sends Windows notifications.
"""
import sys
import hashlib
import json
import queue
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta

import pystray
from PIL import Image, ImageDraw

from ad_automation import get_current_user_site
from access_card_registry import (
    AccessCardAuthenticationRequired,
    AccessCardError,
    AccessCardPending,
    PowerAutomateAccessCardClient,
    cached_reservation_for_ticket,
    reservation_request_from_ticket,
)
from jira_client import DEFAULT_MOVER_JQL, JiraClient
from power_automate_auth import PowerAutomateAuthenticator
from sharepoint_card_queue import SharePointAccessCardClient
from onedrive_card_queue import OneDriveAccessCardClient
from snipeit_client import SnipeITClient
from storage import TaskStorage, load_config, save_config
from ui import MainWindow, SetupDialog, TASKS
import updater

APP_NAME = "Jira Reminders"
MORNING_HOUR = 9   # send daily summary at 9:00 AM


# ── Tray icon ─────────────────────────────────────────────────────────────────

def _make_icon_image() -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill="#0052CC")
    d.text((20, 14), "JR", fill="white")
    return img


# ── Notifications ─────────────────────────────────────────────────────────────

def _notify(title: str, message: str) -> None:
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            timeout=12,
        )
    except Exception as e:
        print(f"[notify] {e}")


# ── Application ───────────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title(APP_NAME)

        self._ui_queue: queue.Queue = queue.Queue()
        self._tickets: list[dict] = []
        self._movers: list[dict] = []
        self._tickets_lock = threading.Lock()
        self._window: MainWindow | None = None
        self._jira: JiraClient | None = None
        self._storage = TaskStorage()
        self._config: dict = {}
        self._tray: pystray.Icon | None = None
        self._snipeit: SnipeITClient | None = None
        self._access_card_client: PowerAutomateAccessCardClient | SharePointAccessCardClient | OneDriveAccessCardClient | None = None
        self._access_card_source = ""
        self._access_card_worker_lock = threading.Lock()
        self._pending_update: dict | None = None
        self._card_printer_enabled = False
        self._operator_office_check_lock = threading.Lock()

    # ── Startup ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        updater.ensure_startup_shortcut()
        updater.ensure_desktop_shortcut()
        cfg = load_config()
        if not cfg:
            cfg = self._run_setup(None)
            if cfg is None:
                sys.exit(0)
            save_config(cfg)

        self._apply_config(cfg)

        self._tray = pystray.Icon(
            "jira-reminders",
            _make_icon_image(),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("Show tickets",      self._tray_show),
                pystray.MenuItem("Check now",         self._tray_check_now),
                pystray.MenuItem("Settings",          self._tray_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Check for updates", self._tray_check_updates),
                pystray.MenuItem("Uninstall",         self._tray_uninstall),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit",              self._tray_quit),
            ),
        )
        self._tray.run_detached()

        threading.Thread(target=self._poll_loop,             daemon=True).start()
        threading.Thread(target=self._morning_summary_loop, daemon=True).start()
        threading.Thread(target=self._update_check_loop,    daemon=True).start()

        # Launch with the main ticket window visible while keeping the app rooted in the tray.
        self._root.after(0, self._show_window)
        self._root.after(100, self._drain_ui_queue)
        self._root.after(15000, self._poll_pending_access_cards)
        self._root.mainloop()

    def _apply_config(self, cfg: dict) -> None:
        self._config = cfg
        self._jira = JiraClient(cfg["jira_url"], cfg["email"], cfg["api_token"])
        snipeit_url   = cfg.get("snipeit_url", "").strip()
        snipeit_token = cfg.get("snipeit_token", "").strip()
        self._snipeit = SnipeITClient(snipeit_url, snipeit_token) if snipeit_url and snipeit_token else None
        self._access_card_client = None
        flow_url = cfg.get("access_card_flow_url", "").strip()
        site_url = cfg.get("access_card_site_url", "").strip().rstrip("/")
        list_id = cfg.get("access_card_list_id", "").strip()
        sync_folder = cfg.get("access_card_sync_folder", "").strip()
        tenant_id = cfg.get("access_card_tenant_id", "").strip()
        client_id = cfg.get("access_card_client_id", "").strip()
        previous_source = self._access_card_source
        source_values = [flow_url, site_url, list_id, tenant_id]
        if sync_folder:
            try:
                self._access_card_client = OneDriveAccessCardClient(sync_folder)
                source_values = ["onedrive", str(self._access_card_client.root)]
            except AccessCardError as exc:
                source_values = ["onedrive", sync_folder]
                print(f"[access cards] folder connection disabled: {exc}")
        elif (flow_url or site_url) and tenant_id and client_id:
            try:
                if site_url:
                    if flow_url:
                        raise AccessCardError("Choose either the SharePoint list or the Premium HTTP flow.")
                    authenticator = PowerAutomateAuthenticator(tenant_id, client_id, service="sharepoint")
                    self._access_card_client = SharePointAccessCardClient(site_url, list_id, authenticator)
                else:
                    authenticator = PowerAutomateAuthenticator(tenant_id, client_id)
                    self._access_card_client = PowerAutomateAccessCardClient(flow_url, authenticator)
            except AccessCardError as exc:
                print(f"[access cards] configuration disabled: {exc}")
        self._access_card_source = hashlib.sha256(json.dumps(
            source_values, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if self._window:
            self._window.set_card_registry_message(self._card_registry_message())
        if previous_source != self._access_card_source:
            with self._tickets_lock:
                for ticket in self._tickets:
                    for field in ("access_card", "access_card_error", "access_card_pending"):
                        ticket.pop(field, None)
                refreshed = list(self._tickets)
            self._ui_queue.put(("update_tickets", refreshed))

    def _card_registry_message(self) -> str:
        return "" if self._access_card_client else "Connect the Excel card registry in Settings, then refresh."

    # ── Background polling ────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        self._fetch_and_notify()
        interval = int(self._config.get("check_interval_minutes", 30)) * 60
        while True:
            time.sleep(interval)
            self._fetch_and_notify()

    def _fetch_and_notify(self) -> None:
        if not self._jira:
            return
        self._start_operator_office_check()
        try:
            tickets = self._jira.get_new_joiner_tickets(
                self._config.get("jql", ""),
                self._config.get("date_field", "customfield_10980"),
            )
            self._attach_cached_access_cards(tickets)
            with self._tickets_lock:
                self._tickets = tickets
            self._ui_queue.put(("update_tickets", tickets))
            self._send_per_ticket_notifications(tickets)
            self._start_access_card_reservations(tickets)
        except Exception as e:
            print(f"[poll joiners] {e}")
        try:
            movers = self._jira.get_mover_tickets(
                self._config.get("mover_jql", DEFAULT_MOVER_JQL)
            )
            with self._tickets_lock:
                self._movers = movers
            self._ui_queue.put(("update_movers", movers))
            self._send_mover_notifications(movers)
        except Exception as e:
            print(f"[poll movers] {e}")

    def _start_operator_office_check(self) -> None:
        if not self._operator_office_check_lock.acquire(blocking=False):
            return
        threading.Thread(target=self._check_operator_office, daemon=True).start()

    def _check_operator_office(self) -> None:
        try:
            try:
                site = get_current_user_site()
            except Exception:
                site = ""
            self._ui_queue.put(("operator_office", site))
        finally:
            self._operator_office_check_lock.release()

    def _attach_cached_access_cards(self, tickets: list[dict]) -> None:
        for ticket in tickets:
            cached = self._storage.get_access_card(str(ticket.get("id", "")))
            if self._cached_card_matches_source(cached) and cached_reservation_for_ticket(ticket, cached):
                ticket["access_card"] = cached
            else:
                ticket.pop("access_card", None)

    def _cached_card_matches_source(self, cached: dict) -> bool:
        return cached.get("registry_source", "") == getattr(self, "_access_card_source", "")

    def _poll_pending_access_cards(self) -> None:
        """Poll only queued work; ordinary Jira refresh still submits new requests."""
        if isinstance(self._access_card_client, (SharePointAccessCardClient, OneDriveAccessCardClient)):
            with self._tickets_lock:
                pending = [dict(ticket) for ticket in self._tickets if ticket.get("access_card_pending")]
            self._start_access_card_reservations(pending)
        self._root.after(15000, self._poll_pending_access_cards)

    def _start_access_card_reservations(self, tickets: list[dict]) -> None:
        if not self._access_card_client or not self._card_printer_enabled:
            return
        snapshot = [dict(ticket) for ticket in tickets if ticket.get("kind", "joiner") == "joiner"]
        if not snapshot:
            return
        if not self._access_card_worker_lock.acquire(blocking=False):
            return
        threading.Thread(
            target=self._reserve_access_cards,
            args=(snapshot,),
            daemon=True,
        ).start()

    def _reserve_access_cards(self, tickets: list[dict]) -> None:
        """Resolve pending card IDs without delaying Jira or the main window."""
        try:
            client = self._access_card_client
            source = getattr(self, "_access_card_source", "")
            if not client:
                return
            changed = False
            for ticket in tickets:
                if client is not self._access_card_client or not self._card_printer_enabled:
                    break
                if ticket.get("kind", "joiner") != "joiner":
                    continue
                ticket_id = str(ticket.get("id", ""))
                cached = self._storage.get_access_card(ticket_id)
                confirmed = cached_reservation_for_ticket(ticket, cached) if self._cached_card_matches_source(cached) else None
                if confirmed and confirmed.is_confirmed:
                    ticket["access_card"] = cached
                    ticket.pop("access_card_error", None)
                    ticket.pop("access_card_pending", None)
                    changed = True
                    continue
                try:
                    ticket.pop("access_card", None)
                    ticket.pop("access_card_pending", None)
                    request = reservation_request_from_ticket(ticket)
                    result = client.reserve_for_ticket(ticket, interactive=False)
                except AccessCardPending as exc:
                    ticket["access_card_error"] = str(exc)
                    ticket["access_card_pending"] = True
                    changed = True
                    continue
                except AccessCardAuthenticationRequired as exc:
                    ticket["access_card_error"] = "Sign in to the access-card service in Settings, then refresh."
                    changed = True
                    continue
                except AccessCardError as exc:
                    ticket["access_card_error"] = str(exc)
                    changed = True
                    continue

                stored_result = result.as_dict()
                stored_result["joiner_type"] = request["joinerType"]
                stored_result["registry_source"] = source
                if client is not self._access_card_client or source != getattr(self, "_access_card_source", ""):
                    break
                self._storage.mark_access_card(ticket_id, stored_result)
                ticket["access_card"] = stored_result
                ticket.pop("access_card_error", None)
                changed = True

            if changed and client is self._access_card_client and source == getattr(self, "_access_card_source", ""):
                with self._tickets_lock:
                    current_by_id = {
                        str(ticket.get("id", "")): ticket for ticket in self._tickets
                    }
                    for ticket in tickets:
                        if "access_card" in ticket or "access_card_error" in ticket:
                            current = current_by_id.get(str(ticket.get("id", "")))
                            if current is not None:
                                identity_fields = ("key", "name", "first_name", "last_name", "rejoiner", "ad_joiner_scenario", "kind")
                                if any(current.get(field) != ticket.get(field) for field in identity_fields):
                                    continue
                                for field in ("access_card", "access_card_error", "access_card_pending"):
                                    if field in ticket:
                                        current[field] = ticket[field]
                                    else:
                                        current.pop(field, None)
                    refreshed = list(self._tickets)
                self._ui_queue.put(("update_tickets", refreshed))
        finally:
            self._access_card_worker_lock.release()

    def _send_per_ticket_notifications(self, tickets: list[dict]) -> None:
        today  = date.today()
        remind = int(self._config.get("remind_days_before", 3))

        for t in tickets:
            sd = t.get("start_date")
            if sd is None:
                continue
            delta = (sd - today).days
            if delta < 0 or delta > remind:
                continue
            if self._storage.notified_today(t["id"]):
                continue

            done      = self._storage.completed_count(t["id"], len(TASKS))
            remaining = len(TASKS) - done
            day_msg   = "starts TODAY" if delta == 0 else (
                        "starts TOMORROW" if delta == 1 else
                        f"starts in {delta} day(s)")
            task_msg  = f"{remaining} task(s) still pending." if remaining else "All tasks done!"
            _notify(
                f"New Joiner: {t['name']}",
                f"{day_msg.capitalize()}  -  {task_msg}",
            )
            self._storage.mark_notified(t["id"])

    def _send_mover_notifications(self, movers: list[dict]) -> None:
        today = date.today()
        remind = int(self._config.get("remind_days_before", 3))
        for mover in movers:
            effective = mover.get("effective_date")
            if effective is None:
                continue
            delta = (effective - today).days
            if delta < 0 or delta > remind or self._storage.notified_today(mover["id"]):
                continue
            when = "is effective TODAY" if delta == 0 else (
                "is effective TOMORROW" if delta == 1 else f"is effective in {delta} day(s)"
            )
            ready = "AD move is verified." if self._storage.ad_setup_done(mover["id"]) else "AD move is pending."
            _notify(f"Employee Mover: {mover['name']}", f"{when}. {ready}")
            self._storage.mark_notified(mover["id"])

    # ── Morning summary ───────────────────────────────────────────────────────

    def _morning_summary_loop(self) -> None:
        while True:
            now    = datetime.now()
            target = now.replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            time.sleep((target - now).total_seconds())
            self._send_morning_summary()

    def _send_morning_summary(self) -> None:
        if self._storage.morning_summary_sent_today():
            return
        # Fetch fresh data at summary time
        if self._jira:
            try:
                tickets = self._jira.get_new_joiner_tickets(
                    self._config.get("jql", ""),
                    self._config.get("date_field", "customfield_10980"),
                )
                self._attach_cached_access_cards(tickets)
                movers = self._jira.get_mover_tickets(
                    self._config.get("mover_jql", DEFAULT_MOVER_JQL)
                )
                with self._tickets_lock:
                    self._tickets = tickets
                    self._movers = movers
                self._ui_queue.put(("update_tickets", tickets))
                self._ui_queue.put(("update_movers", movers))
                self._start_access_card_reservations(tickets)
            except Exception:
                pass

        today        = date.today()
        with self._tickets_lock:
            tickets_snapshot = list(self._tickets)
            movers_snapshot = list(self._movers)
        week_tickets = [
            t for t in tickets_snapshot
            if t.get("start_date") and 0 <= (t["start_date"] - today).days <= 7
        ]

        week_movers = [
            t for t in movers_snapshot
            if t.get("effective_date") and 0 <= (t["effective_date"] - today).days <= 7
        ]

        if not week_tickets and not week_movers:
            self._storage.mark_morning_summary_sent()
            return

        lines = []
        for t in week_tickets:
            delta = (t["start_date"] - today).days
            done  = self._storage.completed_count(t["id"], len(TASKS))
            when  = "TODAY" if delta == 0 else ("Tomorrow" if delta == 1
                    else t["start_date"].strftime("%b %d"))
            lines.append(f"{when}: {t['name']} [{done}/{len(TASKS)} tasks]")
        for mover in week_movers:
            delta = (mover["effective_date"] - today).days
            when = "TODAY" if delta == 0 else (
                "Tomorrow" if delta == 1 else mover["effective_date"].strftime("%b %d")
            )
            state = "AD ready" if self._storage.ad_setup_done(mover["id"]) else "AD pending"
            lines.append(f"{when}: {mover['name']} [Mover - {state}]")

        _notify(
            f"Good morning - {len(week_tickets)} joiner(s), {len(week_movers)} mover(s)",
            "\n".join(lines),
        )
        self._storage.mark_morning_summary_sent()

    # ── Auto-update ───────────────────────────────────────────────────────────

    def _update_check_loop(self) -> None:
        time.sleep(5)  # let the app finish starting before hitting the network
        while True:
            self._do_update_check()
            time.sleep(60 * 60)  # keep long-running tray sessions update-aware

    def _do_update_check(self) -> None:
        try:
            release = updater.check_for_update()
        except updater.UpdateCheckError as e:
            print(f"[updater] automatic check failed: {e}")
            return
        if release and (
            not self._pending_update
            or self._pending_update.get("version") != release.get("version")
        ):
            self._ui_queue.put(("update_available", release))

    def _handle_update_available(self, release: dict) -> None:
        def _do_install():
            import tkinter.messagebox as mb
            try:
                updater.download_and_apply(release)
            except Exception as e:
                mb.showerror("Update failed", str(e), parent=self._root)
                return
            if self._tray:
                self._tray.stop()
            self._root.quit()

        self._pending_update = release
        if self._window and tk.Toplevel.winfo_exists(self._window):
            self._window.show_update_banner(release["version"], _do_install)
        else:
            import tkinter.messagebox as mb
            version = release["version"]
            notes   = release["notes"]
            msg = f"Version {version} is available.\n"
            if notes:
                msg += f"\nWhat's new:\n{notes}\n"
            msg += "\nInstall now? The app will restart automatically."
            if mb.askyesno("Update available", msg, parent=self._root):
                _do_install()

    # ── Uninstall ─────────────────────────────────────────────────────────────

    def _do_uninstall(self) -> None:
        import tkinter.messagebox as mb
        if updater.IS_FROZEN:
            if not mb.askyesno(
                "Uninstall Jira Reminders",
                "Windows Installer will remove Jira Reminders and its shortcuts.\n\n"
                "Your personal settings and checklist history will be kept so they "
                "are available if you reinstall.\n\nContinue?",
                parent=self._root,
            ):
                return
            if not updater.launch_installed_uninstaller():
                mb.showerror(
                    "Uninstall unavailable",
                    "The Windows Installer registration could not be found. You can "
                    "still remove Jira Reminders from Windows Settings → Apps.",
                    parent=self._root,
                )
                return
            if self._tray:
                self._tray.stop()
            self._root.quit()
            return

        if not mb.askyesno(
            "Uninstall",
            "This will remove Jira Reminders from Windows Startup "
            "and quit the app.\n\nContinue?",
            parent=self._root,
        ):
            return

        updater.remove_startup_shortcut()

        mb.showinfo(
            "Uninstalled",
            "Jira Reminders has been removed from Startup.\n\n"
            "Your saved data in %USERPROFILE%\\.jira-reminders\\ was kept.\n"
            "Delete that folder manually if you want to remove it completely.",
            parent=self._root,
        )
        if self._tray:
            self._tray.stop()
        self._root.quit()

    # ── UI queue ──────────────────────────────────────────────────────────────

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                cmd, *args = self._ui_queue.get_nowait()
                if cmd == "show_window":
                    self._show_window()
                elif cmd == "update_tickets" and self._window:
                    self._window.update_tickets(args[0])
                elif cmd == "update_movers" and self._window:
                    self._window.update_movers(args[0])
                elif cmd == "operator_office":
                    self._card_printer_enabled = args[0] == "vilnius"
                    if self._window:
                        self._window.set_card_printer_visible(self._card_printer_enabled)
                    if self._card_printer_enabled:
                        with self._tickets_lock:
                            tickets = list(self._tickets)
                        self._start_access_card_reservations(tickets)
                elif cmd == "open_settings":
                    self._open_settings()
                elif cmd == "update_available":
                    self._handle_update_available(args[0])
                elif cmd == "uninstall":
                    self._do_uninstall()
        except queue.Empty:
            pass
        self._root.after(100, self._drain_ui_queue)

    # ── Window management ─────────────────────────────────────────────────────

    def _show_window(self) -> None:
        if self._window and tk.Toplevel.winfo_exists(self._window):
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
            return
        self._window = MainWindow(
            self._root,
            self._tickets,
            self._storage,
            self._jira,
            on_refresh=self._manual_refresh,
            snipeit=self._snipeit,
            movers=self._movers,
            card_printer_enabled=self._card_printer_enabled,
            card_registry_message=self._card_registry_message(),
        )
        self._window.lift()
        self._window.focus_force()
        if self._pending_update:
            release = self._pending_update
            self._handle_update_available(release)

    def _manual_refresh(self) -> None:
        threading.Thread(target=self._fetch_and_notify, daemon=True).start()

    def _open_settings(self) -> None:
        dlg = SetupDialog(self._root, prefill=self._config)
        self._root.wait_window(dlg)
        if dlg.result:
            save_config(dlg.result)
            self._apply_config(dlg.result)
            threading.Thread(target=self._fetch_and_notify, daemon=True).start()

    def _run_setup(self, prefill) -> dict | None:
        dlg = SetupDialog(self._root, prefill=prefill)
        self._root.wait_window(dlg)
        return dlg.result

    # ── Tray ──────────────────────────────────────────────────────────────────

    def _tray_show(self, icon=None, item=None):
        self._ui_queue.put(("show_window",))

    def _tray_check_now(self, icon=None, item=None):
        threading.Thread(target=self._fetch_and_notify, daemon=True).start()

    def _tray_settings(self, icon=None, item=None):
        self._ui_queue.put(("open_settings",))

    def _tray_check_updates(self, icon=None, item=None):
        def _check():
            try:
                release = updater.check_for_update()
            except updater.UpdateCheckError as e:
                import tkinter.messagebox as mb
                error_message = str(e)
                self._root.after(0, lambda: mb.showwarning(
                    "Update check failed",
                    f"Could not determine the latest version.\n\n{error_message}",
                    parent=self._root,
                ))
                return
            if release:
                self._ui_queue.put(("update_available", release))
            else:
                import tkinter.messagebox as mb
                self._root.after(0, lambda: mb.showinfo(
                    "Up to date",
                    f"You're running the latest version ({updater.current_version()}).",
                    parent=self._root,
                ))
        threading.Thread(target=_check, daemon=True).start()

    def _tray_uninstall(self, icon=None, item=None):
        self._ui_queue.put(("uninstall",))

    def _tray_quit(self, icon=None, item=None):
        if self._tray:
            self._tray.stop()
        self._root.quit()


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        raise SystemExit(0)
    App().run()
