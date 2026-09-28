import re
import unicodedata
import requests
from datetime import date, timedelta
from dateutil.parser import parse as parse_date

DEFAULT_MOVER_JQL = (
    'assignee = currentUser() AND issuetype = "Employee moving" '
    'AND status in (Open, "In Progress", Pending)'
)


def _adf_to_text(node: dict) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if not isinstance(node, dict):
        return ""
    t = node.get("type", "")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "mention":
        return node.get("attrs", {}).get("text", "")
    if t == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    parts = [_adf_to_text(child) for child in node.get("content", [])]
    text = "".join(parts)
    if t in ("paragraph", "heading", "listItem"):
        text = text.rstrip() + "\n"
    if t in ("bulletList", "orderedList"):
        text += "\n"
    return text

# ── Buddy detection ──────────────────────────────────────────────────────────

_EXCLUDE_AUTHORS = {"Aleksandr Kovalevskij"}

# Title-case full name: "Eimantas Tarasevicius"
# (?-i:...) keeps this sub-pattern case-SENSITIVE even when the search uses IGNORECASE,
# which prevents "TSELE for the groups" being greedily captured as a name.
_NAME = (
    r'(?-i:[A-ZŠŽĄČĘĖĮŲŪ][a-zšžąčęėįųū\-]{1,}'
    r'(?:[^\S\r\n]+[A-ZŠŽĄČĘĖĮŲŪ][a-zšžąčęėįųū\-]{1,}){1,2})'
)
# SAM account: exactly 5 chars — first letter of first name + first 4 of last name
# e.g. "TSELE", "tsele"  (upper or lower case)
_SAM = r'[A-Za-z][A-Za-z0-9]{4}\b'

# Combined: full name first (more specific; _SAM is fallback for usernames)
_BUDDY_VALUE = rf'({_NAME}|{_SAM})'

_BUDDY_PATTERNS = [
    # "existing employee: X", "reference employee - X"
    rf"(?:existing|reference|template)\s+employee\s*[:\-]\s*{_BUDDY_VALUE}",
    # "name of existing employee with similar access rights - X"
    rf"(?:name\s+of\s+)?(?:existing|reference)\s+employee\s+with\s+(?:similar\s+)?[^.\n]{{0,60}}[:\-–]\s*{_BUDDY_VALUE}",
    # "similar access rights - X", "similar access rights: X"
    rf"similar\s+access\s+rights?\s*[:\-–]\s*{_BUDDY_VALUE}",
    # "similar rights - X", "similar permissions - X"
    rf"similar\s+(?:rights?|permissions?|accesses?)\s*[:\-–]\s*{_BUDDY_VALUE}",
    # "access rights - X" (general dash/colon separator)
    rf"access\s+rights?\s*[:\-–]\s*{_BUDDY_VALUE}",
    # "employee with similar access/rights - X"
    rf"employee\s+with\s+similar\s+[^.\n]{{0,40}}[:\-–]\s*{_BUDDY_VALUE}",
    # "rights as X", "access as X", "rights like X"
    rf"(?:rights?|access(?:es)?)\s+(?:as|like)\s+{_BUDDY_VALUE}",
    # "rights same as for X", "accesses same as for X"
    rf"(?:rights?|access(?:es?)?)\s+(?:the\s+)?same\s+as\s+(?:for\s+)?{_BUDDY_VALUE}",
    rf"same\s+(?:rights?\s+|access(?:es?)?\s+)?as\s+for\s+{_BUDDY_VALUE}",
    # "use X as similar accesses", "use X as template", "use X as a base"
    rf"use\s+{_BUDDY_VALUE}\s+as\b",
    # "use X’s access rights", "X’s access rights as reference"
    rf"use\s+{_BUDDY_VALUE}(?:’|’)?s?\s+(?:rights?|access(?:es?)?)",
    rf"{_BUDDY_VALUE}(?:’|’)?s?\s+(?:rights?|access(?:es?)?)\s+as\s+(?:a\s+)?(?:reference|template)",
    # "the person X", "person is X", "person who is working in the similar job role – X"
    rf"(?:the\s+)?person\s+(?:is\s+)?{_BUDDY_VALUE}",
    rf"person\s+who\s+.{{0,60}}[–\-]\s*{_BUDDY_VALUE}",
    # "similar job role – X", "similar role – X"
    rf"similar\s+(?:\w+\s+)?(?:job\s+)?role\s*[–\-]\s*{_BUDDY_VALUE}",
    # "it will be X", "will be X"
    rf"will\s+be\s+{_BUDDY_VALUE}",
    rf"copy\s+(?:rights?\s+)?from\s+{_BUDDY_VALUE}",
    rf"buddy\s*(?:is|:|[\-–])\s*{_BUDDY_VALUE}",
    rf"{_BUDDY_VALUE}\s+(?:is\s+)?(?:a\s+|the\s+)?buddy",
    rf"similar\s+(?:rights?\s+|access(?:es?)?\s+)?(?:to|as)\s+{_BUDDY_VALUE}",
    rf"same\s+(?:rights?\s+|access(?:es?)?\s+)?as\s+{_BUDDY_VALUE}",
    rf"template\s*(?:is|:|[\-–])\s*{_BUDDY_VALUE}",
    rf"template\s+user\s*[:\-–]\s*{_BUDDY_VALUE}",
    rf"copy\s+{_BUDDY_VALUE}(?:’s)?\s+(?:rights?|access(?:es?)?|groups?|permissions?)",
    rf"based\s+on\s+{_BUDDY_VALUE}",
    rf"like\s+{_BUDDY_VALUE}(?:’s)?\s+(?:rights?|access(?:es?)?|groups?|permissions?)",
    # "access like X", "access as X"
    rf"access(?:es?)?\s+(?:like|as)\s+{_BUDDY_VALUE}",
    # "same access as X", "same rights as X", "same permissions as X"
    rf"same\s+(?:access(?:es?)?|rights?|permissions?)\s+as\s+{_BUDDY_VALUE}",
    # "give/set/assign same/similar access as X"
    rf"(?:give|set|assign|grant)\s+(?:same|similar)\s+(?:rights?|access(?:es?)?|permissions?)\s+as\s+{_BUDDY_VALUE}",
    # "access as per X"
    rf"access\s+as\s+per\s+{_BUDDY_VALUE}",
    # "X - for reference", "X - as template" (name comes first, role second)
    rf"{_BUDDY_VALUE}\s*[:\-–]\s*(?:for\s+)?(?:reference|template|similar\s+(?:access|rights?))",
]


