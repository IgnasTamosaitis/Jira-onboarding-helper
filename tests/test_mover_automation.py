import unittest

from group_policy import is_blocked_group
from mover_automation import (
    build_mover_plan,
    build_mover_script,
    choose_enabled_account,
    plan_group_changes,
)


class MoverGroupPlanTests(unittest.TestCase):
    def test_audit_confirmed_permission_groups_are_blocked(self):
        for group in (
            "RDS-Disabled",
            "Teams VLS access policy applied",
            "Disable_USB",
            "VPN_IT_integracijos",
            "GrayList_WillGrow Users",
        ):
            with self.subTest(group=group):
                self.assertTrue(is_blocked_group(group))

    def test_exact_group_diff_preserves_shared_and_blocks_restricted(self):
        plan = plan_group_changes(
            ["Shared", "Old Access", "Power BI Pro license"],
            [
                "shared", "New Access", "Power BI Pro license",
                "RDS-Disabled", "Disable_USB",
            ],
        )
        self.assertEqual(plan["add"], ["New Access"])
        self.assertEqual(plan["keep"], ["shared"])
        self.assertEqual(plan["remove"], ["Old Access"])
        self.assertEqual(plan["desired"], ["New Access", "shared"])
        self.assertEqual(plan["preserved_blocked"], ["Power BI Pro license"])
        self.assertEqual(
            plan["blocked_buddy"],
            ["Disable_USB", "Power BI Pro license", "RDS-Disabled"],
        )

    def test_enabled_account_must_be_unambiguous(self):
        self.assertEqual(
            choose_enabled_account(
                [{"username": "USER1", "enabled": True, "disabled": False}], "employee"
            )["username"],
            "USER1",
        )
        with self.assertRaisesRegex(ValueError, "Multiple enabled"):
            choose_enabled_account([
                {"username": "ONE", "enabled": True, "disabled": False},
                {"username": "TWO", "enabled": True, "disabled": False},
            ], "employee")

    def test_explicit_enabled_account_choice_resolves_ambiguity(self):
        accounts = [
            {"username": "MGORO", "enabled": True, "disabled": False},
            {"username": "maksim.gorochovskij_", "enabled": True, "disabled": False},
        ]
        selected = choose_enabled_account(accounts, "buddy", "mgoro")
        self.assertEqual(selected["username"], "MGORO")

        with self.assertRaisesRegex(ValueError, "no longer enabled"):
            choose_enabled_account(accounts, "buddy", "missing")


