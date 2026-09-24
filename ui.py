import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import webbrowser
from datetime import date
import unicodedata

from jira_client import DEFAULT_MOVER_JQL, extract_buddies_from_comments, is_sam_account
from ad_automation import find_user_accounts, classify_scenario
from access_card_registry import AccessCardConfigurationError
from ad_setup_status import check_setup, recover_setup
from mover_ui import MoversPanel
from printer_ui import PrinterPanel

TASKS = [
    "Active Directory account setup",
    "Axapta account import/creation",
    "AX user relations assignment",
    "Assign hardware & licenses in Snipe-IT",
    "Physical access card creation",
]
AD_TASK_INDEX = 0

BG     = "#F7F8FA"
ACCENT = "#0C66E4"
WHITE  = "#FFFFFF"
GREEN  = "#1F845A"
GRAY   = "#5E6C84"
RED    = "#C9372C"
TEXT   = "#172B4D"
BORDER = "#DFE3EA"
SOFT_BLUE = "#E9F2FF"
SOFT_GOLD = "#FFF4E5"
GOLD = "#FF991F"
DARK_GOLD = "#B76E00"
INPUT_BORDER = "#C7D1DB"
INPUT_BG = "#FFFFFF"
ACTION_BTN_WIDTH = 20
PRIORITY_COMPANY_COLOR = RED


