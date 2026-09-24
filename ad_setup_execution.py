"""Shared preflight and read-back checks for the existing AD setup workflow."""
import re


VERIFIED_PREFIX = "AD_SETUP_VERIFIED:"


def setup_result_status(output: str, error: str, code: int, account: str) -> str:
    """A clean process exit alone is not evidence of a completed AD setup."""
    lines = output.rstrip().splitlines()
    if (code != 0 or error.strip() or not account or not lines
            or lines[-1].strip() != VERIFIED_PREFIX + account
            or re.search(r"(?im)^\s*WARNING:(?![ \t]*Group )", output)):
        return "Not completed"
    return "Completed"


def _q(value: str) -> str:
    return str(value).replace("'", "''")


def role_preflight_lines(source: str, buddy: str, title: str = "",
                         department: str = "", *, sf_authoritative: bool = True) -> list[str]:
    """Keep populated SF fields; resolve and audit only missing role fields."""
    lines = [
        "$expected = @{}",
        "$expectedGroups = @()",
        "$groupFailures = @()",
        "$expectedReports = @()",
        f"$roleSource = {source}",
        "$roleParams = @{Title=$roleSource.Title; Description=$roleSource.Description; Department=$roleSource.Department}",
        "$roleSources = [ordered]@{}",
        f"foreach ($field in $roleParams.Keys) {{ $roleSources[$field] = '{'SF' if sf_authoritative else 'Existing AD'}' }}",
    ]
    if not sf_authoritative:
        # A single-account rejoiner has no new SF source account. Preserve the
        # existing Jira-role/buddy-department rule for the new employment.
        if title:
            lines += [
                f"$roleParams.Title = '{_q(title)}'",
                f"$roleParams.Description = '{_q(title)}'",
                "$roleSources['Title'] = 'Jira'; $roleSources['Description'] = 'Jira'",
            ]
        if buddy or department:
            lines.append("$roleParams.Department = $null")
    if buddy:
        lines += [
            "$missingRoleFields = @($roleParams.Keys | Where-Object { [string]::IsNullOrWhiteSpace([string]$roleParams[$_]) })",
            "if ($missingRoleFields.Count) {",
            f"    $buddyRole = Get-ADUser -Identity '{_q(buddy)}' -Properties Title,Description,Department",
            "    foreach ($field in $missingRoleFields) {",
            "        $roleParams[$field] = $buddyRole.$field",
            f"        $roleSources[$field] = 'Buddy: {_q(buddy)}'",
            "    }",
            "}",
        ]
    elif department:
        lines += [
            "if ([string]::IsNullOrWhiteSpace([string]$roleParams.Department)) {",
            f"    $roleParams.Department = '{_q(department)}'",
            "    $roleSources['Department'] = 'Loaded buddy department'",
            "}",
        ]
    lines += [
        "Write-Host ('AD_SETUP_ROLE_SOURCES:' + ($roleSources | ConvertTo-Json -Compress))",
        "foreach ($field in @('Title','Description','Department')) {",
        '    if ([string]::IsNullOrWhiteSpace([string]$roleParams[$field])) { throw "Required role field is empty: $field. Check the selected buddy or SF data before retrying." }',
        "    $expected[$field] = $roleParams[$field]",
        "}",
    ]
    return lines


def apply_role_lines() -> list[str]:
    return [
        "foreach ($field in $roleParams.Keys) { $setParams[$field] = $roleParams[$field] }",
        "foreach ($field in $setParams.Keys) { $expected[$field] = $setParams[$field] }",
    ]


