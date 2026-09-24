# Access-card requests using Standard connectors

**For the current deployment, use the [OneDrive sync guide](ACCESS_CARD_ONEDRIVE_FLOW.md).**
That version needs no desktop Entra registration. This direct-to-list guide is
retained for environments where the registration and selected-site permissions
in section 5 are available; do not follow that registration step here.

Use this guide for the SharePoint request-list version. It replaces the HTTP
trigger and Response actions in `ACCESS_CARD_FLOW.md`. The existing workbook
and `ReserveAccessCard` Office Script are reused.

**Licensing:** SharePoint and Excel Online (Business) are Standard Power
Automate connectors. Office Scripts still requires a qualifying Microsoft 365
business licence, with scripts and these connectors enabled in the tenant.
The desktop app also needs an Entra public-client registration and delegated
Microsoft Graph access. A Power Automate connection alone does not grant that
desktop access.

## 1. Request list

Create a dedicated request list, or reuse the list provisioned by the registry
owner. Its display name is arbitrary; the integration identifies it by GUID.

Site Address (example; use the actual site root supplied by the registry owner):

```text
https://example.sharepoint.com/sites/AccessCards
```

Find the actual List ID in the list's **List settings** URL. Example format:

```text
4e3b4aff-1c0a-43f6-ba8c-12a7be6e651e
```

In the list, keep **Title** and add these columns with the exact names below.
Create the columns with these names initially; renaming an existing column
does not change its internal API name.

| Column | Type | Configuration |
| --- | --- | --- |
| Title (existing) | Single line of text | List settings → Title → Enforce unique values: Yes; accept indexing |
| JiraKey | Single line of text | No default |
| FullName | Single line of text | No default |
| JoinerType | Single line of text | App supplies `new_joiner` or `rejoiner` |
| QueueStatus | Single line of text | Default: `Pending` |
| ResultJson | Multiple lines of text | Plain text; append changes: No |
| ErrorMessage | Multiple lines of text | Plain text; append changes: No |

Leave the new columns optional. Title holds an app-generated unique request
key; do not fill it with the employee's name or Jira key manually. The app checks
these column types and Title's uniqueness/indexing before submitting requests.

The list carries requests/results only. Keep the card registry in Excel. For
team use, share the request list with the intended Vilnius operators and ensure
the flow's Excel connection can edit the workbook. My Lists remains owned by
its creator; a team-owned list can be configured later using its site and GUID.

## 2. Automated cloud flow

In Power Automate, select **Create → Automated cloud flow**. Name it
`Access Card Requests` and select **When an item is created — SharePoint**.
This trigger is selected when creating the flow, not from Add an action.

Use a SharePoint connection signed in with your Girteka account. Enter the
Site Address above as a custom value. Choose **Test** from List Name, or use
**Enter custom value** and the GUID above if it is not listed.

In the trigger's **Settings**, enable **Concurrency Control** and set **Degree
of Parallelism** to **1**. Only one enabled flow may allocate cards from this
workbook. Keep the earlier Premium HTTP flow disabled.

## 3. Run script

Add **Excel Online (Business) → Run script**. Use the same workbook connection,
file and `ReserveAccessCard` script already configured in the previous guide.
The Excel file contains **AccessCards**, with columns A:E named `CardID`,
`EmployeeName`, `JiraKey`, `Status`, and `ReservedAt`.

Set the script parameters through **fx / Expression**:

| Script parameter | Expression |
| --- | --- |
| jiraKey | `triggerBody()?['JiraKey']` |
| fullName | `triggerBody()?['FullName']` |
| joinerType | `triggerBody()?['JoinerType']` |

These are the list column names and start with capital letters. They replace
the lower-case property expressions used by the old HTTP trigger.

## 4. Return the result to the list

After Run script, add **SharePoint → Update item**. Use the same site/list.
Configure these values (use fx for expressions, plain text for Completed):

| Field | Value |
| --- | --- |
| Id | `triggerBody()?['ID']` |
| Title | `triggerBody()?['Title']` |
| JiraKey | `triggerBody()?['JiraKey']` |
| FullName | `triggerBody()?['FullName']` |
| JoinerType | `triggerBody()?['JoinerType']` |
| QueueStatus | `Completed` |
| ResultJson | `outputs('Run_script')?['body/result']` |
| ErrorMessage | Leave empty |

Adjust `Run_script` if the action has a different internal name. ResultJson is
the returned JSON **string**; no HTTP Response action or Parse JSON action is
needed. Completed means the script returned a result, including manual review
or blocked results; it does not itself confirm a printable card.

