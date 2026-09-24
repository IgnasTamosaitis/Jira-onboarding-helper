# Access-card registry flow

**For the current deployment, use the [OneDrive sync guide](ACCESS_CARD_ONEDRIVE_FLOW.md):**
it needs neither Premium HTTP actions nor a desktop Entra registration.

## Licensing prerequisite — check before starting

The HTTP-triggered flow described below uses Premium capabilities: **When an
HTTP request is received** and **Response**. It requires appropriate Power
Automate Premium licensing; Microsoft 365 access alone does not cover this
HTTP design. If Flow checker reports a Premium requirement, stop before
continuing this setup.

An alternative without these Premium actions is a SharePoint request list:
**When an item is created → Run script → Update item**. SharePoint and Excel
Online (Business) are Standard connectors, and Office Scripts in Power Automate
still require a qualifying Microsoft 365 business license and tenant access.
For that alternative, use the [Standard connector setup guide](ACCESS_CARD_LIST_FLOW.md).
It covers the request list, request/result columns, the automated flow,
and the desktop app's Microsoft Graph sign-in. Keep the workbook and
`ReserveAccessCard` script for either design. The remaining sections below
describe the Premium HTTP version.

References: [SharePoint connector](https://learn.microsoft.com/en-us/connectors/sharepointonline/),
[Excel Online (Business) connector](https://learn.microsoft.com/en-us/connectors/excelonlinebusiness/),
and [Office Scripts requirements](https://learn.microsoft.com/en-us/office/dev/scripts/testing/platform-limits).

This flow is the single authority for access-card IDs. Workstations never
calculate the next ID and never write directly to the workbook. Power Automate
serializes all requests, so two colleagues processing joiners at the same time
cannot reserve the same number.

## 1. Prepare the workbook

The supplied `sąrašas Laisvės pr 36.xlsx` workbook is in OneDrive for Business.
Use its **AccessCards** worksheet. An Excel table is not required; the script
reads the existing columns A:E directly and ignores formatting on unused rows.

Keep these existing column names in row 1, in this order (A:E):

| CardID | EmployeeName | JiraKey | Status | ReservedAt |
| --- | --- | --- | --- | --- |
| LT4994 | Example Employee |  | Active |  |

Existing records may leave `JiraKey` and `ReservedAt` blank. Keep disabled and
active records in the same worksheet because all historical IDs remain reserved.
Every listed ID must be canonical, from `LT1` through `LT9999`;
leading zeroes such as `LT0143` are rejected. Prefilled IDs with empty names are
supported. New allocations start at **LT5053** and continue beyond the latest
assigned ID. Historical gaps, including LT869 and LT5001, stay untouched.

The supplied workbook has a historical duplicate `LT1950` in rows 1951 and
4854. Requests involving either record require manual review and cannot print;
unrelated requests continue normally. The script never corrects existing rows
or allocates an ID listed more than once. The registry owner should resolve
historical duplicates using the physical-card records.

A new joiner fills the next eligible prefilled row: column A keeps the LT ID,
column B receives the Jira first and last name, and C:E record the Jira key,
reservation status and timestamp. If the prefilled sequence is exhausted, the
script appends the next ID. A rejoiner with one matching name reuses that row's
ID and name spelling without changing any cell, including C:E.

Do not let people manually add new IDs while the flow is operating. Status
changes and corrections should be made when the flow is idle because the Excel
Online connector does not support simultaneous manual and automated edits.

For production continuity, prefer a team-owned SharePoint document library and
the **Run script from SharePoint library** action over a workbook and script
owned only by one employee. If OneDrive remains the approved location, document
the workbook, script, flow, and Excel connection owners, add an operational
co-owner, retain version history, and include ownership transfer in leaver
procedures. Sharing the workbook alone does not transfer the flow connection.

## 2. Install the Office Script

1. Open the workbook in Excel for the web.
2. Go to **Automate** > **New Script**.
3. Replace the sample code with
   [`power_automate/ReserveAccessCard.ts`](../power_automate/ReserveAccessCard.ts).
4. Save it as `ReserveAccessCard` under the flow owner's account.

The script validates the whole registry before allocating anything. It blocks
malformed IDs and allocation or printing of a duplicated ID, returns an existing reservation for repeat calls
with the same Jira key, reuses exactly one name match for a rejoiner without
changing Excel, and sends ambiguous cases to manual review.

## 3. Create the authenticated flow

Create a cloud flow with these actions:

1. **When an HTTP request is received**
2. **Run script** (Excel Online (Business))
3. **Response**

Use this request schema:

```json
{
  "type": "object",
  "properties": {
    "jiraKey": { "type": "string" },
    "fullName": { "type": "string" },
    "joinerType": {
      "type": "string",
      "enum": ["new_joiner", "rejoiner"]
    }
  },
  "required": ["jiraKey", "fullName", "joinerType"],
  "additionalProperties": false
}
```

In the trigger settings:

- Set **Who can trigger the flow?** to **Specific users in my tenant** and add
  only the colleagues/service identities that run the desktop app. Never use
  the public/anonymous option.
- Turn **Concurrency Control** on and set **Degree of Parallelism** to `1`.
- Enable **Secure inputs** and **Secure outputs** on the trigger and relevant
  actions where company policy requires joiner data to be hidden from flow run
  history. Limit co-owner access to the flow and its connections.

Microsoft is still rolling out authenticated HTTP triggers across regions. If
the **Specific users in my tenant** option is not available in this environment,
do not fall back to **Anyone**. Leave the desktop integration disabled until the
authenticated option is available or an approved Entra-protected API gateway is
placed in front of the flow.

Configure **Run script** (Excel Online (Business)) with:

- Location: **OneDrive for Business**, using the account with access to the workbook;
- Document library: **OneDrive**;
- File: select **Documents/sąrašas Laisvės pr 36.xlsx** using the file picker;
- Script: **ReserveAccessCard**.

Verify the selected file contains the **AccessCards** worksheet and the headers
above. The other **korteles** worksheet is not used or changed.
Map `jiraKey`, `fullName`, and `joinerType` from the trigger body to the script
parameters.

Configure **Response** with status `200`, header
`Content-Type: application/json`, and this expression as its body (adjust the
action name if Power Automate named it differently):

```text
json(outputs('Run_script')?['body/result'])
```

The response is one of:

- `reserved` — the next available row was filled (or appended) and verified;
- `existing` — the Jira key already had the same reservation (safe retry);
- `reused` — exactly one historical card matched a rejoiner; Excel was not
  changed;
- `manual_review` — no ID may be printed until a person resolves ambiguity;
- `blocked` — invalid workbook data or input; no ID may be printed.

Only `reserved`, `existing`, and `reused` include a printable `cardId` and
`numericPart`.

## Card UI integration contract

After a Jira refresh, the desktop app attaches the validated flow result to the
joiner ticket as `ticket["access_card"]`. The Card printer tab lists only joiners
and rejoiners, follows a selected joiner, and preserves its selection across
refreshes. The cached result includes:

```json
{
  "status": "reserved",
  "jira_key": "GSD-123",
  "full_name": "Aistė Žukaitė",
  "registry_name": "Aistė Žukaitė",
  "joiner_type": "new_joiner",
  "card_id": "LT5053",
  "numeric_part": 5053,
  "message": "A new access-card ID was reserved in Excel.",
  "workbook_changed": true,
  "is_confirmed": true
}
```

`full_name` identifies the requested Jira employee; `registry_name` is the
workbook spelling returned as `registryName` by the script. The UI uses that
spelling and puts only `numeric_part` in the number field; `LT` is already in
the template. It validates the Jira key, request name, joiner type, complete
response contract, and matching card ID/number before enabling **Export PNG**.
The populated fields are read-only. `manual_review`, `blocked`, missing service
configuration, sign-in errors, and invalid/stale results keep export disabled.

Reservations run only for a confirmed Vilnius operator and never process mover
tickets. Select **Refresh card details** after signing in or fixing a registry
problem. Repeated calls use the Jira key to avoid duplicate allocations.

## 4. Register the desktop client in Entra ID

Register a **public client / mobile and desktop application** in the Girteka
tenant. Enable the system-browser loopback redirect URI `http://localhost` and
allow public client flows. Under API permissions, add the delegated Power
Automate permission shown as **Access Microsoft Flow as signed-in user**
(`User`) and complete the tenant's consent process. Do not grant flow-management
permissions to this client. No client secret belongs in the app.

The desktop client uses the delegated scope:

```text
https://service.flow.microsoft.com//.default
```

The double slash is intentional: the Power Automate resource identifier ends
in `/`. Tokens are acquired silently from a Windows DPAPI-encrypted cache after
the user's first interactive Microsoft sign-in.

Record these deployment values for the application configuration:

- tenant ID;
- desktop-app client ID;
- authenticated flow trigger URL.

Treat the flow URL as configuration, not as proof of authorization. Entra access
control is required even if the URL is exposed in application logs or config.
If Conditional Access targets individual cloud apps, the policy owner should
also review Microsoft Flow Service (`7df0a125-d3be-4c96-aa54-591f83ff541c`) so
the desktop client and Power Automate have compatible requirements.

## 5. Acceptance tests before enabling automatic reservations

Use a copy of the production workbook and confirm:

1. Two simultaneous new-joiner requests receive consecutive, unique IDs starting
   at LT5053 or after the latest assigned ID; LT869/LT5001 remain untouched.
2. Repeating the same Jira request returns `existing` and adds no row.
3. A rejoiner with one historical name match returns `reused`, preserves the
   workbook spelling in the card preview, and changes no cells.
4. A rejoiner with zero or multiple name matches returns `manual_review`.
5. A new joiner whose name already exists returns `manual_review`.
6. A malformed CardID blocks allocation; a duplicate blocks requests involving
   that ID, while unrelated historical duplicates remain untouched.
7. A caller outside the permitted tenant-user list receives `401` or `403`.
8. Closing/reopening the desktop app allows silent sign-in from the encrypted
   cache.

Test these on a separate workbook copy before enabling the production flow.

## 6. Connect the desktop app

Once the flow and Entra registration are deployed:

1. Open the app's **Settings** and expand the advanced settings.
2. Enter **Access-card Power Automate flow URL**, **Microsoft tenant ID**, and
   **Access-card desktop-app client ID** from the deployment.
3. Select **Sign in to access-card service** and sign in with an allowed account.
4. Save settings. Automatic reservations run for assigned joiner tickets after
   refresh, so connect the production flow only when it is ready to allocate.
5. Open **Card printer**, select a joiner/rejoiner, and choose **Refresh card
   details**. A confirmed name and number enable **Export PNG**.

Until those deployment values are supplied, the app shows the registry setup
message and leaves card export disabled. Installing the desktop app alone does
not create the Power Automate flow or enable Excel writes.

## Local verification

The Python suite checks ticket identity, rejoiner handling, read-only autofill,
stale selections, and exported PNG pixels. The Office Script suite exercises
the actual allocator code with workbook doubles, including retry idempotency,
prefilled rows, historical gaps, duplicate IDs, and failed write verification.

```powershell
python -m unittest discover -s tests -v
npm.cmd install --prefix .tools/typescript-check --no-save --package-lock=false typescript@5.9.2
node .tools/typescript-check/node_modules/typescript/bin/tsc --noEmit --strict --target ES2020 tests/office_scripts.d.ts power_automate/ReserveAccessCard.ts
node --test tests/test_access_card_script.cjs
```

## Microsoft references

- [Authenticate HTTP request triggers with Entra ID](https://learn.microsoft.com/en-us/power-automate/oauth-authentication)
- [Excel Online (Business) connector limitations](https://learn.microsoft.com/en-us/connectors/excelonlinebusiness/)
- [Microsoft Graph workbook concurrency guidance](https://learn.microsoft.com/en-us/graph/workbook-best-practice)
- [Microsoft identity platform scopes and trailing slashes](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc#trailing-slash-and-default)
- [MSAL token acquisition and caching](https://learn.microsoft.com/en-us/entra/msal/msal-acquire-cache-tokens)
