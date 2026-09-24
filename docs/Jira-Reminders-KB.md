# Jira Reminders — Installation and First-Time Setup

| Document information | Value |
|---|---|
| Audience | IT Service Desk team members handling employee onboarding |
| Supported locations | Vilnius, Šiauliai, Poznań / Poland, and GBS |
| Application version | 1.7.0 |
| Owner | IT Service Desk |
| Last updated | 24 September 2026 |
| Estimated setup time | 5–10 minutes |

## Purpose

Jira Reminders brings assigned employee-onboarding and employee-moving tickets,
reminders, the onboarding checklist, Active Directory setup, Jira comments,
and optional Snipe-IT asset information into one Windows application.

The application runs in the Windows system tray and starts automatically when
you sign in.

> **Ticket scope:** Jira Reminders displays onboarding and employee-moving
> tickets assigned to the signed-in Jira user. It does not display the entire
> location queue.

## Employee movers

Use the **Movers** tab for assigned Jira issues of type **Employee moving**.
Review the effective date, new job title, company, manager, Buddy, and Axapta
rights before selecting **Prepare AD move**.

If an employee, Buddy, or manager search finds multiple enabled AD accounts,
select the intended username in the mover window. The selector includes each
account's title and OU so service accounts such as 3CX accounts can be excluded.
The app rebuilds the complete preview after the selection.

The mover workflow changes only Active Directory. It aligns copyable direct
groups and the OU with the Buddy, uses the Buddy's Department, applies the Jira
new title to both Title and Description, and applies the Jira Company and
Manager. Address fields come from the same validated company/location mappings
used for onboarding. Password and UPN are preserved, as are email and proxy
addresses except for the Girteka Dedicated rule below. Jira ticket status
changes remain manual.

The exception is a target company named `TNDM`, `TNDM Trucking`, or `Girteka
Dedicated`. These Jira values all represent Girteka Dedicated. The mover keeps
the existing email local part but uses `@girteka.eu`; the workflow also aligns
`targetAddress` and the primary SMTP proxy and removes any retired
`@tndmtrucking.com` proxy. Password and UPN remain unchanged.

If Buddy and Axapta rights differ, Buddy remains the AD template and the app
shows the separate Axapta user as a follow-up. If Buddy is empty, Axapta rights
is used as the AD buddy. Multiple enabled accounts require explicit selection.
The app blocks a disabled account, unknown address, unacknowledged manager
mismatch, invalid OU, or stale preview. Completion is saved only after final AD
verification passes.

Permission-controlled memberships found in the audit history—including
`Disable_USB`, `VPN_IT_integracijos`, and `GrayList_WillGrow Users`—are excluded
from automatic copying and remain a manual follow-up. If AD denies another group
addition or removal, the mover workflow continues applying the remaining safe
changes, reports every failed membership in the execution output, and does not
record the move as complete.

## Before you start

You need:

- a Girteka Windows 10 or Windows 11 computer;
- access to the Girteka corporate network or VPN;
- an Atlassian account that can open the relevant Jira onboarding tickets;
- a Jira API token created for your own Atlassian account; and
- an authorised Snipe-IT API token only if you need assigned-asset visibility.

You do **not** need to install Python, download repository source files, run
`setup.bat`, or start a VBS file.

## 1. Download and install Jira Reminders

