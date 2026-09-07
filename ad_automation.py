"""
AD automation helpers - Girteka new joiner / rejoiner onboarding.

Accounts are always auto-provisioned by SAP SuccessFactors into the SF OU.
We never create accounts from scratch; we move, enable, and configure them.

Scenarios detected by searching AD by first+last name:
  new_joiner      - one account, located in SF_OU  (just provisioned)
  rejoiner_dual   - two accounts: one in SF_OU (dummy) + one old disabled account
  rejoiner_single - one account, NOT in SF_OU (disabled or already active)
  unknown         - unexpected state, needs manual review
"""
import os
import re
import secrets
import subprocess
import tempfile
import unicodedata
from html import unescape

from group_policy import is_blocked_group

SF_OU_FRAGMENT = "Active_Users_from_SF"   # substring present in the SF provisioning OU

# ── Helpers ───────────────────────────────────────────────────────────────────

_TRANSLIT = str.maketrans("ąčęėįšųūžĄČĘĖĮŠŲŪŽ", "aceeisuuzACEEISUUZ")

GIRTEKA_EMAIL_DOMAIN = "girteka.eu"
RETIRED_TNDM_EMAIL_DOMAIN = "tndmtrucking.com"


def _ascii(name: str) -> str:
    return name.translate(_TRANSLIT)

def _e(s: str) -> str:
    """Escape single quotes for PowerShell single-quoted strings."""
    return str(s).replace("'", "''")


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value or "")


def _normalize_manager_name(value: str) -> str:
    value = unescape(_nfc(value)).strip()
    if not value:
        return ""
    mailto = re.search(r"mailto:([^'\">\s]+)", value, flags=re.IGNORECASE)
    if mailto:
        return mailto.group(1).strip()
    value = re.sub(r"<[^>]+>", "", value).strip()
    value = re.sub(r"^(hiring\s+manager|manager)\s*:\s*", "", value, flags=re.IGNORECASE)
    return value.lstrip(": ").strip()


def _manager_lookup_lines(manager: str) -> list[str]:
    if not manager:
        return []
    lookup_field = "EmailAddress" if "@" in manager else "DisplayName"
    return [
        "$mgrDn = $null",
        "$mgrEmail = $null",
        "try {",
        f"    $mgrMatches = @(Get-ADUser -Filter {{{lookup_field} -eq '{manager}'}} -Properties DistinguishedName, EmailAddress)",
        "    if ($mgrMatches.Count -eq 1) {",
        "        $mgrDn    = $mgrMatches[0].DistinguishedName",
        "        $mgrEmail = $mgrMatches[0].EmailAddress",
        "    } elseif ($mgrMatches.Count -eq 0) {",
        f"        Write-Warning 'Manager not found: {manager}'",
        "    } else {",
        f"        Write-Warning 'Multiple managers found for: {manager}. Manager was not set.'",
        "    }",
        "} catch {",
        f"    Write-Warning 'Manager lookup failed for: {manager} — $_. Manager will not be set.'",
        "}",
        "",
    ]


def _proxy_address_lines(sam: str, email: str) -> list[str]:
    return [
        "# Set primary SMTP proxy address",
        f"$smtpAddress = 'SMTP:{_e(email)}'",
        f"$currentProxies = @(Get-ADUser -Identity '{sam}' -Properties proxyAddresses).proxyAddresses",
        "# Remove addresses from TNDM's retired mail domain",
        f"$retiredTndmProxies = $currentProxies | Where-Object {{ $_ -imatch '^smtp:.+@{RETIRED_TNDM_EMAIL_DOMAIN.replace('.', r'\.')}$' }}",
        "foreach ($addr in $retiredTndmProxies) {",
        f"    Set-ADUser -Identity '{sam}' -Remove @{{proxyAddresses=$addr}}",
        '    Write-Host "OK  Removed retired TNDM proxy $addr"',
        "}",
        "",
        "# Promote this address to the one case-sensitive primary SMTP value",
        "$wrongCaseMatches = $currentProxies | Where-Object { $_ -ieq $smtpAddress -and $_ -cne $smtpAddress }",
        "foreach ($addr in $wrongCaseMatches) {",
        f"    Set-ADUser -Identity '{sam}' -Remove @{{proxyAddresses=$addr}}",
        "}",
        f"$currentProxies = @((Get-ADUser -Identity '{sam}' -Properties proxyAddresses).proxyAddresses)",
        "if ($currentProxies -cnotcontains $smtpAddress) {",
        f"    Set-ADUser -Identity '{sam}' -Add @{{proxyAddresses=$smtpAddress}}",
        '    Write-Host "OK  proxyAddresses set"',
        "} else {",
        '    Write-Host "OK  proxyAddresses already present"',
        "}",
        "",
        "# Ensure girteka.lt proxy address is lowercase smtp (secondary only)",
        "$ltUpper = $currentProxies | Where-Object { $_ -cmatch '^SMTP:.+@girteka\\.lt$' }",
        "foreach ($addr in $ltUpper) {",
        f"    $lower = 'smtp:' + $addr.Substring(5)",
        f"    Set-ADUser -Identity '{sam}' -Remove @{{proxyAddresses=$addr}}",
        f"    Set-ADUser -Identity '{sam}' -Add @{{proxyAddresses=$lower}}",
        '    Write-Host "OK  Lowercased $addr -> $lower"',
        "}",
        "",
        "# Set targetAddress to match primary SMTP",
        f"Set-ADUser -Identity '{sam}' -Replace @{{targetAddress='{_e(email)}'}}",
        'Write-Host "OK  targetAddress set"',
        "",
    ]


