"""Mover list and dedicated Active Directory position-change workflow."""

import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk
import webbrowser

from ad_automation import (
    find_user_accounts_by_name,
    is_girteka_dedicated_company,
    run_ps,
)
from mover_automation import (
    build_mover_plan,
    build_mover_script,
    choose_enabled_account,
    format_mover_plan,
    get_mover_account_info,
)

BG = "#F7F8FA"
WHITE = "#FFFFFF"
ACCENT = "#0C66E4"
GREEN = "#1F845A"
RED = "#C9372C"
ORANGE = "#B76E00"
GRAY = "#5E6C84"
TEXT = "#172B4D"
BORDER = "#DFE3EA"
SOFT_BLUE = "#E9F2FF"
SOFT_GOLD = "#FFF4E5"
SOFT_RED = "#FFECEB"
SUBTLE = "#EEF2F7"


class MoversPanel(tk.Frame):
    def __init__(self, parent, movers: list[dict], storage, jira_client, on_refresh):
        super().__init__(parent, bg=BG)
        self.movers = movers
        self.storage = storage
        self.jira = jira_client
        self.on_refresh = on_refresh
        self._selected: int | None = None
        self._selected_id = ""
        self._notes_box: tk.Text | None = None
        self._comments_cache: dict[str, str] = {}
        self._comments_inflight: set[str] = set()
        self._build()
        self._refresh_list()

    def _button(self, parent, text, command, bg=ACCENT, fg=WHITE):
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg, relief="flat",
            bd=0, font=("Segoe UI", 9), padx=10, pady=4,
            activebackground=ACCENT, activeforeground=WHITE, cursor="hand2",
            width=20,
        )

    def _build(self):
        bottom = tk.Frame(self, bg="#EEF2F7", height=46)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        self._status = tk.StringVar(value="")
        tk.Label(bottom, textvariable=self._status, bg="#EEF2F7", fg=GRAY,
                 font=("Segoe UI", 9)).pack(side="left", padx=12)
        self._button(bottom, "Open in Jira", self._open_jira, WHITE, ACCENT).pack(
            side="right", padx=10, pady=8)
        self._prepare_btn = self._button(
            bottom, "Prepare AD move", self._open_ad_move, "#00875A", WHITE
        )
        self._prepare_btn.pack(side="right", padx=(0, 4), pady=8)
        self._button(bottom, "Refresh", self.on_refresh, SOFT_BLUE, ACCENT).pack(
            side="right", padx=(0, 4), pady=8)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=WHITE, width=420)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Upcoming movers", bg=WHITE, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(14, 6))
        self._listbox = tk.Listbox(
            left, selectmode="single", relief="flat", bd=0,
            bg=WHITE, fg=TEXT, font=("Segoe UI", 10),
            selectbackground=SOFT_BLUE, selectforeground=ACCENT,
            activestyle="none", highlightthickness=0, exportselection=False,
        )
        self._listbox.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self._hint = tk.Label(right, text="Select a mover to view their details",
                              bg=BG, fg=GRAY, font=("Segoe UI", 11))
        self._hint.place(relx=.5, rely=.5, anchor="center")
        self._detail_sb = tk.Scrollbar(right, orient="vertical")
        self._canvas = tk.Canvas(right, bg=BG, highlightthickness=0,
                                 yscrollcommand=self._detail_sb.set)
        self._detail_sb.configure(command=self._canvas.yview)
        self._detail = tk.Frame(self._canvas, bg=BG)
        self._detail_wid = self._canvas.create_window(
            (0, 0), window=self._detail, anchor="nw")
        self._detail.bind("<Configure>", lambda _e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(
            self._detail_wid, width=e.width))

    def _refresh_list(self):
        self._listbox.delete(0, "end")
        today = date.today()
        for index, mover in enumerate(self.movers):
            effective = mover.get("effective_date")
            if effective:
                delta = (effective - today).days
                when = "TODAY" if delta == 0 else (
                    "TOMORROW" if delta == 1 else
                    (f"in {delta}d" if delta > 0 else f"effective {-delta}d ago")
                )
            else:
                delta, when = 99, "no date"
            color = RED if delta in (0, 1) else (
                GREEN if 1 < delta <= 7 else (GRAY if delta < 0 else TEXT)
            )
            ad_tag = "  AD done" if self.storage.ad_setup_done(mover["id"]) else ""
            label = f"  {mover['key']}  {mover['name']}  {when}{ad_tag}"
            self._listbox.insert("end", label)
            self._listbox.itemconfig(index, fg=color)
        if not self.movers:
            self._listbox.insert("end", "  No tickets found")
            self._status.set("No employee moving tickets found.")
        else:
            self._status.set(f"Loaded {len(self.movers)} mover ticket(s).")

    def update_movers(self, movers: list[dict]):
        self._save_notes()
        self.movers = movers
        selected_id = self._selected_id
        self._refresh_list()
        restored = False
        for index, mover in enumerate(movers):
            if mover["id"] == selected_id:
                self._listbox.selection_set(index)
                self._listbox.activate(index)
                self._listbox.see(index)
                self._selected = index
                self._show_detail(mover)
                restored = True
                break
        if selected_id and not restored:
            self._selected = None
            self._selected_id = ""
            for widget in self._detail.winfo_children():
                widget.destroy()
            self._canvas.place_forget()
            self._detail_sb.place_forget()
            self._hint.place(relx=.5, rely=.5, anchor="center")
            self._prepare_btn.configure(state="disabled")

    def _on_select(self, _event=None):
        selection = self._listbox.curselection()
        if not selection or selection[0] >= len(self.movers):
            return
        self._save_notes()
        self._selected = selection[0]
        mover = self.movers[self._selected]
        self._selected_id = mover["id"]
        self._show_detail(mover)

    def _show_detail(self, mover: dict):
        self._hint.place_forget()
        for widget in self._detail.winfo_children():
            widget.destroy()
        self._notes_box = None
        self._detail_sb.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne", width=16)
        self._canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0, width=-16)
        self._canvas.yview_moveto(0.0)

        name_row = tk.Frame(self._detail, bg=BG)
        name_row.pack(anchor="w", padx=24, pady=(20, 2))
        tk.Label(name_row, text=mover["name"], bg=BG, fg=TEXT,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(name_row, text="Mover", bg="#E3FCEF", fg=GREEN,
                 font=("Segoe UI", 9, "bold"), padx=8, pady=3,
                 relief="flat").pack(side="left", padx=(12, 0))

        effective = mover.get("effective_date")
        if effective:
            delta = (effective - date.today()).days
            if delta < 0:
                date_text = f"{effective}  (effective {-delta} day(s) ago)"
                date_color = GRAY
            elif delta == 0:
                date_text = f"{effective}  - EFFECTIVE TODAY"
                date_color = RED
            elif delta <= 3:
                date_text = f"{effective}  - in {delta} day(s)"
                date_color = "#FF991F"
            else:
                date_text = f"{effective}  - in {delta} day(s)"
                date_color = TEXT
        else:
            date_text = "Effective date: not set"
            date_color = GRAY
        tk.Label(self._detail, text=date_text, bg=BG, fg=date_color,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=24, pady=(0, 2))

        meta_parts = []
        if mover.get("new_position"): meta_parts.append(mover["new_position"])
        if mover.get("office"): meta_parts.append(mover["office"])
        if mover.get("manager"): meta_parts.append(f"Mgr: {mover['manager']}")
        if mover.get("status"): meta_parts.append(mover["status"])
        if meta_parts:
            tk.Label(self._detail, text="  |  ".join(meta_parts), bg=BG, fg=GRAY,
                     font=("Segoe UI", 9), wraplength=500, justify="left").pack(
                         anchor="w", padx=24, pady=(0, 2))
        tk.Label(self._detail, text=mover["key"], bg=BG, fg=GRAY,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=24, pady=(0, 10))

        setup = self.storage.get_ad_setup(mover["id"])
        if setup.get("completed_at"):
            ready = tk.Frame(self._detail, bg="#E7F4EC", highlightbackground="#B8E0C7",
                             highlightthickness=1)
            ready.pack(fill="x", padx=24, pady=(2, 12))
            tk.Label(ready, text="AD setup completed", bg="#E7F4EC", fg=GREEN,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
            details = [f"Completed: {setup['completed_at']}"]
            if setup.get("account"): details.append(f"Account: {setup['account']}")
            if setup.get("groups_count") not in (None, ""):
                details.append(f"Groups added: {setup['groups_count']}")
            for detail in details:
                tk.Label(ready, text=detail, bg="#E7F4EC", fg=TEXT,
                         font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=1)
            if setup.get("target_ou"):
                tk.Label(ready, text=f"Target folder: {setup['target_ou']}",
                         bg="#E7F4EC", fg=GRAY, font=("Segoe UI", 8),
                         wraplength=620, justify="left").pack(
                             anchor="w", padx=10, pady=(2, 8))

        buddy = mover.get("ad_buddy") or {}
        card_bg = SOFT_GOLD
        card = tk.Frame(self._detail, bg=card_bg, highlightbackground="#FF991F",
                        highlightthickness=1)
        card.pack(fill="x", padx=24, pady=(0, 10))
        if buddy:
            source = {
                "buddy_and_axapta_fields": "Buddy and Axapta rights match",
                "buddy_field": "Buddy field is the AD source",
                "axapta_fallback": "Axapta rights is used as the AD buddy",
            }.get(buddy.get("source"), "Jira buddy")
            tk.Label(card, text="Suggested buddy", bg=card_bg, fg=ORANGE,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
            tk.Label(card, text=buddy.get("name", ""), bg=card_bg,
                     fg=TEXT, font=("Segoe UI", 11, "bold")).pack(
                         anchor="w", padx=10, pady=(2, 0))
            tk.Label(card, text=source, bg=card_bg, fg=GRAY,
                     font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(2, 8))
        else:
            tk.Label(card, text="No Buddy or Axapta rights user is available. AD preparation is blocked.",
                     bg=card_bg, fg=RED, font=("Segoe UI", 9, "bold"),
                     wraplength=500, justify="left").pack(anchor="w", padx=10, pady=8)

        if mover.get("axapta_notice"):
            warning = tk.Frame(self._detail, bg="#FFF4E5", highlightbackground="#FFAB00",
                               highlightthickness=1)
            warning.pack(fill="x", padx=24, pady=(0, 10))
            tk.Label(warning, text="Axapta follow-up required", bg="#FFF4E5",
                     fg=ORANGE, font=("Segoe UI", 9, "bold")).pack(
                         anchor="w", padx=10, pady=(8, 0))
            tk.Label(warning, text=mover["axapta_notice"], bg=SOFT_GOLD, fg=TEXT,
                     font=("Segoe UI", 9), wraplength=500, justify="left").pack(
                         anchor="w", padx=10, pady=(2, 8))

        tk.Frame(self._detail, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(0, 10))
        tk.Label(self._detail, text="Position change", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(0, 6))
        changes = [
            ("Current title", mover.get("current_position") or "Not set"),
            ("New title", mover.get("new_position") or "Not set"),
            ("Company", mover.get("company_name") or "Not set"),
            ("Department", "Same as buddy"),
            ("Manager", mover.get("manager") or "Not set"),
            ("Office", mover.get("office") or "Derived from company"),
        ]
        for label, value in changes:
            row = tk.Frame(self._detail, bg=BG)
            row.pack(fill="x", padx=24, pady=3)
            tk.Label(row, text=f"{label}:", bg=BG, fg=GRAY,
                     font=("Segoe UI", 9, "bold"), width=18,
                     anchor="w").pack(side="left")
            tk.Label(row, text=value, bg=BG, fg=TEXT,
                     font=("Segoe UI", 10), wraplength=390,
                     justify="left", anchor="w").pack(side="left", fill="x")

        tk.Frame(self._detail, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(14, 8))
        tk.Label(self._detail, text="Notes", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(0, 4))
        self._notes_box = self._make_text_box(self._detail, height=5)
        self._notes_box.pack(fill="x", padx=24, pady=(0, 8))
        self._notes_box.insert("1.0", self.storage.get_notes(mover["id"]))
        self._notes_box.bind("<FocusOut>", lambda _e: self._save_notes())

        tk.Frame(self._detail, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(14, 8))
        tk.Label(self._detail, text="Jira comments", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(0, 4))
        comments_frame = tk.Frame(self._detail, bg=BG)
        comments_frame.pack(fill="x", padx=24, pady=(0, 16))
        comments_frame.columnconfigure(0, weight=1)
        comments = self._make_text_box(comments_frame, height=12, readonly=True,
                                       font=("Segoe UI", 9))
        comments_sb = tk.Scrollbar(comments_frame, orient="vertical", command=comments.yview)
        comments.configure(yscrollcommand=comments_sb.set)
        comments.grid(row=0, column=0, sticky="ew")
        comments_sb.grid(row=0, column=1, sticky="ns")
        cached = self._comments_cache.get(mover["id"])
        self._set_text(comments, cached or "Loading...")
        if cached is None:
            self._load_comments(mover, comments)
        self._bind_text_widget_scroll(self._notes_box)
        self._bind_text_widget_scroll(comments)
        self._bind_detail_scroll(self._detail)
        self._prepare_btn.configure(state="normal" if buddy else "disabled")
        self._status.set(
            "AD move completed." if self.storage.ad_setup_done(mover["id"])
            else "AD move pending."
        )

    @staticmethod
    def _make_text_box(parent, height: int, readonly: bool = False,
                       font=("Segoe UI", 10)) -> tk.Text:
        box = tk.Text(
            parent, height=height, relief="flat", bd=0, font=font,
            bg=WHITE, fg=TEXT, wrap="word", padx=8, pady=6,
            undo=not readonly, insertbackground=TEXT,
            highlightthickness=1, highlightbackground="#C7D1DB",
            highlightcolor=ACCENT,
        )
        if readonly:
            box.configure(state="disabled")
        return box

    def _bind_detail_scroll(self, widget):
        widget.bind("<MouseWheel>", lambda event: self._canvas.yview_scroll(
            int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units"))
        for child in widget.winfo_children():
            if not isinstance(child, tk.Text):
                self._bind_detail_scroll(child)

    def _bind_text_widget_scroll(self, widget: tk.Text):
        def _on_wheel(event):
            units = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
            first, last = widget.yview()
            if (units < 0 and first <= 0.0) or (units > 0 and last >= 1.0):
                self._canvas.yview_scroll(units, "units")
            else:
                widget.yview_scroll(units, "units")
            return "break"
        widget.bind("<MouseWheel>", _on_wheel)

    def _load_comments(self, mover: dict, widget: tk.Text):
        ticket_id = mover["id"]
        if ticket_id in self._comments_inflight:
            return
        self._comments_inflight.add(ticket_id)

        def _work():
            try:
                values = self.jira.get_comments(mover["key"])
                text = "\n\n".join(
                    f"{c['author']}  •  {c['created']}\n{c['body']}" for c in values
                ) or "No comments yet."
            except Exception as exc:
                text = f"Could not load comments: {exc}"

            def _finish():
                self._comments_inflight.discard(ticket_id)
                self._comments_cache[ticket_id] = text
                if self._selected_id != ticket_id:
                    return
                try:
                    if widget.winfo_exists():
                        self._set_text(widget, text)
                except tk.TclError:
                    pass
            self.after(0, _finish)

        threading.Thread(target=_work, daemon=True).start()

    def _save_notes(self):
        if self._notes_box and self._selected_id:
            try:
                self.storage.set_notes(self._selected_id, self._notes_box.get("1.0", "end-1c"))
            except tk.TclError:
                pass

    def _open_jira(self):
        if self._selected is not None and self._selected < len(self.movers):
            webbrowser.open(self.movers[self._selected]["url"])

    def _open_ad_move(self):
        if self._selected is None or self._selected >= len(self.movers):
            return
        mover = self.movers[self._selected]
        MoverADWindow(
            self, mover, self.storage,
            on_completed=lambda: self._on_move_completed(mover),
        )

    def _on_move_completed(self, mover: dict):
        self._refresh_list()
        if self._selected is not None and self._selected < len(self.movers):
            self._listbox.selection_set(self._selected)
            self._listbox.activate(self._selected)
            self._listbox.see(self._selected)
        self._show_detail(mover)

    @staticmethod
    def _set_text(widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


class MoverADWindow(tk.Toplevel):
    def __init__(self, parent, ticket: dict, storage, on_completed=None):
        super().__init__(parent)
        self.ticket = ticket
        self.storage = storage
        self.on_completed = on_completed
        self.plan: dict | None = None
        self.script = ""
        self._prepare_generation = 0
        self._account_overrides: dict[str, str] = {}
        self._account_choice_controls: dict[str, tuple[tk.StringVar, dict[str, str]]] = {}
        self._account_choice_widgets: list[tuple[tk.Widget, str]] = []
        self.title(f"Prepare AD move - {ticket.get('name', '')}")
        self.geometry("980x720")
        self.minsize(840, 620)
        self.configure(bg=BG)
        self.transient(parent.winfo_toplevel())
        self._build()
        self.after(100, self._prepare)

    def _build(self):
        header = tk.Frame(self, bg=ACCENT, height=76)
        header.pack(fill="x")
        header.pack_propagate(False)
        heading = tk.Frame(header, bg=ACCENT)
        heading.pack(side="left", fill="y", padx=22, pady=12)
        tk.Label(heading, text="Prepare Active Directory move",
                 bg=ACCENT, fg=WHITE, font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(
            heading,
            text=f"{self.ticket.get('name', '')}   •   {self.ticket.get('key', '')}",
            bg=ACCENT, fg="#DDEBFF", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(3, 0))
        status_box = tk.Frame(header, bg="#0755A9", padx=12, pady=7)
        status_box.pack(side="right", padx=20)
        self._status = tk.Label(status_box, text="Preparing", bg="#0755A9", fg=WHITE,
                                font=("Segoe UI", 8, "bold"))
        self._status.pack()

        actions = tk.Frame(self, bg=SUBTLE, height=58, highlightbackground=BORDER,
                           highlightthickness=1)
        actions.pack(side="bottom", fill="x")
        actions.pack_propagate(False)
        self._progress = ttk.Progressbar(actions, mode="indeterminate", length=170)
        self._progress.pack(side="left", padx=(18, 8), pady=18)
        self._activity = tk.Label(actions, text="Reading Active Directory…",
                                  bg=SUBTLE, fg=GRAY, font=("Segoe UI", 8))
        self._activity.pack(side="left")
        self._refresh_btn = tk.Button(
            actions, text="Refresh plan", command=self._prepare, bg=SOFT_BLUE,
            fg=ACCENT, relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
            padx=14, pady=7, cursor="hand2",
        )
        self._refresh_btn.pack(side="right", padx=(6, 18), pady=11)
        self._apply_btn = tk.Button(actions, text="Apply verified AD move", command=self._apply,
                                    bg=GREEN, fg=WHITE, relief="flat", bd=0,
                                    font=("Segoe UI", 9, "bold"), padx=16, pady=7,
                                    state="disabled", cursor="hand2")
        self._apply_btn.pack(side="right", pady=11)

        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=18, pady=16)
        overview = tk.Frame(content, bg=WHITE, padx=16, pady=12,
                            highlightbackground=BORDER, highlightthickness=1)
        overview.pack(fill="x", pady=(0, 10))
        tk.Label(overview, text="Review before applying", bg=WHITE, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        effective = self.ticket.get("effective_date")
        if is_girteka_dedicated_company(self.ticket.get("company_name", "")):
            account_scope = (
                "No password or UPN changes; Girteka Dedicated email is enforced"
            )
        else:
            account_scope = "No password, email, UPN, or proxy-address changes"
        tk.Label(
            overview,
            text=f"Effective {effective or 'date not set'}   •   {account_scope}",
            bg=WHITE, fg=GRAY, font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(3, 0))

        notice = self.ticket.get("axapta_notice")
        if notice:
            tk.Label(content, text="AXAPTA FOLLOW-UP  •  " + notice,
                     bg=SOFT_GOLD, fg=ORANGE, font=("Segoe UI", 8, "bold"),
                     padx=12, pady=8, wraplength=880, justify="left",
                     anchor="w").pack(fill="x", pady=(0, 10))
        self._account_picker = tk.Frame(
            content, bg=SOFT_GOLD, padx=12, pady=10,
            highlightbackground="#F0C36D", highlightthickness=1,
        )
        self._ack_var = tk.BooleanVar(value=False)
        self._ack = tk.Checkbutton(
            content, text="I reviewed the manager mismatch; use the Jira Manager",
            variable=self._ack_var, command=self._prepare, bg=SOFT_RED, fg=RED,
            activebackground=SOFT_RED, selectcolor=WHITE,
            font=("Segoe UI", 9, "bold"), padx=10, pady=7, anchor="w",
        )
        style = ttk.Style(self)
        style.configure("Mover.TNotebook", background=BG, borderwidth=0)
        style.configure("Mover.TNotebook.Tab", font=("Segoe UI", 9, "bold"),
                        padding=(15, 8))
        self._review_tabs = ttk.Notebook(content, style="Mover.TNotebook")
        plan_tab = tk.Frame(self._review_tabs, bg=BG)
        script_tab = tk.Frame(self._review_tabs, bg=BG)
        output_tab = tk.Frame(self._review_tabs, bg=BG)
        self._review_tabs.add(plan_tab, text="  Planned changes  ")
        self._review_tabs.add(script_tab, text="  PowerShell preview  ")
        self._review_tabs.add(output_tab, text="  Execution output  ")
        self._review_tabs.pack(fill="both", expand=True)
        self._plan_box = self._text_view(plan_tab, wrap="word", font=("Consolas", 10))
        self._script_box = self._text_view(script_tab, wrap="none", font=("Consolas", 9))
        self._output = self._text_view(output_tab, wrap="word", font=("Consolas", 9))
        self._output_tab = output_tab
        self._set_text(self._plan_box, "Reading mover, buddy, and manager from AD...")
        self._set_text(self._script_box, "PowerShell will appear after all preflight checks pass.")
        self._set_text(self._output, "No changes have been made.")

    @staticmethod
    def _text_view(parent, *, wrap: str, font) -> tk.Text:
        frame = tk.Frame(parent, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        vertical = ttk.Scrollbar(frame, orient="vertical")
        horizontal = ttk.Scrollbar(frame, orient="horizontal")
        text = tk.Text(
            frame, wrap=wrap, font=font, relief="flat", bd=0, bg=WHITE, fg=TEXT,
            padx=14, pady=12, yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set, insertbackground=TEXT,
        )
        vertical.configure(command=text.yview)
        horizontal.configure(command=text.xview)
        vertical.pack(side="right", fill="y")
        if wrap == "none":
            horizontal.pack(side="bottom", fill="x")
        text.pack(side="left", fill="both", expand=True)
        return text

    def _set_busy(self, busy: bool, message: str = ""):
        if busy:
            self._progress.start(12)
            self._activity.configure(text=message)
            self._refresh_btn.configure(state="disabled")
        else:
            self._progress.stop()
            self._activity.configure(text=message)
            self._refresh_btn.configure(state="normal")
        for widget, enabled_state in self._account_choice_widgets:
            try:
                widget.configure(state="disabled" if busy else enabled_state)
            except tk.TclError:
                pass

    def _prepare(self):
        self._prepare_generation += 1
        generation = self._prepare_generation
        self.plan = None
        self.script = ""
        self._apply_btn.configure(state="disabled")
        self._status.configure(text="PREPARING", fg=WHITE)
        self._set_busy(True, "Reading mover, buddy, and manager…")
        self._set_text(self._plan_box, "Preparing a fresh Active Directory comparison…")
        self._set_text(self._script_box, "PowerShell will appear after all preflight checks pass.")
        acknowledge_manager_mismatch = self._ack_var.get()
        account_overrides = dict(self._account_overrides)

        def _work():
            try:
                employee_name = ((self.ticket.get("employee") or {}).get("name")
                                 or self.ticket.get("name", ""))
                buddy_name = (self.ticket.get("ad_buddy") or {}).get("name", "")
                manager_name = self.ticket.get("manager", "")
                if not buddy_name:
                    raise ValueError("No AD buddy is available from Jira.")
                lookups = (
                    ("employee", "Employee", employee_name),
                    ("buddy", "Buddy", buddy_name),
                    ("manager", "Manager", manager_name),
                )
                resolved: dict[str, dict] = {}
                ambiguous: dict[str, dict] = {}
                for role, role_label, name in lookups:
                    accounts = find_user_accounts_by_name(name)
                    enabled = [
                        account for account in accounts
                        if account.get("enabled") and not account.get("disabled")
                    ]
                    selected_username = account_overrides.get(role, "")
                    selected_exists = any(
                        (account.get("username") or "").casefold()
                        == selected_username.casefold()
                        for account in enabled
                    )
                    if len(enabled) > 1 and not selected_exists:
                        ambiguous[role] = {
                            "label": f"{role_label}: {name}",
                            "accounts": enabled,
                        }
                        continue
                    resolved[role] = choose_enabled_account(
                        accounts,
                        f"{role} {name}",
                        selected_username if selected_exists else "",
                    )
                if ambiguous:
                    self.after(
                        0,
                        lambda ambiguous=ambiguous: self._select_accounts(
                            generation, ambiguous
                        ),
                    )
                    return
                mover_account = resolved["employee"]
                buddy_account = resolved["buddy"]
                manager_account = resolved["manager"]
                mover = get_mover_account_info(mover_account["username"])
                buddy = get_mover_account_info(buddy_account["username"])
                manager = get_mover_account_info(manager_account["username"])
                plan = build_mover_plan(
                    self.ticket, mover, buddy, manager,
                    acknowledge_manager_mismatch=acknowledge_manager_mismatch,
                )
                script = build_mover_script(plan)
                self.after(0, lambda: self._prepared(generation, plan, script))
            except Exception as exc:
                message = str(exc)
                self.after(
                    0,
                    lambda message=message: self._prepare_failed(generation, message),
                )
        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _account_choice_label(account: dict) -> str:
        username = account.get("username", "")
        title = account.get("title") or "No title"
        ou = account.get("ou") or "OU not available"
        normalized_ou = ou.casefold()
        if "ou=3cx" in normalized_ou:
            account_type = "3CX / SYSTEM ACCOUNT"
        elif "ou=girteka_system_users" in normalized_ou:
            account_type = "SYSTEM ACCOUNT"
        else:
            account_type = "USER ACCOUNT"
        return f"{username}  •  {account_type}  •  {title}  •  {ou}"

    def _select_accounts(self, generation: int, ambiguous: dict[str, dict]):
        if generation != self._prepare_generation:
            return
        for widget in self._account_picker.winfo_children():
            widget.destroy()
        self._account_choice_controls = {}
        self._account_choice_widgets = []
        tk.Label(
            self._account_picker,
            text="Choose the Active Directory account to use",
            bg=SOFT_GOLD, fg=TEXT, font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            self._account_picker,
            text="Multiple enabled accounts were found. Review the username, title, and OU before continuing.",
            bg=SOFT_GOLD, fg=GRAY, font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(2, 7))
        for role, details in ambiguous.items():
            row = tk.Frame(self._account_picker, bg=SOFT_GOLD)
            row.pack(fill="x", pady=2)
            tk.Label(
                row, text=details["label"], bg=SOFT_GOLD, fg=TEXT,
                font=("Segoe UI", 8, "bold"), width=24, anchor="w",
            ).pack(side="left")
            accounts = sorted(
                details["accounts"],
                key=lambda account: (account.get("username") or "").casefold(),
            )
            labels = [self._account_choice_label(account) for account in accounts]
            label_to_username = {
                label: account.get("username", "")
                for label, account in zip(labels, accounts)
            }
            value = tk.StringVar(value="")
            combo = ttk.Combobox(
                row, textvariable=value, values=labels, state="readonly", width=82,
            )
            combo.pack(side="left", fill="x", expand=True)
            combo.bind("<<ComboboxSelected>>", self._account_choice_changed)
            self._account_choice_controls[role] = (value, label_to_username)
            self._account_choice_widgets.append((combo, "readonly"))
        rebuild_button = tk.Button(
            self._account_picker,
            text="Rebuild preview with selected account(s)",
            command=self._apply_account_choices,
            bg=ACCENT, fg=WHITE, relief="flat", bd=0,
            font=("Segoe UI", 8, "bold"), padx=12, pady=6,
            cursor="hand2",
        )
        rebuild_button.pack(anchor="e", pady=(8, 0))
        self._account_choice_widgets.append((rebuild_button, "normal"))
        self._account_picker.pack(fill="x", before=self._review_tabs, pady=(0, 8))
        self._ack.pack_forget()
        self._set_text(
            self._plan_box,
            "PREPARATION PAUSED\n\nSelect the correct enabled AD account above, then rebuild the preview.",
        )
        self._status.configure(text="SELECT ACCOUNT", fg="#FFF0B3")
        self._set_busy(False, "Waiting for account selection")

    def _account_choice_changed(self, _event=None):
        self._prepare_generation += 1
        self.plan = None
        self.script = ""
        self._apply_btn.configure(state="disabled")
        self._status.configure(text="SELECTION CHANGED", fg="#FFF0B3")
        self._activity.configure(text="Rebuild the preview to use this account")

    def _apply_account_choices(self):
        selected: dict[str, str] = {}
        for role, (value, label_to_username) in self._account_choice_controls.items():
            label = value.get()
            username = label_to_username.get(label, "")
            if not username:
                messagebox.showwarning(
                    "Select an account",
                    "Choose an account for every ambiguous AD lookup before continuing.",
                    parent=self,
                )
                return
            selected[role] = username
        self._account_overrides.update(selected)
        self._prepare()

    def _prepared(self, generation: int, plan: dict, script: str):
        if generation != self._prepare_generation:
            return
        self.plan = plan
        self.script = script
        self._ack.pack_forget()
        if plan.get("manager_mismatch"):
            self._ack.pack(fill="x", before=self._review_tabs, pady=(0, 6))
        self._set_text(self._plan_box, format_mover_plan(plan))
        self._set_text(self._script_box, script)
        self._status.configure(text="READY FOR REVIEW", fg="#D9FBEA")
        self._set_busy(False, "Preflight checks passed")
        self._apply_btn.configure(state="normal")

    def _prepare_failed(self, generation: int, message: str):
        if generation != self._prepare_generation:
            return
        if message.startswith("Manager mismatch:"):
            self._ack.pack(fill="x", before=self._review_tabs, pady=(0, 6))
        else:
            self._ack.pack_forget()
        self._set_text(self._plan_box, "PREPARATION BLOCKED\n\n" + message)
        self._status.configure(text="BLOCKED", fg="#FFD5D2")
        self._set_busy(False, "Review the issue shown above")

    def _apply(self):
        if not self.plan or not self.script:
            return
        summary = format_mover_plan(self.plan)
        if not messagebox.askyesno(
            "Confirm real AD move",
            "This will make real Active Directory changes.\n\n" + summary + "\n\nProceed?",
            parent=self,
        ):
            return
        self._apply_btn.configure(state="disabled")
        self._refresh_btn.configure(state="disabled")
        self._status.configure(text="APPLYING", fg="#FFF0B3")
        self._set_busy(True, "Applying and verifying Active Directory changes…")
        self._review_tabs.select(self._output_tab)
        self._set_text(self._output, f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Applying changes...")

        def _work():
            try:
                out, err, code = run_ps(self.script, timeout=120)
                text = out or ""
                if err:
                    text += ("\n\n" if text else "") + err
                text = f"[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text or '(no output)'}"
                self.after(0, lambda: self._applied(code, text))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._applied(1, message))
        threading.Thread(target=_work, daemon=True).start()

    def _applied(self, code: int, output: str):
        self._set_text(self._output, output)
        status = "Completed and verified" if code == 0 else "Not completed"
        self._write_audit(status, output)
        if code == 0 and self.plan:
            self.storage.mark_ad_setup(self.ticket["id"], {
                "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "status": status,
                "scenario": "mover",
                "account": self.plan["mover"]["sam"],
                "buddy": self.plan["buddy"]["sam"],
                "target_ou": self.plan["target_ou"],
                "groups_count": len(self.plan["groups"]["desired"]),
                "axapta_notice": self.plan.get("axapta_notice", ""),
            })
            self._status.configure(text="COMPLETED & VERIFIED", fg="#D9FBEA")
            self._set_busy(False, "All final-state checks passed")
            if self.on_completed:
                self.on_completed()
        else:
            self._status.configure(text="NOT COMPLETED", fg="#FFD5D2")
            self._set_busy(False, "Review the execution output")
            self._apply_btn.configure(state="normal")

    def _write_audit(self, status: str, output: str):
        try:
            log = Path.home() / ".jira-reminders" / "ad_audit.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            plan = self.plan or {}
            entry = (
                f"\n{'=' * 60}\n"
                f"Timestamp : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"Ticket    : {self.ticket.get('key', '')}  {self.ticket.get('name', '')}\n"
                "Scenario  : mover\n"
                f"Account   : {(plan.get('mover') or {}).get('sam', '')}\n"
                f"Buddy     : {(plan.get('buddy') or {}).get('sam', '')}\n"
                f"OU        : {plan.get('target_ou', '')}\n"
                f"Axapta    : {self.ticket.get('axapta_notice', '')}\n"
                f"Status    : {status}\nOutput:\n{output}\n"
                f"Plan:\n{format_mover_plan(plan) if plan else '(unavailable)'}\n"
            )
            with log.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        except Exception:
            pass

    @staticmethod
    def _set_text(widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
