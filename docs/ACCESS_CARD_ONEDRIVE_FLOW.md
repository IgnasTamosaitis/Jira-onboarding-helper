# Access-card flow using existing OneDrive sync

This is the deployment route for this environment: **no Power Automate Premium
HTTP actions, no desktop Entra app registration, no client ID or tenant ID**.
The app exchanges small JSON files through the existing Windows OneDrive sync.
Power Automate uses the OneDrive for Business and Excel Online (Business)
connections already available to the flow owner.

The Excel workbook and `ReserveAccessCard` script are shared across connection
options. Earlier HTTP/list flows are not used by this connection. Keep those flows
disabled so only one flow writes to the card workbook.

## 1. Queue folders

Create a shared queue folder under the flow owner's OneDrive, with these four
subfolders. Each operator selects their own synced local path in Settings:

```text
<your work OneDrive folder>\AccessCardQueue
    Requests
    Results
    Processed
    Staging
```

Wait until **AccessCardQueue** and all four subfolders appear in OneDrive on
the web. In File Explorer, choose **Always keep on this device** for the queue
folder so results remain available locally. Start with empty queue folders.

The corresponding cloud paths are `/AccessCardQueue/Requests`,
`/AccessCardQueue/Results`, `/AccessCardQueue/Processed` and
`/AccessCardQueue/Staging`. Use the folder picker in Power Automate when a field
requests a folder identifier; use the literal paths where it requests a path.

For other Vilnius operators, share this same folder with the intended users
and sync it to their PCs. Each app points to its own local path to that shared
folder. The single flow stays connected as the **owner** of the folder: the
OneDrive connector cannot process another owner's shortcut as its own drive.
Keep sharing limited to the operators who handle these card requests/results.

## 2. Create one scheduled cloud flow

In Power Automate:

1. **Create → Scheduled cloud flow**.
2. Name: **Access Card Queue**.
3. Repeat every **1 minute**.
4. In the **Recurrence** trigger settings, enable **Concurrency Control** and
   set **Degree of Parallelism** to **1**.

The scheduled scan catches delayed uploads and retries work left in Requests.
OneDrive's new-file trigger does not reliably cover moved files and larger
bursts, so this flow scans the pending folder instead.

## 3. Find pending requests

Add **OneDrive for Business → List files in folder** (the current action,
not the deprecated version). Folder: select **AccessCardQueue/Requests**.
Keep its action name **List files in folder** for the expressions below.

Add **Data Operation → Filter array**.

**From**, through fx:

```text
body('List_files_in_folder')?['value']
```

In the filter's advanced mode, enter:

```text
@and(equals(item()?['IsFolder'], false), startsWith(item()?['Name'], 'card-v1-'), endsWith(item()?['Name'], '.json'))
```

Add **Control → Apply to each**, using this fx expression as its input:

```text
take(body('Filter_array'), 25)
```

In Apply to each's settings, leave concurrency **off** so it processes one
file at a time. Each successful request is moved out of Requests; subsequent
runs collect the remaining files. OneDrive, the flow and Excel can add latency;
the one-minute schedule is not a promise of a one-minute response.

## 4. Read each request

All remaining actions go **inside Apply to each**.

Add **OneDrive for Business → Get file content**. File, through fx:

```text
items('Apply_to_each')?['Id']
```

Add **Data Operation → Parse JSON**:

- **Content:** select **File content** from Get file content in Dynamic content.
- **Schema:** paste the contents of
  [`OneDriveRequest.schema.json`](../power_automate/OneDriveRequest.schema.json).

Keep its action name **Parse JSON**. A request carries `requestKey`, `jiraKey`,
`fullName`, and `joinerType`. The schema limits the key to a safe filename and
accepts joiners/rejoiners only.

## 5. Run the existing workbook script

