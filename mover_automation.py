"""Active Directory discovery and exact-state automation for employee movers."""

import json
import re

from ad_automation import (
    RETIRED_TNDM_EMAIL_DOMAIN,
    _e,
    detect_address_warning,
    detect_company_address,
    detect_site,
    girteka_email_from_existing,
    is_girteka_dedicated_company,
    run_ps,
)
from group_policy import is_blocked_group


def choose_enabled_account(
    accounts: list[dict], label: str, selected_username: str = ""
) -> dict:
    """Return an enabled account, honoring an explicit choice when supplied."""
    enabled = [a for a in accounts if a.get("enabled") and not a.get("disabled")]
    selected = (selected_username or "").strip().casefold()
    if selected:
        match = next(
            (
                account for account in enabled
                if (account.get("username") or "").strip().casefold() == selected
            ),
            None,
        )
        if match:
            return match
        raise ValueError(
            f"The selected AD account {selected_username} is no longer enabled for {label}."
        )
    if len(enabled) == 1:
        return enabled[0]
    if not enabled:
        raise ValueError(f"No enabled AD account was found for {label}.")
    names = ", ".join(a.get("username", "") for a in enabled)
    raise ValueError(f"Multiple enabled AD accounts were found for {label}: {names}")


def plan_group_changes(current_groups: list[str], buddy_groups: list[str]) -> dict:
    """Produce a case-insensitive diff without modifying protected memberships.

    Restricted and redundant groups are outside this automation's authority. They
    are never copied from the buddy, removed from the mover, or included in the
    managed-state verification. This is important for groups such as
    ``RDS-Disabled`` whose ACLs intentionally reject Service Desk changes.
    """
    current = {g.casefold(): g for g in current_groups if g}
    buddy = {g.casefold(): g for g in buddy_groups if g}
    blocked = sorted((g for g in buddy.values() if is_blocked_group(g)), key=str.casefold)
    preserved = sorted(
        (g for g in current.values() if is_blocked_group(g)), key=str.casefold
    )
    managed_current = {k: g for k, g in current.items() if not is_blocked_group(g)}
    desired = {k: g for k, g in buddy.items() if not is_blocked_group(g)}
    return {
        "add": sorted(
            (desired[k] for k in desired.keys() - managed_current.keys()), key=str.casefold
        ),
        "remove": sorted(
            (managed_current[k] for k in managed_current.keys() - desired.keys()),
            key=str.casefold,
        ),
        "keep": sorted(
            (desired[k] for k in desired.keys() & managed_current.keys()), key=str.casefold
        ),
        "desired": sorted(desired.values(), key=str.casefold),
        "blocked_buddy": blocked,
        "preserved_blocked": preserved,
    }


def get_mover_account_info(sam: str) -> dict:
    """Read all attributes required for mover planning in one AD request."""
    script = f"""
Import-Module ActiveDirectory -ErrorAction Stop
$u = Get-ADUser -Identity '{_e(sam)}' -Properties Enabled,DistinguishedName,DisplayName,Title,Description,Department,Company,Office,StreetAddress,City,PostalCode,Country,Manager,MemberOf,EmailAddress -ErrorAction Stop
$mgr = $null
if ($u.Manager) {{ $mgr = Get-ADUser -Identity $u.Manager -Properties SamAccountName,DisplayName,EmailAddress }}
$groups = @($u.MemberOf | ForEach-Object {{ (Get-ADGroup -Identity $_).Name }} | Sort-Object)
[pscustomobject]@{{
    sam = $u.SamAccountName
    display_name = [string]$u.DisplayName
    enabled = [bool]$u.Enabled
    dn = $u.DistinguishedName
    ou = ($u.DistinguishedName -replace '^CN=[^,]+,', '')
    title = [string]$u.Title
    description = [string]$u.Description
    department = [string]$u.Department
    company = [string]$u.Company
    office = [string]$u.Office
    street = [string]$u.StreetAddress
    city = [string]$u.City
    postal_code = [string]$u.PostalCode
    country = [string]$u.Country
    email = [string]$u.EmailAddress
    manager_sam = $(if ($mgr) {{ [string]$mgr.SamAccountName }} else {{ '' }})
    manager_name = $(if ($mgr) {{ [string]$mgr.DisplayName }} else {{ '' }})
    manager_email = $(if ($mgr) {{ [string]$mgr.EmailAddress }} else {{ '' }})
    groups = $groups
}} | ConvertTo-Json -Compress -Depth 4
"""
    out, err, code = run_ps(script, timeout=30)
    if code != 0 or not out:
        raise RuntimeError(err or f"Could not read AD account {sam}.")
    try:
        info = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AD returned an unreadable response for {sam}: {out}") from exc
    groups = info.get("groups") or []
    info["groups"] = [groups] if isinstance(groups, str) else list(groups)
    return info


