# Jira Reminders

Jira Reminders is a Windows tray application for Girteka IT Service Desk. It
keeps assigned new-joiner and employee-mover tickets in one place, provides
guided Active Directory workflows, and sends reminders before effective dates.

Current release: **v1.6.0** · [Download the latest Windows
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
- Can post the predefined "Ask reporter" comment to Jira when access-template
  information is missing.
- Shows assets already assigned to the employee in Snipe-IT.

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

## Safety boundaries

- Snipe-IT access is **read-only**. The app only finds users and displays their
  assigned assets; it does not deploy, check out, update, or delete inventory.
- AD changes require a reviewed plan and confirmation. Results are verified;
  joiner and rejoiner executions are also written to the local AD audit log.
- Restricted AD groups are never copied or removed automatically.
- Jira writes are limited to the explicit **Ask reporter** action.
- A fresh random onboarding password is generated for each AD setup. It is
  masked in the wizard, never saved in `tasks.json`, and stored for handoff in
  Windows Credential Manager. Sensitive clipboard copies clear after 30 seconds.
- Jira and Snipe-IT API tokens are stored in Windows Credential Manager, not in
  repository files or local JSON configuration.

## Install and configure

Requirements:

- Windows 10 or 11
- access to Girteka Jira and, for AD work, the corporate network/VPN and domain
- a classic Jira API token
- an optional Snipe-IT API token for assigned-asset visibility

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

Build and release instructions are in [BUILDING.md](BUILDING.md). The installer
also requires the .NET 8 SDK; build dependencies are installed by the packaging
script. Generated builds, caches, logs, local environments, credential files,
and private-key formats are excluded by `.gitignore`.

For field mappings, supported offices, operational safeguards, and
troubleshooting, use the [team KB](docs/Jira-Reminders-KB.md) rather than
duplicating those changing details here.