class MainWindow(tk.Toplevel):
    def __init__(self, parent, tickets: list, storage, jira_client, on_refresh,
                 snipeit=None, movers: list | None = None,
                 card_printer_enabled: bool = False, card_registry_message: str = ""):
        super().__init__(parent)
        self.tickets   = tickets
        self.storage   = storage
        self.jira      = jira_client
        self.on_refresh = on_refresh
        self._sel: int | None = None
        self._selected_ticket_id: str | None = None
        self._task_vars: list[tk.BooleanVar] = []
        self._notes_box: tk.Text | None = None
        self._notes_ticket_id: str | None = None
        self._notes_save_job = None
        self._ad_btn: tk.Button | None = None
        self._ask_btn: tk.Button | None = None
        self._comments_cache: dict = {}
        self._joiner_snipe_frame: tk.Frame | None = None
        self._joiner_snipe_ticket_id: str = ""
        self._joiner_snipe_refresh_job = None
        self._joiner_snipe_fetching: set[str] = set()
        self._buddy_hint: dict = {}    # ticket_id -> {name, author, date} | None
        self._buddy_fetched: set = set()
        self._buddy_box_frame: tk.Frame | None = None
        self._buddy_box_ticket: str | None = None
        self._manual_buddies: set = set()        # ticket_ids with a persisted manual buddy
        self._dismissed_buddy_names: dict = {}   # ticket_id -> display name of cleared buddy
        self._ad_joiner_checks: set = set()      # ticket_ids with in-flight AD joiner-type checks
        self._ad_status_checks: dict = {}
        self._ad_status_times: dict = {}
        self._ad_status_job = None
        self._load_manual_buddies()
        self._load_dismissed_buddies()
        self._snipeit = snipeit
        self.movers = movers or []
        self._movers_panel: MoversPanel | None = None
        self._card_printer_enabled = card_printer_enabled
        self._printer_tab: tk.Frame | None = None
        self._printer_panel: PrinterPanel | None = None
        self._card_registry_message = card_registry_message

        self.title("ITSD Jira Helper")
        self.geometry("980x620")
        self.resizable(True, True)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self._build()
        self._refresh_list()
        self._update_ad_button()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Top bar ───────────────────────────────────────────────────────────────
        bar = tk.Frame(self, bg=ACCENT, height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="  ITSD Jira Helper", bg=ACCENT, fg=WHITE,
                 font=("Segoe UI", 13, "bold")).pack(side="left", pady=10)
        self._make_btn(bar, "Refresh", self._do_refresh, WHITE, ACCENT).pack(
            side="right", padx=10, pady=8)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        self._main_frame = tk.Frame(self, bg=BG)
        style = ttk.Style(self)
        style.configure("Main.TNotebook", background=BG, borderwidth=0, tabmargins=(12, 8, 0, 0))
        style.configure("Main.TNotebook.Tab", font=("Segoe UI", 9, "bold"), padding=(18, 8))
        notebook = ttk.Notebook(self._main_frame, style="Main.TNotebook")
        self._notebook = notebook
        joiners_tab = tk.Frame(notebook, bg=BG)
        movers_tab = tk.Frame(notebook, bg=BG)
        notebook.add(joiners_tab, text="  New joiners  ")
        notebook.add(movers_tab, text="  Movers  ")
        self._build_joiners_tab(joiners_tab)
        self._movers_panel = MoversPanel(
            movers_tab, self.movers, self.storage, self.jira, self.on_refresh
        )
        self._movers_panel.pack(fill="both", expand=True)
        self.set_card_printer_visible(self._card_printer_enabled)
        notebook.pack(fill="both", expand=True)
        self._main_frame.pack(fill="both", expand=True)

    def set_card_printer_visible(self, enabled: bool) -> None:
        """Apply the operator's AD office check without rebuilding their ticket views."""
        if enabled is True:
            if self._printer_tab is None:
                self._printer_tab = tk.Frame(self._notebook, bg=BG)
                self._printer_panel = PrinterPanel(
                    self._printer_tab, tickets=self.tickets, on_refresh=self.on_refresh,
                    registry_message=self._card_registry_message,
                )
                self._printer_panel.pack(fill="both", expand=True)
                if self._selected_ticket_id:
                    self._printer_panel.select_ticket(self._selected_ticket_id)
            self._notebook.add(self._printer_tab, text="  Card printer  ")
        elif self._printer_tab is not None:
            if str(self._printer_tab) in self._notebook.tabs():
                self._notebook.forget(self._printer_tab)

    def set_card_registry_message(self, message: str) -> None:
        self._card_registry_message = message
        if self._printer_panel:
            self._printer_panel.set_registry_message(message)

    def show_update_banner(self, version: str, on_install) -> None:
        if getattr(self, "_update_banner", None):
            return
        BANNER_BG = "#FFFAE6"
        BANNER_FG = "#172B4D"
        AMBER     = "#FF991F"

        banner = tk.Frame(self, bg=BANNER_BG, height=34)
        banner.pack(fill="x", before=self._main_frame)
        banner.pack_propagate(False)
        self._update_banner = banner

        tk.Label(
            banner,
            text=f"  ⬆  Version {version} is available — install and restart to update.",
            bg=BANNER_BG, fg=BANNER_FG,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0))

        def _dismiss():
            banner.destroy()
            self._update_banner = None

        def _install():
            _dismiss()
            on_install()

        tk.Button(
            banner, text="Install & restart",
            bg=AMBER, fg=WHITE, relief="flat", bd=0,
            font=("Segoe UI", 9, "bold"), padx=10, cursor="hand2",
            activebackground="#E08010", activeforeground=WHITE,
            command=_install,
        ).pack(side="right", padx=(4, 8), pady=4)

        tk.Button(
            banner, text="×",
            bg=BANNER_BG, fg=GRAY, relief="flat", bd=0,
            font=("Segoe UI", 11), padx=6, cursor="hand2",
            activebackground=BANNER_BG, activeforeground=TEXT,
            command=_dismiss,
        ).pack(side="right", pady=4)

    def _build_joiners_tab(self, parent: tk.Frame):
        # Bottom bar — packed first so it anchors to bottom before body expands
        btm = tk.Frame(parent, bg="#EEF2F7", height=46)
        btm.pack(fill="x", side="bottom")
        btm.pack_propagate(False)
        self._status = tk.StringVar(value="")
        tk.Label(btm, textvariable=self._status, bg="#EEF2F7", fg=GRAY,
                 font=("Segoe UI", 9)).pack(side="left", padx=12)
        self._make_btn(btm, "Open in Jira", self._open_jira, WHITE, ACCENT,
                       width=ACTION_BTN_WIDTH).pack(
            side="right", padx=10, pady=8)
        self._ad_btn = self._make_btn(btm, "AD Setup", self._open_ad_setup, WHITE, "#00875A",
                                      width=ACTION_BTN_WIDTH)
        self._ad_btn.pack(side="right", padx=(0, 4), pady=8)
        self._make_btn(btm, "Back up data", self._backup_data, ACCENT, SOFT_BLUE,
                       width=ACTION_BTN_WIDTH).pack(
            side="right", padx=(0, 4), pady=8)
        self._ask_btn = self._make_btn(btm, "Ask reporter", self._open_ask_reporter,
                                       WHITE, "#6554C0", width=ACTION_BTN_WIDTH)
        self._ask_btn.pack(side="right", padx=(0, 4), pady=8)

        # Body: left (list) + right (detail)
        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True)

        # Left panel
        left = tk.Frame(body, bg=WHITE, width=420)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="Upcoming joiners", bg=WHITE, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(14, 6))

        self._listbox = tk.Listbox(
            left, selectmode="single", relief="flat", bd=0,
            bg=WHITE, fg=TEXT, font=("Segoe UI", 10),
            selectbackground=SOFT_BLUE, selectforeground=ACCENT,
            activestyle="none", highlightthickness=0, exportselection=False,
        )
        self._listbox.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        # Right panel
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self._right = right

        self._hint = tk.Label(right, text="Select a joiner to view their checklist",
                              bg=BG, fg=GRAY, font=("Segoe UI", 11))
        self._hint.place(relx=0.5, rely=0.5, anchor="center")

        self._detail_sb = tk.Scrollbar(right, orient="vertical")
        self._detail_canvas = tk.Canvas(right, bg=BG, highlightthickness=0,
                                        yscrollcommand=self._detail_sb.set)
        self._detail_sb.configure(command=self._detail_canvas.yview)
        self._detail = tk.Frame(self._detail_canvas, bg=BG)
        self._detail_wid = self._detail_canvas.create_window(
            (0, 0), window=self._detail, anchor="nw")
        self._detail.bind("<Configure>", lambda e: self._detail_canvas.configure(
            scrollregion=self._detail_canvas.bbox("all")))
        self._detail_canvas.bind("<Configure>", lambda e: self._detail_canvas.itemconfig(
            self._detail_wid, width=e.width))
        self._bind_detail_scroll(self._detail)

    # ── Ticket list ───────────────────────────────────────────────────────────

    def _refresh_list(self):
        self._listbox.delete(0, "end")
        today = date.today()
        for i, t in enumerate(self.tickets):
            self._sync_ad_task(t["id"])
            done  = self.storage.completed_count(t["id"], len(TASKS))
            sd    = t["start_date"]
            color = TEXT  # default - more than 7 days away or no date

            if sd:
                delta = (sd - today).days
                if delta < 0:
                    date_tag = f"started {-delta}d ago"
                    color = GRAY
                elif delta == 0:
                    date_tag = "TODAY"
                    color = RED
                elif delta == 1:
                    date_tag = "TOMORROW"
                    color = RED
                elif delta <= 7:
                    date_tag = f"in {delta}d"
                    color = GREEN
                else:
                    date_tag = f"{sd.strftime('%b %d')} ({delta}d)"
            else:
                date_tag = "no date"

            ad_tag = "  AD done" if self.storage.ad_setup_done(t["id"]) else ""
            company_tag = self._company_title_suffix(t) if self._is_priority_company(t) else ""
            label = f"  {t['key']}  {t['name']}{company_tag}  [{done}/{len(TASKS)}]  {date_tag}{ad_tag}"
            self._listbox.insert("end", label)
            if self._is_priority_company(t):
                color = PRIORITY_COMPANY_COLOR
            self._listbox.itemconfig(i, fg=color)

        if not self.tickets:
            self._listbox.insert("end", "  No tickets found")
            self._status.set("No new joiner tickets found.")

    def _on_select(self, _=None):
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self.tickets):
            return
        self._save_current_notes()
        self._sel = sel[0]
        self._selected_ticket_id = self.tickets[self._sel]["id"]
        if self._printer_panel:
            self._printer_panel.select_ticket(self._selected_ticket_id)
        self._show_detail(self.tickets[self._sel])
        self._update_ad_button()

    def _joiner_type(self, ticket: dict) -> str:
        """Return new_joiner/rejoiner/checking for the header badge."""
        if ticket.get("rejoiner", "").strip().casefold() == "yes":
            return "rejoiner"
        setup_scenario = self.storage.get_ad_setup(ticket.get("id", "")).get("scenario", "")
        if setup_scenario == "new_joiner":
            return "new_joiner"
        if setup_scenario in ("rejoiner_dual", "rejoiner_single"):
            return "rejoiner"
        scenario = ticket.get("ad_joiner_scenario", "")
        if scenario in ("rejoiner_dual", "rejoiner_single"):
            return "rejoiner"
        if scenario == "checking":
            return "checking"
        return "new_joiner"

    def _ensure_ad_joiner_type_check(self, ticket: dict):
        """Use AD as the source of truth when Jira says the person is not a rejoiner."""
        ticket_id = ticket.get("id", "")
        if not ticket_id:
            return
        if ticket.get("rejoiner", "").strip().casefold() == "yes":
            return
        setup_scenario = self.storage.get_ad_setup(ticket_id).get("scenario", "")
        if setup_scenario:
            ticket["ad_joiner_scenario"] = setup_scenario
            return
        if ticket.get("ad_joiner_scenario"):
            return
        first = ticket.get("first_name", "")
        last = ticket.get("last_name", "")
        if not first or not last:
            return
        if ticket_id in self._ad_joiner_checks:
            return

        self._ad_joiner_checks.add(ticket_id)
        ticket["ad_joiner_scenario"] = "checking"

        def _do():
            try:
                accounts = find_user_accounts(first, last)
                scenario = classify_scenario(accounts)
            except Exception:
                scenario = "ad_check_failed"

            def _apply():
                self._ad_joiner_checks.discard(ticket_id)
                selected_id = (
                    self.tickets[self._sel].get("id")
                    if self._sel is not None and self._sel < len(self.tickets)
                    else ""
                )
                updated_index = None
                for idx, current in enumerate(self.tickets):
                    if current.get("id") == ticket_id:
                        current["ad_joiner_scenario"] = scenario
                        updated_index = idx
                        break
                self._refresh_list()
                restore_id = selected_id or ticket_id
                for idx, current in enumerate(self.tickets):
                    if current.get("id") == restore_id:
                        self._sel = idx
                        self._listbox.selection_clear(0, "end")
                        self._listbox.selection_set(idx)
                        self._listbox.activate(idx)
                        self._listbox.see(idx)
                        if restore_id == ticket_id or updated_index == idx:
                            self._show_detail(current)
                        break
                self._update_ad_button()
                self._update_ask_btn()

            self.after(0, _apply)

        threading.Thread(target=_do, daemon=True).start()

    # ── Detail / task panel ───────────────────────────────────────────────────

    @staticmethod
    def _is_priority_company(ticket: dict) -> bool:
        return "willgrow" in (ticket.get("company_name") or "").casefold()

    @staticmethod
    def _company_title_suffix(ticket: dict) -> str:
        company = (ticket.get("company_name") or "").strip()
        return f"  -  {company}" if company else ""

    def _show_detail(self, t: dict):
        self._save_current_notes()
        self._ensure_ad_status_check(t)
        if self._ad_status_job:
            self.after_cancel(self._ad_status_job)
        self._ad_status_job = self.after(60000, lambda: self._refresh_selected_ad(t))
        self._sync_ad_task(t["id"])
        self._ensure_ad_joiner_type_check(t)
        if self._joiner_snipe_refresh_job:
            try:
                self.after_cancel(self._joiner_snipe_refresh_job)
            except tk.TclError:
                pass
            self._joiner_snipe_refresh_job = None
        self._hint.place_forget()
        for w in self._detail.winfo_children():
            w.destroy()
        self._task_vars = []
        self._notes_box = None
        self._notes_ticket_id = None
        self._joiner_snipe_frame = None
        self._joiner_snipe_ticket_id = ""
        self._detail_sb.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne", width=16)
        self._detail_canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0, width=-16)
        self._detail_canvas.yview_moveto(0.0)

        # Name heading + joiner type badge
        joiner_type = self._joiner_type(t)
        is_rejoiner = joiner_type == "rejoiner"
        name_row = tk.Frame(self._detail, bg=BG)
        name_row.pack(anchor="w", padx=24, pady=(20, 2))
        title_color = PRIORITY_COMPANY_COLOR if self._is_priority_company(t) else TEXT
        tk.Label(name_row, text=f"{t['name']}{self._company_title_suffix(t)}", bg=BG, fg=title_color,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        if joiner_type == "checking":
            badge_text = "Checking AD..."
            badge_bg = SOFT_BLUE
            badge_fg = ACCENT
        else:
            badge_text = "Rejoiner" if is_rejoiner else "New Joiner"
            badge_bg   = "#FF991F" if is_rejoiner else "#E3FCEF"
            badge_fg   = WHITE     if is_rejoiner else GREEN
        tk.Label(name_row, text=badge_text, bg=badge_bg, fg=badge_fg,
                 font=("Segoe UI", 9, "bold"), padx=8, pady=3,
                 relief="flat").pack(side="left", padx=(12, 0))

        # Meta row
        today = date.today()
        sd = t["start_date"]
        if sd:
            delta = (sd - today).days
            if delta < 0:
                date_text = f"{sd}  (started {-delta} day(s) ago)"
                date_color = GRAY
            elif delta == 0:
                date_text = f"{sd}  - STARTS TODAY"
                date_color = RED
            elif delta <= 3:
                date_text = f"{sd}  - in {delta} day(s)"
                date_color = "#FF991F"
            else:
                date_text = f"{sd}  - in {delta} day(s)"
                date_color = TEXT
        else:
            date_text  = "Start date: not set"
            date_color = GRAY

        tk.Label(self._detail, text=date_text, bg=BG, fg=date_color,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=24, pady=(0, 2))

        meta_parts = []
        if t.get("position"): meta_parts.append(t["position"])
        if t.get("office"):   meta_parts.append(t["office"])
        if t.get("manager"):  meta_parts.append(f"Mgr: {t['manager']}")
        if t.get("status"):   meta_parts.append(t["status"])
        if meta_parts:
            tk.Label(self._detail, text="  |  ".join(meta_parts), bg=BG, fg=GRAY,
                     font=("Segoe UI", 9), wraplength=500, justify="left").pack(
                     anchor="w", padx=24, pady=(0, 2))

        tk.Label(self._detail, text=t["key"], bg=BG, fg=GRAY,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=24, pady=(0, 10))

        summary_row = tk.Frame(self._detail, bg=BG)
        summary_row.pack(fill="x", padx=24, pady=(2, 12))
        summary_row.columnconfigure(0, weight=1, uniform="joiner_summary")
        summary_row.columnconfigure(1, weight=1, uniform="joiner_summary")
        self._show_ad_setup_summary(t, parent=summary_row, column=0)
        if self._snipeit:
            self._show_joiner_snipe_assets(t, parent=summary_row, column=1)
        self._show_buddy_box(t)

        # Divider
        tk.Frame(self._detail, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(0, 10))

        # Tasks
        tk.Label(self._detail, text="Onboarding checklist", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(0, 6))

        state = self.storage.get(t["id"], len(TASKS))
        for i, task in enumerate(TASKS):
            var = tk.BooleanVar(value=state[i])
            self._task_vars.append(var)
            row = tk.Frame(self._detail, bg=BG)
            row.pack(fill="x", padx=20, pady=3)
            tk.Checkbutton(
                row, variable=var, bg=BG, activebackground=BG,
                command=lambda idx=i, v=var, tid=t["id"]: self._toggle(tid, idx, v),
            ).pack(side="left")
            fg = GRAY if var.get() else TEXT
            tk.Label(row, text=task, bg=BG, fg=fg,
                     font=("Segoe UI", 10), wraplength=500, justify="left",
                     anchor="w").pack(side="left")

        # Notes
        tk.Frame(self._detail, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(14, 8))
        tk.Label(self._detail, text="Notes", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(0, 4))

        notes_box = self._make_text_box(self._detail, height=5)
        notes_box.insert("1.0", self.storage.get_notes(t["id"]))
        notes_box.pack(fill="x", padx=24, pady=(0, 8))
        self._notes_box = notes_box
        self._notes_ticket_id = t["id"]
        notes_box.bind("<KeyRelease>",
                       lambda e, tid=t["id"], nb=notes_box:
                       self._queue_notes_save(tid, nb))
        notes_box.bind("<FocusOut>",
                       lambda e, tid=t["id"], nb=notes_box:
                       self._save_notes(tid, nb))
        self._bind_text_widget_scroll(notes_box, self._detail_canvas)

        # Comments
        tk.Frame(self._detail, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(14, 8))
        tk.Label(self._detail, text="Jira comments", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(0, 4))

        comments_frame = tk.Frame(self._detail, bg=BG)
        comments_frame.pack(fill="x", padx=24, pady=(0, 16))
        comments_frame.columnconfigure(0, weight=1)

        comments_box = self._make_text_box(comments_frame, height=12, readonly=True,
                                           font=("Segoe UI", 9))
        comments_sb = tk.Scrollbar(comments_frame, orient="vertical",
                                   command=comments_box.yview)
        comments_box.configure(yscrollcommand=comments_sb.set)
        comments_box.grid(row=0, column=0, sticky="ew")
        comments_sb.grid(row=0, column=1, sticky="ns")
        self._bind_text_widget_scroll(comments_box, self._detail_canvas)

        if t["key"] in self._comments_cache:
            cached_comments = self._comments_cache[t["key"]]
            self._populate_comments(comments_box, cached_comments)
            if t["id"] not in self._buddy_fetched:
                # Buddy resolution involves PowerShell AD calls — always off-thread.
                threading.Thread(
                    target=self._resolve_and_show_buddy,
                    args=(t["id"], cached_comments),
                    daemon=True,
                ).start()
        else:
            self._set_comments_text(comments_box, "Loading...")

        # Always refresh comments in the background so new Jira replies show up
        # even when the app has been running with a stale in-memory cache.
        threading.Thread(
            target=self._fetch_comments,
            args=(t["key"], t["id"], comments_box),
            daemon=True,
        ).start()

        self._bind_detail_scroll(self._detail)
        done = self.storage.completed_count(t["id"], len(TASKS))
        self._status.set(f"  {done}/{len(TASKS)} tasks completed")
        self._update_ad_button()
        self._update_ask_btn()

    def _bind_detail_scroll(self, widget):
        """Recursively bind mousewheel on all detail children to scroll the outer canvas."""
        widget.bind("<MouseWheel>",
            lambda e: self._scroll_canvas_from_event(self._detail_canvas, e))
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                continue
            self._bind_detail_scroll(child)

    def _toggle(self, ticket_id: str, idx: int, var: tk.BooleanVar):
        if idx == AD_TASK_INDEX:
            var.set(self.storage.ad_setup_done(ticket_id))
            ticket = next((t for t in self.tickets if t["id"] == ticket_id), None)
            if ticket:
                self._ensure_ad_status_check(ticket, force=True)
        self.storage.set(ticket_id, idx, var.get(), len(TASKS))
        done = self.storage.completed_count(ticket_id, len(TASKS))
        self._status.set(f"  {done}/{len(TASKS)} tasks completed")
        self._refresh_list()
        if self._sel is not None:
            self._listbox.selection_set(self._sel)

    def _queue_notes_save(self, ticket_id: str, notes_box: tk.Text):
        self._cancel_notes_save()
        self._notes_save_job = self.after(
            400, lambda: self._save_notes(ticket_id, notes_box))

    def _save_notes(self, ticket_id: str, notes_box: tk.Text):
        self._cancel_notes_save()
        try:
            if notes_box.winfo_exists():
                self.storage.set_notes(ticket_id, notes_box.get("1.0", "end-1c"))
        except tk.TclError:
            pass

    def _save_current_notes(self):
        if self._notes_ticket_id and self._notes_box:
            self._save_notes(self._notes_ticket_id, self._notes_box)

    def _cancel_notes_save(self):
        if self._notes_save_job is not None:
            try:
                self.after_cancel(self._notes_save_job)
            except tk.TclError:
                pass
            self._notes_save_job = None

    # ── Buddy box ─────────────────────────────────────────────────────────────

    def _load_manual_buddies(self):
        for t in self.tickets:
            sam = self.storage.get_manual_buddy(t["id"])
            if sam:
                self._buddy_hint[t["id"]] = {"name": sam, "author": "manual", "date": ""}
                self._buddy_fetched.add(t["id"])
                self._manual_buddies.add(t["id"])

    def _load_dismissed_buddies(self):
        for t in self.tickets:
            tid = t["id"]
            if tid in self._manual_buddies:
                continue  # manual buddy was set after dismissal — it wins
            name = self.storage.get_dismissed_buddy(tid)
            if name:
                self._dismissed_buddy_names[tid] = unicodedata.normalize("NFC", name)

    def _sync_persisted_buddy_state(self):
        # Tickets are often loaded after the window has already been created,
        # so resync stored manual/dismissed buddy state whenever the live list changes.
        self._load_manual_buddies()
        self._load_dismissed_buddies()

    def _show_buddy_box(self, t: dict):
        tid = t["id"]
        frame = tk.Frame(self._detail, bg=SOFT_GOLD,
                         highlightbackground=GOLD, highlightthickness=1)
        frame.pack(fill="x", padx=24, pady=(2, 10))
        self._buddy_box_frame = frame
        self._buddy_box_ticket = tid

        if tid in self._buddy_fetched:
            buddy = self._buddy_hint.get(tid)
            if buddy:
                self._fill_buddy_box(frame, buddy, tid)
                if not buddy.get("multiple") and "disabled" not in buddy:
                    threading.Thread(
                        target=self._check_buddy_disabled,
                        args=(tid, buddy), daemon=True,
                    ).start()
            else:
                self._draw_buddy_no_result(frame, tid)
        else:
            tk.Label(frame, text="Scanning comments for buddy info...",
                     bg=SOFT_GOLD, fg=GRAY,
                     font=("Segoe UI", 9, "italic")).pack(anchor="w", padx=10, pady=6)

    def _fill_buddy_box(self, frame: tk.Frame, buddy: dict, ticket_id: str):
        for w in frame.winfo_children():
            w.destroy()

        if buddy.get("multiple"):
            self._fill_multiple_buddy_box(frame, buddy, ticket_id)
            return

        if buddy.get("disabled"):
            self._fill_disabled_buddy_box(frame, buddy, ticket_id)
            return

        frame.configure(bg=SOFT_GOLD, highlightbackground=GOLD)
        tk.Label(frame, text="Suggested buddy", bg=SOFT_GOLD, fg=DARK_GOLD,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        self._make_selectable_label(
            frame, self._buddy_caption(buddy), SOFT_GOLD, TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", fill="x", padx=10, pady=(2, 0))
        source = self._buddy_source_text(buddy)
        self._make_selectable_label(frame, source, SOFT_GOLD, GRAY,
                                    font=("Segoe UI", 8)).pack(
                                        anchor="w", fill="x", padx=10, pady=(2, 8))

    @staticmethod
    def _buddy_caption(buddy: dict) -> str:
        display_name = buddy.get("display_name", "")
        sam = buddy.get("name", "")
        if display_name and display_name.lower() != sam.lower():
            return f"{display_name} ({sam})"
        return sam or display_name

    @staticmethod
    def _buddy_source_text(buddy: dict) -> str:
        if buddy.get("author") == "manual":
            return "Set manually"
        if buddy.get("source") == "similar_role_field":
            return "From Jira field: Person who is working in the similar job role"
        return f"From comment by {buddy.get('author', '')}  •  {buddy.get('date', '')}"

    def _fill_multiple_buddy_box(self, frame: tk.Frame, buddy: dict, ticket_id: str):
        frame.configure(bg="#FFF4E5", highlightbackground="#FF991F", highlightthickness=1)

        tk.Label(frame, text="Multiple buddies found — choose one",
                 bg="#FFF4E5", fg="#B76E00",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        sources = {
            (candidate.get("author", ""), candidate.get("date", ""))
            for candidate in buddy.get("candidates", [])
        }
        if len(sources) == 1:
            source = f"From comment by {buddy['author']}  •  {buddy['date']}"
        else:
            source = "Detected across multiple Jira comments"
        tk.Label(frame, text=source, bg="#FFF4E5", fg=GRAY,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(2, 4))
        tk.Label(frame,
                 text="The comment mentioned more than one possible buddy. Choose the account to use as the template.",
                 bg="#FFF4E5", fg="#B76E00",
                 font=("Segoe UI", 9), wraplength=480, justify="left").pack(
                     anchor="w", padx=10, pady=(0, 8))

        active_candidates = 0
        for candidate in buddy.get("candidates", []):
            card_bg = "#FFFFFF" if not candidate.get("disabled") else "#FFEBE6"
            card = tk.Frame(frame, bg=card_bg, highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", padx=10, pady=(0, 6))

            top = tk.Frame(card, bg=card_bg)
            top.pack(fill="x", padx=8, pady=(8, 2))
            tk.Label(top, text=self._buddy_caption(candidate), bg=card_bg, fg=TEXT,
                     font=("Segoe UI", 10, "bold")).pack(side="left")
            if candidate.get("disabled"):
                tk.Label(top, text="Disabled in AD", bg=card_bg, fg=RED,
                         font=("Segoe UI", 8, "bold")).pack(side="right")
            else:
                active_candidates += 1

            source = self._buddy_source_text(candidate)
            tk.Label(card, text=source, bg=card_bg, fg=GRAY,
                     font=("Segoe UI", 8), wraplength=440, justify="left").pack(
                         anchor="w", padx=8, pady=(0, 4))

            status = "This account cannot be used as a template." if candidate.get("disabled") else "Ready to use as the template account."
            tk.Label(card, text=status, bg=card_bg,
                     fg=RED if candidate.get("disabled") else GRAY,
                     font=("Segoe UI", 8), wraplength=440, justify="left").pack(
                         anchor="w", padx=8, pady=(0, 6))

            tk.Button(card,
                      text="Use this buddy",
                      command=lambda c=candidate: self._set_manual_buddy(ticket_id, c, keep_source=False),
                      state="disabled" if candidate.get("disabled") else "normal",
                      relief="flat", bd=0,
                      bg="#FF991F" if not candidate.get("disabled") else "#DDE3EA",
                      fg=WHITE if not candidate.get("disabled") else GRAY,
                      font=("Segoe UI", 9, "bold"),
                      padx=10, pady=4, cursor="hand2").pack(anchor="w", padx=8, pady=(0, 8))

        if active_candidates == 0:
            tk.Label(frame,
                     text="All detected buddy accounts are disabled. Use \"Ask reporter\" to request another template account.",
                     bg="#FFF4E5", fg=RED,
                     font=("Segoe UI", 9), wraplength=480, justify="left").pack(
                         anchor="w", padx=10, pady=(0, 8))

    def _fill_disabled_buddy_box(self, frame: tk.Frame, buddy: dict, ticket_id: str):
        """Renders the buddy box in error state when the AD account is disabled."""
        LIGHT_RED = "#FFEBE6"
        DARK_RED   = "#AE2A19"

        frame.configure(bg=LIGHT_RED, highlightbackground=RED, highlightthickness=2)

        tk.Label(frame, text="Buddy account disabled — action required",
                 bg=LIGHT_RED, fg=DARK_RED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        tk.Label(frame, text=self._buddy_caption(buddy), bg=LIGHT_RED, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(2, 0))
        source = self._buddy_source_text(buddy)
        tk.Label(frame, text=source, bg=LIGHT_RED, fg=GRAY,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(2, 4))

        # Warning + instructions
        warn = tk.Frame(frame, bg="#FFBDAD",
                        highlightbackground=DARK_RED, highlightthickness=1)
        warn.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(warn,
                 text="This account is disabled in Active Directory and cannot be used as a template.",
                 bg="#FFBDAD", fg=DARK_RED,
                 font=("Segoe UI", 9, "bold"),
                 wraplength=400, justify="left", anchor="w").pack(anchor="w", padx=8, pady=(6, 2))
        tk.Label(warn,
                 text="1. Click \"Remove disabled buddy\" below to clear this suggestion.\n"
                      "2. Use \"Ask reporter\" to request a valid buddy from the manager or reporter.",
                 bg="#FFBDAD", fg=DARK_RED,
                 font=("Segoe UI", 9),
                 wraplength=400, justify="left", anchor="w").pack(anchor="w", padx=8, pady=(0, 6))

        tk.Button(frame,
                  text="Remove disabled buddy",
                  command=lambda: self._remove_disabled_buddy(ticket_id),
                  relief="flat", bd=0, bg=RED, fg=WHITE,
                  font=("Segoe UI", 9, "bold"),
                  padx=10, pady=4, cursor="hand2").pack(anchor="w", padx=10, pady=(0, 10))

    def _remove_disabled_buddy(self, ticket_id: str):
        if ticket_id is None:
            return
        buddy = self._buddy_hint.get(ticket_id)
        if buddy:
            name = buddy.get("display_name") or buddy.get("name", "")
            if name:
                normalized = unicodedata.normalize("NFC", name)
                self._dismissed_buddy_names[ticket_id] = normalized
                self.storage.set_dismissed_buddy(ticket_id, normalized)
        self._buddy_hint[ticket_id] = None
        self._buddy_fetched.discard(ticket_id)
        self._manual_buddies.discard(ticket_id)
        self.storage.set_manual_buddy(ticket_id, "")  # clear any persisted manual buddy
        self._refresh_buddy_box(ticket_id, None)
        self._update_ask_btn()

    def _draw_buddy_no_result(self, frame: tk.Frame, ticket_id: str):
        for w in frame.winfo_children():
            w.destroy()
        frame.configure(bg="#FFF4E5", highlightbackground="#FF991F")
        tk.Label(frame,
                 text="Buddy not mentioned in comments — consider asking the reporter",
                 bg="#FFF4E5", fg="#B76E00",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(6, 2))

        entry_row = tk.Frame(frame, bg="#FFF4E5")
        entry_row.pack(anchor="w", padx=10, pady=(0, 6))
        tk.Label(entry_row, text="Manual buddy:", bg="#FFF4E5", fg="#B76E00",
                 font=("Segoe UI", 9)).pack(side="left")
        buddy_var = tk.StringVar()
        entry = tk.Entry(entry_row, textvariable=buddy_var, font=("Consolas", 10),
                         relief="solid", bd=1, width=14)
        entry.pack(side="left", padx=6, ipady=2)

        def _set_manual():
            name = buddy_var.get().strip()
            if not name:
                return
            self._set_manual_buddy(ticket_id, {"name": name, "author": "manual", "date": ""})

        tk.Button(entry_row, text="Set as buddy", command=_set_manual,
                  relief="flat", bd=0, bg="#FF991F", fg=WHITE,
                  font=("Segoe UI", 9), padx=8, pady=2, cursor="hand2").pack(side="left")
        entry.bind("<Return>", lambda _: _set_manual())

    def _set_manual_buddy(self, ticket_id: str, buddy_data: dict, keep_source: bool = True):
        # Clear any prior dismissed-buddy state so the new buddy isn't blocked.
        self._dismissed_buddy_names.pop(ticket_id, None)
        self.storage.set_dismissed_buddy(ticket_id, "")

        selected = dict(buddy_data)
        if not keep_source:
            selected["author"] = "manual"
            selected["date"] = ""

        self._buddy_hint[ticket_id] = selected
        self._manual_buddies.add(ticket_id)
        self.storage.set_manual_buddy(ticket_id, selected["name"])
        self._refresh_buddy_box(ticket_id, selected)
        self._update_ask_btn()
        self._update_ad_button()
        threading.Thread(
            target=self._check_buddy_disabled,
            args=(ticket_id, selected), daemon=True,
        ).start()

    def _check_buddy_disabled(self, ticket_id: str, buddy: dict):
        try:
            from ad_automation import get_account_enabled_status, DISABLED_OU_FRAGMENT
            enabled, dn, display_name = get_account_enabled_status(buddy["name"])
            if enabled is None:
                return
            is_dis = not enabled or DISABLED_OU_FRAGMENT.lower() in dn.lower()
        except Exception:
            return
        updated = {**buddy, "disabled": is_dis,
                   "display_name": display_name or buddy.get("display_name") or buddy["name"]}
        self._buddy_hint[ticket_id] = updated
        self.after(0, lambda: self._refresh_buddy_box(ticket_id, updated))

    def _refresh_buddy_box(self, ticket_id: str, buddy: dict | None):
        if self._buddy_box_ticket != ticket_id or not self._buddy_box_frame:
            return
        try:
            if not self._buddy_box_frame.winfo_exists():
                return
        except tk.TclError:
            return
        if buddy:
            self._fill_buddy_box(self._buddy_box_frame, buddy, ticket_id)
        else:
            self._draw_buddy_no_result(self._buddy_box_frame, ticket_id)
        self._update_ask_btn()
        self._update_ad_button()

    # ── Ask reporter ──────────────────────────────────────────────────────────

    def _update_ask_btn(self):
        if not self._ask_btn:
            return
        if self._sel is None or self._sel >= len(self.tickets):
            self._ask_btn.config(state="disabled", bg="#DDE3EA", fg=GRAY)
            return
        tid = self.tickets[self._sel]["id"]
        buddy = self._buddy_hint.get(tid) if tid in self._buddy_fetched else None
        buddy_active = bool(buddy) and (
            (buddy.get("multiple") and any(not c.get("disabled") for c in buddy.get("candidates", [])))
            or (not buddy.get("multiple") and not buddy.get("disabled"))
        )
        if buddy_active:
            self._ask_btn.config(state="disabled", bg="#DDE3EA", fg=GRAY)
        else:
            self._ask_btn.config(state="normal", bg="#6554C0", fg=WHITE)

    def _open_ask_reporter(self):
        if self._sel is None or self._sel >= len(self.tickets):
            return
        ticket = self.tickets[self._sel]

        def on_sent():
            key, tid = ticket["key"], ticket["id"]
            self._comments_cache.pop(key, None)
            self._buddy_hint.pop(tid, None)
            self._buddy_fetched.discard(tid)
            if (self._sel is not None and self._sel < len(self.tickets)
                    and self.tickets[self._sel]["id"] == tid):
                self._show_detail(ticket)

        disabled_buddy = self._dismissed_buddy_names.get(ticket["id"], "")
        AskReporterDialog(self, ticket, self.jira, on_sent=on_sent,
                          disabled_buddy=disabled_buddy)

    # ── Comments ──────────────────────────────────────────────────────────────

    def _fetch_comments(self, issue_key: str, ticket_id: str, box: tk.Text):
        try:
            comments = self.jira.get_comments(issue_key)
            self._comments_cache[issue_key] = comments
            buddy = self._resolve_buddy_from_comments(ticket_id, comments)
            self._buddy_hint[ticket_id] = buddy
            self._buddy_fetched.add(ticket_id)
            self.after(0, lambda: self._populate_comments(box, comments))
            self.after(0, lambda: self._refresh_buddy_box(ticket_id, buddy))
        except Exception as e:
            self.after(0, lambda: self._set_comments_text(box, f"Could not load comments: {e}"))

    def _resolve_and_show_buddy(self, ticket_id: str, comments: list[dict]):
        """Resolve buddy from already-cached comments, off the UI thread."""
        buddy = self._resolve_buddy_from_comments(ticket_id, comments)
        self._buddy_hint[ticket_id] = buddy
        self._buddy_fetched.add(ticket_id)
        self.after(0, lambda: self._refresh_buddy_box(ticket_id, buddy))

    def _resolve_buddy_from_comments(self, ticket_id: str, comments: list[dict]) -> dict | None:
        if ticket_id in self._manual_buddies:
            return self._buddy_hint.get(ticket_id)
        ticket_buddy = self._similar_role_buddy_candidate(ticket_id)
        if ticket_buddy:
            resolved = self._resolve_detected_buddies(ticket_id, [ticket_buddy])
            if resolved:
                return resolved
        return self._resolve_detected_buddies(
            ticket_id, extract_buddies_from_comments(comments)
        )

    def _similar_role_buddy_candidate(self, ticket_id: str) -> dict | None:
        for ticket in self.tickets:
            if ticket.get("id") == ticket_id:
                return ticket.get("similar_role_buddy")
        return None

    def _resolve_detected_buddies(self, ticket_id: str, detected: list[dict]) -> dict | None:
        if not detected:
            return None

        dismissed = unicodedata.normalize(
            "NFC", self._dismissed_buddy_names.get(ticket_id, "")
        )
        resolved: list[dict] = []
        seen: set[str] = set()
        for candidate in detected:
            resolved_candidate = self._resolve_detected_buddy(candidate)
            if not resolved_candidate:
                continue
            if dismissed and self._buddy_matches_dismissed(resolved_candidate, dismissed):
                continue
            key = resolved_candidate["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            resolved.append(resolved_candidate)

        if not resolved:
            return None
        if len(resolved) == 1:
            return resolved[0]
        return {
            "multiple": True,
            "author": resolved[0]["author"],
            "date": resolved[0]["date"],
            "candidates": resolved,
        }

    @staticmethod
    def _buddy_matches_dismissed(candidate: dict, dismissed_name: str) -> bool:
        dismissed_folded = dismissed_name.casefold()
        names = {
            unicodedata.normalize("NFC", candidate.get("name", "")).casefold(),
            unicodedata.normalize("NFC", candidate.get("display_name", "")).casefold(),
        }
        return dismissed_folded in names

    @staticmethod
    def _resolve_detected_buddy(candidate: dict) -> dict | None:
        try:
            from ad_automation import (
                DISABLED_OU_FRAGMENT,
                find_user_accounts_by_name,
                get_account_enabled_status,
            )
        except Exception:
            return None

        if is_sam_account(candidate["name"]):
            try:
                enabled, dn, display_name = get_account_enabled_status(candidate["name"])
            except Exception:
                return None
            if enabled is None:
                return None
            is_dis = not enabled or DISABLED_OU_FRAGMENT.lower() in dn.lower()
            return {
                **candidate,
                "disabled": is_dis,
                "display_name": display_name or candidate["name"],
            }

        try:
            original_name = unicodedata.normalize("NFC", candidate["name"])
            accounts = find_user_accounts_by_name(original_name)
        except Exception:
            return None

        enabled_accounts = [a for a in accounts if a["enabled"]]
        match = enabled_accounts or accounts
        if not match:
            return None

        selected = match[0]
        is_dis = not selected["enabled"] or DISABLED_OU_FRAGMENT.lower() in selected["dn"].lower()
        return {
            **candidate,
            "name": selected["username"],
            "disabled": is_dis,
            "display_name": original_name,
        }

    def _populate_comments(self, box: tk.Text, comments: list[dict]):
        if not comments:
            self._set_comments_text(box, "No comments.")
            return
        parts = []
        for c in comments:
            parts.append(f"[{c['created']}]  {c['author']}\n{c['body']}")
        self._set_comments_text(box, "\n\n─────────────────────────────\n\n".join(parts))

    @staticmethod
    def _set_comments_text(box: tk.Text, text: str):
        try:
            box.config(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.config(state="disabled")
        except tk.TclError:
            pass

    # ── Actions ───────────────────────────────────────────────────────────────

    def _open_jira(self):
        if self._sel is not None and self._sel < len(self.tickets):
            webbrowser.open(self.tickets[self._sel]["url"])

    def _open_ad_setup(self):
        if self._sel is None or self._sel >= len(self.tickets):
            return
        ticket = self.tickets[self._sel]
        from ad_ui import ADSetupWindow
        buddy_info = self._buddy_hint.get(ticket["id"])
        buddy_name = buddy_info["name"] if buddy_info and not buddy_info.get("multiple") else ""
        ADSetupWindow(
            self,
            ticket,
            storage=self.storage,
            on_completed=lambda: self._ad_setup_completed(ticket),
            buddy_hint=buddy_name,
        )

    def _do_refresh(self):
        self._status.set("Refreshing...")
        self.on_refresh()

    def _backup_data(self):
        self._save_current_notes()
        try:
            path = self.storage.backup_tasks()
            self._status.set(f"Backup saved: {path}")
            messagebox.showinfo("Backup saved", f"Notes and checklist progress were backed up to:\n{path}", parent=self)
        except Exception as e:
            messagebox.showerror("Backup failed", str(e), parent=self)

    def update_tickets(self, tickets: list):
        self.tickets = tickets
        if self._printer_panel:
            self._printer_panel.update_tickets(tickets)
        self._sync_persisted_buddy_state()
        self._refresh_list()
        self._status.set(f"Loaded {len(tickets)} ticket(s).")
        if self._selected_ticket_id is not None:
            self._sel = next(
                (i for i, t in enumerate(tickets) if t["id"] == self._selected_ticket_id),
                None,
            )
            if self._sel is not None:
                self._listbox.selection_clear(0, "end")
                self._listbox.selection_set(self._sel)
                self._listbox.activate(self._sel)
                self._show_detail(tickets[self._sel])
            else:
                self._selected_ticket_id = None
        self._update_ad_button()
        self._update_ask_btn()

    def update_movers(self, movers: list):
        self.movers = movers
        if self._movers_panel:
            self._movers_panel.update_movers(movers)

    def _show_joiner_snipe_assets(self, t: dict, parent: tk.Frame, column: int = 1):
        box = tk.Frame(parent, bg="#E9F2FF", highlightbackground=ACCENT,
                       highlightthickness=1)
        box.grid(row=0, column=column, sticky="nsew", padx=(6, 0), pady=0)
        box.columnconfigure(0, weight=1)
        self._joiner_snipe_frame = box
        self._joiner_snipe_ticket_id = t["id"]
        self._draw_joiner_snipe_state("Loading Snipe-IT assets...")
        self._fetch_joiner_snipe_assets(t, schedule_next=True)

    def _draw_joiner_snipe_state(
        self,
        message: str = "",
        assets: list[dict] | None = None,
        error: bool = False,
    ):
        frame = self._joiner_snipe_frame
        if not frame:
            return
        try:
            if not frame.winfo_exists():
                return
        except tk.TclError:
            return

        for child in frame.winfo_children():
            child.destroy()
        bg = "#FFF4E5" if error else "#E9F2FF"
        fg = "#B76E00" if error else ACCENT
        frame.configure(bg=bg, highlightbackground=fg if error else ACCENT)

        header = tk.Frame(frame, bg=bg)
        header.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(header, text="Assigned assets (Snipe-IT)", bg=bg, fg=fg,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        if assets is not None:
            if not assets:
                self._make_selectable_label(frame, "No assets assigned yet.", bg, GRAY,
                                            font=("Segoe UI", 9)).pack(
                                                anchor="w", fill="x", padx=10, pady=(0, 8))
                return
            for asset in assets:
                self._draw_joiner_snipe_asset(frame, asset, bg)
            return

        self._make_selectable_label(frame, message, bg, fg if error else GRAY,
                                    font=("Segoe UI", 9)).pack(
                                        anchor="w", fill="x", padx=10, pady=(0, 8))

    def _draw_joiner_snipe_asset(self, parent: tk.Frame, asset: dict, bg: str):
        model = ((asset.get("model") or {}).get("name") or asset.get("name") or "Asset").strip()
        tag = (asset.get("asset_tag") or "").strip()
        serial = (asset.get("serial") or "").strip()
        category = ((asset.get("category") or {}).get("name") or "").strip()
        status = self._snipe_asset_status(asset)
        location = asset.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")

        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", padx=10, pady=(0, 8))
        title_bits = [part for part in (model, f"#{tag}" if tag else "") if part]
        self._make_selectable_label(row, "  ".join(title_bits), bg, TEXT,
                                    font=("Segoe UI", 10, "bold")).pack(
                                        anchor="w", fill="x")
        meta = []
        if serial:
            meta.append(f"SN: {serial}")
        if category:
            meta.append(category)
        if status:
            meta.append(str(status))
        if location:
            meta.append(str(location))
        if meta:
            self._make_selectable_label(row, "  |  ".join(meta), bg, GRAY,
                                        font=("Segoe UI", 8), wrap=520).pack(
                                            anchor="w", fill="x", pady=(1, 0))

    @staticmethod
    def _snipe_asset_status(asset: dict) -> str:
        status = asset.get("status_label", "")
        if isinstance(status, dict):
            status = status.get("name", "")
        return str(status or "").strip().casefold()

    def _fetch_joiner_snipe_assets(self, ticket: dict, schedule_next: bool = False):
        ticket_id = ticket.get("id", "")
        if not self._snipeit or not ticket_id:
            return
        if ticket_id in self._joiner_snipe_fetching:
            return
        self._joiner_snipe_fetching.add(ticket_id)

        def _do():
            assets = None
            message = ""
            error = False
            try:
                user = self._snipeit.find_exact_user(
                    ticket.get("first_name", ""), ticket.get("last_name", "")
                )
                if not user:
                    assets = []
                    message = "Exact Snipe-IT user not found."
                else:
                    assets = [
                        asset for asset in self._snipeit.get_user_assets(user["id"])
                        if self._snipe_asset_status(asset) != "archived"
                    ]
            except Exception as exc:
                message = f"Could not load Snipe-IT assets: {exc}"
                error = True

            def _apply():
                self._joiner_snipe_fetching.discard(ticket_id)
                if self._joiner_snipe_ticket_id != ticket_id:
                    return
                if assets is not None:
                    if assets:
                        self._draw_joiner_snipe_state(assets=assets)
                    elif message:
                        self._draw_joiner_snipe_state(message)
                    else:
                        self._draw_joiner_snipe_state(assets=[])
                else:
                    self._draw_joiner_snipe_state(message, error=error)
                if schedule_next and self._joiner_snipe_ticket_id == ticket_id:
                    self._joiner_snipe_refresh_job = self.after(
                        30000,
                        lambda tid=ticket_id: self._refresh_joiner_snipe_assets(tid),
                    )

            self.after(0, _apply)

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_joiner_snipe_assets(self, ticket_id: str):
        if self._sel is None or self._sel >= len(self.tickets):
            return
        ticket = self.tickets[self._sel]
        if ticket.get("id") != ticket_id:
            return
        self._fetch_joiner_snipe_assets(ticket, schedule_next=True)

    def _show_ad_setup_summary(self, t: dict, parent: tk.Frame | None = None, column: int = 0):
        info = self.storage.get_ad_setup(t["id"])
        checking = t["id"] in self._ad_status_checks
        result = info.get("live_check", {})
        done = self.storage.ad_setup_done(t["id"])
        state = "checking" if checking else result.get("state", "unknown")
        headings = {"checking": "Checking Active Directory...", "verified": "AD setup verified",
                    "drift": "AD setup needs attention", "partial": "AD history recovered — partial verification",
                    "unavailable": "AD verification unavailable", "unknown": "AD setup not verified"}
        heading = headings.get(state, headings["unknown"])
        if state == "partial" and done:
            heading = "AD completed — limited setup history"
        if state == "verified" and not done:
            heading = "AD check expired — refresh required"
        bg = "#E7F4EC" if done and not checking else SOFT_GOLD

        parent = parent or self._detail
        box = tk.Frame(parent, bg=bg, highlightbackground=BORDER,
                       highlightthickness=1)
        if parent is self._detail:
            box.pack(fill="x", padx=24, pady=(2, 12))
        else:
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 6), pady=0)
        details = []
        if info.get("email"):
            details.append(f"Expected email: {info['email']}")
        if info.get("groups_count") not in (None, ""):
            details.append(f"Expected groups: {info['groups_count']}")
        actual = result.get("actual", {})
        if actual and not checking:
            details.append("Account: " + ("enabled" if actual.get("enabled") else "DISABLED"))
            details.append("Locked: " + ("yes" if actual.get("locked") else "no"))
            if actual.get("email"):
                details.append(f"Email in AD: {actual['email']}")
            if actual.get("ou") and actual["ou"].casefold() != info.get("target_ou", "").casefold():
                details.append(f"Current folder in AD: {actual['ou']}")
        if result.get("checked_at"):
            details.append(f"Last checked: {result['checked_at']}")
        if result.get("server"):
            details.append(f"Directory server: {result['server']}")
        if info.get("recovered_from"):
            details.append(f"History recovered from: {info['recovered_from']}")
        details.extend(result.get("issues", []) if not checking else [])
        if not checking:
            details.extend(f"Group advisory (does not block completion): {warning}"
                           for warning in result.get("group_warnings", []))

        account = (info.get("account") or "").strip()
        password = (info.get("password") or "").strip() if done and not checking else ""
        sms_template = info.get("sms_template") or (
            "Hello,\n\nYour username and password is:\n\n"
            f"Username: {account}\nPassword: {password}\n\nHave a great day!"
            if account and password else ""
        )
        if not done or checking:
            sms_template = ""
        header = tk.Frame(box, bg=bg)
        header.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(header, text=heading, bg=bg, fg=GREEN if done and not checking else DARK_GOLD,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self._make_btn(header, "Check AD", lambda: self._refresh_selected_ad(t), WHITE, ACCENT).pack(side="right")

        if account:
            account_frame = tk.Frame(box, bg=bg)
            account_frame.pack(fill="x", padx=10, pady=(4, 8))
            self._make_selectable_label(
                account_frame, f"Account: {account}", bg, TEXT,
                font=("Segoe UI", 12, "bold"),
            ).pack(anchor="w", fill="x")
            if password:
                password_row = tk.Frame(account_frame, bg=bg)
                password_row.pack(fill="x", pady=(2, 0))
                self._make_selectable_label(
                    password_row, "Password: stored securely", bg, TEXT,
                    font=("Segoe UI", 10, "bold"),
                ).pack(side="left", fill="x", expand=True)
                self._make_btn(
                    password_row, "Copy password",
                    lambda text=password: self._copy_sensitive_to_clipboard(text),
                    WHITE, GREEN,
                ).pack(side="right", padx=(8, 0))

        details_frame = tk.Frame(box, bg=bg)
        details_frame.pack(fill="x", padx=10, pady=(0, 8))
        for detail in details:
            self._make_selectable_label(details_frame, detail, bg, TEXT,
                                        font=("Segoe UI", 9), wrap=620).pack(
                                            anchor="w", fill="x", pady=1)

        if info.get("target_ou"):
            self._make_selectable_label(
                box, f"Target folder: {info['target_ou']}", bg, GRAY,
                font=("Segoe UI", 8), wrap=620,
            ).pack(anchor="w", fill="x", padx=10, pady=(0, 8))

        if info.get("phone") or sms_template:
            handoff = tk.Frame(box, bg=bg)
            handoff.pack(fill="x", padx=10, pady=(0, 10))

            action_row = tk.Frame(handoff, bg=bg)
            action_row.pack(fill="x")
            action_row.columnconfigure(0, weight=1)
            action_col = 0

            if info.get("phone"):
                phone = self._dedupe_repeated_country_code(info["phone"])
                self._make_selectable_label(
                    action_row, f"Phone: {phone}", bg, TEXT,
                    font=("Segoe UI", 9, "bold"),
                ).grid(row=0, column=0, sticky="ew")
                self._make_btn(
                    action_row, "Copy phone",
                    lambda phone=phone: self._copy_to_clipboard(phone),
                    WHITE, GREEN,
                ).grid(row=0, column=1, sticky="e", padx=(8, 0))
                action_col = 2

            if sms_template:
                self._make_btn(
                    action_row, "Copy message",
                    lambda text=sms_template: self._copy_sensitive_to_clipboard(text),
                    WHITE, GREEN,
                ).grid(row=0, column=action_col, sticky="e", padx=(8, 0))

    def _update_ad_button(self):
        if not self._ad_btn:
            return
        if self._sel is None or self._sel >= len(self.tickets):
            self._ad_btn.config(state="disabled", text="AD Setup", bg="#DDE3EA", fg=GRAY)
            return
        self._ad_btn.config(state="normal", text="AD Setup", bg="#00875A", fg=WHITE)

    def _ad_setup_completed(self, ticket: dict):
        self._ad_status_checks.pop(ticket["id"], None)
        self._ad_status_times.pop(ticket["id"], None)
        done = self.storage.ad_setup_done(ticket["id"])
        self.storage.set(ticket["id"], AD_TASK_INDEX, done, len(TASKS))
        setup_scenario = self.storage.get_ad_setup(ticket["id"]).get("scenario", "")
        if setup_scenario:
            ticket["ad_joiner_scenario"] = setup_scenario
        self._refresh_list()
        if self._sel is not None and self._sel < len(self.tickets):
            self._listbox.selection_set(self._sel)
        self._show_detail(ticket)
        self._status.set("AD setup marked as completed." if done else "AD setup is not completed. Review the execution output.")

    def _sync_ad_task(self, ticket_id: str):
        done = self.storage.ad_setup_done(ticket_id)
        state = self.storage.get(ticket_id, len(TASKS))
        if state[AD_TASK_INDEX] != done:
            self.storage.set(ticket_id, AD_TASK_INDEX, done, len(TASKS))

    def _refresh_selected_ad(self, ticket: dict):
        if self._selected_ticket_id != ticket["id"]:
            return
        self._ensure_ad_status_check(ticket, force=True)
        self._show_detail(ticket)

    def _ensure_ad_status_check(self, ticket: dict, force: bool = False):
        ticket_id = ticket["id"]
        if ticket_id in self._ad_status_checks:
            return
        if not force and time.monotonic() - self._ad_status_times.get(ticket_id, -60) < 60:
            return
        token = object()
        self._ad_status_checks[ticket_id] = token
        saved = self.storage.get_ad_setup(ticket_id)

        def _do():
            info = recover_setup(ticket, saved)
            result = check_setup(info)

            def _apply():
                if self._ad_status_checks.get(ticket_id) is not token:
                    return
                self._ad_status_checks.pop(ticket_id, None)
                self._ad_status_times[ticket_id] = time.monotonic()
                # A setup completed while this read was running supersedes it.
                if self.storage.get_ad_setup(ticket_id) != saved:
                    self._ad_status_times.pop(ticket_id, None)
                else:
                    self.storage.update_ad_verification(ticket_id, info, result)
                self._refresh_list()
                if self._sel is not None and self._sel < len(self.tickets):
                    self._listbox.selection_set(self._sel)
                    if self.tickets[self._sel]["id"] == ticket_id:
                        self._show_detail(self.tickets[self._sel])

            try:
                self.after(0, _apply)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=_do, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self._status.set("Copied to clipboard.")

    def _copy_sensitive_to_clipboard(self, text: str):
        self._copy_to_clipboard(text)
        expected = text or ""
        self.after(
            30_000,
            lambda expected=expected: self._clear_sensitive_clipboard(expected),
        )
        self._status.set("Copied to clipboard; it will be cleared after 30 seconds.")

    def _clear_sensitive_clipboard(self, expected: str):
        try:
            if self.clipboard_get() == expected:
                self.clipboard_clear()
                self.clipboard_append("")
        except tk.TclError:
            pass

    @staticmethod
    def _dedupe_repeated_country_code(phone: str) -> str:
        return re.sub(r"^(\+\d{1,3})(?:[\s-]*\1)+", r"\1", str(phone or "").strip())

    @staticmethod
    def _make_btn(parent, text, cmd, fg, bg, width: int | None = None):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief="flat", bd=0, font=("Segoe UI", 9),
                         padx=10, pady=4, cursor="hand2", width=width,
                         activebackground=ACCENT, activeforeground=WHITE)

    @staticmethod
    def _make_text_box(parent, height: int, readonly: bool = False,
                       font=("Segoe UI", 10)) -> tk.Text:
        box = tk.Text(
            parent,
            height=height,
            relief="flat",
            bd=0,
            font=font,
            bg=INPUT_BG,
            fg=TEXT,
            wrap="word",
            padx=8,
            pady=6,
            undo=not readonly,
            insertbackground=TEXT,
            highlightthickness=1,
            highlightbackground=INPUT_BORDER,
            highlightcolor=ACCENT,
        )
        if readonly:
            box.config(state="disabled")
        return box

    @staticmethod
    def _make_selectable_label(
        parent,
        text: str,
        bg: str,
        fg: str,
        font=("Segoe UI", 9),
        wrap: int = 0,
    ) -> tk.Text:
        box = tk.Text(
            parent,
            height=max(1, str(text or "").count("\n") + 1),
            relief="flat",
            bd=0,
            font=font,
            bg=bg,
            fg=fg,
            wrap="word",
            padx=0,
            pady=0,
            cursor="xterm",
            insertwidth=0,
            highlightthickness=0,
            selectbackground="#B3D4FC",
            selectforeground=TEXT,
        )
        if wrap:
            box.configure(width=max(20, wrap // 7))
        box.insert("1.0", text or "")
        box.config(state="disabled")
        return box

    @staticmethod
    def _wheel_units(event) -> int:
        delta = getattr(event, "delta", 0)
        if not delta:
            return -1
        units = int(-delta / 120)
        if units:
            return units
        return -1 if delta > 0 else 1

    def _scroll_canvas_from_event(self, canvas: tk.Canvas, event) -> str:
        canvas.yview_scroll(self._wheel_units(event), "units")
        return "break"

    def _bind_text_widget_scroll(self, widget: tk.Text, canvas: tk.Canvas):
        def _on_wheel(event):
            units = self._wheel_units(event)
            first, last = widget.yview()
            at_top = first <= 0.0
            at_bottom = last >= 1.0
            if (units < 0 and at_top) or (units > 0 and at_bottom):
                canvas.yview_scroll(units, "units")
            else:
                widget.yview_scroll(units, "units")
            return "break"

        widget.bind("<MouseWheel>", _on_wheel)


class SetupDialog(tk.Toplevel):
    """First-run / settings configuration dialog. Result in .result dict."""

    DEFAULTS = {
        "jira_url":              "https://girteka.atlassian.net",
        "email":                 "",
        "api_token":             "",
        "jql":                   'assignee = currentUser() AND issuetype = "SF: Employee onboarding" AND status in (Open, "In Progress", Pending)',
        "date_field":            "customfield_10980",
        "mover_jql":             DEFAULT_MOVER_JQL,
        "snipeit_url":           "https://inventory.girteka.eu",
        "snipeit_token":         "",
        "access_card_flow_url":  "",
        "access_card_site_url":  "",
        "access_card_list_id":   "",
        "access_card_sync_folder": "",
        "access_card_tenant_id": "",
        "access_card_client_id": "",
        "remind_days_before":    "3",
        "check_interval_minutes": "30",
    }

    def __init__(self, parent, prefill: dict | None = None):
        super().__init__(parent)
        self.result = None
        self._first_run = prefill is None
        self._advanced_visible = not self._first_run
        self.title("Welcome to Jira Reminders" if self._first_run else "Jira Reminders - Settings")
        self.geometry("620x650" if self._first_run else "620x720")
        self.minsize(560, 520)
        self.resizable(False, True)
        self.configure(bg=BG)
        self.grab_set()

        cfg = {**self.DEFAULTS, **(prefill or {})}
        if self._first_run and not cfg["email"]:
            cfg["email"] = self._guess_email()
        self._vars = {k: tk.StringVar(value=str(v)) for k, v in cfg.items()}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    @staticmethod
    def _guess_email() -> str:
        """Use the signed-in Windows UPN when it looks like an email address."""
        try:
            result = subprocess.run(
                ["whoami.exe", "/upn"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            candidate = result.stdout.strip()
            if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate):
                return candidate
        except Exception:
            pass
        return ""

    def _build(self):
        header = tk.Frame(self, bg=ACCENT)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Welcome to Jira Reminders" if self._first_run else "Jira Reminders settings",
            bg=ACCENT,
            fg=WHITE,
            font=("Segoe UI", 15, "bold"),
            anchor="w",
        ).pack(fill="x", padx=26, pady=(16, 2))
        tk.Label(
            header,
            text=(
                "Connect your accounts once, then the app will start automatically."
                if self._first_run
                else "Update your account connection or notification preferences."
            ),
            bg=ACCENT,
            fg="#DDEBFF",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", padx=26, pady=(0, 16))

        btm = tk.Frame(self, bg=BG, bd=0)
        btm.pack(side="bottom", fill="x", padx=26, pady=(8, 16))
        self._msg = tk.Label(btm, text="", bg=BG, fg=GRAY, font=("Segoe UI", 9))
        self._msg.pack(side="bottom", fill="x", pady=(7, 0))
        self._save_btn = tk.Button(
            btm,
            text="Save & start" if self._first_run else "Save",
            command=self._save,
            relief="flat",
            bd=0,
            bg=ACCENT,
            fg=WHITE,
            activebackground="#0055B8",
            activeforeground=WHITE,
            font=("Segoe UI", 9, "bold"),
            padx=18,
            pady=7,
        )
        self._save_btn.pack(side="right")
        self._test_btn = tk.Button(
            btm,
            text="Test Jira connection",
            command=self._test,
            relief="flat",
            bd=0,
            bg="#DEEBFF",
            fg=ACCENT,
            activebackground="#CCE0FF",
            font=("Segoe UI", 9),
            padx=12,
            pady=7,
        )
        self._test_btn.pack(side="left")

        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        form = tk.Frame(canvas, bg=BG)
        form_id = canvas.create_window((0, 0), window=form, anchor="nw")

        def _on_frame_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(form_id, width=event.width)

        form.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.bind("<MouseWheel>", _on_mousewheel)

        tk.Label(
            form,
            text="Your account",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=26, pady=(20, 2))
        tk.Label(
            form,
            text="Tokens are saved securely in Windows Credential Manager.",
            bg=BG,
            fg=GRAY,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", padx=26, pady=(0, 7))

        self._add_field(form, "Atlassian email", "email", hint="Usually your Girteka work email")
        self._add_field(form, "Jira API token", "api_token", secret=True)

        token_row = tk.Frame(form, bg=BG)
        token_row.pack(fill="x", padx=26, pady=(2, 4))
        token_link = tk.Label(
            token_row,
            text="Create a Jira API token ↗",
            bg=BG,
            fg=ACCENT,
            cursor="hand2",
            font=("Segoe UI", 8, "underline"),
        )
        token_link.pack(side="left")
        token_link.bind(
            "<Button-1>",
            lambda _event: webbrowser.open(
                "https://id.atlassian.com/manage-profile/security/api-tokens"
            ),
        )

        self._add_field(
            form,
            "Snipe-IT API token (optional)",
            "snipeit_token",
            secret=True,
            hint="Needed only to show assigned assets",
        )
        self._show_tokens = tk.BooleanVar(value=False)
        tk.Checkbutton(
            form,
            text="Show tokens",
            variable=self._show_tokens,
            command=self._toggle_tokens,
            bg=BG,
            fg=GRAY,
            activebackground=BG,
            selectcolor=WHITE,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=22, pady=(0, 4))

        self._add_field(
            form,
            "Notify this many days before a start date",
            "remind_days_before",
            hint="The default is 3 days",
        )

        self._advanced_btn = tk.Button(
            form,
            text="Advanced settings ▾" if self._advanced_visible else "Advanced settings ▸",
            command=self._toggle_advanced,
            relief="flat",
            bd=0,
            bg=BG,
            fg=ACCENT,
            activebackground=BG,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=0,
        )
        self._advanced_btn.pack(fill="x", padx=26, pady=(15, 5))

        self._advanced = tk.Frame(form, bg=BG)
        self._add_field(
            self._advanced,
            "Jira URL",
            "jira_url",
            hint="Managed default — normally do not change",
        )
        self._add_field(
            self._advanced,
            "Assigned onboarding tickets JQL",
            "jql",
            hint="Keeps tickets filtered to assignee = currentUser()",
        )
        self._add_field(
            self._advanced,
            "Start date Jira field",
            "date_field",
            hint="Managed default — normally do not change",
        )
        self._add_field(
            self._advanced,
            "Assigned mover tickets JQL",
            "mover_jql",
            hint="Employee moving tickets assigned to the current user",
        )
        self._add_field(
            self._advanced,
            "Snipe-IT URL",
            "snipeit_url",
            hint="Managed default — normally do not change",
        )
        self._add_field(
            self._advanced,
            "Access-card OneDrive queue folder",
            "access_card_sync_folder",
            hint="Uses your existing OneDrive sync. No tenant ID, client ID or app sign-in needed.",
        )
        tk.Button(self._advanced, text="Choose queue folder…", command=self._choose_card_queue_folder,
                  relief="flat", bg="#DEEBFF", fg=ACCENT, padx=12, pady=6).pack(anchor="w", padx=26, pady=(4, 8))
        tk.Button(self._advanced, text="Other access-card connection options", command=self._toggle_card_api_settings,
                  relief="flat", bg=BG, fg=GRAY).pack(anchor="w", padx=26, pady=4)
        self._card_api_settings = tk.Frame(self._advanced, bg=BG)
        if not self._vars["access_card_sync_folder"].get() and any(
                self._vars[key].get() for key in ("access_card_flow_url", "access_card_site_url", "access_card_tenant_id")):
            self._card_api_settings.pack(fill="x")
        self._add_field(
            self._card_api_settings,
            "Access-card SharePoint site address",
            "access_card_site_url",
            hint="For the Standard connector flow; enter the site address before /Lists/",
        )
        self._add_field(
            self._card_api_settings,
            "Access-card request list ID",
            "access_card_list_id",
            hint="The GUID from the request list's List settings URL",
        )
        self._add_field(
            self._card_api_settings,
            "Access-card HTTP flow URL (Premium only)",
            "access_card_flow_url",
            hint="Leave blank when using the SharePoint request list",
        )
        self._add_field(
            self._card_api_settings,
            "Microsoft tenant ID",
            "access_card_tenant_id",
            hint="Required for either access-card connection",
        )
        self._add_field(
            self._card_api_settings,
            "Access-card desktop-app client ID",
            "access_card_client_id",
            hint="Public Entra client; never enter a client secret",
        )
        self._access_card_signin_btn = tk.Button(
            self._card_api_settings,
            text="Sign in to access-card service",
            command=self._sign_in_access_cards,
            relief="flat",
            bd=0,
            bg="#DEEBFF",
            fg=ACCENT,
            activebackground="#CCE0FF",
            font=("Segoe UI", 9),
            padx=12,
            pady=6,
        )
        self._access_card_signin_btn.pack(anchor="w", padx=26, pady=(8, 4))
        self._add_field(
            self._advanced,
            "Check for Jira updates every N minutes",
            "check_interval_minutes",
            hint="The default is 30 minutes",
        )
        if self._advanced_visible:
            self._advanced.pack(fill="x")

    def _add_field(self, parent, label: str, key: str, secret: bool = False, hint: str = ""):
        tk.Label(
            parent,
            text=label,
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x", padx=26, pady=(8, 2))
        entry = tk.Entry(
            parent,
            textvariable=self._vars[key],
            show="•" if secret else "",
            relief="solid",
            bd=1,
            font=("Segoe UI", 10),
            highlightthickness=1,
            highlightbackground=INPUT_BORDER,
            highlightcolor=ACCENT,
        )
        entry.pack(fill="x", padx=26, ipady=5)
        if secret:
            if not hasattr(self, "_token_entries"):
                self._token_entries = []
            self._token_entries.append(entry)
        if hint:
            tk.Label(
                parent,
                text=hint,
                bg=BG,
                fg=GRAY,
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(fill="x", padx=26, pady=(2, 0))

    def _toggle_tokens(self):
        show = "" if self._show_tokens.get() else "•"
        for entry in getattr(self, "_token_entries", []):
            entry.configure(show=show)

    def _toggle_advanced(self):
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self._advanced.pack(fill="x")
            self._advanced_btn.configure(text="Advanced settings ▾")
        else:
            self._advanced.pack_forget()
            self._advanced_btn.configure(text="Advanced settings ▸")

    def _choose_card_queue_folder(self):
        initial = self._vars["access_card_sync_folder"].get() or os.environ.get("OneDriveCommercial", "")
        folder = filedialog.askdirectory(parent=self, title="Choose the synced AccessCardQueue folder",
                                        initialdir=initial or None, mustexist=True)
        if folder:
            self._vars["access_card_sync_folder"].set(folder)
            self._card_api_settings.pack_forget()

    def _toggle_card_api_settings(self):
        if self._card_api_settings.winfo_manager():
            self._card_api_settings.pack_forget()
        else:
            self._card_api_settings.pack(fill="x")

    def _collect(self) -> dict:
        cfg = {
            "jira_url":               self._vars["jira_url"].get().strip().rstrip("/"),
            "email":                  self._vars["email"].get().strip(),
            "api_token":              self._vars["api_token"].get().strip(),
            "jql":                    self._vars["jql"].get().strip(),
            "date_field":             self._vars["date_field"].get().strip() or "customfield_10980",
            "mover_jql":              self._vars["mover_jql"].get().strip() or DEFAULT_MOVER_JQL,
            "snipeit_url":            self._vars["snipeit_url"].get().strip().rstrip("/"),
            "snipeit_token":          self._vars["snipeit_token"].get().strip(),
            "access_card_flow_url":   self._vars["access_card_flow_url"].get().strip(),
            "access_card_site_url":   self._vars["access_card_site_url"].get().strip().rstrip("/"),
            "access_card_list_id":    self._vars["access_card_list_id"].get().strip(),
            "access_card_sync_folder": self._vars["access_card_sync_folder"].get().strip(),
            "access_card_tenant_id":  self._vars["access_card_tenant_id"].get().strip(),
            "access_card_client_id":  self._vars["access_card_client_id"].get().strip(),
            "remind_days_before":     int(self._vars["remind_days_before"].get() or 3),
            "check_interval_minutes": int(self._vars["check_interval_minutes"].get() or 30),
        }
        if not cfg["jira_url"].startswith("https://"):
            raise ValueError("Jira URL must start with https://")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cfg["email"]):
            raise ValueError("Enter your Atlassian account email address.")
        if not cfg["api_token"]:
            raise ValueError("Enter your Jira API token.")
        if not cfg["jql"]:
            raise ValueError("The assigned onboarding tickets JQL cannot be empty.")
        if not 0 <= cfg["remind_days_before"] <= 30:
            raise ValueError("Reminder days must be between 0 and 30.")
        if not 5 <= cfg["check_interval_minutes"] <= 1440:
            raise ValueError("The Jira check interval must be between 5 and 1440 minutes.")
        if cfg["access_card_sync_folder"]:
            from onedrive_card_queue import validate_sync_folder
            cfg["access_card_sync_folder"] = str(validate_sync_folder(cfg["access_card_sync_folder"]))
            for key in ("access_card_flow_url", "access_card_site_url", "access_card_list_id",
                        "access_card_tenant_id", "access_card_client_id"):
                cfg[key] = ""
            return cfg
        use_list = bool(cfg["access_card_site_url"] or cfg["access_card_list_id"])
        if use_list and cfg["access_card_flow_url"]:
            raise ValueError("Leave the Premium HTTP flow URL blank when using a SharePoint list.")
        access_card_values = ((cfg["access_card_site_url"], cfg["access_card_list_id"]) if use_list
                              else (cfg["access_card_flow_url"],)) + (
                                  cfg["access_card_tenant_id"], cfg["access_card_client_id"])
        if any(access_card_values) and not all(access_card_values):
            raise ValueError(
                "Enter the access-card location, Microsoft tenant ID, and client ID together."
            )
        if use_list:
            from sharepoint_card_queue import validate_list_location
            cfg["access_card_site_url"], cfg["access_card_list_id"] = validate_list_location(
                cfg["access_card_site_url"], cfg["access_card_list_id"])
        if cfg["access_card_flow_url"] and not cfg["access_card_flow_url"].startswith("https://"):
            raise ValueError("The access-card Power Automate URL must start with https://")
        if all(access_card_values):
            guid_pattern = re.compile(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                re.IGNORECASE,
            )
            if not guid_pattern.fullmatch(cfg["access_card_tenant_id"]):
                raise ValueError("The Microsoft tenant ID must be a GUID.")
            if not guid_pattern.fullmatch(cfg["access_card_client_id"]):
                raise ValueError("The access-card client ID must be a GUID.")
        return cfg

    def _sign_in_access_cards(self):
        try:
            cfg = self._collect()
            if cfg["access_card_sync_folder"]:
                self._msg.config(text="The folder connection uses OneDrive's existing sign-in. Save settings to use it.", fg=GREEN)
                return
            if not (cfg["access_card_flow_url"] or cfg["access_card_site_url"]):
                raise ValueError("Enter the access-card connection settings first.")
            from power_automate_auth import PowerAutomateAuthenticator
            authenticator = PowerAutomateAuthenticator(
                cfg["access_card_tenant_id"], cfg["access_card_client_id"],
                service="sharepoint" if cfg["access_card_site_url"] else "flow",
            )
        except Exception as e:
            self._msg.config(text=f"Error: {e}", fg=RED)
            return

        self._msg.config(text="Opening Microsoft sign-in…", fg=GRAY)
        self._access_card_signin_btn.configure(state="disabled")

        def _connect():
            try:
                authenticator.get_access_token(interactive=True)
                self.after(
                    0,
                    lambda: self._finish_access_card_signin(
                        "Microsoft sign-in saved securely. Save settings to enable reservations.",
                        GREEN,
                    ),
                )
            except Exception as e:
                error = str(e)
                self.after(
                    0,
                    lambda message=error: self._finish_access_card_signin(
                        f"Microsoft sign-in failed: {message}", RED
                    ),
                )

        threading.Thread(target=_connect, daemon=True).start()

    def _finish_access_card_signin(self, message: str, color: str):
        self._msg.config(text=message, fg=color)
        self._access_card_signin_btn.configure(state="normal")

    def _test(self):
        from jira_client import JiraClient
        try:
            cfg = self._collect()
        except Exception as e:
            self._msg.config(text=f"Error: {e}", fg=RED)
            return
        self._msg.config(text="Connecting to Jira…", fg=GRAY)
        self._test_btn.configure(state="disabled")

        def _connect():
            try:
                name = JiraClient(
                    cfg["jira_url"], cfg["email"], cfg["api_token"]
                ).test_connection()
                self.after(
                    0,
                    lambda: self._finish_test(f"Connected successfully as {name}", GREEN),
                )
            except Exception as e:
                error = str(e)
                self.after(
                    0,
                    lambda message=error: self._finish_test(
                        f"Connection failed: {message}", RED
                    ),
                )

        threading.Thread(target=_connect, daemon=True).start()

    def _finish_test(self, message: str, color: str):
        self._msg.config(text=message, fg=color)
        self._test_btn.configure(state="normal")

    def _save(self):
        try:
            cfg = self._collect()
        except (ValueError, AccessCardConfigurationError) as e:
            messagebox.showerror("Invalid input", str(e), parent=self)
            return
        self.result = cfg
        self.destroy()


class AskReporterDialog(tk.Toplevel):
    _TEMPLATE = (
        "Hello, @manager,\n"
        "Could you please let us know the name of an existing employee with similar "
        "access rights that we can use as a template for {name}'s account setup?\n"
        "Thank you."
    )
    _TEMPLATE_DISABLED_BUDDY = (
        "Hello, @manager,\n"
        "{buddy_name} is no longer working, so we cannot use them as a buddy, because "
        "the account is disabled. Could you please let us know the name of an existing "
        "employee with similar access rights that we can use as a template for {name}'s "
        "account setup?\n"
        "Thank you."
    )

    def __init__(self, parent, ticket: dict, jira_client, on_sent,
                 disabled_buddy: str = ""):
        super().__init__(parent)
        self._ticket         = ticket
        self._jira           = jira_client
        self._on_sent        = on_sent
        self._disabled_buddy = disabled_buddy
        self._mention_map: dict[str, str] = {}
        self.title(f"Ask reporter — {ticket.get('key', '')}")
        self.geometry("560x400")
        self.resizable(False, True)
        self.configure(bg=BG)
        self.grab_set()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        threading.Thread(target=self._resolve_mentions, daemon=True).start()

    def _resolve_mentions(self):
        manager_mention  = None
        reporter_mention = None

        manager = self._ticket.get("manager", "").strip()
        if manager:
            try:
                manager_mention = self._jira.search_user(manager)
            except Exception:
                pass

        if self._ticket.get("reporter_id"):
            reporter_mention = {
                "id":   self._ticket["reporter_id"],
                "text": f"@{self._ticket.get('reporter_name', 'Reporter')}",
            }

        # Build ordered greeting list; skip reporter if same account as manager
        greeting: list[dict] = []
        if manager_mention:
            greeting.append(manager_mention)
        if reporter_mention:
            if not manager_mention or reporter_mention["text"] != manager_mention["text"]:
                greeting.append(reporter_mention)

        self._mention_map = {m["text"]: m["id"] for m in greeting}

        def _update():
            current = self._text.get("1.0", "end-1c")
            if greeting:
                substitution = ", ".join(m["text"] for m in greeting)
                new_text = current.replace("@manager", substitution, 1)
            else:
                new_text = current
            if new_text != current:
                self._text.delete("1.0", "end")
                self._text.insert("1.0", new_text)

            if self._mention_map:
                label = "Will tag: " + "   ".join(self._mention_map)
                color = ACCENT
            else:
                label = "No Jira accounts found to tag"
                color = GRAY
            self._tag_label.config(text=label, fg=color)

        try:
            self.after(0, _update)
        except Exception:
            pass

    def _build(self):
        tk.Label(self, text="  Post comment to Jira", bg=ACCENT, fg=WHITE,
                 font=("Segoe UI", 11, "bold")).pack(fill="x", ipady=8)
        tk.Label(self,
                 text=f"  {self._ticket['key']}  —  {self._ticket.get('name', '')}",
                 bg=BG, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(10, 2))

        self._tag_label = tk.Label(self, text="Resolving mentions…",
                                   bg=BG, fg=GRAY, font=("Segoe UI", 8, "italic"))
        self._tag_label.pack(anchor="w", padx=20, pady=(0, 6))

        self._text = tk.Text(self, height=10, relief="solid", bd=1,
                             font=("Segoe UI", 10), bg=WHITE, fg=TEXT,
                             wrap="word", padx=8, pady=6)
        if self._disabled_buddy:
            body = self._TEMPLATE_DISABLED_BUDDY.format(
                buddy_name=self._disabled_buddy,
                name=self._ticket.get("name", "the new joiner"),
            )
        else:
            body = self._TEMPLATE.format(name=self._ticket.get("name", "the new joiner"))
        self._text.insert("1.0", body)
        self._text.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        btm = tk.Frame(self, bg=BG)
        btm.pack(fill="x", padx=20, pady=(0, 14))
        self._msg = tk.Label(btm, text="", bg=BG, fg=GRAY, font=("Segoe UI", 9))
        self._msg.pack(side="bottom", anchor="w", pady=(4, 0))
        tk.Button(btm, text="Cancel", command=self.destroy,
                  relief="flat", bd=0, bg="#DDE3EA", fg=TEXT,
                  font=("Segoe UI", 9), padx=12, pady=4).pack(side="right")
        tk.Button(btm, text="Post comment", command=self._send,
                  relief="flat", bd=0, bg=ACCENT, fg=WHITE,
                  font=("Segoe UI", 9, "bold"), padx=12, pady=4).pack(
                      side="right", padx=(0, 8))

    def _send(self):
        text = self._text.get("1.0", "end-1c").strip()
        if not text:
            return
        self._msg.config(text="Posting…", fg=GRAY)
        self.update()
        try:
            self._jira.post_comment(self._ticket["key"], text, self._mention_map or None)
            self._msg.config(text="Comment posted!", fg=GREEN)
            self.after(800, lambda: (self._on_sent(), self.destroy()))
        except Exception as e:
            self._msg.config(text=f"Error: {e}", fg=RED)
