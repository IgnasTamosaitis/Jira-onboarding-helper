"""Shared policy for groups that AD template workflows must not copy."""

RESTRICTED_GROUP_NAMES = {
    "Microsoft 365 Business Premium",
    "Power BI Premium Per User license",
    "Power BI Pro license",
    "Bitwarden Access",
    "VISIO_Plan_2",
    "M365 Add-In Microsoft Visio Data Visualizer",
    "M365 Add-In Breaktime",
    "M365 Add-In Power BI",
    "M365 Add-in Claude by Anthropic for Excel",
    "M365 Add-In Odoo for Outlook",
    "M365 Add-In SAP Analytics Cloud for Office",
    "Power BI PowerPoint Add in",
    "Test Add new users without teams policy",
    "Intune Managed devices (HASH manually add)",
    "Disable_USB",
    "VPN_IT_integracijos",
    "GrayList_WillGrow Users",
    "Driver Hiring Manager",
    "Driver Hiring Admin",
    "App - Users - Salesforce ClassTruck",
    "App - Users - Salesforce Service Cloud GL PROD",
    "App - Admins - Salesforce Sales Cloud GL UAT",
    "App - Users - Salesforce Sales Cloud GL PROD",
    "App - Users - Salesforce Service Cloud GL SAT",
    "App - Admins - Salesforce Service Cloud GL SAT",
    "App - Admins - Salesforce Service Cloud GL PROD",
    "App - Users - Salesforce Sales Cloud GL UAT",
    "App - Users - Salesforce Service Cloud GL UAT",
    "App - Admins - Salesforce Sales Cloud GL SIT",
    "App - Admins - Salesforce Service Cloud GL UAT",
    "App - Users - Salesforce Sales Cloud GL SIT",
    "App - Admins - Salesforce Sales Cloud GL PROD",
    "App - Users - Salesforce Sales Cloud GL PRE-PROD",
    "App - Admins - Salesforce Sales Cloud GL PRE-PROD",
}
RESTRICTED_GROUP_KEYS = {name.casefold() for name in RESTRICTED_GROUP_NAMES}

REDUNDANT_GROUP_NAMES = {"Teams VLS access policy applied", "RDS-Disabled"}
REDUNDANT_GROUP_KEYS = {name.casefold() for name in REDUNDANT_GROUP_NAMES}


def is_restricted_group(group_name: str) -> bool:
    return (group_name or "").strip().casefold() in RESTRICTED_GROUP_KEYS


def is_redundant_group(group_name: str) -> bool:
    return (group_name or "").strip().casefold() in REDUNDANT_GROUP_KEYS


def is_blocked_group(group_name: str) -> bool:
    return is_restricted_group(group_name) or is_redundant_group(group_name)