1. Open the official
   [Jira Reminders v1.7.0 release](https://github.com/IgnasTamosaitis/Jira-onboarding-helper/releases/tag/v1.7.0).
2. Download
   [Jira-Reminders-1.7.0.msi](https://github.com/IgnasTamosaitis/Jira-onboarding-helper/releases/download/v1.7.0/Jira-Reminders-1.7.0.msi).
3. Open the downloaded MSI.
4. Complete the Windows Installer process.
5. Wait for **Welcome to Jira Reminders** to open automatically.

The installer creates:

- a **Jira Reminders** Desktop shortcut;
- a **Jira Reminders** Start menu shortcut; and
- a Windows Startup shortcut so the app launches whenever you sign in.

> **Unknown publisher:** Version 1.7.0 is not Authenticode-signed, so Windows
> may show an **Unknown publisher** message. Only continue when the MSI was
> downloaded from the official GitHub release above. If company policy blocks
> it, contact the application owner instead of bypassing the policy.

## 2. Create your Jira API token

Jira Reminders uses a personal Jira API token instead of your password.

1. In the welcome screen, select **Create a Jira API token**. Alternatively,
   open
   [Atlassian API tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
   in your browser.
2. Sign in with the same Atlassian account you use for Girteka Jira.
3. Select **Create API token**.
4. Do **not** select **Create API token with scopes**. This app currently uses
   the direct Girteka Jira URL and therefore requires the classic/unscoped
   token option.
5. Give the token a clear name, for example `Jira Reminders - Work laptop`.
6. Select an expiration date allowed by company policy.
7. Select **Create**, then **Copy to clipboard**.
8. Return to Jira Reminders and paste the token into **Jira API token**.

> Atlassian shows the token only once. Do not send it in email, Teams, Jira
> comments, screenshots, or support requests.

API tokens expire. Atlassian currently allows an expiration period from 1 to
365 days and defaults new tokens to one year. Create a replacement token and
update Jira Reminders before or when the old token expires.

## 3. Complete first-time setup

Fill in the welcome screen:

| Field | What to enter |
|---|---|
| Atlassian email | Your own Atlassian account email. The app may pre-fill it from Windows; verify it is correct. |
| Jira API token | The personal classic/unscoped token created in the previous section. |
| Snipe-IT API token | Optional. Enter only a token you are authorised to use. Leave blank if you do not need asset visibility. |
| Notify this many days before a start date | Keep the default `3` unless your team uses a different reminder window. |

1. Select **Test Jira connection**.
2. Confirm the message says **Connected successfully** and shows your name.
3. Select **Save & start**.
4. The ticket window opens and the Jira Reminders icon appears in the Windows
   system tray.

### Advanced settings

The following managed values are already configured. Do not change them unless
the application owner instructs you to do so.

| Setting | Managed value |
|---|---|
| Jira URL | `https://girteka.atlassian.net` |
| Assigned onboarding tickets JQL | `assignee = currentUser() AND issuetype = "SF: Employee onboarding" AND status in (Open, "In Progress", Pending)` |
| Start date Jira field | `customfield_10980` |
| Snipe-IT URL | `https://inventory.girteka.eu` |
| Jira check interval | `30` minutes |

## 4. Confirm the installation

After saving:

1. Confirm the main Jira Reminders window opens.
2. Confirm the window shows onboarding tickets assigned to you.
3. Close the window and confirm the app remains available in the system tray.
4. Right-click the tray icon and select **Show tickets**.
5. Confirm the Desktop shortcut opens the application.

Seeing no tickets is not automatically an error. The default filter only
returns tickets that:

- are assigned to the signed-in Jira user;
- use issue type **SF: Employee onboarding**; and
- have status **Open**, **In Progress**, or **Pending**.

## Daily use

Right-click the Jira Reminders system-tray icon to access:

| Menu option | Action |
|---|---|
| Show tickets | Opens the main onboarding window. |
| Check now | Immediately refreshes your assigned Jira tickets. |
| Settings | Updates your email, tokens, or reminder preferences. |
| Check for updates | Checks the latest GitHub release. |
| Uninstall | Opens the standard Windows Installer removal flow. |
| Quit | Closes Jira Reminders until it is started again. |

The app automatically:

- checks Jira every 30 minutes by default;
- sends reminders for approaching start dates;
- sends a morning summary when relevant joiners are due within seven days; and
- checks GitHub releases for application updates.

## Joiner AD setup and completion

Review the plan in **AD Setup** before applying it with administrator credentials.
For new joiners and dual-account rejoiners, populated SuccessFactors title,
description, and department are preserved; the selected buddy supplies only
missing role fields. Required missing data blocks the run. Single-account
rejoiners retain the Jira-role/buddy-department rule.

Execution verifies the resulting account on the same domain controller before
recording completion. Denied group additions or missing memberships are shown
as advisories and require follow-up, but do not block joiner/rejoiner completion.
Other failed checks leave setup incomplete. When restoring an old account,
reporting links are transferred before the SF duplicate can be deleted.

Opening a ticket checks its setup against live AD. The app checks again every
minute while the ticket is open; **Check AD** runs an immediate check. A disabled
or locked account, changed attributes or mail routing, incomplete password state,
or lost reporting links clears the completion checkmark. An unavailable AD
connection also clears the checkmark without deleting setup history. Connect to
the corporate network/VPN and check again.

Older setup evidence is recovered from the local audit log. Previously completed
setups can remain complete when available checks pass, but the summary explicitly
shows when the old log lacks a full baseline. Review all group advisories and
verification limitations before handing the account over.

## Access-card printing

The **Card printer** tab appears only when the signed-in Windows operator's own
AD Office is recognised as Vilnius, including Girteka Park. Other or unavailable
offices keep the tab hidden. Connect to the corporate network/VPN and refresh
if the expected tab is missing.

The registry owner must deploy and acceptance-test the shared workbook flow
using the [OneDrive sync guide](https://github.com/IgnasTamosaitis/Jira-onboarding-helper/blob/v1.7.0/docs/ACCESS_CARD_ONEDRIVE_FLOW.md).
Then sync the shared `AccessCardQueue` folder to your PC and select it under
**Settings → Advanced settings → Choose queue folder…**. It must contain
`Requests`, `Results`, `Processed`, and `Staging`. This connection uses existing
OneDrive sign-in; no additional app registration or Microsoft sign-in is needed.
The installer does not deploy the flow.

After saving, refresh Jira and choose an assigned joiner or rejoiner in Card
printer. The app automatically requests the registry record and checks pending
results every 15 seconds. New joiners receive IDs from LT5053 onward; rejoiners
reuse one unambiguous historical record without changing Excel. Movers are
excluded. The workbook flow is the only card-number allocator.

The confirmed name and numeric ID fill automatically. Select **Export PNG** and
open the image in your card-printing software. Manual entry is unavailable, and
export stays disabled for pending, blocked, or ambiguous results. If a request
stays pending, check OneDrive sync and ask the registry owner to inspect the
flow run history. Follow the guide's recovery steps for manual-review results.

## Supported locations

Jira **Office Location** is the primary location signal. Company name is used
as a fallback. If they disagree, the AD setup wizard shows a warning and uses
Office Location for its proposed changes.

| Jira location | Handling |
|---|---|
| Girteka Park / Vilnius | Uses the confirmed Vilnius address and standard Lithuania settings. |
| Siauliai Campus / Šiauliai | Uses Pročiūnų g. 16, LT-77103 Šiauliai and standard Lithuania settings. |
| Tbilisi / GBS | Uses the configured Tbilisi address and GBS settings. |
| Poznańska 4, Sady / Girpoltrans | Uses the confirmed Sady address and Poland settings. |
| Other recognised Polish companies | Uses Poland defaults but preserves authoritative SuccessFactors street, city, and postal values when the exact campus address is not confirmed. |

Recognised Šiauliai companies include Mireli, Trasis, Girmeta, Termolita,
Girtrans, KLP Transport, Premium Trans, and TermoTrans.

Recognised Polish companies include Girpoltrans, TransEu Poland, Eupoltrans,
Scanpoltrans, Polservice, GoTrans, ME Trailers Poland, and ClassTrucks Poland.

Recognised Vilnius companies include Trucks Merchant, Willgrow, GCC, TNDM/TNDM
Trucking, Girteka Dedicated, Girteka Nordic, Girteka Transport, Girteka, Girteka
Group, Girteka Logistics, ME Trailers, and Girteka Cargo. Both the old and new
Dedicated names use Girteka attributes, the standard Girteka email format, and
the `@girteka.eu` domain during onboarding.

## Settings, data, and security

- Jira and Snipe-IT tokens are stored in **Windows Credential Manager**.
- Tokens are not written to `config.json`.
- Snipe-IT access is read-only and is used only to display assets already
  assigned to a joiner.
- Each joiner/rejoiner AD setup uses the required fixed password `Welcome123`.
  Completed handoff passwords are stored in Windows Credential Manager, not in
  `tasks.json` or its new backups.
- Passwords are masked in the AD wizard and sensitive clipboard copies clear
  automatically after 30 seconds.
- Personal settings, checklist progress, notes, backups, and the AD audit log
  are stored under `%USERPROFILE%\.jira-reminders`.
- The optional SharePoint-list and HTTP card connections store Microsoft tokens
  in Windows DPAPI-encrypted caches. The OneDrive connection uses existing sync.
- Uninstalling the app keeps this personal data so it is available after a
  reinstall.
- Never include API tokens, passwords, or generated AD scripts in screenshots
  or support requests.

## Updating Jira Reminders

When a newer release is available, the app displays an update prompt.

1. Select **Install now**.
2. Wait while Windows Installer updates the application.
3. Jira Reminders restarts automatically.

You can also right-click the tray icon and select **Check for updates**.

## Troubleshooting

| Problem | What to do |
|---|---|
| The MSI is blocked | Verify it came from the official release link. If company policy blocks unsigned applications, contact the application owner; do not bypass the policy. |
| Jira connection fails | Confirm the email matches the account that created the token. Create a new classic/unscoped API token and try again. |
| Connection worked before but now fails | The API token may have expired or been revoked. Create a replacement token, then open **Tray icon → Settings** and save it. |
| No tickets are displayed | Select **Check now**. Confirm the ticket is assigned to you and has the correct issue type and one of the supported statuses. |
| Another colleague sees the ticket but I do not | Jira Reminders intentionally uses `assignee = currentUser()`. Reassign the Jira ticket if you are responsible for it. |
| The app appears to have closed | Check the hidden system-tray icons. Select **Show tickets**, or use the Desktop shortcut. |
| Snipe-IT assets are not displayed | Open **Settings** and confirm an authorised Snipe-IT API token is present. Jira functionality works without it. |
| Notifications do not appear | Confirm Windows notifications are enabled for Jira Reminders and that Focus/Do Not Disturb is not suppressing them. |
| A location or address looks wrong | Compare the ticket's **Office Location** and company fields. Do not apply AD changes until the mismatch is reviewed. |
| A previously completed AD task is unchecked | Open the ticket and select **Check AD** while connected to the corporate network/VPN. Review the live mismatch or unavailable-AD message; saved history is retained. |
| Card printer is missing | The tab requires the operator's own AD Office to resolve to Vilnius. Reconnect to the corporate network/VPN and refresh. |
| Card export is disabled | Wait for a matching confirmed registry result. Check OneDrive sync and the flow run history, or ask the registry owner to resolve the displayed manual-review issue. |
| Reinstalling did not show first-time setup | Existing settings are intentionally retained in `%USERPROFILE%\.jira-reminders`. Open **Tray icon → Settings** to update them. |

## Uninstalling

Use either:

- **Tray icon → Uninstall**; or
- **Windows Settings → Apps → Installed apps → Jira Reminders → Uninstall**.

The app and its shortcuts are removed. Personal settings and checklist history
remain in `%USERPROFILE%\.jira-reminders`.

## Information to include when requesting support

Provide:

- the Jira ticket key;
- the Jira **Office Location** and company values;
- the Jira Reminders version;
- the exact error text;
- whether you are connected to the corporate network or VPN; and
- a screenshot only after confirming it contains no API token, password, or
  generated AD script.

Never provide your Jira or Snipe-IT API token.

## References

- [Jira Reminders v1.7.0 release](https://github.com/IgnasTamosaitis/Jira-onboarding-helper/releases/tag/v1.7.0)
- [Atlassian — Manage API tokens for your account](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