def is_sam_account(value: str) -> bool:
    """Recognize legacy five-character SAMs and SF x-prefixed employee accounts."""
    return bool(re.fullmatch(r'(?:[A-Za-z][A-Za-z0-9]{4}|[xX][0-9]{5})', value))


def _looks_like_full_name(value: str) -> bool:
    parts = [p.strip(".,:;!?()[]{}\"'") for p in value.split() if p.strip(".,:;!?()[]{}\"'")]
    if not 2 <= len(parts) <= 3:
        return False
    for part in parts:
        for subpart in part.split("-"):
            if len(subpart) < 2 or not subpart[0].isupper() or not subpart[1:].isalpha():
                return False
    return True


def _normalize_buddy_candidate(value: str, author: str) -> str:
    cleaned = unicodedata.normalize("NFC", value).strip().strip(".,:;!?()[]{}\"'")
    cleaned = re.sub(r"^@", "", cleaned).strip()
    if cleaned.lower() in {"my", "mine", "me"}:
        return unicodedata.normalize("NFC", author).strip()
    return cleaned


def _parse_buddy_candidate_list(value: str, author: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|/|&)\s*|\s+\band\b\s+", value, flags=re.IGNORECASE)
    results: list[str] = []
    seen: set[str] = set()
    for part in parts:
        candidate = _normalize_buddy_candidate(part, author)
        if not candidate:
            continue
        if not (is_sam_account(candidate) or _looks_like_full_name(candidate)):
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(candidate)
    return results