def _set_user_attribute_lines(sam: str, email: str, title: str,
                              office: str, manager: str,
                              company: str = "", address: dict = None,
                              department: str = "") -> list[str]:
    addr = address or {}
    effective_office = addr.get("office") or office
    L = [
        "# Update organisation attributes",
        "$setParams = @{",
        f"    EmailAddress = '{_e(email)}'",
    ]
    if title:
        L.append(f"    Title = '{title}'")
        L.append(f"    Description = '{title}'")
    if effective_office:
        L.append(f"    Office = '{_e(effective_office)}'")
    if department:
        L.append(f"    Department = '{_e(department)}'")
    if company:
        L.append(f"    Company = '{_e(company)}'")
    if addr.get("street"):
        L.append(f"    StreetAddress = '{_e(addr['street'])}'")
    if addr.get("city"):
        L.append(f"    City = '{_e(addr['city'])}'")
    if addr.get("zip"):
        L.append(f"    PostalCode = '{_e(addr['zip'])}'")
    if addr.get("country"):
        L.append(f"    Country = '{_e(addr['country'])}'")
    L.append("}")
    if manager:
        L.append("if ($mgrDn) { $setParams['Manager'] = $mgrDn }")
    L += [
        f"Set-ADUser -Identity '{sam}' @setParams",
        'Write-Host "OK  Attributes updated"',
        "if ($mgrEmail) {",
        f"    Set-ADUser -Identity '{sam}' -Replace @{{extensionAttribute10=$mgrEmail}}",
        '    Write-Host "OK  extensionAttribute10 set to $mgrEmail"',
        "} else {",
        '    Write-Warning "extensionAttribute10 not set — manager email not found"',
        "}",
    ]
    ext15 = addr.get("ext15", "") if addr else ""
    if ext15:
        L += [
            f"Set-ADUser -Identity '{sam}' -Replace @{{extensionAttribute15='{_e(ext15)}'}}",
            f'Write-Host "OK  extensionAttribute15 set to {ext15}"',
        ]
    else:
        L += [
            f"Set-ADUser -Identity '{sam}' -Clear extensionAttribute15",
            'Write-Host "OK  extensionAttribute15 cleared"',
        ]
    return L

# ── Location / domain detection ───────────────────────────────────────────────

def _normalise_location_text(value: str) -> str:
    """Return punctuation/diacritic-insensitive text for Jira location matching."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("ł", "l")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _matches_any(value: str, keywords: list[str]) -> bool:
    padded = f" {_normalise_location_text(value)} "
    return any(f" {_normalise_location_text(keyword)} " in padded for keyword in keywords)


_GIRTEKA_DEDICATED_COMPANY_ALIASES = [
    "tndm",
    "tndm trucking",
    "girteka dedicated",
]


def is_girteka_dedicated_company(company: str) -> bool:
    """Return whether Jira names the former TNDM/Girteka Dedicated company."""
    return _matches_any(company, _GIRTEKA_DEDICATED_COMPANY_ALIASES)


_OFFICE_SITE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("georgia", ["georgia", "gbs", "tbilisi", "kutaisi", "business services"]),
    ("poland", ["poland", "warszawa", "warsaw", "krakow", "wroclaw",
                "poznan", "poznan campus", "poznanska", "sady"]),
    ("siauliai", ["siauliai", "siauliai campus"]),
    ("vilnius", ["vilnius", "girteka park", "laisves"]),
]

_COMPANY_SITE_KEYWORDS: list[tuple[str, list[str]]] = [
    # Specific locations must precede generic Girteka/ME Trailers/ClassTrucks
    # matches so Polish and GBS legal entities cannot fall through to Vilnius.
    ("georgia", ["girteka business services", "business services"]),
    ("poland", [
        "girpoltrans", "transeu poland", "eupoltrans", "scanpoltrans",
        "polservice", "gotrans", "me trailers poland", "classtrucks poland",
    ]),
    ("siauliai", [
        "mireli", "trasis", "girmeta", "termolita", "girtrans",
        "klp transport", "premium trans", "termotrans",
    ]),
    ("vilnius", [
        "trucks merchant", "willgrow", "gcc",
        *_GIRTEKA_DEDICATED_COMPANY_ALIASES,
        "girteka nordic", "girteka transport", "girteka group",
        "girteka logistics", "me trailers", "girteka cargo",
        "classtrucks", "girteka",
    ]),
]


def _detect_site_from(value: str, mappings: list[tuple[str, list[str]]]) -> str:
    for site, keywords in mappings:
        if _matches_any(value, keywords):
            return site
    return ""


def detect_site(office: str, company: str = "") -> str:
    """Detect the physical site, preferring the employee's Jira office field."""
    return (
        _detect_site_from(office, _OFFICE_SITE_KEYWORDS)
        or _detect_site_from(company, _COMPANY_SITE_KEYWORDS)
    )


def detect_location_conflict(office: str, company: str = "") -> str:
    """Describe a disagreement between independently recognised Jira fields."""
    office_site = _detect_site_from(office, _OFFICE_SITE_KEYWORDS)
    company_site = _detect_site_from(company, _COMPANY_SITE_KEYWORDS)
    if not office_site or not company_site or office_site == company_site:
        return ""
    labels = {
        "vilnius": "Vilnius",
        "siauliai": "Šiauliai",
        "poland": "Poland",
        "georgia": "GBS",
    }
    return (
        f"Office Location indicates {labels[office_site]}, but Company indicates "
        f"{labels[company_site]}. Office Location was selected; verify before applying."
    )


def detect_address_warning(office: str, company: str = "") -> str:
    """Warn when a site is known but its exact fixed address is not."""
    if detect_site(office, company) != "poland":
        return ""
    if (
        _matches_any(office, ["poznanska", "sady"])
        or _matches_any(company, ["girpoltrans"])
    ):
        return ""
    return (
        "This is a recognised Polish company, but its exact campus address is not "
        "confirmed. The Jira office and Poland defaults will be used without "
        "forcing the Sady street, city, or postal code."
    )


def detect_location(office: str, company: str = "") -> str:
    """Return the regional default-group key used by the AD setup UI."""
    site = detect_site(office, company)
    if site == "poland":
        return "poland"
    if site == "georgia":
        return "georgia"
    return "lithuania"

