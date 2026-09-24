/**
 * Resolves or reserves an access-card ID in the AccessCards worksheet (A:E).
 *
 * Run this script only from a Power Automate flow whose trigger concurrency is
 * set to 1.  The script returns JSON so the flow can use json(...) in its HTTP
 * Response action.
 */
function main(
  workbook: ExcelScript.Workbook,
  jiraKey: string,
  fullName: string,
  joinerType: string
): string {
  const cleanJiraKey = String(jiraKey || "").trim().toUpperCase();
  const cleanFullName = collapseSpaces(String(fullName || ""));
  const cleanJoinerType = String(joinerType || "").trim().toLowerCase();

  if (!/^[A-Z][A-Z0-9_]*-[1-9][0-9]*$/.test(cleanJiraKey)) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "Invalid Jira key.", false);
  }
  if (!cleanFullName || cleanFullName.length > 200 || /^[=+\-@]/.test(cleanFullName)) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "Employee name is missing or unsafe for Excel.", false);
  }
  if (cleanJoinerType !== "new_joiner" && cleanJoinerType !== "rejoiner") {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "joinerType must be new_joiner or rejoiner.", false);
  }

  const sheet = workbook.getWorksheet("AccessCards");
  if (!sheet) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "The AccessCards worksheet was not found.", false);
  }

  const headers = sheet.getRange("A1:E1").getTexts()[0].map(value => value.trim());
  const requiredHeaders = ["CardID", "EmployeeName", "JiraKey", "Status", "ReservedAt"];
  const indexes: { [key: string]: number } = {};
  for (const header of requiredHeaders) {
    const matches: number[] = [];
    headers.forEach((value, index) => {
      if (value === header) matches.push(index);
    });
    if (matches.length !== 1 || matches[0] !== requiredHeaders.indexOf(header)) {
      return response("blocked", cleanJiraKey, cleanFullName, null,
        "Columns A:E must be CardID, EmployeeName, JiraKey, Status, ReservedAt.", false);
    }
    indexes[header] = matches[0];
  }

  // valuesOnly ignores the workbook's formatting across all 1,048,576 rows.
  const rows = sheet.getRange("A:E").getUsedRange(true)?.getTexts().slice(1) || [];
  const parsedRows: RegistryRow[] = [];
  const cardsByNumber: { [key: string]: number[] } = {};

  for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
    const values = rows[rowIndex];
    const rawCardId = values[indexes.CardID].trim().toUpperCase();
    const employeeName = collapseSpaces(values[indexes.EmployeeName]);
    const rowJiraKey = values[indexes.JiraKey].trim().toUpperCase();

    const hasMetadata = values.slice(2, 5).some(value => value.trim() !== "");
    if (!rawCardId && !employeeName && !hasMetadata) continue;
    const cardNumber = parseCardNumber(rawCardId);
    if (cardNumber === null) {
      return response("blocked", cleanJiraKey, cleanFullName, null,
        `Invalid CardID in Excel row ${rowIndex + 2}. Use LT1-LT9999 without leading zeroes.`, false);
    }
    if (!employeeName && rowJiraKey) {
      return response("blocked", cleanJiraKey, cleanFullName, null,
        `EmployeeName is empty in Excel row ${rowIndex + 2}.`, false);
    }
    const numberKey = String(cardNumber);
    if (!cardsByNumber[numberKey]) cardsByNumber[numberKey] = [];
    cardsByNumber[numberKey].push(rowIndex + 2);
    parsedRows.push({
      excelRow: rowIndex + 2,
      cardId: `LT${cardNumber}`,
      cardNumber,
      employeeName,
      normalizedName: normalizeName(employeeName),
      jiraKey: rowJiraKey,
      assigned: employeeName !== "" || hasMetadata
    });
  }

  const jiraMatches = parsedRows.filter(row => row.jiraKey === cleanJiraKey);
  if (jiraMatches.length > 1) {
    return response("manual_review", cleanJiraKey, cleanFullName, null,
      "More than one workbook row uses this Jira key.", false);
  }
  if (jiraMatches.length === 1) {
    const existing = jiraMatches[0];
    if (cardsByNumber[String(existing.cardNumber)].length > 1) {
      return response("manual_review", cleanJiraKey, cleanFullName, null,
        `Card ${existing.cardId} appears in multiple Excel rows. Resolve the duplicate before printing.`, false);
    }
    if (existing.normalizedName !== normalizeName(cleanFullName)) {
      return response("manual_review", cleanJiraKey, cleanFullName, null,
        "This Jira key is already linked to a different employee name.", false);
    }
    return response("existing", cleanJiraKey, cleanFullName, existing.cardNumber,
      "This Jira ticket already has a confirmed card ID.", false, existing.employeeName);
  }

  const nameMatches = parsedRows.filter(
    row => row.normalizedName === normalizeName(cleanFullName)
  );
  if (cleanJoinerType === "rejoiner") {
    if (nameMatches.length === 1) {
      const existing = nameMatches[0];
      if (cardsByNumber[String(existing.cardNumber)].length > 1) {
        return response("manual_review", cleanJiraKey, cleanFullName, null,
          `Card ${existing.cardId} appears in multiple Excel rows. Resolve the duplicate before printing.`, false);
      }
      return response("reused", cleanJiraKey, cleanFullName, existing.cardNumber,
        "The rejoiner's existing card ID was reused; Excel was not changed.", false, existing.employeeName);
    }
    return response("manual_review", cleanJiraKey, cleanFullName, null,
      nameMatches.length === 0
        ? "No previous card was found for this rejoiner."
        : "More than one previous card matches this rejoiner.", false);
  }

  if (nameMatches.length > 0) {
    return response("manual_review", cleanJiraKey, cleanFullName, null,
      "This name already exists in the registry. Confirm whether the person is a rejoiner or a namesake.", false);
  }

  const maxNumber = parsedRows.filter(row => row.assigned).reduce(
    (currentMax, row) => Math.max(currentMax, row.cardNumber), 0
  );
  // LT869/LT5001 and other historical gaps must never be recycled.
  const firstAvailable = Math.max(5053, maxNumber + 1);
  const freeRow = parsedRows.filter(row => !row.assigned && row.cardNumber >= firstAvailable)
    .sort((left, right) => left.cardNumber - right.cardNumber)[0];
  const nextNumber = freeRow ? freeRow.cardNumber : Math.max(firstAvailable,
    parsedRows.reduce((maximum, row) => Math.max(maximum, row.cardNumber + 1), 5053));
  if (nextNumber > 9999) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "The LT card-number range is exhausted.", false);
  }

  const newRow: string[] = headers.map(() => "");
  newRow[indexes.CardID] = `LT${nextNumber}`;
  newRow[indexes.EmployeeName] = cleanFullName;
  newRow[indexes.JiraKey] = cleanJiraKey;
  newRow[indexes.Status] = "Reserved";
  newRow[indexes.ReservedAt] = new Date().toISOString();
  const destination = sheet.getRangeByIndexes(freeRow ? freeRow.excelRow - 1 : rows.length + 1, 0, 1, 5);
  const beforeWrite = destination.getTexts()[0];
  if (beforeWrite.slice(1).some(value => value.trim() !== "") ||
      (beforeWrite[0].trim() && beforeWrite[0].trim().toUpperCase() !== `LT${nextNumber}`)) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "The candidate row changed. Refresh and try again.", false);
  }
  // Legacy duplicates do not prevent unrelated requests, but no ambiguous ID
  // may be allocated or printed, including duplicate prefilled blank rows.
  if ((cardsByNumber[String(nextNumber)] || []).length > 1) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      `The next card LT${nextNumber} appears in multiple Excel rows. Resolve the duplicate before reserving it.`, false);
  }
  destination.setValues([newRow]);

  const verificationRows = sheet.getRange("A:E").getUsedRange(true)?.getTexts().slice(1) || [];
  const verificationMatches = verificationRows.filter(values =>
    values[indexes.JiraKey].trim().toUpperCase() === cleanJiraKey &&
    values[indexes.CardID].trim().toUpperCase() === `LT${nextNumber}` &&
    collapseSpaces(values[indexes.EmployeeName]) === cleanFullName
  );
  const sameCardRows = verificationRows.filter(values => values[0].trim().toUpperCase() === `LT${nextNumber}`);
  const sameJiraRows = verificationRows.filter(values => values[2].trim().toUpperCase() === cleanJiraKey);
  if (verificationMatches.length !== 1 || sameCardRows.length !== 1 || sameJiraRows.length !== 1) {
    return response("blocked", cleanJiraKey, cleanFullName, null,
      "The workbook write could not be verified. Do not print a card.", true);
  }

  return response("reserved", cleanJiraKey, cleanFullName, nextNumber,
    "A new access-card ID was reserved in Excel.", true);
}

interface RegistryRow {
  excelRow: number;
  cardId: string;
  cardNumber: number;
  employeeName: string;
  normalizedName: string;
  jiraKey: string;
  assigned: boolean;
}

function parseCardNumber(cardId: string): number | null {
  const match = /^LT([1-9][0-9]{0,3})$/.exec(cardId);
  if (!match) return null;
  const number = Number(match[1]);
  return Number.isInteger(number) && number >= 1 && number <= 9999 ? number : null;
}

function normalizeName(value: string): string {
  return collapseSpaces(collapseSpaces(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[łŁ]/g, "l")
    .replace(/[’'`.,]/g, "")
    .replace(/-/g, " ")
    .toLowerCase());
}

function collapseSpaces(value: string): string {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function response(
  status: string,
  jiraKey: string,
  fullName: string,
  cardNumber: number | null,
  message: string,
  workbookChanged: boolean,
  registryName: string = fullName
): string {
  return JSON.stringify({
    status,
    jiraKey,
    fullName,
    registryName,
    cardId: cardNumber === null ? null : `LT${cardNumber}`,
    numericPart: cardNumber,
    message,
    workbookChanged
  });
}