Prepare the workbook and install the supplied
[`ReserveAccessCard.ts`](../power_automate/ReserveAccessCard.ts) Office Script
using [the workbook setup steps](ACCESS_CARD_FLOW.md#1-prepare-the-workbook).
Only follow the workbook and script steps there; this guide supplies the queue
flow. If they are already installed, reuse them.

Add **Excel Online (Business) → Run script**, selecting that workbook and
`ReserveAccessCard` script. It reads **AccessCards**, columns A:E.

Set these script parameters through fx:

| Parameter | Expression |
| --- | --- |
| jiraKey | `body('Parse_JSON')?['jiraKey']` |
| fullName | `body('Parse_JSON')?['fullName']` |
| joinerType | `body('Parse_JSON')?['joinerType']` |

Keep the action name **Run script**. New joiners continue from LT5053 onward;
rejoiners reuse a unique existing record without changing Excel. Repeated
requests for the same Jira identity reuse the reservation. Historical gaps
remain untouched, and ambiguous/duplicated card records require review.

## 6. Publish the result file

Add **OneDrive for Business → Create file**:

- **Folder Path:** `/AccessCardQueue/Staging`
- **File Name**, fx: `concat(guid(), '.tmp')`
- **File Content**, fx:

```text
string(setProperty(setProperty(json('{}'), 'request', body('Parse_JSON')), 'result', json(outputs('Run_script')?['body/result'])))
```

This writes an envelope with the original request and the script's result.
The app verifies both before accepting a printable card.

Add **OneDrive for Business → Move or rename a file**:

- **File:** select **Id** from the Create file action.
- **Destination File Path**, fx:

```text
concat('/AccessCardQueue/Results/', body('Parse_JSON')?['requestKey'], '.json')
```

- **Overwrite:** **Yes**.

Writing to Staging first and then moving with explicit overwrite makes repeat
delivery recoverable. The desktop ignores temporary files and waits if a
downloaded result is incomplete.

## 7. Archive the handled request

Add another **OneDrive for Business → Move or rename a file**:

- **File**, fx: `items('Apply_to_each')?['Id']`
- **Destination File Path**, fx:

```text
concat('/AccessCardQueue/Processed/', body('Parse_JSON')?['requestKey'], '.json')
```

- **Overwrite:** **Yes**.

Keep the default run-after condition **is successful** for these actions.
A request is archived only after its result was published. If Excel, result
publication or archiving fails, the request remains pending for the next run;
the script's Jira-key check prevents another card being allocated on retry.

Finally add **Schedule → Delay**, with **Count: 4**, **Unit: Second**, after
archiving inside the loop. This spaces out workbook-script calls during batches.
Save the flow.

## 8. Connect the desktop app

Once the cloud flow is ready, open **Settings → Advanced settings**:

1. Select **Choose queue folder…** and choose the local **AccessCardQueue**
   folder from section 1.
2. Save settings and select **Refresh card details** in Card printer.

The folder connection needs no extra app sign-in. Saving it clears the earlier
HTTP/list API settings from this app's configuration. Windows OneDrive supplies
the existing authenticated sync connection.

The app saves a stable request file, then checks pending results every 15
seconds. It does not repeatedly rewrite requests or allocate IDs locally.
If Processed syncs before Results, the app keeps waiting. It enables PNG export
only for a matching, confirmed result. Movers and non-Vilnius operators do not
submit requests.

## Verification and recovery

For the first end-to-end test, use a separate queue-folder copy and a **copy of
the workbook**, and point both the app and flow to those test locations. Verify
one new joiner and one historical rejoiner, repeat delivery, and the returned
PNG before connecting the production queue. Local tests do not prove live
OneDrive delivery or tenant flow execution.

If a request stays pending, check OneDrive is running on the PC and inspect
the flow's run history. A failed request remains in Requests for retry. Do not
manually assign an ID to work around a pending result.

For manual-review or blocked results, correct the underlying workbook issue
and move that request from Processed back to Requests. The scheduled flow will
replace its result on the next successful run. Select Refresh card details to
read the replacement result. Keep the request's contents and filename intact.

## Microsoft references

- [OneDrive for Business: Standard connector, actions and limitations](https://learn.microsoft.com/en-us/connectors/onedriveforbusiness/)
- [Excel Online (Business): Standard connector and script limits](https://learn.microsoft.com/en-us/connectors/excelonlinebusiness/)
- [Create a scheduled cloud flow](https://learn.microsoft.com/en-us/power-automate/run-scheduled-tasks)
- [Flow concurrency and loop settings](https://learn.microsoft.com/en-us/power-automate/guidance/coding-guidelines/implement-parallel-execution)
