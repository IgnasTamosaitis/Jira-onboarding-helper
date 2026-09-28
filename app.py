"""
Jira New Joiner Reminders
Runs in the system tray, polls Jira, and sends Windows notifications.
"""
import sys
import queue
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta

import pystray
from PIL import Image, ImageDraw

from jira_client import DEFAULT_MOVER_JQL, JiraClient
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
        self._pending_update: dict | None = None

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
        self._root.mainloop()

    def _apply_config(self, cfg: dict) -> None:
        self._config = cfg
        self._jira = JiraClient(cfg["jira_url"], cfg["email"], cfg["api_token"])
        snipeit_url   = cfg.get("snipeit_url", "").strip()
        snipeit_token = cfg.get("snipeit_token", "").strip()
        self._snipeit = SnipeITClient(snipeit_url, snipeit_token) if snipeit_url and snipeit_token else None

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
        try:
            tickets = self._jira.get_new_joiner_tickets(
                self._config.get("jql", ""),
                self._config.get("date_field", "customfield_10980"),
            )
            with self._tickets_lock:
                self._tickets = tickets
            self._ui_queue.put(("update_tickets", tickets))
            self._send_per_ticket_notifications(tickets)
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
                movers = self._jira.get_mover_tickets(
                    self._config.get("mover_jql", DEFAULT_MOVER_JQL)
                )
                with self._tickets_lock:
                    self._tickets = tickets
                    self._movers = movers
                self._ui_queue.put(("update_tickets", tickets))
                self._ui_queue.put(("update_movers", movers))
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
