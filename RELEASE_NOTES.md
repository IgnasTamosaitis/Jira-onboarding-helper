# Release Notes

---

## v1.6.0 - Girteka Dedicated and safer onboarding

Released 4 September 2026.

- Migrated TNDM onboarding to the standard Girteka email format and
  `@girteka.eu` domain; both the old `TNDM` names and `Girteka Dedicated` are
  recognised in joiner and mover tickets
- Added targeted Girteka email/proxy alignment for movers into Girteka Dedicated
  and cleanup of any remaining `@tndmtrucking.com` proxy addresses
- Refined the onboarding checklist to five key tasks: removed Hardware
  preparation and SIM assignment, added AX user relations assignment, and
  preserved applicable completion state from older checklist layouts
- Removed the standalone hardware deployment workflow; Snipe-IT integration is
  now read-only and limited to showing assigned assets
- Replaced the fixed onboarding password with a fresh random password per AD
  setup and moved completed handoff passwords out of `tasks.json` into Windows
  Credential Manager

---

## v1.5.3 - Manual AD account fallback

Released 21 August 2026.

- New-joiner tickets now read Jira's **Person id external** field when SAP
  SuccessFactors has not provisioned an AD account
- The Jira value is validated through an exact AD `SamAccountName` lookup before
  the account can be selected
- Existing SuccessFactors and rejoiner account-selection paths retain priority,
  and disabled non-SF accounts continue through the existing rejoiner safeguards

---

## v1.5.2 - Reliable automatic updates

Released 17 August 2026.

- Update checks now use GitHub's public latest-release redirect instead of the
  rate-limited API, avoiding false results for colleagues sharing a corporate
  public IP address
- Failed update checks now display a clear error instead of claiming that the
  locally installed version is the latest
- Releases are published only after their MSI is ready, preventing installed
  copies from discovering an incomplete release
- The updater now uses the repository's canonical GitHub address

---

## v1.5.1 - Large AD script execution fix

Released 17 August 2026.

- Large new-joiner, rejoiner, and mover PowerShell scripts now execute from a
  temporary UTF-8 `.ps1` file instead of being placed directly on the Windows
  command line, preventing `[WinError 206]` for users with many AD groups
- Temporary scripts are removed after successful execution, failures, and timeouts

---

## v1.5.0 - Safer account and group handling

Released 17 August 2026.

- Mover preflight now provides an explicit account selector when the employee,
  Buddy, or manager resolves to multiple enabled AD accounts
- Audit-confirmed permission-controlled memberships (`Disable_USB`,
  `VPN_IT_integracijos`, and `GrayList_WillGrow Users`) are now manual, and
  unexpected group permission failures no longer prevent the remaining mover
  changes from running; unresolved groups are reported and keep the run incomplete
- Joiner, rejoiner, and mover group operations now resolve exact group names to
  Distinguished Names, supporting names that contain characters such as `/` and `=`
- Protected and redundant groups are left unchanged during mover reconciliation,
  preventing ACL-protected groups such as `RDS-Disabled` from aborting the run
- The confirmed GBS mapping now retains the full Tbilisi campus address, postal
  code `0162`, and `extensionAttribute15` value `SF`

---

## v1.4.0 - Employee movers

- Added a dedicated Movers tab for Jira `Employee moving` tickets
- Added Buddy/Axapta-rights reconciliation with visible follow-up warnings
- Added a mover-specific AD workflow for exact group replacement, buddy OU and
  Department, Jira title/company/manager, and mapped company addresses
- Added manager cross-checking, restricted-group enforcement, pre-apply drift
  detection, PowerShell preview, audit logging, and final-state verification
- Added mover reminders and inclusion in the morning summary
- Aligned the Movers workspace with the New Joiners layout and components,
  while retaining cached comment loading and the smoother tabbed AD review wizard

---

## v1.3.1 - AD safeguards and team setup guide

- Added Power BI Pro to the restricted AD groups that require explicit review
- Added a complete team installation and first-time setup knowledge base in Markdown and Word formats
- Added hourly update checks so already-running tray sessions prompt when a newer installer is published
- Updated installation guidance with direct installer links, classic Jira API token requirements, and unsigned-installer safety notes

---

## v1.3.0 - Multi-location onboarding and Windows installer

- Added office-first onboarding detection for Vilnius, Šiauliai, Poland, and GBS
- Added the confirmed Šiauliai Campus address and all known Šiauliai company aliases
- Added all known Polish company aliases without assuming that every Polish company uses the Sady address
- Preserved SuccessFactors street, city, and postal values for Polish companies whose exact campus address is not confirmed
- Added an AD wizard warning when Jira Office Location and company indicate different sites
- Added support for Jira manager values supplied as email addresses
- Kept the default Jira query scoped to `assignee = currentUser()`
- Added automated tests for company aliases, Jira office values, address safety, and generated AD scripts
- Added a polished per-user MSI that bundles Python and all application dependencies
- Added automatic Desktop, Start menu, and Windows Startup shortcuts
- The MSI launches the app after installation and opens first-time configuration automatically
- Simplified first-time setup to request only the user's email and API tokens; managed Jira fields and the current-user JQL remain under Advanced settings
- Installed builds now update through MSI release assets and uninstall cleanly through Windows Installer