def verification_lines(sam: str, ou: str, email: str) -> list[str]:
    return [
        "# Verify all intended changes on the same DC, with the same credentials",
        "$verifyProperties = @($expected.Keys) + @('Title','Description','Department','Company','Office','Manager','EmailAddress','targetAddress','proxyAddresses','Enabled','LockedOut','PasswordLastSet','pwdLastSet','MemberOf','DirectReports','UserPrincipalName','EmployeeID','extensionAttribute5','extensionAttribute10','extensionAttribute14','extensionAttribute15','msExchHideFromAddressLists')",
        f"$verified = Get-ADUser -Identity '{_q(sam)}' -Properties ($verifyProperties | Select-Object -Unique)",
        "$failures = @(); $groupWarnings = @($groupFailures)",
        "foreach ($field in $expected.Keys) {",
        '    if ([string]$verified.$field -cne [string]$expected[$field]) { $failures += "Attribute mismatch: $field" }',
        "}",
        "foreach ($field in @('Title','Description','Department','Company','Office','Manager')) {",
        '    if ([string]::IsNullOrWhiteSpace([string]$verified.$field)) { $failures += "Required field missing: $field" }',
        "}",
        r"$actualOu = $verified.DistinguishedName -replace '^CN=(?:\\.|[^\\,])+,', ''",
        f"if ($actualOu -ine '{_q(ou)}') {{ $failures += 'Target OU mismatch' }}",
        "if (-not $verified.Enabled) { $failures += 'Account is disabled' }",
        "if ($verified.LockedOut) { $failures += 'Account is locked' }",
        "if (-not $verified.PasswordLastSet -or $verified.pwdLastSet -eq 0) { $failures += 'Password state is incomplete' }",
        f"if ($verified.EmailAddress -ine '{_q(email)}' -or $verified.targetAddress -ine '{_q(email)}') {{ $failures += 'Email or targetAddress mismatch' }}",
        "$primary = @($verified.proxyAddresses | Where-Object { $_ -cmatch '^SMTP:' })",
        f"if ($primary.Count -ne 1 -or $primary[0] -cne 'SMTP:{_q(email)}') {{ $failures += 'Primary SMTP mismatch' }}",
        "if (@($verified.proxyAddresses | Where-Object { $_ -imatch '^smtp:.+@tndmtrucking\\.com$' }).Count) { $failures += 'Retired TNDM proxy remains' }",
        "foreach ($group in $expectedGroups) {",
        '    if ($group -notin $verified.MemberOf) { $groupWarnings += "Missing group: $group" }',
        "}",
        "foreach ($report in $expectedReports) {",
        '    if ($report -notin $verified.DirectReports) { $failures += "Missing direct report: $report" }',
        "}",
        "$reportData = [ordered]@{account=$verified.SamAccountName; server=$dc; roleSources=$roleSources; attributes=[ordered]@{}; directReports=@($verified.DirectReports); failures=@($failures); group_warnings=@($groupWarnings | Select-Object -Unique)}",
        "foreach ($field in $expected.Keys) { $reportData.attributes[$field] = $verified.$field }",
        "$reportData['current'] = [ordered]@{enabled=[bool]$verified.Enabled; locked=[bool]$verified.LockedOut; email=[string]$verified.EmailAddress; ou=$actualOu; groups=@($verified.MemberOf)}",
        "$baselineAttributes = [ordered]@{}",
        "foreach ($field in (@($expected.Keys) + @('UserPrincipalName','EmployeeID','extensionAttribute5','extensionAttribute10','extensionAttribute14','extensionAttribute15','msExchHideFromAddressLists') | Select-Object -Unique)) { if ($expected.ContainsKey($field)) { $baselineAttributes[$field] = $expected[$field] } else { $baselineAttributes[$field] = $verified.$field } }",
        f"$reportData['baseline'] = [ordered]@{{version=1; account=$verified.SamAccountName; object_guid=[string]$verified.ObjectGUID; attributes=$baselineAttributes; groups=@(@($verified.MemberOf) + @($expectedGroups) | Select-Object -Unique); direct_reports=@(@($verified.DirectReports) + @($expectedReports) | Select-Object -Unique); target_ou='{_q(ou)}'; email='{_q(email)}'; complete=($failures.Count -eq 0)}}",
        "Write-Host ('AD_SETUP_READBACK:' + ($reportData | ConvertTo-Json -Depth 5 -Compress))",
        "if ($failures.Count) { throw ('AD setup verification failed: ' + ($failures -join '; ')) }",
    ]


def success_lines(sam: str) -> list[str]:
    return [f"Write-Host '{VERIFIED_PREFIX}{_q(sam)}'"]
