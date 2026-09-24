# Jira Reminders

Jira Reminders is a Windows tray application for Girteka IT Service Desk. It
keeps assigned new-joiner and employee-mover tickets in one place, provides
guided Active Directory workflows, and sends reminders before effective dates.

Current release: **v1.7.0** · [Download the latest Windows
installer](https://github.com/IgnasTamosaitis/Jira-onboarding-helper/releases/latest)
· [Team setup and operating guide](docs/Jira-Reminders-KB.md) · [Release
notes](RELEASE_NOTES.md)

## Supported workflows

### New joiners and rejoiners

- Shows assigned onboarding tickets, start dates, ticket details, Jira comments,
  notes, buddy suggestions, and checklist progress.
- Detects new-joiner, dual-account rejoiner, and single-account rejoiner AD
  scenarios. Accounts are provisioned by SuccessFactors; the app does not create
  AD accounts from scratch.
- Builds a reviewable AD plan for the account, OU, permitted direct groups,
  email/proxy addresses, manager, location, and supported extension attributes.
- Applies the reviewed plan through Active Directory PowerShell and verifies the
  resulting account state before recording completion.
- Preserves populated title, description, and department from the SF account.
  The selected buddy only fills missing role fields; the source of each field
  is logged. Single-account rejoiners without a new SF account retain the
  existing Jira-role/buddy-department rule. Missing required data blocks setup.
- Verifies attributes, selected permitted groups, mail routing, and account
  state using the same administrator session and domain controller. Missing
  required values or non-group mismatches leave setup incomplete. Failed group
  additions and missing memberships remain visible as non-blocking advisories.
- Transfers reporting links from a temporary SF duplicate to the restored
  account before deleting the duplicate, and only deletes after verification.
  Selecting a buddy for access/role copying does not reassign that buddy's team.
- Can post the predefined "Ask reporter" comment to Jira when access-template
  information is missing.
- Shows assets already assigned to the employee in Snipe-IT.
- When the tenant access-card flow is configured, automatically reserves the
  joiner's unique `LT` card ID in the shared Excel registry. Rejoiners reuse one
  unambiguous historical ID; uncertain matches stop for manual review.

The onboarding checklist contains only these five tasks:

1. Active Directory account setup
2. Axapta account import/creation
3. AX user relations assignment
4. Assign hardware & licenses in Snipe-IT
5. Physical access card creation

### Employee movers

- Shows assigned `Employee moving` tickets and their effective dates.
- Resolves the employee, buddy, and manager to enabled AD accounts; ambiguous
  matches require an explicit account choice.
- Previews OU, organisation fields, address, manager, and permitted direct-group
  changes before applying them.
- Verifies the final AD state. Unknown addresses, disabled accounts, manager
  mismatches, or changes after preview block automatic completion.
- Leaves approval-controlled and redundant groups unchanged and reports manual
  follow-up items.

The mover workflow does not reset passwords, change UPNs, perform Axapta work,
or change Jira ticket status.

### Girteka Dedicated migration

Jira may still identify the company as `TNDM`, `TNDM Trucking`, or `Girteka
Dedicated`. New-joiner and mover workflows recognise all three values as the
same migrated company:

- new joiners use the standard Girteka attributes and
  `First.Last@girteka.eu` address format;
- movers into that company keep the existing email local part and move it to
  `@girteka.eu`;
- `EmailAddress`, `targetAddress`, and the primary SMTP proxy are aligned; and
- any remaining `@tndmtrucking.com` proxy is removed.

There is no active `@tndmtrucking.com` email path in the application.

### Access-card images

The **Card printer** tab is available only when the signed-in Windows user's
Active Directory **Office** is recognised as Vilnius (including Girteka Park).
It stays hidden for GBS, Poznań, Šiauliai, and unknown or unavailable offices.
This uses the IT team member's office, independently of the tickets they handle.
The check runs in the background at startup and on each Jira refresh; reconnect
to the corporate network/VPN and select **Refresh** if the tab is missing.

Choose an assigned joiner or rejoiner in the tab, or select them in **New joiners**.
After the Excel registry confirms the record, the employee name and numeric card
ID fill automatically; the template supplies the `LT` prefix. New joiners claim
the next ID from LT5053 onward and populate the matching Excel row. Rejoiners
reuse one matching historical record and its name spelling without changing Excel.
Movers are excluded. Older IDs with blank employee names are not recycled.

**Export PNG** saves the preview at the template's full resolution for use in
card-printing software. The fields are read-only, and export stays disabled until
a valid registry record is confirmed. Missing or ambiguous rejoiner matches
require manual review. The tenant flow must be deployed and configured using the
[OneDrive sync guide](docs/ACCESS_CARD_ONEDRIVE_FLOW.md). This version uses your
existing OneDrive sync and Standard Power Automate connectors, with no desktop
Entra registration. Choose the synced queue folder in Settings; the app writes
unique request files and checks pending results every 15 seconds. Card
allocation still happens only in the serialized workbook script. The
[direct-list](docs/ACCESS_CARD_LIST_FLOW.md) and [Premium HTTP](docs/ACCESS_CARD_FLOW.md)
connections remain available for separately provisioned deployments.

## Safety boundaries

- Snipe-IT access is **read-only**. The app only finds users and displays their
  assigned assets; it does not deploy, check out, update, or delete inventory.
- AD changes require a reviewed plan and confirmation. Results are verified;
  joiner and rejoiner executions are also written to the local AD audit log.
- Opening a joiner ticket checks its recorded setup against live AD, then
  rechecks every minute while open. **Check AD** refreshes immediately. The
  summary and AD checklist reflect enabled/locked/password state, expected
  groups, OU, email routing, attributes, and reporting links. Checks only read AD.
  Earlier setups are recovered by Jira key from `ad_audit.log`; incomplete old
  logs are labelled partially verified rather than treated as a full match.
  Group failures and missing memberships are advisory and never block completion.
  Previously completed setups can remain completed when available live checks
  pass, even if older logs lack a full baseline; that limitation stays visible.
  Unavailable AD and non-group mismatches clear the completion checkmark without
  deleting setup history. New setups save the full baseline for future comparisons.
- Restricted AD groups are never copied or removed automatically.
- Jira writes are limited to the explicit **Ask reporter** action.
- Every new-joiner and rejoiner AD setup sets the password to `Welcome123`.
  It is fixed and masked in the wizard, never saved in `tasks.json`, and stored
  for handoff in Windows Credential Manager. Sensitive clipboard copies clear
  after 30 seconds.
- Jira and Snipe-IT API tokens are stored in Windows Credential Manager, not in
  repository files or local JSON configuration.
- Access-card numbers are allocated only by one serialized Power Automate flow
  connected to the shared workbook. OneDrive uses the operator's existing sync
  sign-in; the optional list and HTTP connections use Microsoft sign-in with a
  Windows DPAPI-encrypted token cache. The desktop never calculates a number
  locally and accepts only a verified `LT1`–`LT9999` response.

## Install and configure

Requirements:

- Windows 10 or 11
- access to Girteka Jira and, for AD work, the corporate network/VPN and domain
- a classic Jira API token
- an optional Snipe-IT API token for assigned-asset visibility
- for automatic access-card reservation, access to the shared OneDrive queue
  folder and a deployed workbook flow; only the optional list/HTTP connections
  require an Entra public-client registration

Download the MSI from the [latest GitHub
release](https://github.com/IgnasTamosaitis/Jira-onboarding-helper/releases/latest)
and run it. The per-user installer includes Python, creates Start menu, Desktop,
and Startup shortcuts, and normally does not require local administrator rights.
If Windows reports an unknown publisher, install only an artifact obtained from
the official release page and follow company policy.

At first launch, enter your Atlassian email, Jira token, optional Snipe-IT token,
and reminder timing. Use **Test Jira connection**, then **Save & start**. Managed
URLs, Jira queries, field IDs, and the polling interval are under **Advanced
settings** and normally should not be changed.

Access-card automation must first be deployed and acceptance-tested using the
[OneDrive sync guide](docs/ACCESS_CARD_ONEDRIVE_FLOW.md). Under **Advanced
settings**, select **Choose queue folder…**, choose the synced `AccessCardQueue`
folder containing `Requests`, `Results`, `Processed`, and `Staging`, then save
and refresh. No additional app sign-in is needed for this connection. For
operators whose AD Office is recognised as Vilnius, assigned joiners and
rejoiners are submitted after Jira refreshes, and pending results are checked
every 15 seconds. The installer does not deploy the tenant flow.

Separately provisioned [SharePoint list](docs/ACCESS_CARD_LIST_FLOW.md) and
[Premium HTTP](docs/ACCESS_CARD_FLOW.md) connections are available under **Other
access-card connection options**. Use only one writer flow for the workbook.

The app polls Jira every 30 minutes by default. It sends individual reminders
within the configured lead time and a 09:00 summary for joiners and movers due in
the next seven days. Settings, manual refresh, update checks, and uninstall are
available from the tray menu.

## Local data

Runtime data is stored outside the repository in:

```text
%USERPROFILE%\.jira-reminders\
```

| Item | Purpose |
|---|---|
| `config.json` | Non-secret application settings |
| `tasks.json` | Checklist state, notes, buddy choices, and non-secret AD results |
| `power-automate-token-cache.bin` | DPAPI-encrypted Microsoft sign-in cache |
| `sharepoint-token-cache.bin` | DPAPI-encrypted sign-in cache for the optional list connection |
| `ad_audit.log` | Timestamped AD execution results |
| `backups\` | User-created task and note snapshots |
| Windows Credential Manager | Jira/Snipe-IT tokens and AD handoff passwords |

`config.json` and `tasks.json` are restricted to the signed-in Windows user when
written. Uninstalling the application keeps this data so a later installation
can restore the user's working state.

## Development

Use Python 3.11 or later on Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Run the test suite before committing:

```powershell
python -m unittest discover -s tests -v
```

The workbook allocator also has TypeScript checks and Node.js tests (Node.js
22 or later):

```powershell
npm.cmd install --prefix .tools/typescript-check --no-save --package-lock=false typescript@5.9.2
node .tools/typescript-check/node_modules/typescript/bin/tsc --noEmit --strict --target ES2020 tests/office_scripts.d.ts power_automate/ReserveAccessCard.ts
node --test tests/test_access_card_script.cjs
```

AD execution tests use a simulated directory; they do not change live AD. Local
tests also do not verify tenant flow deployment or live OneDrive delivery.

Build and release instructions are in [BUILDING.md](BUILDING.md). The installer
also requires the .NET 8 SDK; build dependencies are installed by the packaging
script. Generated builds, caches, logs, local environments, credential files,
and private-key formats are excluded by `.gitignore`.

For field mappings, supported offices, operational safeguards, and
troubleshooting, use the [team KB](docs/Jira-Reminders-KB.md) rather than
duplicating those changing details here.