def build_mover_plan(
    ticket: dict,
    mover: dict,
    buddy: dict,
    manager: dict,
    *,
    acknowledge_manager_mismatch: bool = False,
) -> dict:
    """Validate inputs and build a complete before/after plan without changing AD."""
    title = (ticket.get("new_position") or "").strip()
    company = (ticket.get("company_name") or "").strip()
    manager_name = (ticket.get("manager") or "").strip()
    if not title:
        raise ValueError("The Jira New job title field is empty.")
    if not company:
        raise ValueError("The Jira Company field is empty.")
    if not manager_name:
        raise ValueError("The Jira Manager field is empty.")
    if not mover.get("enabled"):
        raise ValueError("The mover's AD account is disabled.")
    if not buddy.get("enabled"):
        raise ValueError("The buddy's AD account is disabled.")
    if not manager.get("enabled"):
        raise ValueError("The Jira manager's AD account is disabled.")
    if mover.get("sam", "").casefold() == buddy.get("sam", "").casefold():
        raise ValueError("The mover cannot be used as their own buddy.")
    if not buddy.get("department"):
        raise ValueError("The buddy has no Department value in AD.")
    target_ou = (buddy.get("ou") or "").strip()
    if not re.match(r"(?i)^(OU|CN|DC)=", target_ou):
        raise ValueError("The buddy's target OU is not a valid Distinguished Name.")

    # Jira location precedence for movers:
    #   1. New Office Location, when explicitly populated
    #   2. Office Location (the employee remains at that site when New Office is '-')
    #   3. Company mapping, only when neither office field provides a location
    new_office = (ticket.get("new_office") or "").strip()
    office = (ticket.get("office") or ticket.get("current_office") or "").strip()
    address_signal = new_office or office
    if not detect_site(address_signal, company):
        raise ValueError(
            f"No safe address mapping exists for company/office: {company} / {address_signal}"
        )
    address_warning = detect_address_warning(address_signal, company)
    if address_warning:
        raise ValueError(address_warning)
    address = dict(detect_company_address(company, address_signal))
    if not address:
        raise ValueError(
            f"No safe address mapping exists for company/office: {company} / {address_signal}"
        )

    expected_manager_sam = (manager.get("sam") or "").strip()
    buddy_manager_sam = (buddy.get("manager_sam") or "").strip()
    if not buddy_manager_sam:
        raise ValueError("The buddy has no Manager in AD, so Jira Manager cannot be cross-checked.")
    manager_mismatch = bool(
        buddy_manager_sam
        and expected_manager_sam
        and buddy_manager_sam.casefold() != expected_manager_sam.casefold()
    )
    if manager_mismatch and not acknowledge_manager_mismatch:
        raise ValueError(
            f"Manager mismatch: Jira resolves to {expected_manager_sam}, but the buddy's "
            f"manager is {buddy_manager_sam}. Acknowledge the mismatch to use Jira Manager."
        )
    if not manager.get("email"):
        raise ValueError("The Jira manager has no email address in AD; extensionAttribute10 cannot be set.")

    groups = plan_group_changes(mover.get("groups", []), buddy.get("groups", []))
    if not groups["desired"]:
        raise ValueError("The buddy has no copyable direct AD groups.")

    # TNDM was migrated to Girteka Dedicated. Jira may still use either company
    # name, but both now follow the Girteka email policy. Other mover email
    # domains remain outside this workflow, as before.
    email_target = ""
    if is_girteka_dedicated_company(company):
        email_target = girteka_email_from_existing(mover.get("email", ""))

    return {
        "ticket": ticket,
        "mover": mover,
        "buddy": buddy,
        "manager": manager,
        "target_ou": target_ou,
        "title": title,
        "department": buddy["department"],
        "company": company,
        "office": address.get("office") or new_office,
        "address": address,
        "email_target": email_target,
        "groups": groups,
        "manager_mismatch": manager_mismatch,
        "axapta_notice": ticket.get("axapta_notice", ""),
    }