_DOMAIN_MAP = [
    ("everwest",    "everwest.net"),
    ("willgrow",    "willgrow.com"),
    ("sirin",       "sirin.eu"),
    ("classtrucks", "classtrucks.com"),
    ("girteka",     GIRTEKA_EMAIL_DOMAIN),
]

def detect_domain(company: str) -> str:
    if is_girteka_dedicated_company(company):
        return GIRTEKA_EMAIL_DOMAIN
    for keyword, domain in _DOMAIN_MAP:
        if keyword in company.lower():
            return domain
    return GIRTEKA_EMAIL_DOMAIN


def uses_retired_tndm_email_domain(email: str) -> bool:
    """Return whether an address still uses TNDM's retired email domain."""
    _local, separator, domain = (email or "").strip().rpartition("@")
    return bool(separator and domain.casefold() == RETIRED_TNDM_EMAIL_DOMAIN)


def girteka_email_from_existing(email: str) -> str:
    """Keep an existing email local part and move it to Girteka's domain."""
    email = (email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("The AD account has no valid email address to migrate to Girteka.")
    local, _separator, _domain = email.rpartition("@")
    return f"{local}@{GIRTEKA_EMAIL_DOMAIN}"

_LAISVES_ADDRESS = {
    "street":  "Laisvės pr. 36",
    "city":    "Vilnius",
    "zip":     "5623",
    "country": "LT",
    "office":  "Vilnius",
    "ext15":   "SF",
}

_SIAULIAI_ADDRESS = {
    "street":  "Pročiūnų g. 16",
    "city":    "Šiauliai",
    "zip":     "77103",
    "country": "LT",
    "office":  "Siauliai Campus",
    "ext15":   "SF",
}

_GBS_ADDRESS = {
    "street":  "Georgia, Tbilisi, Vake District, Ilia Chavchavadze Avenue, No. 37L, Commercial Space No. 5, Floor 3-4, Block A",
    "city":    "Tbilisi",
    "zip":     "0162",
    "country": "GE",
    "office":  "Tbilisi",
    "ext15":   "SF",
}

_POZNAN_ADDRESS = {
    "street":  "Poznańska 4",
    "city":    "Sady",
    "zip":     "",
    "country": "PL",
    "office":  "Poznan",
    "ext15":   "",
}

def detect_company_address(company: str, office: str = "") -> dict:
    """Return safe AD location overrides derived from real Jira fields.

    Office Location is authoritative.  For Polish companies whose exact campus
    address is unknown, only the Jira office and country are returned so SF
    street/city/postal data is not replaced with the Sady address.
    """
    site = detect_site(office, company)
    if site == "georgia":
        return _GBS_ADDRESS
    if site == "siauliai":
        return _SIAULIAI_ADDRESS
    if site == "poland":
        # The Sady address is confirmed by the explicit Jira address and by the
        # existing Girpoltrans mapping, but not for every Polish legal entity.
        if (
            _matches_any(office, ["poznanska", "sady"])
            or _matches_any(company, ["girpoltrans"])
        ):
            return _POZNAN_ADDRESS
        return {
            "street":  "",
            "city":    "",
            "zip":     "",
            "country": "PL",
            "office":  (office or "Poznan").strip(),
            "ext15":   "",
        }
    if site == "vilnius":
        return _LAISVES_ADDRESS
    return {}

def build_email(first: str, last: str, domain: str) -> str:
    f = _ascii(first).strip()
    l = _ascii(last).strip()
    if (domain or "").strip().casefold() == RETIRED_TNDM_EMAIL_DOMAIN:
        domain = GIRTEKA_EMAIL_DOMAIN
    return f"{f.capitalize()}.{l.capitalize()}@{domain}"

# ── Default AD groups ─────────────────────────────────────────────────────────

DEFAULT_GROUPS: dict[str, list[str]] = {
    "lithuania": ["AX30", "AX30_LT", "VPN Work From Home", "M365_E3", "MFA Users", "WFG All"],
    "poland":    ["AX30", "AX30_LT", "M365_E3", "MFA Users", "VPN Work From Home",
                  "WFG All", "PL Baze", "PrintSrv PL"],
    "georgia":   ["AX30", "AX30_LT", "M365_E3", "MFA Users", "VPN Work From Home",
                  "WFG All", "DL_GBS_office"],
}

# ── Password generation ───────────────────────────────────────────────────────

def generate_password(length: int = 10) -> str:
    """Generate a short readable password with one special character."""
    if length < 4:
        raise ValueError("Password length must be at least 4 characters.")

    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    special = "#@!"
    pool = upper + lower + digits

    chars = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    chars.extend(secrets.choice(pool) for _ in range(length - len(chars)))

    randomizer = secrets.SystemRandom()
    randomizer.shuffle(chars)
    return "".join(chars)

# ── PowerShell runner ─────────────────────────────────────────────────────────

def run_ps(script: str, timeout: int = 90) -> tuple[str, str, int]:
    script = (
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8;\n"
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;\n"
        "$OutputEncoding = [System.Text.Encoding]::UTF8;\n"
        + script
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    script_path = ""
    try:
        # Passing generated scripts through -Command can exceed Windows' 32K
        # process command-line limit for users with many group memberships.
        # UTF-8 with BOM is used because Windows PowerShell 5.1 otherwise reads
        # non-ASCII .ps1 files using the system code page.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".ps1",
            delete=False,
            encoding="utf-8-sig",
            newline="\n",
        ) as script_file:
            script_file.write(script)
            script_path = script_file.name

        r = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "RemoteSigned",
                "-File", script_path,
            ],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    finally:
        if script_path:
            try:
                os.remove(script_path)
            except FileNotFoundError:
                pass


def sam_exists(sam: str) -> bool:
    """Returns True if the SAM account exists in Active Directory."""
    _, _, code = run_ps(
        f"Import-Module ActiveDirectory -ErrorAction Stop; "
        f"Get-ADUser -Identity '{_e(sam)}' -ErrorAction Stop | Out-Null",
        timeout=10,
    )
    return code == 0