---

## v1.2.8 - Remove Leavers Workspace

This release removes the app-side leaver workflow now that offboarding automation runs directly from Jira tickets.

- Removed the Leavers tab, leaver polling, return-act generation, accountants automation, and app-side AD/Snipe-IT offboarding actions
- Kept Snipe-IT read-only asset visibility for new joiner onboarding
- Removed leaver document templates and the unused `python-docx` dependency
- Updated settings and documentation to reflect the joiner-only workflow

---

## v1.2.5 - AD Rejoiner Conflict Fixes

This release fixes the AD Setup rejoiner-dual flow when SuccessFactors dummy accounts have AD conflict duplicates.

---

### AD Setup fixes

- Rejoiner-dual scripts now use the SF dummy account Distinguished Name, avoiding ambiguous `SamAccountName` lookups when AD contains `CNF` conflict objects
- The AD account search parser now ignores `CNF` conflict objects instead of hiding the real account behind a duplicate username
- **Apply changes** and **Copy review** now use the script currently visible in the review box, so manual review edits are respected

### Included since v1.2.4

- Added leaver offboarding and Snipe-IT asset panels

---

## v1.2.2 - AD Setup Safeguards and Leaver Cleanup

This release improves repeatable AD onboarding work, adds stronger safeguards around group assignment, and cleans up the leaver workflow and return-act output.

---

### AD Setup improvements

- The **AD Setup** button now stays available after a completed run so the same ticket can be reopened and rerun when needed
- **Rejoiner (single)** now supports a single existing non-SF account whether it is disabled or already active
- The rejoiner-single flow now skips OU move and enable steps when they are already satisfied instead of falling into manual-review state
- Jira manager values such as `Hiring Manager: ...` are normalized before AD lookup

### Password reset hardening

- Password resets are now pinned to one writable domain controller for the full run
- The script now unlocks the account when needed after resetting the password
- The new password is validated immediately after reset before the run reports success
- Verification output now includes `LockedOut`, `PasswordLastSet`, and `UserPrincipalName`

### Group-copy safeguards

- Buddy-group copying now blocks a curated list of approval-only groups by exact name
- Blocked approval-only groups are shown in **red** and cannot be selected or added
- Redundant groups such as `Teams VLS access policy applied` and `RDS-Disabled` are shown in **gray**, labeled as redundant, and are never added
- Group blocking is enforced both in the UI and when building the final AD script input

### Leavers updates

- Closed, resolved, completed, declined, cancelled, rejected, withdrawn, and past-date leaver tickets no longer remain visible in the leaver list
- The leaver detail pane now resets cleanly if the selected ticket disappears after refresh
- Generated return acts now fill asset-table defaults with `+` under **Būklė** and `Def. nėra` under **Pastebėti defektai**

---

## v1.2.1 - Windows Installer and Launcher Fixes

This release fixes the packaging issues in `v1.2.0` that could prevent a fresh GitHub download from installing or launching correctly on another Windows machine.

---

### Installer fixes

- `setup.bat` now accepts both `python` and the Windows `py -3` launcher
- Python detection now verifies that the installed version is 3.11 or later
- Dependency installation now uses `python -m pip` or `py -3 -m pip` instead of relying on a standalone `pip` command being on `PATH`

### Launcher fixes

- Fixed `start_reminders.vbs` so it no longer contains an absolute path from the release author's machine
- The launcher now resolves `app.py` relative to its own folder, making downloaded release folders portable
- Startup now falls back across `pyw`, `py`, `pythonw`, and `python` so the app can launch on more Windows setups

### Updater fixes

- The auto-updater now installs dependencies through the currently running Python interpreter instead of calling bare `pip`

### Notes

- If Python was installed just before running setup, reopening the terminal or Explorer window may still be necessary so the new launcher/path entries are visible

---

## v1.1.0 — Leavers Workflow, Snipe-IT Integration, and UI Polish

This release expands the app from a joiner-focused workflow into a full ITSD helper for both onboarding and offboarding. It adds a dedicated Leavers workspace, Jira automation support, Snipe-IT integration, document generation, and a broader UI cleanup pass.

---

### Leavers workflow

- Added a dedicated **Leavers** tab in the main window
- Added polling and manual refresh support for leaver tickets
- Added per-ticket leaver detail view with date/status metadata, notes, comments, and offboarding actions
- Added leaver-specific Jira JQL configuration in Settings

### Jira automation