Add a parallel **Update item** action called `Report failure`, configured to
run after **Run script has failed or timed out**. Use the same Id, Title and
request fields, set QueueStatus to `Failed`, leave ResultJson empty, and set
ErrorMessage to `Workbook script failed. Review this flow run.`

Leave the success Update item action's run-after setting as **is successful**.
If writing the result back fails after Excel was changed, the item can remain
Pending. Fix the connection and resubmit the original flow run; Jira-key
idempotency prevents another allocation. Do not create a replacement request.

Save the flow. Its main path is:

**When an item is created → Run script → Update item**

## 5. Desktop Microsoft sign-in

This step requires the tenant's Entra/SharePoint administrator where your
account cannot register apps, grant consent, or assign selected-site access.

1. Register a single-tenant public **mobile and desktop application** in Entra.
2. Add the loopback redirect URI `http://localhost`, and allow public client
   flows. No client secret is used.
3. Add the **Microsoft Graph delegated** permission **Sites.Selected** and
   complete the tenant consent process. The desktop app requests
   `https://graph.microsoft.com/Sites.Selected`.
4. An administrator must also grant that application **write** access to the
   request list's site. Consent to Sites.Selected alone grants no site access.
   The signed-in operator must separately have list access.

For the administrator, resolve the site ID with an appropriately authorised
Graph session:

```http
GET https://graph.microsoft.com/v1.0/sites/example.sharepoint.com:/sites/AccessCards?$select=id
```

Grant the desktop application access using the resulting site ID:

```http
POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
Content-Type: application/json

{
  "roles": ["write"],
  "grantedToIdentities": [{
    "application": {
      "id": "DESKTOP-APP-CLIENT-ID",
      "displayName": "Jira Reminders Access Cards"
    }
  }]
}
```

This provisioning operation requires an administrator's authorised management
session; do not add Sites.FullControl.All to the desktop app. Sites.Selected
is scoped to the selected site collection, which for My Lists is the owner's
personal SharePoint site. The client itself accesses only the configured list.
Verify this Graph access independently: seeing Test in the Power Automate
dropdown proves connector access, not access for the desktop registration.

## 6. Connect and verify

In desktop **Settings → Advanced settings**, enter:

- **Access-card SharePoint site address:** the site address in section 1;
- **Access-card request list ID:** the GUID in section 1;
- **Microsoft tenant ID** and **Access-card desktop-app client ID** from Entra;
- leave **Access-card HTTP flow URL (Premium only)** blank.

Select **Sign in to access-card service**, complete Microsoft sign-in and save
settings. Tokens are saved in a separate Windows DPAPI-encrypted Graph cache.

After a Jira refresh, the app creates an item for each eligible, unresolved
joiner/rejoiner. It checks pending items every 15 seconds without delaying Jira
refresh or the UI. Trigger delivery can take longer; this is asynchronous.
The same request reuses its unique Title across retries, PCs and app restarts.

Once the flow writes Completed and ResultJson, the app validates the request
identity and result contract before enabling PNG export. Pending, failed,
malformed, mismatched and ambiguous results cannot be printed. Movers and
operators outside Vilnius do not submit requests.

Before enabling production allocations, use a separate request list and a copy
of the workbook to check:

1. A new joiner fills LT5053 (or the next eligible ID) and keeps older gaps.
2. A unique-name rejoiner reuses the workbook name/ID and changes no Excel cells.
3. Repeating a request creates no duplicate list item or card reservation.
4. A changed name/type cannot consume the earlier request's result.
5. Duplicate historical card IDs and ambiguous rejoiner names require review.
6. An interrupted flow can be resubmitted without allocating another card.

For a blocked/manual-review result, resolve the workbook issue, **resubmit the
original flow run**, and select **Refresh card details**. A desktop refresh
reads the existing result; it does not automatically resubmit completed flows.

## References

- [SharePoint Standard connector](https://learn.microsoft.com/en-us/connectors/sharepointonline/)
- [Excel Online (Business) Standard connector](https://learn.microsoft.com/en-us/connectors/excelonlinebusiness/)
- [Office Scripts licence and platform requirements](https://learn.microsoft.com/en-us/office/dev/scripts/testing/platform-limits)
- [Create an automated flow with a SharePoint trigger](https://learn.microsoft.com/en-us/power-automate/modern-approvals#create-an-automated-cloud-flow)
- [Selected permissions and explicit resource grants](https://learn.microsoft.com/en-us/graph/permissions-selected-overview)
- [Create list items with Microsoft Graph](https://learn.microsoft.com/en-us/graph/api/listitem-create?view=graph-rest-1.0)