DISABLED_OU_FRAGMENT = "OU=Disabled by Jira"


def get_account_enabled_status(sam: str) -> tuple[bool | None, str, str]:
    """
    Returns (enabled, dn, display_name) for a SAM account.
    enabled is None if the account was not found.
    display_name is the AD Name attribute (typically 'Firstname Lastname').
    """
    out, _, code = run_ps(
        f"Import-Module ActiveDirectory -ErrorAction Stop; "
        f"$u = Get-ADUser -Identity '{_e(sam)}' -Properties Enabled,DistinguishedName,Name -ErrorAction Stop; "
        f"\"$($u.Enabled)|$($u.DistinguishedName)|$($u.Name)\"",
        timeout=10,
    )
    if code != 0 or not out:
        return None, "", ""
    parts = out.split("|", 2)
    if len(parts) < 3:
        return None, "", ""
    return parts[0].strip() == "True", parts[1].strip(), parts[2].strip()


def is_buddy_disabled(sam: str) -> bool:
    """Returns True if the account is disabled or sits in the disabled OU."""
    enabled, dn, _ = get_account_enabled_status(sam)
    if enabled is None:
        return False
    return not enabled or DISABLED_OU_FRAGMENT.lower() in dn.lower()


def _format_ps_error(out: str, err: str, code: int) -> str:
    details = "\n".join(part for part in (out, err) if part).strip()
    if details:
        return details
    return f"PowerShell exited with code {code}."


# ── AD queries ────────────────────────────────────────────────────────────────

def _without_diacritics(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(ch)
    )


def _unique_values(*values: str) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _nfc(value).strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(value)
    return results


def _ps_eq_any(prop: str, values: list[str]) -> str:
    if not values:
        return "$false"
    return "(" + " -or ".join(f"{prop} -eq '{_e(v)}'" for v in values) + ")"


def _ps_like_any(prop: str, values: list[str]) -> str:
    if not values:
        return "$false"
    return "(" + " -or ".join(f"{prop} -like '{_e(v)}'" for v in values) + ")"


def _parse_account_search_output(out: str) -> list[dict]:
    results = []
    seen: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 4:
            continue
        username = parts[0].strip()
        dn = parts[3].strip()
        if "\\0ACNF:" in dn or "CNF:" in dn:
            continue
        key = f"{username}|{dn}".casefold()
        if key in seen:
            continue
        seen.add(key)
        enabled = parts[1].strip() == "True"
        results.append({
            "username":    username,
            "enabled":     enabled,
            "disabled":    (not enabled) or DISABLED_OU_FRAGMENT.lower() in dn.lower(),
            "title":       parts[2].strip(),
            "dn":          dn,
            "ou":          re.sub(r"^CN=[^,]+,", "", dn),
            "is_sf":       SF_OU_FRAGMENT in dn,
            "employee_id": parts[4].strip() if len(parts) > 4 else "",
        })
    return results


def find_user_accounts(first: str, last: str) -> list[dict]:
    """
    Search AD by first+last name. Returns all matching accounts with metadata.
    Each dict has: username, enabled, title, dn, ou, is_sf, employee_id
    """
    first = _nfc(first)
    last = _nfc(last)
    first_values = _unique_values(first, _ascii(first), _without_diacritics(first))
    last_values = _unique_values(last, _ascii(last), _without_diacritics(last))
    display_values = _unique_values(
        f"{first} {last}",
        f"{_ascii(first)} {_ascii(last)}",
        f"{_without_diacritics(first)} {_without_diacritics(last)}",
        f"{last} {first}",
        f"{_ascii(last)} {_ascii(first)}",
        f"{_without_diacritics(last)} {_without_diacritics(first)}",
    )
    display_patterns = _unique_values(*[
        pattern
        for first_value in first_values
        for last_value in last_values
        for pattern in (
            f"{first_value}*{last_value}*",
            f"{last_value}*{first_value}*",
        )
    ])
    first_clause = _ps_eq_any("GivenName", first_values)
    last_clause = _ps_eq_any("Surname", last_values)
    first_prefix_clause = _ps_like_any("GivenName", [f"{v}*" for v in first_values])
    last_prefix_clause = _ps_like_any("Surname", [f"{v}*" for v in last_values])
    display_clause = _ps_eq_any("Name", display_values)
    display_name_clause = _ps_eq_any("DisplayName", display_values)
    display_like_clause = _ps_like_any("Name", display_patterns)
    display_name_like_clause = _ps_like_any("DisplayName", display_patterns)
    out, err, code = run_ps(f"""
Import-Module ActiveDirectory -ErrorAction Stop
$users = Get-ADUser -Filter {{({first_clause} -and {last_clause}) -or ({first_prefix_clause} -and {last_prefix_clause}) -or {display_clause} -or {display_name_clause} -or {display_like_clause} -or {display_name_like_clause}}} `
    -Properties SamAccountName, Enabled, Title, DistinguishedName, EmployeeID
foreach ($u in $users) {{
    $emp = if ($u.EmployeeID) {{ $u.EmployeeID }} else {{ '' }}
    "$($u.SamAccountName)|$($u.Enabled)|$($u.Title)|$($u.DistinguishedName)|$emp"
}}
""")
    if code != 0 or not out:
        return []
    return _parse_account_search_output(out)


def find_user_account_by_username(username: str) -> dict | None:
    """Return the AD account whose SamAccountName exactly matches username."""
    username = _nfc(username).strip()
    if not username:
        return None

    out, err, code = run_ps(f"""
Import-Module ActiveDirectory -ErrorAction Stop
$users = Get-ADUser -Filter {{SamAccountName -eq '{_e(username)}'}} `
    -Properties SamAccountName, Enabled, Title, DistinguishedName, EmployeeID
foreach ($u in $users) {{
    $emp = if ($u.EmployeeID) {{ $u.EmployeeID }} else {{ '' }}
    "$($u.SamAccountName)|$($u.Enabled)|$($u.Title)|$($u.DistinguishedName)|$emp"
}}
""")
    if code != 0 or not out:
        return None
    accounts = _parse_account_search_output(out)
    return accounts[0] if len(accounts) == 1 else None


