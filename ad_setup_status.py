"""Recover setup evidence and compare it with AD without changing the directory."""
import base64
from datetime import datetime
import json
from pathlib import Path
import re

from ad_automation import run_ps
from ad_setup_execution import verification_lines


READBACK = "AD_SETUP_READBACK:"


def readback_from_output(output: str, account: str) -> dict:
    for line in reversed(output.splitlines()):
        if line.startswith(READBACK):
            try:
                report = json.loads(line[len(READBACK):])
                if str(report.get("account", "")).casefold() == account.casefold():
                    return report
            except (ValueError, AttributeError):
                continue
    return {}


def recover_setup(ticket: dict, saved: dict, audit_path: Path | None = None) -> dict:
    """Match exact Jira keys, never names or a guessed SAM, and use the latest attempt."""
    if (saved.get("baseline") or {}).get("complete"):
        return dict(saved)
    path = audit_path or Path.home() / ".jira-reminders" / "ad_audit.log"
    try:
        audit = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return dict(saved)
    match = None
    for block in re.split(r"(?m)^={60}\s*$", audit):
        headers = dict(re.findall(
            r"(?m)^(Timestamp|Ticket|Scenario|Account|Email|OU|Status)\s*:\s*([^\r\n]*)", block))
        if not headers.get("Ticket", "").split():
            continue
        if headers["Ticket"].split()[0] != ticket.get("key"):
            continue
        if saved.get("account") and headers.get("Account", "").casefold() != saved["account"].casefold():
            continue
        if headers.get("Account"):
            match = headers, block
    if match is None:
        return dict(saved)
    headers, block = match
    report = readback_from_output(block, headers["Account"])
    groups = re.findall(r"(?m)^OK\s+Added group:\s*([^\r\n]+)", block)
    # Old scripts could say Completed even after a denied group addition.
    groups += re.findall(r"(?m)^WARNING: Group (.+?): [^\r\n]+", block)
    attributes = dict(report.get("attributes", {}))
    if not report:
        # Retain exact values that older, human-readable logs actually contain.
        # Never fill missing expectations from today's account and call them historical.
        for field in ("Title", "Description", "Department", "Company", "Office",
                      "UserPrincipalName", "EmailAddress", "extensionAttribute5",
                      "extensionAttribute10", "extensionAttribute14", "extensionAttribute15"):
            values = re.findall(rf"(?m)^{field}:\s*([^\r\n]*)", block)
            if values:
                attributes[field] = values[-1].strip()
        for field, pattern in (
            ("EmployeeID", r"(?m)^OK\s+EmployeeID copied:\s*([^\r\n]+)"),
            ("extensionAttribute10", r"(?m)^OK\s+extensionAttribute10 set to ([^\r\n]+)"),
            ("extensionAttribute15", r"(?m)^OK\s+extensionAttribute15 set to ([^\r\n]+)"),
        ):
            values = re.findall(pattern, block)
            if values:
                attributes[field] = values[-1].strip()
    baseline = report.get("baseline") or {
        "version": 0, "account": headers["Account"],
        "attributes": attributes,
        "groups": list(dict.fromkeys(groups)),
        "direct_reports": report.get("directReports", []),
        "target_ou": headers.get("OU", ""), "email": headers.get("Email", ""),
        "complete": False,
    }
    if headers.get("Status") != "Completed" or report.get("failures"):
        baseline = dict(baseline, complete=False)
    result = dict(saved)
    result.update(account=headers["Account"], email=headers.get("Email", ""),
                  target_ou=headers.get("OU", ""), scenario=headers.get("Scenario", ""),
                  baseline=baseline, groups_count=len(baseline.get("groups", [])),
                  recovered_from="AD audit log", setup_recorded_at=headers.get("Timestamp", ""),
                  history_completed=headers.get("Status") == "Completed")
    return result


def build_status_script(info: dict) -> str:
    baseline = info.get("baseline") or {}
    sam = info.get("account", "")
    if not sam or (baseline.get("account") and baseline["account"].casefold() != sam.casefold()):
        raise ValueError("No matching setup account is recorded.")
    payload = base64.b64encode(json.dumps(baseline).encode("utf-8")).decode("ascii")
    # Base64 is data, never executable PowerShell. Groups can be historical
    # names or DNs; resolving both also detects deleted/renamed legacy groups.
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "Import-Module ActiveDirectory -ErrorAction Stop",
        "$dc = [string](Get-ADDomainController -Discover -Writable | Select-Object -ExpandProperty HostName)",
        "$PSDefaultParameterValues = @{'*-AD*:Server' = $dc}",
        f"$baseline = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json",
        "$expected = @{}; $expectedGroups = @(); $expectedReports = @($baseline.direct_reports); $groupFailures = @(); $roleSources = @{}",
        "if ($baseline.attributes) { foreach ($p in $baseline.attributes.PSObject.Properties) { $expected[$p.Name] = $p.Value } }",
        "if ($baseline.object_guid) { $expected['ObjectGUID'] = $baseline.object_guid }",
        "foreach ($group in $baseline.groups) {",
        "    if ($group -match '^CN=') { $expectedGroups += $group; continue }",
        "    try {",
        "        $found = @(Get-ADGroup -Filter { Name -eq $group })",
        "        if ($found.Count -eq 1) { $expectedGroups += $found[0].DistinguishedName }",
        "        else { $groupFailures += ('Cannot resolve expected group: ' + $group) }",
        "    } catch { $groupFailures += ('Cannot read expected group: ' + $group) }",
        "}",
    ]
    lines += verification_lines(sam, baseline.get("target_ou") or info.get("target_ou", ""),
                                baseline.get("email") or info.get("email", ""))
    return "\n".join(lines)


def check_setup(info: dict) -> dict:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base = {"checked_at": checked_at, "verified": False, "completed": False}
    if not info.get("account"):
        return dict(base, state="unknown", issues=["No setup history found for this ticket."])
    try:
        output, error, code = run_ps(build_status_script(info), timeout=45)
        report = readback_from_output(output, info["account"])
        if not report:
            return dict(base, state="unavailable", issues=[
                "Could not read the account from AD. Check connectivity, read permissions, and whether the account still exists."])
        failures = report.get("failures")
        if not isinstance(failures, list):
            raise ValueError("Invalid AD verification response")
        incomplete = not (info.get("baseline") or {}).get("complete")
        issues = list(failures)
        if incomplete:
            issues.append("Older setup history does not contain a full attribute baseline; an exact setup match cannot be confirmed.")
        if not failures and (code != 0 or error):
            return dict(base, state="unavailable", issues=["AD verification did not finish successfully."])
        return dict(base, state="drift" if failures else "partial" if incomplete else "verified",
                    verified=not failures and not incomplete, issues=issues,
                    completed=not failures and (not incomplete or bool(info.get("history_completed"))
                        or bool(info.get("completed_at") and info.get("status") == "Completed")),
                    group_warnings=report.get("group_warnings", []),
                    server=report.get("server", ""),
                    actual=report.get("current", {}))
    except Exception:
        return dict(base, state="unavailable", issues=["Could not complete the AD check. Check connectivity and read permissions, then retry."])