def _extract_multi_buddy_candidates(body: str, author: str) -> list[str]:
    patterns = [
        r"\b(?:take|use)\s+(.+?)\s+users?\b",
        r"\b(?:take|use)\s+(.+?)\s+budd(?:y|ies)\b",
        r"\b(?:take|use)\s+(.+?)\s+as\s+(?:templates?|budd(?:y|ies))\b",
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if not m:
            continue
        candidates = _parse_buddy_candidate_list(m.group(1), author)
        if candidates:
            return candidates
    return []


def _extract_buddy_candidates_from_comment(body: str, author: str) -> list[str]:
    candidates = _extract_multi_buddy_candidates(body, author)
    if candidates:
        return candidates
    for pat in _BUDDY_PATTERNS:
        m = re.search(pat, body, re.IGNORECASE)
        if not m:
            continue
        name = _normalize_buddy_candidate(m.group(1), author)
        if is_sam_account(name) or _looks_like_full_name(name):
            return [name]
    return []


def extract_buddies_from_comments(comments: list[dict]) -> list[dict]:
    """Scan comments (excluding IT authors) for one or more buddy/template users.

    Returns a list of {name, author, date} dicts aggregated from matching comments,
    newest first. This keeps the latest suggestions first while still surfacing
    older mentioned buddies as additional context. SAM matches must still be
    validated against AD.
    """
    collected: list[dict] = []
    seen: set[str] = set()
    for c in reversed(comments):
        if c["author"] in _EXCLUDE_AUTHORS:
            continue
        author = unicodedata.normalize("NFC", c["author"])
        body = unicodedata.normalize("NFC", c["body"])[:2000]  # guard against ReDoS on huge comments
        for name in _extract_buddy_candidates_from_comment(body, author):
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            collected.append({"name": name, "author": author, "date": c["created"]})
    return collected


def extract_buddy_from_comments(comments: list[dict]) -> dict | None:
    """Scan comments (excluding IT authors) for a buddy/template user.
    Returns {name, author, date} on first confident match, or None.
    SAM matches (5-char) must be validated against AD by the caller before use."""
    buddies = extract_buddies_from_comments(comments)
    return buddies[0] if buddies else None


# Custom field IDs from the real Girteka Jira tickets
_CF_START_DATE   = "customfield_10980"   # text field, value: "2026-05-18"
_CF_FIRST_NAME   = "customfield_10109"
_CF_LAST_NAME    = "customfield_10111"
_CF_POSITION     = "customfield_10977"
_CF_OFFICE       = "customfield_10983"
_CF_MANAGER      = "customfield_10978"
_CF_REJOINER     = "customfield_14703"
_CF_PHONE        = "customfield_10113"   # Phone Number (new joiner's phone)
_CF_COMPANY_NAME = "customfield_10976"   # Company's name
_CF_COMPANY_READONLY = "customfield_10993"  # Company name (sometimes populated)
_CF_COUNTRY      = "customfield_14076"
_CF_SIMILAR_ROLE = "customfield_11436"   # Person who is working in the similar job role
_CF_PERSON_ID_EXTERNAL = "customfield_11171"  # AD username when SF provisioning is handled manually

_BASE_FETCH_FIELDS = [
    "summary", "status", "reporter",
    _CF_FIRST_NAME, _CF_LAST_NAME,
    _CF_POSITION, _CF_OFFICE, _CF_MANAGER, _CF_REJOINER,
    _CF_PHONE, _CF_COMPANY_NAME, _CF_COMPANY_READONLY, _CF_COUNTRY,
    _CF_SIMILAR_ROLE, _CF_PERSON_ID_EXTERNAL,
]

def _parse_start_date(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    # ISO / year-first format: 2026-05-04 or 2026.05.04 - never use dayfirst
    if re.match(r'^\d{4}[.\-/]\d{2}[.\-/]\d{2}$', raw):
        raw = raw.replace(".", "-").replace("/", "-")
        try:
            return parse_date(raw).date()
        except Exception:
            return None
    # Other formats: "4 May 2026", "01 Jun 2026", "01/04/2026" (European DD/MM/YYYY)
    try:
        return parse_date(raw, dayfirst=True).date()
    except Exception:
        return None


def _jira_userpicker_to_buddy_candidate(raw, source: str = "similar_role_field") -> dict | None:
    """Return a buddy candidate dict from a Jira user picker field value."""
    if not raw:
        return None

    if isinstance(raw, dict):
        display_name = (raw.get("displayName") or raw.get("name") or "").strip()
        account_id = (raw.get("accountId") or "").strip()
        if not display_name:
            display_name = account_id
    else:
        display_name = str(raw).strip()
        account_id = ""

    if not display_name:
        return None

    return {
        "name": display_name,
        "author": "Jira field",
        "date": "",
        "source": source,
        "jira_account_id": account_id,
    }


def _same_jira_user(left: dict | None, right: dict | None) -> bool:
    """Compare normalized Jira user-picker values without trusting display casing."""
    if not left or not right:
        return False
    left_id = (left.get("jira_account_id") or "").strip()
    right_id = (right.get("jira_account_id") or "").strip()
    if left_id and right_id:
        return left_id == right_id
    return (left.get("name") or "").strip().casefold() == (
        right.get("name") or ""
    ).strip().casefold()


def resolve_mover_buddy(buddy: dict | None, axapta_rights: dict | None) -> dict:
    """Apply the mover ticket's Buddy/Axapta precedence rules.

    The returned mapping contains ``ad_buddy`` and an optional ``axapta_notice``.
    Keeping this decision pure makes it independently testable and ensures the
    list view and the AD wizard always make the same choice.
    """
    if buddy and axapta_rights:
        if _same_jira_user(buddy, axapta_rights):
            return {
                "ad_buddy": {**buddy, "source": "buddy_and_axapta_fields"},
                "axapta_notice": "",
                "confirmed_by_both": True,
            }
        return {
            "ad_buddy": {**buddy, "source": "buddy_field"},
            "axapta_notice": (
                "Axapta rights must match " + (axapta_rights.get("name") or "the Axapta rights user")
            ),
            "confirmed_by_both": False,
        }
    if buddy:
        return {
            "ad_buddy": {**buddy, "source": "buddy_field"},
            "axapta_notice": "",
            "confirmed_by_both": False,
        }
    if axapta_rights:
        return {
            "ad_buddy": {**axapta_rights, "source": "axapta_fallback"},
            "axapta_notice": "",
            "confirmed_by_both": False,
        }
    return {"ad_buddy": None, "axapta_notice": "", "confirmed_by_both": False}


def _jira_scalar_text(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        value = raw.strip()
        return "" if value.casefold() in {"none", "n/a", "-"} else value
    if isinstance(raw, dict):
        for key in ("value", "name", "displayName"):
            if raw.get(key):
                return str(raw[key]).strip()
        return ""
    if isinstance(raw, list):
        return ", ".join(filter(None, (_jira_scalar_text(item) for item in raw)))
    return str(raw).strip()


class JiraClient:
    def __init__(self, url: str, email: str, token: str):
        self.base = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (email, token)
        self.session.headers.update({"Accept": "application/json"})

    def test_connection(self) -> str:
        """Returns the logged-in user's display name, or raises on failure."""
        r = self.session.get(f"{self.base}/rest/api/3/myself", timeout=10)
        r.raise_for_status()
        return r.json().get("displayName", "OK")

    def get_comments(self, issue_key: str) -> list[dict]:
        """Returns list of {author, created, body} dicts, oldest first."""
        r = self.session.get(
            f"{self.base}/rest/api/3/issue/{issue_key}/comment",
            params={"maxResults": 100, "orderBy": "created"},
            timeout=15,
        )
        r.raise_for_status()
        comments = []
        for c in r.json().get("comments", []):
            author  = c.get("author", {}).get("displayName", "Unknown")
            created = c.get("created", "")[:16].replace("T", "  ")
            body    = _adf_to_text(c.get("body", {})).strip()
            if body:
                comments.append({"author": author, "created": created, "body": body})
        return comments

    def search_user(self, query: str) -> dict | None:
        """Search Jira for a user by display name. Returns {id, text} for the first
        match, or None if no match. Used to resolve manager names for @mentions."""
        r = self.session.get(
            f"{self.base}/rest/api/3/user/search",
            params={"query": query, "maxResults": 1},
            timeout=10,
        )
        r.raise_for_status()
        users = r.json()
        if not users:
            return None
        u = users[0]
        account_id = u.get("accountId")
        if not account_id:
            return None
        return {"id": account_id, "text": f"@{u.get('displayName', query)}"}

    def post_comment(self, issue_key: str, text: str,
                     mention_map: dict[str, str] | None = None) -> None:
        """Post a comment to a Jira issue (ADF).

        mention_map maps display text (e.g. '@John Smith') to a Jira accountId.
        Occurrences of those tokens in the text are converted to inline ADF
        mention nodes so the tagged users receive an email notification.
        """
        def _line_to_nodes(line: str) -> list[dict]:
            if not mention_map or not line:
                return [{"type": "text", "text": line}] if line else []
            pattern = "|".join(
                re.escape(k) for k in sorted(mention_map, key=len, reverse=True)
            )
            nodes: list[dict] = []
            for part in re.split(f"({pattern})", line):
                if not part:
                    continue
                if part in mention_map:
                    nodes.append({"type": "mention",
                                  "attrs": {"id": mention_map[part], "text": part}})
                else:
                    nodes.append({"type": "text", "text": part})
            return nodes

        content = []
        for para in text.split("\n\n"):
            if not para.strip():
                continue
            para_nodes: list[dict] = []
            for i, line in enumerate(para.split("\n")):
                if i > 0:
                    para_nodes.append({"type": "hardBreak"})
                para_nodes.extend(_line_to_nodes(line))
            if para_nodes:
                content.append({"type": "paragraph", "content": para_nodes})
        if not content:
            content = [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
        r = self.session.post(
            f"{self.base}/rest/api/3/issue/{issue_key}/comment",
            json={"body": {"type": "doc", "version": 1, "content": content}},
            timeout=15,
        )
        r.raise_for_status()

    def get_new_joiner_tickets(self, jql: str, _date_field: str = _CF_START_DATE) -> list[dict]:
        date_field = (_date_field or _CF_START_DATE).strip() or _CF_START_DATE
        fields = list(dict.fromkeys([*_BASE_FETCH_FIELDS, date_field]))
        params = {
            "jql": jql,
            "fields": ",".join(fields),
            "maxResults": 50,
        }
        r = self.session.get(
            f"{self.base}/rest/api/3/search/jql",
            params=params,
            timeout=15,
        )
        r.raise_for_status()

        tickets = []
        for issue in r.json().get("issues", []):
            f = issue["fields"]

            first = (f.get(_CF_FIRST_NAME) or "").strip()
            last  = (f.get(_CF_LAST_NAME)  or "").strip()
            name  = f"{first} {last}".strip() or f.get("summary", issue["key"])

            raw_date   = f.get(date_field) or ""
            start_date = _parse_start_date(raw_date)

            reporter_raw  = f.get("reporter") or {}
            reporter_id   = reporter_raw.get("accountId", "")
            reporter_name = reporter_raw.get("displayName", "")

            manager_raw = (f.get(_CF_MANAGER) or "").strip().lstrip(": ")

            rejoiner_raw = f.get(_CF_REJOINER) or {}
            if isinstance(rejoiner_raw, dict):
                rejoiner = rejoiner_raw.get("value", "").strip()
            else:
                rejoiner = str(rejoiner_raw).strip()

            phone        = (f.get(_CF_PHONE)        or "").strip()
            company_name = (
                _jira_scalar_text(f.get(_CF_COMPANY_NAME))
                or _jira_scalar_text(f.get(_CF_COMPANY_READONLY))
            )
            country = _jira_scalar_text(f.get(_CF_COUNTRY))
            similar_role_buddy = _jira_userpicker_to_buddy_candidate(f.get(_CF_SIMILAR_ROLE))
            person_id_external = _jira_scalar_text(f.get(_CF_PERSON_ID_EXTERNAL)).strip()

            tickets.append(
                {
                    "id":         issue["id"],
                    "key":        issue["key"],
                    "summary":    f.get("summary", issue["key"]),
                    "name":       name,
                    "first_name": first,
                    "last_name":  last,
                    "position":   (f.get(_CF_POSITION) or "").strip(),
                    "office":     (f.get(_CF_OFFICE)   or "").strip(),
                    "manager":      manager_raw,
                    "rejoiner":     rejoiner,
                    "phone":        phone,
                    "company_name": company_name,
                    "country":      country,
                    "person_id_external": person_id_external,
                    "similar_role_buddy": similar_role_buddy,
                    "status":         (f.get("status") or {}).get("name", ""),
                    "start_date":     start_date,
                    "url":            f"{self.base}/browse/{issue['key']}",
                    "reporter_id":    reporter_id,
                    "reporter_name":  reporter_name,
                    "kind":             "joiner",
                }
            )

        # Sort by start date (no date -> end of list)
        tickets.sort(key=lambda t: t["start_date"] or date.max)

        # Hide tickets where the person already started more than 1 day ago
        cutoff = date.today() - timedelta(days=1)
        tickets = [t for t in tickets if t["start_date"] is None or t["start_date"] >= cutoff]

        return tickets

    def get_mover_tickets(self, jql: str) -> list[dict]:
        """Fetch assigned employee-moving tickets using their dedicated schema."""
        fields = [
            "summary", "status", "reporter", "issuetype",
            "customfield_11213",  # Employee
            "customfield_10192",  # Start Date (1)
            "customfield_10996",  # Current job title
            "customfield_11167",  # New job title
            "customfield_10992",  # Current department
            "customfield_10993",  # New company
            "customfield_10057",  # New manager
            "customfield_10191",  # Office Location
            "customfield_10145",  # New Office Location
            "customfield_10894",  # Buddy
            "customfield_10900",  # Axapta rights
            "customfield_10899",  # Axapta access
            "customfield_14076",  # Country
        ]
        r = self.session.get(
            f"{self.base}/rest/api/3/search/jql",
            params={"jql": jql, "fields": ",".join(fields), "maxResults": 50},
            timeout=15,
        )
        r.raise_for_status()

        tickets: list[dict] = []
        for issue in r.json().get("issues", []):
            f = issue.get("fields") or {}
            issue_type = (f.get("issuetype") or {}).get("name", "")
            if issue_type and issue_type.casefold() != "employee moving":
                continue
            employee = _jira_userpicker_to_buddy_candidate(
                f.get("customfield_11213"), "employee_field"
            )
            buddy = _jira_userpicker_to_buddy_candidate(
                f.get("customfield_10894"), "buddy_field"
            )
            axapta = _jira_userpicker_to_buddy_candidate(
                f.get("customfield_10900"), "axapta_rights_field"
            )
            buddy_decision = resolve_mover_buddy(buddy, axapta)
            reporter = f.get("reporter") or {}
            manager = _jira_userpicker_to_buddy_candidate(
                f.get("customfield_10057"), "manager_field"
            )
            new_office = _jira_scalar_text(f.get("customfield_10145"))
            current_office = _jira_scalar_text(f.get("customfield_10191"))
            effective_date = _parse_start_date(
                _jira_scalar_text(f.get("customfield_10192"))
            )
            name = (employee or {}).get("name") or f.get("summary") or issue["key"]

            tickets.append({
                "id": issue["id"],
                "key": issue["key"],
                "summary": f.get("summary", issue["key"]),
                "kind": "mover",
                "name": name,
                "employee": employee,
                "effective_date": effective_date,
                # Keep start_date as a compatibility alias for reminder/storage helpers.
                "start_date": effective_date,
                "current_position": _jira_scalar_text(f.get("customfield_10996")),
                "new_position": _jira_scalar_text(f.get("customfield_11167")),
                "position": _jira_scalar_text(f.get("customfield_11167")),
                "current_department": _jira_scalar_text(f.get("customfield_10992")),
                "company_name": _jira_scalar_text(f.get("customfield_10993")),
                "manager_user": manager,
                "manager": (manager or {}).get("name", ""),
                "current_office": current_office,
                "new_office": new_office,
                "office": new_office or current_office,
                "country": _jira_scalar_text(f.get("customfield_14076")),
                "buddy_field": buddy,
                "axapta_rights": axapta,
                "ad_buddy": buddy_decision["ad_buddy"],
                "axapta_notice": buddy_decision["axapta_notice"],
                "buddy_confirmed_by_both": buddy_decision["confirmed_by_both"],
                "axapta_access": _jira_scalar_text(f.get("customfield_10899")),
                "status": (f.get("status") or {}).get("name", ""),
                "url": f"{self.base}/browse/{issue['key']}",
                "reporter_id": reporter.get("accountId", ""),
                "reporter_name": reporter.get("displayName", ""),
            })

        tickets.sort(key=lambda t: t["effective_date"] or date.max)
        # Keep overdue movers visible until Jira leaves the configured active
        # statuses; an unfinished AD move must not disappear after its date.
        return tickets