def select_new_joiner_account(accounts: list[dict], requested_username: str = "") -> dict:
    """Prefer an SF account; otherwise accept only an exact requested username."""
    sf_accounts = [account for account in accounts if account.get("is_sf")]
    if sf_accounts:
        return sf_accounts[0]

    requested = (requested_username or "").strip().casefold()
    if not requested:
        return {}
    matches = [
        account for account in accounts
        if (account.get("username") or "").strip().casefold() == requested
    ]
    return matches[0] if len(matches) == 1 else {}


def find_user_accounts_by_name(name: str) -> list[dict]:
    """Search AD by display/name first, then fall back to first+last parsing."""
    name = _nfc(name).strip()
    if not name:
        return []

    out, err, code = run_ps(f"""
Import-Module ActiveDirectory -ErrorAction Stop
$users = Get-ADUser -Filter {{Name -eq '{_e(name)}' -or DisplayName -eq '{_e(name)}'}} `
    -Properties SamAccountName, Enabled, Title, DistinguishedName, EmployeeID
foreach ($u in $users) {{
    $emp = if ($u.EmployeeID) {{ $u.EmployeeID }} else {{ '' }}
    "$($u.SamAccountName)|$($u.Enabled)|$($u.Title)|$($u.DistinguishedName)|$emp"
}}
""")
    if code == 0 and out:
        results = _parse_account_search_output(out)
        if results:
            return results

    parts = name.split()
    if len(parts) >= 2:
        return find_user_accounts(parts[0], parts[-1])
    return []


def classify_scenario(accounts: list[dict]) -> str:
    """
    new_joiner      - single account in SF OU
    rejoiner_dual   - SF account + separate old account
    rejoiner_single - single account NOT in SF OU
    unknown         - anything else
    """
    if not accounts:
        return "unknown"
    sf  = [a for a in accounts if a["is_sf"]]
    old = [a for a in accounts if not a["is_sf"]]
    disabled_old = [a for a in old if a.get("disabled") or not a.get("enabled")]
    if len(accounts) == 1 and sf:
        return "new_joiner"
    if sf and disabled_old:
        return "rejoiner_dual"
    if sf and old:
        return "rejoiner_dual"
    if disabled_old:
        return "rejoiner_single"
    if len(accounts) == 1 and old:
        return "new_joiner"
    return "unknown"


def get_buddy_info(sam: str) -> tuple[str, list[str], str, str, dict]:
    """Returns (ou, [group_names], error, department, ext_attrs). On success error is empty string."""
    out, err, code = run_ps(f"""
Import-Module ActiveDirectory -ErrorAction Stop
$u = Get-ADUser -Identity '{_e(sam)}' -Properties MemberOf, DistinguishedName, Department, `
    extensionAttribute5, extensionAttribute14, extensionAttribute15 -ErrorAction Stop
$ou = $u.DistinguishedName -replace '^CN=[^,]+,', ''
"OU:$ou"
"DEPT:$(if ($u.Department) {{ $u.Department }} else {{ '' }})"
"EA5:$(if ($u.extensionAttribute5) {{ $u.extensionAttribute5 }} else {{ '' }})"
"EA14:$(if ($u.extensionAttribute14) {{ $u.extensionAttribute14 }} else {{ '' }})"
"EA15:$(if ($u.extensionAttribute15) {{ $u.extensionAttribute15 }} else {{ '' }})"
foreach ($g in $u.MemberOf) {{ "GRP:$((Get-ADGroup $g).Name)" }}
""")
    if code != 0:
        return "", [], (err or "User not found"), "", {}
    ou, groups, department = "", [], ""
    ext_attrs: dict[str, str] = {"extensionAttribute5": "", "extensionAttribute14": "", "extensionAttribute15": ""}
    for line in out.splitlines():
        if line.startswith("OU:"):
            ou = line[3:].strip()
        elif line.startswith("DEPT:"):
            department = line[5:].strip()
        elif line.startswith("EA5:"):
            ext_attrs["extensionAttribute5"] = line[4:].strip()
        elif line.startswith("EA14:"):
            ext_attrs["extensionAttribute14"] = line[5:].strip()
        elif line.startswith("EA15:"):
            ext_attrs["extensionAttribute15"] = line[5:].strip()
        elif line.startswith("GRP:"):
            g = line[4:].strip()
            groups.append(g)
    return ou, sorted(groups), "", department, ext_attrs

def get_account_groups(sam: str) -> tuple[list[str], str]:
    """Returns ([group_names], error) for any account."""
    out, err, code = run_ps(f"""
Import-Module ActiveDirectory -ErrorAction Stop
$u = Get-ADUser -Identity '{_e(sam)}' -Properties MemberOf -ErrorAction Stop
foreach ($g in $u.MemberOf) {{ "GRP:$((Get-ADGroup $g).Name)" }}
""")
    if code != 0:
        return [], (err or "User not found")
    groups = []
    for line in out.splitlines():
        if line.startswith("GRP:"):
            g = line[4:].strip()
            groups.append(g)
    return sorted(groups), ""