def _ps_array(values: list[str]) -> str:
    return "@(" + ",".join(f"'{_e(v)}'" for v in values) + ")"


def build_mover_script(plan: dict) -> str:
    """Build an idempotent, drift-detecting mover script with final verification."""
    mover = plan["mover"]
    buddy = plan["buddy"]
    manager = plan["manager"]
    groups = plan["groups"]
    address = plan["address"]
    sam = _e(mover["sam"])
    buddy_sam = _e(buddy["sam"])
    manager_sam = _e(manager["sam"])
    original_groups = sorted(mover.get("groups", []), key=str.casefold)
    buddy_groups = sorted(buddy.get("groups", []), key=str.casefold)
    email_target = (plan.get("email_target") or "").strip()
    current_email = (mover.get("email") or "").strip()
    mover_properties = (
        "Enabled,DistinguishedName,MemberOf,Title,Description,Department,Company,"
        "Office,StreetAddress,City,PostalCode,Country,Manager"
    )
    verify_properties = (
        "DistinguishedName,Title,Description,Department,Company,Office,"
        "StreetAddress,City,PostalCode,Country,Manager,MemberOf,"
        "extensionAttribute10,extensionAttribute15"
    )
    if email_target:
        mover_properties += ",EmailAddress,targetAddress,proxyAddresses"
        verify_properties += ",EmailAddress,targetAddress,proxyAddresses"

    lines = [
        "$cred = Get-Credential -Message 'Enter your AD admin credentials'",
        '$ErrorActionPreference = "Stop"',
        "Import-Module ActiveDirectory -ErrorAction Stop",
        "$dc = [string](Get-ADDomainController -Discover -Writable | Select-Object -ExpandProperty HostName)",
        "$PSDefaultParameterValues = @{'*-AD*:Credential' = $cred; '*-AD*:Server' = $dc}",
        f"$mover = Get-ADUser -Identity '{sam}' -Properties {mover_properties} -ErrorAction Stop",
        f"$buddy = Get-ADUser -Identity '{buddy_sam}' -Properties Enabled,DistinguishedName,MemberOf,Department,Manager -ErrorAction Stop",
        f"$manager = Get-ADUser -Identity '{manager_sam}' -Properties Enabled,EmailAddress -ErrorAction Stop",
        "if (-not $mover.Enabled) { throw 'Mover account is disabled.' }",
        "if (-not $buddy.Enabled) { throw 'Buddy account is disabled.' }",
        "if (-not $manager.Enabled) { throw 'Manager account is disabled.' }",
        "if (-not $manager.EmailAddress) { throw 'Manager email is empty.' }",
        f"if (($buddy.DistinguishedName -replace '^CN=[^,]+,','') -ne '{_e(plan['target_ou'])}') {{ throw 'Buddy OU changed after preview. Refresh the plan.' }}",
        f"if ([string]$buddy.Department -ne '{_e(plan['department'])}') {{ throw 'Buddy Department changed after preview. Refresh the plan.' }}",
        "if (-not $buddy.Manager) { throw 'Buddy Manager was cleared after preview. Refresh the plan.' }",
        "$buddyManagerSam = (Get-ADUser -Identity $buddy.Manager -Properties SamAccountName).SamAccountName",
        f"if ($buddyManagerSam -ne '{_e(buddy.get('manager_sam', ''))}') {{ throw 'Buddy Manager changed after preview. Refresh the plan.' }}",
        "$currentGroups = @($mover.MemberOf | ForEach-Object { (Get-ADGroup -Identity $_).Name } | Sort-Object)",
        "$buddyGroups = @($buddy.MemberOf | ForEach-Object { (Get-ADGroup -Identity $_).Name } | Sort-Object)",
        f"$expectedCurrent = @({_ps_array(original_groups)} | Sort-Object)",
        f"$expectedBuddy = @({_ps_array(buddy_groups)} | Sort-Object)",
        "if (@(Compare-Object $currentGroups $expectedCurrent).Count -ne 0) { throw 'Mover groups changed after preview. Refresh the plan.' }",
        "if (@(Compare-Object $buddyGroups $expectedBuddy).Count -ne 0) { throw 'Buddy groups changed after preview. Refresh the plan.' }",
        'Write-Host "=== Before ==="',
        f"Write-Host 'Mover: {sam}'",
        "Write-Host \"OU: $($mover.DistinguishedName -replace '^CN=[^,]+,','')\"",
        'Write-Host "Title: $($mover.Title)"',
        'Write-Host "Description: $($mover.Description)"',
        'Write-Host "Department: $($mover.Department)"',
        'Write-Host "Company: $($mover.Company)"',
        'Write-Host "Office: $($mover.Office)"',
        'Write-Host "Address: $($mover.StreetAddress), $($mover.City), $($mover.PostalCode), $($mover.Country)"',
        'Write-Host "Manager DN: $($mover.Manager)"',
        *(['Write-Host "Email: $($mover.EmailAddress)"'] if email_target else []),
        'Write-Host "Groups: $($currentGroups -join \' | \')"',
        "",
        "# Add desired memberships first, so the account never loses shared access mid-run.",
        "$failedAddGroups = @()",
        "$failedRemoveGroups = @()",
        "$groupFailureMessages = @()",
    ]
    for group in groups["add"]:
        escaped_group = _e(group)
        lines += [
            "try {",
            f"    $groupName = '{escaped_group}'",
            "    $targetGroups = @(Get-ADGroup -Filter { Name -eq $groupName } -ErrorAction Stop)",
            "    if ($targetGroups.Count -ne 1) { throw \"Expected one AD group named '$groupName'; found $($targetGroups.Count).\" }",
            f"    Add-ADGroupMember -Identity $targetGroups[0].DistinguishedName -Members '{sam}' -ErrorAction Stop",
            f"    Write-Host 'OK  Added group: {escaped_group}'",
            "} catch {",
            f"    $failedAddGroups += '{escaped_group}'",
            f"    $groupFailureMessages += ('ADD | ' + '{escaped_group}' + ' | ' + $_.Exception.Message)",
            f"    Write-Warning ('MANUAL GROUP FOLLOW-UP - could not add: ' + '{escaped_group}')",
            "}",
        ]

    if email_target:
        retired_domain_pattern = RETIRED_TNDM_EMAIL_DOMAIN.replace(".", r"\.")
        lines += [
            "",
            "# Apply Girteka email policy for TNDM / Girteka Dedicated movers.",
            f"if ([string]$mover.EmailAddress -ne '{_e(current_email)}') {{ throw 'Mover email changed after preview. Refresh the plan.' }}",
            f"$newEmail = '{_e(email_target)}'",
            "$newPrimary = 'SMTP:' + $newEmail",
            "$currentProxies = @($mover.proxyAddresses)",
            "foreach ($addr in $currentProxies) {",
            f"    if ($addr -imatch '^smtp:.+@{retired_domain_pattern}$') {{",
            f"        Set-ADUser -Identity '{sam}' -Remove @{{proxyAddresses=$addr}} -ErrorAction Stop",
            "        continue",
            "    }",
            "    if ($addr -ieq $newPrimary -and $addr -cne $newPrimary) {",
            f"        Set-ADUser -Identity '{sam}' -Remove @{{proxyAddresses=$addr}} -ErrorAction Stop",
            "        continue",
            "    }",
            "    if ($addr -cmatch '^SMTP:' -and $addr -cne $newPrimary) {",
            "        $secondary = 'smtp:' + $addr.Substring(5)",
            f"        Set-ADUser -Identity '{sam}' -Remove @{{proxyAddresses=$addr}} -ErrorAction Stop",
            f"        Set-ADUser -Identity '{sam}' -Add @{{proxyAddresses=$secondary}} -ErrorAction Stop",
            "    }",
            "}",
            f"$updatedProxies = @((Get-ADUser -Identity '{sam}' -Properties proxyAddresses).proxyAddresses)",
            "if ($updatedProxies -cnotcontains $newPrimary) {",
            f"    Set-ADUser -Identity '{sam}' -Add @{{proxyAddresses=$newPrimary}} -ErrorAction Stop",
            "}",
            f"Set-ADUser -Identity '{sam}' -EmailAddress $newEmail -Replace @{{targetAddress=$newEmail}} -ErrorAction Stop",
            'Write-Host "OK  Girteka email and proxy addresses applied"',
        ]

    lines += [
        "",
        "$setParams = @{",
        f"    Title = '{_e(plan['title'])}'",
        f"    Description = '{_e(plan['title'])}'",
        f"    Department = '{_e(plan['department'])}'",
        f"    Company = '{_e(plan['company'])}'",
        f"    Office = '{_e(plan['office'])}'",
        f"    Manager = $manager.DistinguishedName",
    ]
    for param, key in (
        ("StreetAddress", "street"),
        ("City", "city"),
        ("PostalCode", "zip"),
        ("Country", "country"),
    ):
        if address.get(key):
            lines.append(f"    {param} = '{_e(address[key])}'")
    lines += [
        "}",
        f"Set-ADUser -Identity '{sam}' @setParams -ErrorAction Stop",
        f"Set-ADUser -Identity '{sam}' -Replace @{{extensionAttribute10=$manager.EmailAddress}} -ErrorAction Stop",
    ]
    clear_fields = [
        param for param, key in (
            ("StreetAddress", "street"), ("City", "city"),
            ("PostalCode", "zip"), ("Country", "country"),
        ) if not address.get(key)
    ]
    if clear_fields:
        lines.append(
            f"Set-ADUser -Identity '{sam}' -Clear {','.join(clear_fields)} -ErrorAction Stop"
        )
    if address.get("ext15"):
        lines.append(
            f"Set-ADUser -Identity '{sam}' -Replace @{{extensionAttribute15='{_e(address['ext15'])}'}} -ErrorAction Stop"
        )
    else:
        lines.append(f"Set-ADUser -Identity '{sam}' -Clear extensionAttribute15 -ErrorAction Stop")
    lines += [
        'Write-Host "OK  Organisation, manager, and address updated"',
        "",
        f"$latest = Get-ADUser -Identity '{sam}' -Properties DistinguishedName",
        f"if (($latest.DistinguishedName -replace '^CN=[^,]+,','') -ne '{_e(plan['target_ou'])}') {{",
        f"    Move-ADObject -Identity $latest.DistinguishedName -TargetPath '{_e(plan['target_ou'])}' -ErrorAction Stop",
        '    Write-Host "OK  Moved to buddy OU"',
        "} else { Write-Host 'OK  Already in buddy OU' }",
        "",
        "# Remove only memberships that are outside the approved buddy-derived final state.",
    ]
    for group in groups["remove"]:
        escaped_group = _e(group)
        lines += [
            "try {",
            f"    $groupName = '{escaped_group}'",
            "    $targetGroups = @(Get-ADGroup -Filter { Name -eq $groupName } -ErrorAction Stop)",
            "    if ($targetGroups.Count -ne 1) { throw \"Expected one AD group named '$groupName'; found $($targetGroups.Count).\" }",
            f"    Remove-ADGroupMember -Identity $targetGroups[0].DistinguishedName -Members '{sam}' -Confirm:$false -ErrorAction Stop",
            f"    Write-Host 'OK  Removed group: {escaped_group}'",
            "} catch {",
            f"    $failedRemoveGroups += '{escaped_group}'",
            f"    $groupFailureMessages += ('REMOVE | ' + '{escaped_group}' + ' | ' + $_.Exception.Message)",
            f"    Write-Warning ('MANUAL GROUP FOLLOW-UP - could not remove: ' + '{escaped_group}')",
            "}",
        ]

    desired = groups["desired"]
    lines += [
        "",
        f"$verify = Get-ADUser -Identity '{sam}' -Properties {verify_properties}",
        "$verifyGroups = @($verify.MemberOf | ForEach-Object { (Get-ADGroup -Identity $_).Name } | Sort-Object)",
        f"$ignoredGroups = @({_ps_array(groups['preserved_blocked'])} | Sort-Object)",
        "$verifyManagedGroups = @($verifyGroups | Where-Object { $ignoredGroups -notcontains $_ } | Sort-Object)",
        f"$desiredGroups = @({_ps_array(desired)} | Sort-Object)",
        "$expectedManagedGroups = @($desiredGroups | Where-Object { $failedAddGroups -notcontains $_ })",
        "$expectedManagedGroups += @($failedRemoveGroups)",
        "$expectedManagedGroups = @($expectedManagedGroups | Sort-Object -Unique)",
        "if (@(Compare-Object $verifyManagedGroups $expectedManagedGroups).Count -ne 0) { throw 'Final managed-group verification failed.' }",
        f"if (($verify.DistinguishedName -replace '^CN=[^,]+,','') -ne '{_e(plan['target_ou'])}') {{ throw 'OU verification failed.' }}",
        f"if ($verify.Title -ne '{_e(plan['title'])}' -or $verify.Description -ne '{_e(plan['title'])}') {{ throw 'Title/Description verification failed.' }}",
        f"if ($verify.Department -ne '{_e(plan['department'])}') {{ throw 'Department verification failed.' }}",
        f"if ($verify.Company -ne '{_e(plan['company'])}') {{ throw 'Company verification failed.' }}",
        f"if ($verify.Office -ne '{_e(plan['office'])}') {{ throw 'Office verification failed.' }}",
        "if ($verify.Manager -ne $manager.DistinguishedName) { throw 'Manager verification failed.' }",
        "if ($verify.extensionAttribute10 -ne $manager.EmailAddress) { throw 'Manager email attribute verification failed.' }",
        'Write-Host "Title: $($verify.Title)"',
        'Write-Host "Department: $($verify.Department)"',
        'Write-Host "Company: $($verify.Company)"',
        'Write-Host "Office: $($verify.Office)"',
        *(['Write-Host "Email: $($verify.EmailAddress)"'] if email_target else []),
        'Write-Host "Groups: $($verifyGroups -join \' | \')"',
    ]
    if email_target:
        retired_domain_pattern = RETIRED_TNDM_EMAIL_DOMAIN.replace(".", r"\.")
        lines.insert(
            -6,
            f"if ([string]$verify.EmailAddress -ine '{_e(email_target)}') {{ throw 'Email verification failed.' }}",
        )
        lines.insert(
            -6,
            f"if ([string]$verify.targetAddress -ine '{_e(email_target)}') {{ throw 'Target address verification failed.' }}",
        )
        lines.insert(
            -6,
            f"if (@($verify.proxyAddresses | Where-Object {{ $_ -imatch '^smtp:.+@{retired_domain_pattern}$' }}).Count -ne 0) {{ throw 'Retired TNDM proxy verification failed.' }}",
        )
        lines.insert(
            -6,
            f"if (@($verify.proxyAddresses | Where-Object {{ $_ -ceq 'SMTP:{_e(email_target)}' }}).Count -ne 1) {{ throw 'Primary SMTP verification failed.' }}",
        )
    for prop, key, label in (
        ("StreetAddress", "street", "Street address"),
        ("City", "city", "City"),
        ("PostalCode", "zip", "Postal code"),
        ("Country", "country", "Country"),
        ("extensionAttribute15", "ext15", "extensionAttribute15"),
    ):
        expected = _e(address.get(key, ""))
        lines.insert(
            -5,
            f"if ([string]$verify.{prop} -ne '{expected}') {{ throw '{label} verification failed.' }}",
        )
    lines.insert(-5, 'Write-Host "=== Verification passed for applied changes ==="')
    lines += [
        "if ($groupFailureMessages.Count -gt 0) {",
        "    Write-Warning 'The AD move continued, but manual group follow-up is required:'",
        "    $groupFailureMessages | ForEach-Object { Write-Warning $_ }",
        "    exit 2",
        "}",
    ]
    return "\n".join(lines)