- Added support for Jira manual automation rules through the internal automation API
- Added the **Add accountants** action for the rule `Add accountants for deduction | LT Group 1`
- Fixed rule triggering to use the correct `idUuid` invocation identifier from the Jira search response
- Updated UI text so this action is clearly documented as a Jira automation trigger, not a workflow transition

### Snipe-IT integration

- Added `snipeit_client.py`
- Added Snipe-IT URL and token fields to Settings
- Added secure token storage for Snipe-IT in Windows Credential Manager alongside the Jira token
- Added user lookup, user-details lookup, and assigned-asset lookup in Snipe-IT
- Added laptop auto-detection for leaver buyout flow using category/model scoring
- Added reusable buyout comment autofill and direct posting using Snipe-IT laptop model and serial number

### Return act generation

- Added `leaver_document.py`
- Added a tracked Word template under `templates/leaver_return_template.docx`
- Added **Generate return act** in the leaver view
- The generated document fills:
  - leaver name
  - leaver role
  - return date
  - receiver name from the Jira assignee
  - location from Jira/Snipe-IT data
  - employee contact email
  - Jira ticket key
  - equipment rows from assigned Snipe-IT assets when available
- Added `python-docx` dependency
- Added `generated_docs/` to `.gitignore`

### UI polish

- Standardised bottom-bar action button widths for cleaner alignment
- Reworked notes/comment text boxes with a more consistent visual style
- Increased text area heights to reduce cramped editing
- Improved mousewheel handling so scrolling works properly while the cursor is over text boxes and hands off to the outer panel at the bounds
- Cleaned up loading/status text in the detail views

### Jira data model changes

- Added leaver ticket fetching in `jira_client.py`
- Included assignee and office data in leaver ticket payloads so the receiver and document location can be filled automatically

### Notes

- Return-act generation is resilient: if Snipe-IT returns no assigned assets, the document is still created and the user gets a warning instead of a hard failure

---

## v1.0.2 — Hardened & Complete: New Joiner Onboarding

This release completes the New Joiner onboarding workflow and hardens the application for production use in a corporate environment. All known security gaps have been addressed and UI responsiveness has been significantly improved.

---

### Security

**Credential protection**
The Jira API token is no longer stored in `config.json`. It is now saved in **Windows Credential Manager** and never written to disk in plaintext. Existing installations migrate automatically on first launch — no manual steps required.

**File permissions**
`config.json` and `tasks.json` are now restricted to the current Windows user only using `icacls /inheritance:r` the first time each file is written.

**Password handling in AD Setup**
- The wizard now opens with a **freshly generated random password** — the old hardcoded placeholder `Initial123` has been removed.
- The password field is **masked by default**. A **Show / Hide** toggle reveals it when needed.
- Copying the password, the SMS template, or the generated PowerShell script now **automatically clears the clipboard after 30 seconds**, so sensitive content does not persist in clipboard history.

**PowerShell execution policy**
Generated scripts now run with `-ExecutionPolicy RemoteSigned` instead of `-ExecutionPolicy Bypass`, respecting the domain's module signing policy.

**Input validation**
The AD Setup wizard now validates the email address format and the target OU Distinguished Name before generating or running any script. Invalid input produces a clear error message rather than a broken PowerShell script.

**Audit log**
Every AD setup run is now recorded to `~/.jira-reminders/ad_audit.log` with a full timestamp, ticket details, account, email, target OU, run status, and complete PowerShell output. The log is append-only.

---

### Performance

**Instant ticket navigation**
Switching between tickets is now immediate. Two separate root causes had been blocking the UI thread on every ticket click:

1. Buddy resolution (which runs PowerShell to look up AD accounts) was executing synchronously on the main thread when comments were already cached. It now always runs in a background thread.
2. The new file-permission hardening (`icacls`) was running synchronously on every disk write, including every notes auto-save. It now runs in a background daemon thread and only once per file path.

---

### Dependencies

`keyring>=24.0` added — provides the Windows Credential Manager integration. Installed automatically by `setup.bat`.

---

## v1.0.1

- Improved launch flow and AD loading UX
- Buddy chooser now keeps older buddy mentions visible alongside the latest
- Always prefers the latest buddy comment for the primary suggestion
- Jira comments refresh in the background even when a cached copy is available
- Fixed buddy rescanning and Lithuanian name handling (ą, č, ę, ė, etc.)
- Support for multiple buddy candidates from Jira comments
- Disabled buddy detection and follow-up flow

## v1.0

Initial release. Full New Joiner and Rejoiner onboarding workflow:

- System tray app with Jira polling and Windows notifications
- Onboarding checklist, notes, and AD setup summary per ticket
- Buddy detection from Jira comments with AD resolution
- Ask reporter flow with ADF mention support
- AD Setup wizard: scenario detection (new joiner / rejoiner dual / rejoiner single), OU move, group assignment, email and attribute configuration, password reset, post-run verification, stale group cleanup
- Morning summary notifications
- Local JSON storage with backup support