def build_verification_script(sam: str) -> str:
    """Returns a PS script that fetches and displays key attributes for verification."""
    return f"""
Import-Module ActiveDirectory -ErrorAction Stop
$u = Get-ADUser -Identity '{_e(sam)}' -Properties Title,Description,Department,Company,Office,EmailAddress,Manager,extensionAttribute5,extensionAttribute10,extensionAttribute14,extensionAttribute15,targetAddress,proxyAddresses,Enabled,LockedOut,PasswordLastSet,PasswordExpired,UserPrincipalName -ErrorAction Stop
$mgrName = if ($u.Manager) {{ $u.Manager -replace '^CN=([^,]+),.+$','$1' }} else {{ '' }}
Write-Host "=== Verification: $($u.SamAccountName) ==="
Write-Host "Enabled:              $($u.Enabled)"
Write-Host "Locked out:           $($u.LockedOut)"
Write-Host "PasswordLastSet:      $($u.PasswordLastSet)"
Write-Host "Must change password: $($u.PasswordExpired)"
Write-Host "UserPrincipalName:    $($u.UserPrincipalName)"
Write-Host "Title:                $($u.Title)"
Write-Host "Description:          $($u.Description)"
Write-Host "Department:           $($u.Department)"
Write-Host "Company:              $($u.Company)"
Write-Host "Office:               $($u.Office)"
Write-Host "EmailAddress:         $($u.EmailAddress)"
Write-Host "Manager:              $mgrName"
Write-Host "extensionAttribute5:  $($u.extensionAttribute5)"
Write-Host "extensionAttribute10: $($u.extensionAttribute10)"
Write-Host "extensionAttribute14: $($u.extensionAttribute14)"
Write-Host "extensionAttribute15: $($u.extensionAttribute15)"
Write-Host "targetAddress:        $($u.targetAddress)"
Write-Host "proxyAddresses:       $($u.proxyAddresses -join ' | ')"
""".strip()


def build_ext_attr_lines(sam: str, ext_attrs: dict) -> list[str]:
    """Generates Set-ADUser -Replace for selected extensionAttributes."""
    if not ext_attrs:
        return []
    pairs = "; ".join(f"{k}='{_e(v)}'" for k, v in ext_attrs.items() if v)
    if not pairs:
        return []
    return [
        "# Set extended attributes from buddy",
        f"Set-ADUser -Identity '{sam}' -Replace @{{{pairs}}}",
        'Write-Host "OK  Extended attributes set"',
        "",
    ]


def _group_add_lines(sam: str, groups: list[str]) -> list[str]:
    """Build safe group additions, excluding policy-controlled memberships."""
    sam_e = _e(sam)
    lines: list[str] = []
    for group in groups:
        if is_blocked_group(group):
            continue
        group_e = _e(group)
        lines += [
            "try {",
            f"    $groupName = '{group_e}'",
            "    $targetGroups = @(Get-ADGroup -Filter { Name -eq $groupName } -ErrorAction Stop)",
            "    if ($targetGroups.Count -ne 1) { throw \"Expected one AD group named '$groupName'; found $($targetGroups.Count).\" }",
            f"    Add-ADGroupMember -Identity $targetGroups[0].DistinguishedName -Members '{sam_e}' -ErrorAction Stop",
            f"    Write-Host 'OK  Added group: {group_e}'",
            "} catch {",
            f"    Write-Warning ('Group ' + '{group_e}' + ': ' + $_.Exception.Message)",
            "}",
        ]
    return lines


def _password_reset_lines(sam: str, password: str) -> list[str]:
    sam_e = _e(sam)
    pwd_e = _e(password)
    return [
        "# Reset password, unlock the account if needed, and validate on the same DC",
        f"$plainPassword = '{pwd_e}'",
        "$securePassword = ConvertTo-SecureString $plainPassword -AsPlainText -Force",
        f"Set-ADAccountPassword -Identity '{sam_e}' -Reset -NewPassword $securePassword",
        f"$postReset = Get-ADUser -Identity '{sam_e}' -Properties LockedOut,PasswordLastSet",
        "if ($postReset.LockedOut) {",
        f"    Unlock-ADAccount -Identity '{sam_e}'",
        '    Write-Host "OK  Account unlocked"',
        "} else {",
        '    Write-Host "OK  Account already unlocked"',
        "}",
        f"Set-ADUser -Identity '{sam_e}' -ChangePasswordAtLogon $false",
        'Write-Host "OK  User must change password at next logon disabled"',
        "Add-Type -AssemblyName System.DirectoryServices.AccountManagement",
        "$ctx = New-Object System.DirectoryServices.AccountManagement.PrincipalContext(",
        "    [System.DirectoryServices.AccountManagement.ContextType]::Domain, $dc)",
        f"$passwordValid = $ctx.ValidateCredentials('{sam_e}', $plainPassword)",
        "if (-not $passwordValid) {",
        '    throw \"Password validation failed on $dc after reset.\"',
        "}",
        'Write-Host "OK  Password reset and validated"',
        'Write-Host "PasswordLastSet: $($postReset.PasswordLastSet)"',
        "",
    ]

# ── Script builders ───────────────────────────────────────────────────────────