def format_mover_plan(plan: dict) -> str:
    """Human-readable review shown before PowerShell is generated or applied."""
    groups = plan["groups"]
    mover = plan["mover"]
    buddy = plan["buddy"]
    address = plan["address"]
    lines = [
        f"Account: {mover['sam']}",
        f"Buddy: {buddy['sam']}",
        f"OU: {mover.get('ou', '')}  ->  {plan['target_ou']}",
        f"Title: {mover.get('title', '')}  ->  {plan['title']}",
        f"Description: {mover.get('description', '')}  ->  {plan['title']}",
        f"Department: {mover.get('department', '')}  ->  {plan['department']}",
        f"Company: {mover.get('company', '')}  ->  {plan['company']}",
        f"Manager: {mover.get('manager_name', '')}  ->  {plan['manager'].get('display_name') or plan['manager'].get('sam', '')}",
        f"Address: {address.get('street', '')}, {address.get('city', '')}, {address.get('country', '')}",
        "",
        f"Groups to add ({len(groups['add'])}): " + (", ".join(groups["add"]) or "none"),
        f"Groups to remove ({len(groups['remove'])}): " + (", ".join(groups["remove"]) or "none"),
        f"Groups already correct ({len(groups['keep'])}): " + (", ".join(groups["keep"]) or "none"),
    ]
    email_target = plan.get("email_target", "")
    if email_target:
        current_email = mover.get("email", "")
        email_note = "already correct" if current_email.casefold() == email_target.casefold() else "update"
        lines.insert(8, f"Email ({email_note}): {current_email}  ->  {email_target}")
    if groups["blocked_buddy"]:
        lines += [
            "",
            "Manual/approval-controlled groups not copied: "
            + ", ".join(groups["blocked_buddy"]),
        ]
    if groups.get("preserved_blocked"):
        lines += [
            "Protected memberships left unchanged: "
            + ", ".join(groups["preserved_blocked"]),
        ]
    if plan.get("manager_mismatch"):
        lines += ["", "WARNING: Buddy manager differs; the Jira Manager will be used."]
    if plan.get("axapta_notice"):
        lines += ["", "AXAPTA: " + plan["axapta_notice"]]
    return "\n".join(lines)