class MoverScriptTests(unittest.TestCase):
    def setUp(self):
        self.ticket = {
            "key": "PNC-1",
            "new_position": "Team Lead",
            "company_name": "Girteka Logistics UAB",
            "manager": "New Manager",
            "office": "Girteka Park",
            "axapta_notice": "Axapta rights must match AXUSER",
        }
        self.mover = {
            "sam": "MOVER",
            "enabled": True,
            "ou": "OU=Old,DC=example,DC=com",
            "title": "Old title",
            "description": "Old title",
            "department": "Old Dept",
            "company": "Old Company",
            "manager_name": "Old Manager",
            "groups": ["Shared", "Old Group"],
        }
        self.buddy = {
            "sam": "BUDDY",
            "enabled": True,
            "ou": "OU=New,DC=example,DC=com",
            "department": "New Dept",
            "manager_sam": "MANAGER",
            "groups": ["Shared", "New Group", "Power BI Pro license"],
        }
        self.manager = {
            "sam": "MANAGER",
            "display_name": "New Manager",
            "enabled": True,
            "email": "manager@example.com",
        }

    def test_plan_and_script_set_required_state_without_onboarding_changes(self):
        plan = build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        script = build_mover_script(plan)

        self.assertEqual(plan["groups"]["add"], ["New Group"])
        self.assertEqual(plan["groups"]["remove"], ["Old Group"])
        self.assertIn("Title = 'Team Lead'", script)
        self.assertIn("Description = 'Team Lead'", script)
        self.assertIn("Department = 'New Dept'", script)
        self.assertIn("Company = 'Girteka Logistics UAB'", script)
        self.assertIn("Manager = $manager.DistinguishedName", script)
        self.assertIn("TargetPath 'OU=New,DC=example,DC=com'", script)
        self.assertIn("$groupName = 'New Group'", script)
        self.assertIn("$groupName = 'Old Group'", script)
        self.assertIn("Add-ADGroupMember -Identity $targetGroups[0].DistinguishedName", script)
        self.assertIn("Remove-ADGroupMember -Identity $targetGroups[0].DistinguishedName", script)
        self.assertIn("Final managed-group verification failed", script)
        self.assertIn("$ignoredGroups", script)
        self.assertIn("$failedAddGroups", script)
        self.assertIn("$failedRemoveGroups", script)
        self.assertIn("MANUAL GROUP FOLLOW-UP", script)
        self.assertIn("exit 2", script)
        self.assertNotIn("Set-ADAccountPassword", script)
        self.assertNotIn("proxyAddresses", script)
        self.assertNotIn("Power BI Pro license' -Members", script)

    def test_disable_usb_is_manual_and_never_added(self):
        self.buddy["groups"].append("Disable_USB")
        plan = build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        script = build_mover_script(plan)

        self.assertIn("Disable_USB", plan["groups"]["blocked_buddy"])
        self.assertNotIn("$groupName = 'Disable_USB'", script)

    def test_manager_mismatch_requires_acknowledgement(self):
        self.buddy["manager_sam"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "Manager mismatch"):
            build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        plan = build_mover_plan(
            self.ticket, self.mover, self.buddy, self.manager,
            acknowledge_manager_mismatch=True,
        )
        self.assertTrue(plan["manager_mismatch"])

    def test_missing_buddy_manager_blocks_crosscheck(self):
        self.buddy["manager_sam"] = ""
        with self.assertRaisesRegex(ValueError, "buddy has no Manager"):
            build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)

    def test_unknown_address_blocks_plan(self):
        self.ticket["company_name"] = "Unknown Company"
        self.ticket["office"] = "Unknown Office"
        with self.assertRaisesRegex(ValueError, "No safe address mapping"):
            build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)

    def test_current_office_wins_when_no_new_office_is_requested(self):
        self.ticket["office"] = "Girteka HUB (GBS)"
        self.ticket["new_office"] = ""
        plan = build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        self.assertEqual(plan["address"]["city"], "Tbilisi")
        self.assertEqual(plan["address"]["country"], "GE")

    def test_explicit_new_office_can_override_company_site(self):
        self.ticket["office"] = "Vilnius"
        self.ticket["new_office"] = "Tbilisi"
        plan = build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        self.assertEqual(plan["address"]["city"], "Tbilisi")
        self.assertEqual(plan["address"]["country"], "GE")

    def test_company_is_fallback_when_both_office_fields_are_empty(self):
        self.ticket["office"] = ""
        self.ticket["current_office"] = ""
        self.ticket["new_office"] = ""
        plan = build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        self.assertEqual(plan["address"]["city"], "Vilnius")
        self.assertEqual(plan["address"]["country"], "LT")

    def test_tndm_and_dedicated_movers_receive_girteka_email_policy(self):
        self.mover["email"] = "Legacy.User@tndmtrucking.com"
        for company in ("TNDM", "TNDM Trucking, UAB", "Girteka Dedicated"):
            with self.subTest(company=company):
                self.ticket["company_name"] = company
                self.ticket["office"] = ""
                plan = build_mover_plan(
                    self.ticket, self.mover, self.buddy, self.manager
                )
                script = build_mover_script(plan)

                self.assertEqual(plan["email_target"], "Legacy.User@girteka.eu")
                self.assertEqual(plan["address"]["city"], "Vilnius")
                self.assertEqual(plan["address"]["ext15"], "SF")
                self.assertIn(f"Company = '{company}'", script)
                self.assertIn("$newEmail = 'Legacy.User@girteka.eu'", script)
                self.assertIn("@tndmtrucking\\.com$", script)
                self.assertIn("Primary SMTP verification failed", script)

    def test_other_company_movers_keep_existing_email_behavior(self):
        self.mover["email"] = "Existing.User@example.com"
        plan = build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)
        script = build_mover_script(plan)

        self.assertEqual(plan["email_target"], "")
        self.assertNotIn("$newEmail", script)
        self.assertNotIn("targetAddress", script)

    def test_dedicated_mover_requires_an_existing_email_local_part(self):
        self.ticket["company_name"] = "Girteka Dedicated"
        self.mover["email"] = ""
        with self.assertRaisesRegex(ValueError, "no valid email address"):
            build_mover_plan(self.ticket, self.mover, self.buddy, self.manager)


if __name__ == "__main__":
    unittest.main()