def build_new_joiner_script(ticket: dict, sf_account: dict, target_ou: str,
                             email: str, password: str, groups: list[str],
                             department: str = "", ext_attrs: dict = None) -> str:
    """
    New joiner: SF already sets Title, Description, Department, Company, Office,
    StreetAddress, Manager, EmployeeID, extensionAttribute5, extensionAttribute15.
    We only fill the gaps: OU move, groups, email, City/PostalCode/Country,
    extensionAttribute10 (manager email), extensionAttribute14 (buddy), password.
    """
    username = _e(sf_account["username"])
    address  = detect_company_address(
        ticket.get("company_name", ""),
        ticket.get("office", ""),
    )

    L = [
        "$cred = Get-Credential -Message 'Enter your AD admin credentials'",
        '$ErrorActionPreference = "Stop"',
        "Import-Module ActiveDirectory -ErrorAction Stop",
        "$dc = [string](Get-ADDomainController -Discover -Writable | Select-Object -ExpandProperty HostName)",
        "$PSDefaultParameterValues = @{'*-AD*:Credential' = $cred; '*-AD*:Server' = $dc}",
        'Write-Host "Using DC: $dc"',
        "",
        f'Write-Host "Processing NEW JOINER: {sf_account["username"]}"',
        "",
        "# Resolve manager email from SF account's Manager DN (SF already set this correctly)",
        f"$sfMgrDn = (Get-ADUser -Identity '{username}' -Properties Manager).Manager",
        "$mgrEmail = $null",
        "if ($sfMgrDn) {",
        "    try {",
        "        $mgrEmail = (Get-ADUser -Identity $sfMgrDn -Properties EmailAddress).EmailAddress",
        '        Write-Host "Manager email: $mgrEmail"',
        "    } catch {",
        '        Write-Warning "Could not get manager email: $_"',
        "    }",
        "}",
        "",
        "# Move account from SF OU to correct OU",
        f"$userDN = (Get-ADUser -Identity '{username}' -Properties DistinguishedName).DistinguishedName",
        f"Move-ADObject -Identity $userDN -TargetPath '{_e(target_ou)}'",
        'Write-Host "OK  Account moved to correct OU"',
        "",
        "# Add to AD groups (failures logged but non-fatal)",
    ]
    L += _group_add_lines(sf_account["username"], groups)
    L += ['Write-Host "OK  Groups assigned"', ""]

    L += _password_reset_lines(sf_account["username"], password)

    L += _proxy_address_lines(username, email)

    # Only set what SF leaves blank
    L += [
        "# Set email and location fields (SF does not provision these)",
        "$setParams = @{",
        f"    EmailAddress = '{_e(email)}'",
    ]
    if address.get("city"):
        L.append(f"    City = '{_e(address['city'])}'")
    if address.get("zip"):
        L.append(f"    PostalCode = '{_e(address['zip'])}'")
    if address.get("country"):
        L.append(f"    Country = '{_e(address['country'])}'")
    L += [
        "}",
        f"Set-ADUser -Identity '{username}' @setParams",
        'Write-Host "OK  Email and location set"',
        "",
        "# Set manager email in extensionAttribute10",
        "if ($mgrEmail) {",
        f"    Set-ADUser -Identity '{username}' -Replace @{{extensionAttribute10=$mgrEmail}}",
        '    Write-Host "OK  extensionAttribute10 set to $mgrEmail"',
        "} else {",
        '    Write-Warning "extensionAttribute10 not set — manager email not found"',
        "}",
        "",
    ]

    ea14 = (ext_attrs or {}).get("extensionAttribute14", "")
    if ea14:
        L += [
            f"Set-ADUser -Identity '{username}' -Replace @{{extensionAttribute14='{_e(ea14)}'}}",
            'Write-Host "OK  extensionAttribute14 set"',
            "",
        ]

    return "\n".join(L)


def build_rejoiner_dual_script(ticket: dict, sf_account: dict, old_account: dict,
                                target_ou: str, email: str, password: str,
                                groups: list[str], department: str = "",
                                ext_attrs: dict = None) -> str:
    """
    Rejoiner with two accounts.
    SF dummy already has correct new employment data (Title, Description, Department,
    Company, Manager, extensionAttribute5, extensionAttribute15) — read directly from it.
    We fill the rest: email, location, extensionAttribute10/14, groups, password.
    """
    sf_sam  = _e(sf_account["username"])
    sf_identity = _e(sf_account.get("dn") or sf_account["username"])
    old_sam = _e(old_account["username"])
    address = detect_company_address(
        ticket.get("company_name", ""),
        ticket.get("office", ""),
    )
    ea14    = _e((ext_attrs or {}).get("extensionAttribute14", ""))
    ext15   = address.get("ext15", "")

    L = [
        "$cred = Get-Credential -Message 'Enter your AD admin credentials'",
        '$ErrorActionPreference = "Stop"',
        "Import-Module ActiveDirectory -ErrorAction Stop",
        "$dc = [string](Get-ADDomainController -Discover -Writable | Select-Object -ExpandProperty HostName)",
        "$PSDefaultParameterValues = @{'*-AD*:Credential' = $cred; '*-AD*:Server' = $dc}",
        'Write-Host "Using DC: $dc"',
        "",
        f'Write-Host "Processing REJOINER (dual account): restoring {old_account["username"]}"',
        "",
        "# Read all new employment data from SF dummy",
        f"$sfDN = '{sf_identity}'",
        "$sf = Get-ADUser -Identity $sfDN -Properties EmployeeID,Title,Description,Department,Company,Manager,Office,StreetAddress,City,PostalCode,Country,extensionAttribute5",
        'Write-Host "SF data loaded — EmpID: $($sf.EmployeeID)  Title: $($sf.Title)"',
        "",
        "# Resolve manager email from SF dummy manager DN",
        "$mgrEmail = $null",
        "if ($sf.Manager) {",
        "    try {",
        "        $mgrEmail = (Get-ADUser -Identity $sf.Manager -Properties EmailAddress).EmailAddress",
        '        Write-Host "Manager email: $mgrEmail"',
        "    } catch {",
        '        Write-Warning "Could not get manager email: $_"',
        "    }",
        "}",
        "",
        "# Copy EmployeeID to old account",
        f"Set-ADUser -Identity '{old_sam}' -EmployeeID $sf.EmployeeID",
        'Write-Host "OK  EmployeeID copied: $($sf.EmployeeID)"',
        "",
        "# Move old account to correct OU",
        f"$oldDN = (Get-ADUser -Identity '{old_sam}' -Properties DistinguishedName).DistinguishedName",
        f"Move-ADObject -Identity $oldDN -TargetPath '{_e(target_ou)}'",
        'Write-Host "OK  Account moved to correct OU"',
        "",
        "# Enable account and clear hide-from-address-list flag",
        f"Enable-ADAccount -Identity '{old_sam}'",
        'Write-Host "OK  Account enabled"',
        f"Set-ADUser -Identity '{old_sam}' -Clear msExchHideFromAddressLists",
        'Write-Host "OK  msExchHideFromAddressLists cleared"',
        "",
        "# Add to AD groups",
    ]
    L += _group_add_lines(old_account["username"], groups)
    L += ['Write-Host "OK  Groups assigned"', ""]

    L += _password_reset_lines(old_account["username"], password)

    L += _proxy_address_lines(old_sam, email)

    # Set attributes — employment fields from SF dummy, location from address map
    L += [
        "# Update attributes: employment from SF dummy, location from address map",
        "$setParams = @{",
        f"    EmailAddress  = '{_e(email)}'",
        "    Title         = $sf.Title",
        "    Description   = $sf.Title",
        "    Department    = $sf.Department",
        "    Company       = $sf.Company",
    ]
    if address.get("office"):
        L.append(f"    Office        = '{_e(address['office'])}'")
    if address.get("street"):
        L.append(f"    StreetAddress = '{_e(address['street'])}'")
    if address.get("city"):
        L.append(f"    City          = '{_e(address['city'])}'")
    if address.get("zip"):
        L.append(f"    PostalCode    = '{_e(address['zip'])}'")
    if address.get("country"):
        L.append(f"    Country       = '{_e(address['country'])}'")
    L += [
        "}",
    ]
    # If a location is recognised but its exact address is not confirmed, keep
    # the authoritative SF dummy values rather than imposing another campus.
    for field, sf_field in (
        ("office", "Office"),
        ("street", "StreetAddress"),
        ("city", "City"),
        ("zip", "PostalCode"),
        ("country", "Country"),
    ):
        if not address.get(field):
            L.append(
                f"if ($sf.{sf_field}) {{ $setParams['{sf_field}'] = $sf.{sf_field} }}"
            )
    L += [
        "if ($sf.Manager) { $setParams['Manager'] = $sf.Manager }",
        f"Set-ADUser -Identity '{old_sam}' @setParams",
        'Write-Host "OK  Attributes updated"',
        "",
        "# Copy extensionAttribute5 from SF dummy (org hierarchy)",
        "if ($sf.extensionAttribute5) {",
        f"    Set-ADUser -Identity '{old_sam}' -Replace @{{extensionAttribute5=$sf.extensionAttribute5}}",
        '    Write-Host "OK  extensionAttribute5: $($sf.extensionAttribute5)"',
        "}",
        "",
        "# Set extensionAttribute10 (manager email)",
        "if ($mgrEmail) {",
        f"    Set-ADUser -Identity '{old_sam}' -Replace @{{extensionAttribute10=$mgrEmail}}",
        '    Write-Host "OK  extensionAttribute10 set to $mgrEmail"',
        "} else {",
        '    Write-Warning "extensionAttribute10 not set — manager email not found"',
        "}",
        "",
    ]

    if ea14:
        L += [
            f"Set-ADUser -Identity '{old_sam}' -Replace @{{extensionAttribute14='{ea14}'}}",
            'Write-Host "OK  extensionAttribute14 set"',
            "",
        ]

    if ext15:
        L += [
            f"Set-ADUser -Identity '{old_sam}' -Replace @{{extensionAttribute15='{_e(ext15)}'}}",
            f'Write-Host "OK  extensionAttribute15 set to {ext15}"',
            "",
        ]
    else:
        L += [
            f"Set-ADUser -Identity '{old_sam}' -Clear extensionAttribute15",
            'Write-Host "OK  extensionAttribute15 cleared"',
            "",
        ]

    L += [
        "# !! DELETE SF DUMMY ACCOUNT - verify above output before this runs !!",
        "Remove-ADUser -Identity $sfDN -Confirm:$false",
        'Write-Host "OK  SF dummy account deleted"',
    ]
    return "\n".join(L)


