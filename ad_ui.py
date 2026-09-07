"""
AD Setup window - new joiner / rejoiner onboarding automation.
Account is always pre-created by SAP SF. We detect the scenario, then configure it.
"""
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime

from ad_automation import (
    detect_location, detect_site, detect_location_conflict, detect_address_warning,
    detect_domain, build_email, uses_retired_tndm_email_domain, DEFAULT_GROUPS,
    generate_password,
    find_user_accounts, find_user_account_by_username,
    select_new_joiner_account, classify_scenario,
    get_buddy_info, get_account_groups, build_verification_script,
    build_new_joiner_script, build_rejoiner_dual_script,
    build_rejoiner_single_script, run_ps,
)
from group_policy import is_blocked_group, is_redundant_group, is_restricted_group

BG     = "#F7F8FA"
WHITE  = "#FFFFFF"
ACCENT = "#0C66E4"
GREEN  = "#1F845A"
RED    = "#C9372C"
ORANGE = "#B76E00"
GRAY   = "#5E6C84"
TEXT   = "#172B4D"
BORDER = "#DFE3EA"
SOFT_BLUE = "#E9F2FF"
MONO   = ("Consolas", 9)
_CLIPBOARD_CLEAR_MS = 30_000   # clear clipboard 30 s after copying sensitive data

SCENARIO_LABELS = {
    "new_joiner":      ("New joiner", GREEN),
    "rejoiner_dual":   ("Rejoiner - restore old account", ORANGE),
    "rejoiner_single": ("Rejoiner - single existing account", ORANGE),
    "unknown":         ("Needs manual review", RED),
}