def build_rejoiner_single_script(ticket: dict, account: dict, target_ou: str,
                                  email: str, password: str, groups: list[str],
                                  department: str = "", ext_attrs: dict = None) -> str:
    """
    Rejoiner with only one account (no SF duplicate).
    Steps: move if needed -> enable if needed -> clear hide flag -> groups -> attributes -> password.
    """
    sam     = _e(account["username"])
    manager = _e(_normalize_manager_name(ticket.get("manager", "")))
    title   = _e(ticket.get("position", ""))
    office  = _e(ticket.get("office", ""))
    company = ticket.get("company_name", "")
    address = detect_company_address(company, ticket.get("office", ""))

    L = [
        "$cred = Get-Credential -Message 'Enter your AD admin credentials'",
        '$ErrorActionPreference = "Stop"',
        "Import-Module ActiveDirectory -ErrorAction Stop",
        "$dc = [string](Get-ADDomainController -Discover -Writable | Select-Object -ExpandProperty HostName)",
        "$PSDefaultParameterValues = @{'*-AD*:Credential' = $cred; '*-AD*:Server' = $dc}",
        'Write-Host "Using DC: $dc"',
        "",
        f'Write-Host "Processing REJOINER (single account): {account["username"]}"',
        "",
    ]

    L += _manager_lookup_lines(manager)

    L += [
        f"$acct = Get-ADUser -Identity '{sam}' -Properties DistinguishedName,Enabled",
        "$currentOu = $acct.DistinguishedName -replace '^CN=[^,]+,', ''",
        f"if ($currentOu -ne '{_e(target_ou)}') {{",
        f"    Move-ADObject -Identity $acct.DistinguishedName -TargetPath '{_e(target_ou)}'",
        '    Write-Host "OK  Moved to correct OU"',
        "} else {",
        '    Write-Host "OK  Already in correct OU"',
        "}",
        "if (-not $acct.Enabled) {",
        f"    Enable-ADAccount -Identity '{sam}'",
        '    Write-Host "OK  Account enabled"',
        "} else {",
        '    Write-Host "OK  Account already enabled"',
        "}",
        f"Set-ADUser -Identity '{sam}' -Clear msExchHideFromAddressLists",
        'Write-Host "OK  msExchHideFromAddressLists cleared"',
        "",
        "# Add to AD groups",
    ]
    L += _group_add_lines(account["username"], groups)
    L += ['Write-Host "OK  Groups assigned"', ""]

    L += _password_reset_lines(account["username"], password)

    L += _proxy_address_lines(sam, email)
    L += _set_user_attribute_lines(sam, email, title, office, manager, _e(company), address, department)
    L += build_ext_attr_lines(sam, ext_attrs or {})
    return "\n".join(L)