class BusyDialog(tk.Toplevel):
    def __init__(self, parent, title: str, message: str):
        super().__init__(parent)
        self.configure(bg=WHITE)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        card = tk.Frame(self, bg=WHITE, padx=20, pady=18)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="⚙", bg=WHITE, fg=ACCENT,
                 font=("Segoe UI Symbol", 28)).pack(pady=(0, 6))
        self._message = tk.Label(card, text=message, bg=WHITE, fg=TEXT,
                                 font=("Segoe UI", 10, "bold"),
                                 wraplength=280, justify="center")
        self._message.pack()

        self._progress = ttk.Progressbar(card, mode="indeterminate", length=220)
        self._progress.pack(pady=(14, 0))
        self._progress.start(12)

        self.update_idletasks()
        self._center_over(parent)
        self.grab_set()

    def _center_over(self, parent):
        parent.update_idletasks()
        width = self.winfo_width() or 320
        height = self.winfo_height() or 150
        x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
        y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def set_message(self, title: str, message: str):
        self.title(title)
        self._message.config(text=message)
        self.update_idletasks()

    def close(self):
        try:
            self._progress.stop()
        except tk.TclError:
            pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class ADSetupWindow(tk.Toplevel):
    def __init__(self, parent, ticket: dict, storage=None, on_completed=None,
                 buddy_hint: str = ""):
        super().__init__(parent)
        self.ticket    = ticket
        self.storage   = storage
        self.on_completed = on_completed
        self._buddy_hint = buddy_hint
        self._location = detect_location(ticket.get("office", ""), ticket.get("company_name", ""))
        self._site = detect_site(ticket.get("office", ""), ticket.get("company_name", ""))
        self._location_conflict = detect_location_conflict(
            ticket.get("office", ""),
            ticket.get("company_name", ""),
        )
        self._address_warning = detect_address_warning(
            ticket.get("office", ""),
            ticket.get("company_name", ""),
        )
        self._domain   = detect_domain(ticket.get("company_name", ""))

        # AD search results
        self._accounts: list[dict] = []
        self._scenario: str        = ""
        self._sf_account: dict     = {}
        self._old_account: dict    = {}
        self._old_accounts: list[dict] = []
        self._old_account_var = tk.StringVar()

        # Buddy department and extended attributes (fetched alongside groups)
        self._buddy_department: str = ""
        self._buddy_ext_attrs: dict = {}
        self._ext_attr_vars: dict[str, tk.BooleanVar] = {}
        self._stale_group_vars: dict[str, tk.BooleanVar] = {}
        self._busy_dialog: BusyDialog | None = None

        # Groups state
        self._group_vars: dict[str, tk.BooleanVar] = {}
        self._buddy_group_vars: dict[str, tk.BooleanVar] = {}

        self.title(f"AD Setup - {ticket.get('name', '')}")
        self.geometry("880x820")
        self.resizable(True, True)
        self.configure(bg=BG)
        self._build()

    # ── Scaffold ──────────────────────────────────────────────────────────────

    def _build(self):
        tk.Label(self, text=f"  AD Setup - {self.ticket.get('name', '')}",
                 bg=ACCENT, fg=WHITE, font=("Segoe UI", 13, "bold")).pack(fill="x", ipady=10)

        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb     = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        body   = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * e.delta / 120), "units"))
        self._fill(body)
        if self._buddy_hint:
            self._buddy_var.set(self._buddy_hint)
            self.after(150, self._fetch_buddy)

    def _section(self, parent, title: str) -> tk.Frame:
        tk.Label(parent, text=title, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=20, pady=(18, 2))
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(0, 8))
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=20, pady=2)
        return f

    def _btn(self, parent, text, cmd, bg=ACCENT, fg=WHITE, bold=False):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief="flat", bd=0, cursor="hand2",
                         font=("Segoe UI", 9, "bold" if bold else "normal"),
                         padx=11, pady=5,
                         activebackground=ACCENT, activeforeground=WHITE)

    def _scrollable_text(self, parent, *, height: int, bg: str, fg: str,
                         state: str = "normal") -> tk.Text:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(frame, height=height, relief="solid", bd=1,
                       font=MONO, bg=bg, fg=fg, state=state,
                       wrap="none")
        yscroll = tk.Scrollbar(frame, orient="vertical", command=text.yview)
        xscroll = tk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        return text

    def _show_busy(self, title: str, message: str):
        if self._busy_dialog:
            try:
                if self._busy_dialog.winfo_exists():
                    self._busy_dialog.set_message(title, message)
                    return
            except tk.TclError:
                pass
            self._busy_dialog = None
        self._busy_dialog = BusyDialog(self, title, message)

    def _hide_busy(self):
        if not self._busy_dialog:
            return
        self._busy_dialog.close()
        self._busy_dialog = None

    def _handle_async_error(self, title: str, message: str):
        self._hide_busy()
        messagebox.showerror(title, message, parent=self)

    # ── Main content ──────────────────────────────────────────────────────────

    def _fill(self, body):
        # ── 1. Ticket summary ─────────────────────────────────────────────────
        sec = self._section(body, "1. Joiner details")
        for label, key in [("Name", "name"), ("Position", "position"),
                            ("Company", "company_name"), ("Office", "office"),
                            ("Manager", "manager"), ("Phone", "phone"),
                            ("Start date", "start_date")]:
            row = tk.Frame(sec, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{label}:", bg=BG, fg=GRAY, font=("Segoe UI", 9),
                     width=12, anchor="e").pack(side="left")
            tk.Label(row, text=str(self.ticket.get(key, "") or ""), bg=BG, fg=TEXT,
                     font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left")

        site_label = {
            "vilnius": "Vilnius",
            "siauliai": "Šiauliai",
            "poland": "Poland",
            "georgia": "GBS",
        }.get(self._site, self._location.title())
        tk.Label(sec, text=f"Location: {site_label}  |  "
                           f"Starting groups: {', '.join(DEFAULT_GROUPS[self._location])}",
                  bg=BG, fg=GRAY, font=("Segoe UI", 8), wraplength=780, justify="left"
                  ).pack(anchor="w", pady=(6, 0))
        for warning in (self._location_conflict, self._address_warning):
            if not warning:
                continue
            tk.Label(
                sec,
                text=f"⚠  {warning}",
                bg="#FFF4E5",
                fg=ORANGE,
                font=("Segoe UI", 9, "bold"),
                wraplength=780,
                justify="left",
                anchor="w",
                padx=8,
                pady=6,
            ).pack(fill="x", pady=(6, 0))

        # ── 2. Find AD accounts ───────────────────────────────────────────────
        sec2 = self._section(body, "2. Find the account")

        top_row = tk.Frame(sec2, bg=BG)
        top_row.pack(fill="x")
        self._btn(top_row, "Find account", self._search_ad,
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left")
        self._scenario_label = tk.Label(top_row, text="", bg=BG,
                                        font=("Segoe UI", 10, "bold"))
        self._scenario_label.pack(side="left", padx=16)

        self._search_out = tk.Text(sec2, height=6, relief="solid", bd=1,
                                   font=MONO, bg=WHITE, fg="#172B4D", state="disabled")
        self._search_out.pack(fill="x", pady=(6, 4))

        tk.Label(sec2,
                 text="New joiner: one prepared account is moved and completed.\n"
                      "Rejoiner: the previous account is restored; the temporary SF account is removed.",
                 bg=BG, fg=GRAY, font=("Segoe UI", 8), justify="left").pack(anchor="w")

        self._rejoiner_single_warning = tk.Label(
            sec2,
            text="⚠  Rejoiner single — no SF dummy account exists. The single existing account may be "
                 "disabled or already active. All employment data comes from the Jira ticket. Verify "
                 "position, company, and manager are correct before applying.",
            bg="#FFF4E5", fg="#B76E00", font=("Segoe UI", 9, "bold"),
            wraplength=760, justify="left", anchor="w", padx=8, pady=6)
        # shown only when scenario is rejoiner_single

        # ── 3. Account username (filled after search) ─────────────────────────
        sec3 = self._section(body, "3. Account to use")

        self._username_var = tk.StringVar()
        urow = tk.Frame(sec3, bg=BG)
        urow.pack(fill="x")
        tk.Label(urow, text="Username:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9), width=12, anchor="e").pack(side="left")
        tk.Entry(urow, textvariable=self._username_var, font=("Consolas", 11),
                 relief="solid", bd=1, width=14, state="readonly").pack(side="left", padx=6, ipady=3)
        tk.Label(urow, text="filled automatically after search",
                 bg=BG, fg=GRAY, font=("Segoe UI", 8)).pack(side="left", padx=6)

        # Rejoiner extra: SF username for reference
        self._sf_username_frame = tk.Frame(sec3, bg=BG)
        self._sf_username_frame.pack(fill="x")
        self._sf_label = tk.Label(self._sf_username_frame, text="", bg=BG,
                                  fg=ORANGE, font=("Segoe UI", 9))
        self._sf_label.pack(anchor="w")

        self._old_account_frame = tk.Frame(sec3, bg=BG)
        tk.Label(self._old_account_frame, text="Old account:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9), width=12, anchor="e").pack(side="left")
        self._old_account_menu_holder = tk.Frame(self._old_account_frame, bg=BG)
        self._old_account_menu_holder.pack(side="left", padx=6)
        self._old_account_hint = tk.Label(self._old_account_frame, text="",
                                          bg=BG, fg=ORANGE, font=("Segoe UI", 8))
        self._old_account_hint.pack(side="left", padx=6)

        # ── 4. Email ──────────────────────────────────────────────────────────
        sec4 = self._section(body, "4. Email address")

        default_email = build_email(
            self.ticket.get("first_name", ""),
            self.ticket.get("last_name", ""),
            self._domain,
        )
        self._email_var = tk.StringVar(value=default_email)
        erow = tk.Frame(sec4, bg=BG)
        erow.pack(fill="x")
        tk.Label(erow, text="Email:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9), width=12, anchor="e").pack(side="left")
        tk.Entry(erow, textvariable=self._email_var, font=("Segoe UI", 10),
                 relief="solid", bd=1, width=42).pack(side="left", padx=6, ipady=3)

        # Section 5. Buddy -> OU + group copy
        sec5 = self._section(body, "5. Match a similar user")

        buddy_row = tk.Frame(sec5, bg=BG)
        buddy_row.pack(fill="x")
        tk.Label(buddy_row, text="Template user:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9), width=12, anchor="e").pack(side="left")
        self._buddy_var = tk.StringVar()
        tk.Entry(buddy_row, textvariable=self._buddy_var, font=("Consolas", 10),
                 relief="solid", bd=1, width=14).pack(side="left", padx=6, ipady=3)
        self._btn(buddy_row, "Use as template", self._fetch_buddy,
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left")
        self._buddy_status = tk.Label(buddy_row, text="", bg=BG, fg=GRAY, font=("Segoe UI", 9))
        self._buddy_status.pack(side="left", padx=8)

        ou_row = tk.Frame(sec5, bg=BG)
        ou_row.pack(fill="x", pady=(6, 0))
        tk.Label(ou_row, text="Target folder:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9), width=12, anchor="e").pack(side="left")
        self._ou_var = tk.StringVar()
        tk.Entry(ou_row, textvariable=self._ou_var, font=("Segoe UI", 8),
                 relief="solid", bd=1, width=62).pack(side="left", padx=6, ipady=3)

        tk.Label(sec5, text="Groups from the template user:",
                 bg=BG, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 2))
        tk.Label(sec5,
                 text="Approval-only license groups are shown in red and cannot be copied from the buddy.",
                 bg=BG, fg=RED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 2))
        tk.Label(sec5,
                 text="Redundant groups are shown in gray and are never copied.",
                 bg=BG, fg=GRAY, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 2))

        self._buddy_groups_frame = tk.Frame(sec5, bg=WHITE, relief="solid", bd=1)
        self._buddy_groups_frame.pack(fill="x", pady=(0, 4))
        tk.Label(self._buddy_groups_frame, text="  (fetch buddy first)",
                 bg=WHITE, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=4)

        copy_row = tk.Frame(sec5, bg=BG)
        copy_row.pack(anchor="w")
        self._btn(copy_row, "Select all",   self._buddy_select_all,   bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 6))
        self._btn(copy_row, "Clear", self._buddy_deselect_all, bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 6))
        self._btn(copy_row, "Add selected groups", self._copy_buddy_groups,
                  bg=GREEN, fg=WHITE).pack(side="left")

        tk.Label(sec5, text="Extended attributes from buddy:",
                 bg=BG, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 2))
        self._ext_attr_frame = tk.Frame(sec5, bg=WHITE, relief="solid", bd=1)
        self._ext_attr_frame.pack(fill="x", pady=(0, 4))
        tk.Label(self._ext_attr_frame, text="  (fetch buddy first)",
                 bg=WHITE, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=4)

        self._stale_section = tk.Frame(sec5, bg=BG)
        tk.Label(self._stale_section, text="Stale groups on old account (not on buddy):",
                 bg=BG, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 2))
        self._stale_groups_frame = tk.Frame(self._stale_section, bg=WHITE, relief="solid", bd=1)
        self._stale_groups_frame.pack(fill="x", pady=(0, 4))
        stale_btn_row = tk.Frame(self._stale_section, bg=BG)
        stale_btn_row.pack(anchor="w")
        self._btn(stale_btn_row, "Select all",  lambda: [v.set(True)  for v in self._stale_group_vars.values()],
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 6))
        self._btn(stale_btn_row, "Clear",       lambda: [v.set(False) for v in self._stale_group_vars.values()],
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 6))
        self._btn(stale_btn_row, "Remove selected from old account", self._remove_stale_groups,
                  bg="#C9372C", fg=WHITE).pack(side="left")
        # hidden until buddy is fetched for a rejoiner

        # ── 6. Groups for new user ────────────────────────────────────────────
        sec6 = self._section(body, "6. Groups to add")

        tk.Label(sec6, text="Default groups pre-selected. Add buddy groups above, then adjust.",
                 bg=BG, fg=GRAY, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 4))
        tk.Label(sec6,
                 text="Restricted approval-only license groups stay disabled in red and are never added by this tool.",
                 bg=BG, fg=RED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 4))
        tk.Label(sec6,
                 text="Redundant groups stay disabled in gray and are never added by this tool.",
                 bg=BG, fg=GRAY, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 4))

        self._groups_frame = tk.Frame(sec6, bg=WHITE, relief="solid", bd=1)
        self._groups_frame.pack(fill="x")
        self._rebuild_groups_panel(DEFAULT_GROUPS[self._location])

        # ── 7. Password ───────────────────────────────────────────────────────
        sec7 = self._section(body, "7. Password")

        self._pwd_var = tk.StringVar(value=generate_password())
        self._pwd_visible = False
        pwd_row = tk.Frame(sec7, bg=BG)
        pwd_row.pack(fill="x")
        tk.Label(pwd_row, text="Password:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9), width=12, anchor="e").pack(side="left")
        self._pwd_entry = tk.Entry(pwd_row, textvariable=self._pwd_var, font=("Consolas", 11),
                                   relief="solid", bd=1, width=20, show="*")
        self._pwd_entry.pack(side="left", padx=6, ipady=3)
        self._show_btn = self._btn(pwd_row, "Show", self._toggle_pwd_visibility,
                                   bg="#DEEBFF", fg=ACCENT)
        self._show_btn.pack(side="left", padx=(0, 4))
        self._btn(pwd_row, "Copy", lambda: self._copy_sensitive(self._pwd_var.get()),
                  bg="#DEEBFF", fg=ACCENT).pack(side="left")

        # SMS template
        tk.Label(sec7, text="SMS template (click anywhere on box to copy):",
                 bg=BG, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))
        self._sms_box = tk.Text(sec7, height=5, relief="solid", bd=1,
                                font=("Segoe UI", 9), bg="#FFFAE6",
                                fg="#172B4D", cursor="hand2", state="disabled")
        self._sms_box.pack(fill="x")
        self._sms_box.bind("<Button-1>", lambda e: self._copy_sms_sensitive())
        self._update_sms()
        self._username_var.trace_add("write", lambda *_: self._update_sms())

        # ── 8. Preview & run ──────────────────────────────────────────────────
        sec8 = self._section(body, "8. Review and apply")

        btn_row = tk.Frame(sec8, bg=BG)
        btn_row.pack(fill="x", pady=4)
        self._btn(btn_row, "Review changes", self._generate_preview,
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Copy review", self._copy_script,
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Copy result", self._copy_output,
                  bg=SOFT_BLUE, fg=ACCENT).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Apply changes", self._run_script,
                  bg=GREEN, fg=WHITE, bold=True).pack(side="left")

        script_frame = tk.Frame(sec8, bg=BG)
        script_frame.pack(fill="x", pady=(6, 4))
        self._script_box = self._scrollable_text(
            script_frame, height=18, bg=WHITE, fg=TEXT)
        self._script_box.config(insertbackground=TEXT)

        tk.Label(sec8, text="Result:", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self._run_status = tk.Label(sec8, text="", bg=BG, fg=GRAY,
                                    font=("Segoe UI", 9, "bold"))
        self._run_status.pack(anchor="w")
        output_frame = tk.Frame(sec8, bg=BG)
        output_frame.pack(fill="x", pady=(2, 24))
        self._output_box = self._scrollable_text(
            output_frame, height=7, bg=WHITE, fg="#172B4D", state="disabled")

    # ── AD search & scenario detection ───────────────────────────────────────

    def _search_ad(self):
        first = self.ticket.get("first_name", "")
        last  = self.ticket.get("last_name", "")
        requested_username = self.ticket.get("person_id_external", "").strip()
        rejoiner_value = str(self.ticket.get("rejoiner", "")).strip().casefold()
        ticket_is_rejoiner = rejoiner_value not in {
            "", "no", "false", "0", "none", "n/a", "-",
        }
        self._set_text(self._search_out, "Searching AD...")
        self._scenario_label.config(text="", fg=GRAY)
        self._show_busy("Searching AD", "Looking up matching accounts in Active Directory...")

        def _do():
            try:
                accounts = find_user_accounts(first, last)
                manual_account = {}
                if requested_username and not ticket_is_rejoiner:
                    manual_account = select_new_joiner_account(accounts, requested_username)
                    if manual_account.get("is_sf"):
                        manual_account = {}
                    elif not manual_account:
                        manual_account = find_user_account_by_username(requested_username) or {}

                # An exact, active AD match from Jira is the supported fallback
                # when SAP SF did not prepare an account. Disabled non-SF users
                # keep the existing rejoiner classification path.
                if (manual_account
                        and manual_account.get("enabled")
                        and not manual_account.get("disabled")):
                    accounts = [manual_account]
                    scenario = "new_joiner"
                else:
                    scenario = classify_scenario(accounts)
                self.after(0, lambda: self._apply_search_result(accounts, scenario))
            except Exception as e:
                self.after(0, lambda: self._handle_async_error("AD search failed", str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def _apply_search_result(self, accounts: list[dict], scenario: str):
        self._hide_busy()
        self._accounts = accounts
        self._scenario = scenario

        # Populate scenario badge
        label_text, color = SCENARIO_LABELS.get(scenario, ("UNKNOWN", RED))
        self._scenario_label.config(text=label_text, fg=color)

        # Show/hide rejoiner_single warning
        if scenario == "rejoiner_single":
            self._rejoiner_single_warning.pack(fill="x", pady=(6, 0))
        else:
            self._rejoiner_single_warning.pack_forget()

        # Build summary text
        if not accounts:
            summary = "No accounts found. Check the name spelling."
        else:
            lines = []
            for a in accounts:
                status = "DISABLED" if a.get("disabled") else "ACTIVE"
                sf_tag = "  temporary SF account" if a["is_sf"] else ""
                emp    = f"  employee ID: {a['employee_id']}" if a["employee_id"] else ""
                lines.append(f"[{status}]  {a['username']}{sf_tag}{emp}")
                lines.append(f"         {a['ou']}")
            summary = "\n".join(lines)
        self._set_text(self._search_out, summary)

        # Pre-fill username based on scenario
        sf_accounts  = [a for a in accounts if a["is_sf"]]
        old_accounts = sorted(
            [a for a in accounts if not a["is_sf"]],
            key=lambda a: (not bool(a.get("disabled")), a.get("username", "").casefold()),
        )

        self._sf_account = select_new_joiner_account(
            accounts,
            self.ticket.get("person_id_external", ""),
        )
        self._old_accounts = old_accounts
        self._old_account = old_accounts[0] if old_accounts else {}
        self._hide_old_account_picker()

        if scenario == "new_joiner":
            self._username_var.set(self._sf_account.get("username", ""))
            self._sf_label.config(text="")
        elif scenario == "rejoiner_dual":
            if len(old_accounts) > 1:
                self._show_old_account_picker(old_accounts)
            else:
                self._username_var.set(self._old_account.get("username", ""))
            self._sf_label.config(
                text=f"Temporary SF account to remove later: {self._sf_account.get('username', '')}  "
                     f"employee ID: {self._sf_account.get('employee_id', 'N/A')}")
        elif scenario == "rejoiner_single":
            if len(old_accounts) > 1:
                self._show_old_account_picker(old_accounts)
            self._username_var.set(self._old_account.get("username", "") or (accounts[0]["username"] if accounts else ""))
            self._sf_label.config(text="")
        elif scenario == "unknown":
            self._username_var.set(accounts[0]["username"] if accounts else "")
            self._sf_label.config(text="")

    def _show_old_account_picker(self, old_accounts: list[dict]):
        for w in self._old_account_menu_holder.winfo_children():
            w.destroy()
        values = [a["username"] for a in old_accounts]
        self._old_account_var.set(values[0])
        tk.OptionMenu(
            self._old_account_menu_holder,
            self._old_account_var,
            *values,
            command=lambda _=None: self._select_old_account(),
        ).pack(side="left")
        self._old_account_hint.config(text="Multiple previous accounts found. Choose the one to restore.")
        self._old_account_frame.pack(fill="x", pady=(4, 0))
        self._select_old_account()

    def _hide_old_account_picker(self):
        self._old_account_frame.pack_forget()
        self._old_account_hint.config(text="")

    def _select_old_account(self):
        username = self._old_account_var.get()
        for account in self._old_accounts:
            if account["username"] == username:
                self._old_account = account
                self._username_var.set(username)
                return

    # ── Buddy ─────────────────────────────────────────────────────────────────

    def _fetch_buddy(self):
        sam = self._buddy_var.get().strip()
        if not sam:
            messagebox.showwarning("No buddy", "Enter the buddy's SAM account name.", parent=self)
            return
        self._buddy_status.config(text="Loading...", fg=GRAY)
        self._show_busy("Loading Buddy", "Fetching OU, groups, and extra attributes from Active Directory...")

        def _do():
            try:
                ou, groups, err, department, ext_attrs = get_buddy_info(sam)

                # For rejoiners, fetch old account groups to compute stale ones
                stale_groups: list[str] = []
                if self._scenario in ("rejoiner_dual", "rejoiner_single") and not err:
                    old_sam = (self._old_account.get("username")
                               if self._scenario == "rejoiner_dual"
                               else self._old_account.get("username"))
                    if old_sam:
                        old_groups, _ = get_account_groups(old_sam)
                        buddy_set = set(groups)
                        stale_groups = [g for g in old_groups if g not in buddy_set]

                def _update():
                    self._hide_busy()
                    if err:
                        self._buddy_status.config(text=f"Error: {err}", fg=RED)
                        return
                    self._ou_var.set(ou)
                    self._buddy_department = department
                    self._buddy_ext_attrs = ext_attrs
                    dept_hint = f"  (dept: {department})" if department else ""
                    self._buddy_status.config(text=f"{len(groups)} groups found{dept_hint}", fg=GREEN)

                    for w in self._buddy_groups_frame.winfo_children():
                        w.destroy()
                    self._buddy_group_vars = {}
                    for i, g in enumerate(groups):
                        var = tk.BooleanVar(value=False)
                        self._buddy_group_vars[g] = var
                        restricted = is_restricted_group(g)
                        redundant = is_redundant_group(g)
                        blocked = restricted or redundant
                        label = f"{g} (redundant)" if redundant else g
                        if i % 2 == 0:
                            rf = tk.Frame(self._buddy_groups_frame, bg=WHITE)
                            rf.pack(fill="x")
                        tk.Checkbutton(
                            rf,
                            text=label,
                            variable=var,
                            bg=WHITE,
                            fg=RED if restricted else (GRAY if redundant else TEXT),
                            disabledforeground=RED if restricted else GRAY,
                            font=("Segoe UI", 9),
                            anchor="w",
                            state="disabled" if blocked else "normal",
                            selectcolor=WHITE,
                            activebackground=WHITE,
                        ).pack(side="left", padx=12, pady=1)

                    for w in self._ext_attr_frame.winfo_children():
                        w.destroy()
                    self._ext_attr_vars = {}
                    labels = (
                        {"extensionAttribute14": "extensionAttribute14"}
                        if self._scenario in ("new_joiner", "rejoiner_dual") else
                        {"extensionAttribute5": "extensionAttribute5",
                         "extensionAttribute14": "extensionAttribute14"}
                    )
                    any_value = False
                    for attr, label in labels.items():
                        value = ext_attrs.get(attr, "")
                        row = tk.Frame(self._ext_attr_frame, bg=WHITE)
                        row.pack(fill="x", padx=8, pady=2)
                        var = tk.BooleanVar(value=bool(value))
                        self._ext_attr_vars[attr] = var
                        tk.Checkbutton(row, variable=var, bg=WHITE,
                                       activebackground=WHITE).pack(side="left")
                        tk.Label(row, text=f"{label}:", bg=WHITE, fg=GRAY,
                                 font=("Segoe UI", 9), width=20, anchor="w").pack(side="left")
                        tk.Label(row, text=value or "(empty)", bg=WHITE,
                                 fg=TEXT if value else GRAY,
                                 font=("Segoe UI", 9, "italic" if not value else "normal"),
                                 anchor="w").pack(side="left", padx=4)
                        if value:
                            any_value = True
                    if not any_value:
                        tk.Label(self._ext_attr_frame, text="  All extended attributes are empty on buddy",
                                 bg=WHITE, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=4)

                    # Stale groups section
                    for w in self._stale_groups_frame.winfo_children():
                        w.destroy()
                    self._stale_group_vars = {}
                    if self._scenario in ("rejoiner_dual", "rejoiner_single"):
                        self._stale_section.pack(fill="x", pady=(4, 0))
                        if stale_groups:
                            for i, g in enumerate(stale_groups):
                                var = tk.BooleanVar(value=False)
                                self._stale_group_vars[g] = var
                                if i % 2 == 0:
                                    rf = tk.Frame(self._stale_groups_frame, bg=WHITE)
                                    rf.pack(fill="x")
                                tk.Checkbutton(rf, text=g, variable=var,
                                               bg=WHITE, font=("Segoe UI", 9),
                                               anchor="w").pack(side="left", padx=12, pady=1)
                        else:
                            tk.Label(self._stale_groups_frame,
                                     text="  No stale groups — old account matches buddy",
                                     bg=WHITE, fg=GRAY, font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=4)
                    else:
                        self._stale_section.pack_forget()

                self.after(0, _update)
            except Exception as e:
                self.after(0, lambda: self._handle_async_error("Buddy lookup failed", str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def _buddy_select_all(self):
        for g, v in self._buddy_group_vars.items():
            if not is_blocked_group(g):
                v.set(True)

    def _buddy_deselect_all(self):
        for v in self._buddy_group_vars.values(): v.set(False)

    def _copy_buddy_groups(self):
        selected = [g for g, v in self._buddy_group_vars.items()
                    if v.get() and not is_blocked_group(g)]
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick some buddy groups first.", parent=self)
            return
        existing = list(self._group_vars.keys())
        combined = list(dict.fromkeys(existing + selected))
        if combined != existing:
            self._rebuild_groups_panel(combined)

    # ── Groups panel ──────────────────────────────────────────────────────────

    def _rebuild_groups_panel(self, groups: list[str]):
        for w in self._groups_frame.winfo_children():
            w.destroy()
        self._group_vars.clear()
        for i, g in enumerate(groups):
            restricted = is_restricted_group(g)
            redundant = is_redundant_group(g)
            blocked = restricted or redundant
            label = f"{g} (redundant)" if redundant else g
            var = tk.BooleanVar(value=not blocked)
            self._group_vars[g] = var
            if i % 2 == 0:
                rf = tk.Frame(self._groups_frame, bg=WHITE)
                rf.pack(fill="x")
            tk.Checkbutton(
                rf,
                text=label,
                variable=var,
                bg=WHITE,
                fg=RED if restricted else (GRAY if redundant else TEXT),
                disabledforeground=RED if restricted else GRAY,
                font=("Segoe UI", 9),
                anchor="w",
                state="disabled" if blocked else "normal",
                selectcolor=WHITE,
                activebackground=WHITE,
            ).pack(side="left", padx=12, pady=1)

    def _active_groups(self) -> list[str]:
        return [g for g, v in self._group_vars.items()
                if v.get() and not is_blocked_group(g)]

    # ── SMS ───────────────────────────────────────────────────────────────────

    def _update_sms(self):
        phone    = self.ticket.get("phone", "N/A") or "N/A"
        username = self._username_var.get() or "(username)"
        msg = f"Click box to copy SMS template  (send to {phone})\n\n{self._sms_template(username)}"
        self._sms_box.config(state="normal")
        self._sms_box.delete("1.0", "end")
        self._sms_box.insert("1.0", msg)
        self._sms_box.config(state="disabled")

    @staticmethod
    def _sms_template(username: str) -> str:
        return (
            "Hello,\n\nYour username is:\n\n"
            f"Username: {username}\n\nHave a great day!"
        )

    # ── Script ────────────────────────────────────────────────────────────────

    def _build_script(self) -> str:
        email    = self._email_var.get().strip()
        password = self._pwd_var.get().strip()
        ou       = self._ou_var.get().strip()
        groups   = self._active_groups()

        if not self._scenario:
            raise ValueError("Search for AD accounts first (Section 2).")
        if not email:
            raise ValueError("Email address is required.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError(f"'{email}' does not look like a valid email address.")
        if uses_retired_tndm_email_domain(email):
            raise ValueError(
                "The @tndmtrucking.com domain is retired. Use the employee's "
                "@girteka.eu address."
            )
        if not password:
            raise ValueError("Password is required.")
        if not ou:
            raise ValueError("Target OU is required.\nEnter a buddy username and click 'Fetch OU + Groups'.")
        if not re.search(r"(?i)^(OU|CN|DC)=", ou):
            raise ValueError(f"Target OU does not look like a valid Distinguished Name:\n{ou}")

        dept = self._buddy_department
        ext_attrs = {attr: self._buddy_ext_attrs.get(attr, "")
                     for attr, var in self._ext_attr_vars.items() if var.get()}
        if self._scenario == "new_joiner":
            if not self._sf_account.get("username"):
                raise ValueError("Temporary SF account was not detected. Search again before preparing changes.")
            return build_new_joiner_script(self.ticket, self._sf_account, ou, email, password, groups, dept, ext_attrs)
        elif self._scenario == "rejoiner_dual":
            if not self._sf_account.get("username") or not self._old_account.get("username"):
                raise ValueError("Both the temporary SF account and previous account are required.")
            return build_rejoiner_dual_script(
                self.ticket, self._sf_account, self._old_account, ou, email, password, groups, dept, ext_attrs)
        elif self._scenario == "rejoiner_single":
            account = self._old_account or (self._accounts[0] if self._accounts else {})
            if not account:
                raise ValueError("No AD account is available for this rejoiner.")
            return build_rejoiner_single_script(
                self.ticket, account, ou, email, password, groups, dept, ext_attrs)
        else:
            raise ValueError(f"Scenario '{self._scenario}' requires manual review before changes can be prepared.")

    def _generate_preview(self):
        try:
            self._script_box.delete("1.0", "end")
            self._script_box.insert("1.0", self._build_script())
        except ValueError as e:
            messagebox.showwarning("Missing info", str(e), parent=self)

    def _copy_script(self):
        try:
            script = self._script_box.get("1.0", "end-1c").strip()
            self._copy_sensitive(script or self._build_script())
        except ValueError as e:
            messagebox.showwarning("Missing info", str(e), parent=self)

    def _copy_output(self):
        text = self._output_box.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("No result yet", "There is no result to copy yet.", parent=self)
            return
        self._copy(text)

    def _run_script(self):
        try:
            script = self._script_box.get("1.0", "end-1c").strip()
            if not script:
                script = self._build_script()
        except ValueError as e:
            messagebox.showwarning("Missing info", str(e), parent=self)
            return

        if not messagebox.askyesno(
            "Confirm - Real AD changes",
            self._build_safety_summary(),
            parent=self,
        ):
            return

        started = datetime.now()
        self._run_status.config(text="In progress", fg=ORANGE)
        self._set_text(self._output_box, f"[{started:%Y-%m-%d %H:%M:%S}] Applying changes...")
        self._show_busy("Applying Changes", "Applying Active Directory changes. Please wait...")

        def _do():
            try:
                out, err, code = run_ps(script, timeout=120)
                finished = datetime.now()
                status = "Completed" if code == 0 and not err else ("Not completed" if code != 0 else "Completed with notes")
                result = f"[{finished:%Y-%m-%d %H:%M:%S}] {status}\n\n"
                result += out
                if err:
                    result += f"\n\n--- Details ---\n{err}"
                if code != 0:
                    result += f"\n\nResult code: {code}"
                self.after(0, lambda: self._apply_run_result(status, result or "(no output)"))
            except Exception as e:
                self.after(0, lambda: self._handle_async_error("Apply changes failed", str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def _build_safety_summary(self) -> str:
        groups = self._active_groups()
        target_user = self._username_var.get().strip() or "(not selected)"
        sf_user = self._sf_account.get("username", "")
        manager = self.ticket.get("manager", "") or "(not set in Jira)"
        delete_line = ""
        if self._scenario == "rejoiner_dual":
            delete_line = f"\nTemporary SF account to remove: {sf_user}"

        group_text = f"{len(groups)} group(s)"
        if not groups:
            group_text += " - none selected"

        return (
            "This will make real changes to Active Directory.\n\n"
            f"Case type: {self._scenario.replace('_', ' ')}\n"
            f"Account to update: {target_user}"
            f"{delete_line}\n"
            f"Target folder: {self._ou_var.get().strip()}\n"
            f"Email: {self._email_var.get().strip()}\n"
            f"Groups: {group_text}\n"
            f"Manager from Jira: {manager}\n"
            "Manager will be added only when there is one clear match.\n\n"
            "Proceed?"
        )

    def _apply_run_result(self, status: str, text: str):
        color = GREEN if status == "Completed" else (RED if status == "Not completed" else ORANGE)
        self._run_status.config(text=status, fg=color)
        self._set_text(self._output_box, text)
        self._write_audit_log(status, text)
        if status != "Not completed":
            self._mark_setup_completed(status)
        sam = self._username_var.get().strip()
        if sam:
            self._show_busy("Verifying Changes", "Checking the updated account in Active Directory...")
            self._run_status.config(text=f"{status}  —  verifying...", fg=color)
            threading.Thread(target=self._run_verification, args=(sam, text, color), daemon=True).start()
        else:
            self._hide_busy()

    def _run_verification(self, sam: str, existing_output: str, color: str):
        try:
            out, err, _ = run_ps(build_verification_script(sam), timeout=30)
            verification = out or err or "Verification returned no output."
            full = existing_output + "\n\n─────────────────────────────\n" + verification
            def _update():
                self._hide_busy()
                self._set_text(self._output_box, full)
                self._run_status.config(
                    text=self._run_status.cget("text").replace("  —  verifying...", ""), fg=color)
            self.after(0, _update)
        except Exception as e:
            self.after(0, lambda: self._handle_async_error("Verification failed", str(e)))

    def _remove_stale_groups(self):
        selected = [g for g, v in self._stale_group_vars.items() if v.get()]
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick some stale groups first.", parent=self)
            return
        sam = self._username_var.get().strip()
        if not sam:
            return
        if not messagebox.askyesno(
            "Confirm group removal",
            f"Remove {len(selected)} group(s) from {sam}?\n\n" + "\n".join(selected),
            parent=self,
        ):
            return
        lines = ["Import-Module ActiveDirectory -ErrorAction Stop"]
        for g in selected:
            from ad_automation import _e
            lines.append(
                f"try {{ Remove-ADGroupMember -Identity '{_e(g)}' -Members '{_e(sam)}' -Confirm:$false; "
                f'Write-Host "OK  Removed from {_e(g)}" }} '
                f"catch {{ Write-Warning \"Failed to remove from '{_e(g)}': $_\" }}"
            )
        out, err, code = run_ps("\n".join(lines), timeout=60)
        result = (out or "") + ("\n" + err if err else "")
        messagebox.showinfo("Group removal result", result or "Done.", parent=self)

    def _mark_setup_completed(self, status: str):
        if not self.storage:
            return
        info = {
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": status,
            "scenario": self._scenario,
            "account": self._username_var.get().strip(),
            "email": self._email_var.get().strip(),
            "target_ou": self._ou_var.get().strip(),
            "groups_count": len(self._active_groups()),
            "phone": self.ticket.get("phone", ""),
            "password": self._pwd_var.get().strip(),
            "sms_template": self._sms_template(
                self._username_var.get().strip(),
            ),
        }
        self.storage.mark_ad_setup(self.ticket["id"], info)
        if self.on_completed:
            self.after(0, self.on_completed)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _toggle_pwd_visibility(self):
        self._pwd_visible = not self._pwd_visible
        self._pwd_entry.config(show="" if self._pwd_visible else "*")
        self._show_btn.config(text="Hide" if self._pwd_visible else "Show")

    def _copy(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_sensitive(self, text: str):
        """Copy text and schedule an automatic clipboard clear after 30 s."""
        self._copy(text)
        self.after(
            _CLIPBOARD_CLEAR_MS,
            lambda expected=text: self._clear_clipboard_if_unchanged(expected),
        )

    def _clear_clipboard_if_unchanged(self, expected: str):
        try:
            current = self.clipboard_get()
        except tk.TclError:
            return
        if current == expected:
            self.clipboard_clear()
            self.clipboard_append("")

    def _copy_sms_sensitive(self):
        username = self._username_var.get()
        self._copy_sensitive(self._sms_template(username))

    def _write_audit_log(self, status: str, output: str):
        try:
            from pathlib import Path
            log_dir = Path.home() / ".jira-reminders"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "ad_audit.log"
            entry = (
                f"\n{'='*60}\n"
                f"Timestamp : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Ticket    : {self.ticket.get('key', '')}  {self.ticket.get('name', '')}\n"
                f"Scenario  : {self._scenario}\n"
                f"Account   : {self._username_var.get().strip()}\n"
                f"Email     : {self._email_var.get().strip()}\n"
                f"OU        : {self._ou_var.get().strip()}\n"
                f"Status    : {status}\n"
                f"Output:\n{output}\n"
            )
            with log_file.open("a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    @staticmethod
    def _set_text(widget: tk.Text, text: str):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")
