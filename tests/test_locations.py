import unittest
from pathlib import Path

from ad_automation import (
    DEFAULT_GROUPS,
    build_new_joiner_script,
    build_rejoiner_dual_script,
    build_rejoiner_single_script,
    build_email,
    detect_address_warning,
    detect_company_address,
    detect_domain,
    detect_location,
    detect_location_conflict,
    detect_site,
)


class LocationDetectionTests(unittest.TestCase):
    def test_all_known_company_names_map_to_their_sites(self):
        companies = {
            "siauliai": [
                "Mireli, UAB",
                "Trasis, UAB",
                "Girmeta, UAB",
                "Termolita, UAB",
                "Girtrans, UAB",
                'UAB "KLP transport"',
                "Premium trans, UAB",
                "TermoTrans, UAB",
            ],
            "poland": [
                "GIRPOLTRANS Sp.zo.o",
                "TransEu Poland Sp.zo.o",
                "Eupoltrans Sp.zo.o",
                "Scanpoltrans Sp.zo.o",
                "POLSERVICE Sp.zo.o",
                "GoTrans",
                "Me trailers Poland",
                "Classtrucks Poland sp. z o.o.",
            ],
            "vilnius": [
                "Trucks merchant, uab",
                "Willgrow, UAB",
                "GCC, UAB",
                "TNDM Trucking, UAB",
                "Girteka Dedicated",
                "Girteka Nordic, UAB",
                "Girteka Transport, UAB",
                "Girteka, UAB",
                "Girteka group, UAB",
                "Girteka Logistics, UAB",
                "ME Trailers, UAB",
                "Girteka Cargo, UAB",
            ],
        }
        for expected_site, names in companies.items():
            for company in names:
                with self.subTest(company=company):
                    self.assertEqual(detect_site("", company), expected_site)

    def test_real_jira_office_values_are_recognised(self):
        cases = {
            "Girteka Park": "vilnius",
            "Siauliai Campus": "siauliai",
            "Šiauliai": "siauliai",
            "Poznan Campus": "poland",
            "Poznańska 4, Sady": "poland",
            "Tbilisi": "georgia",
        }
        for office, expected_site in cases.items():
            with self.subTest(office=office):
                self.assertEqual(detect_site(office), expected_site)

    def test_office_wins_and_conflict_is_reported(self):
        self.assertEqual(
            detect_site("Siauliai Campus", "Girpoltrans Sp. z o.o."),
            "siauliai",
        )
        warning = detect_location_conflict(
            "Siauliai Campus",
            "Girpoltrans Sp. z o.o.",
        )
        self.assertIn("Šiauliai", warning)
        self.assertIn("Poland", warning)

    def test_site_maps_to_existing_default_group_regions(self):
        self.assertEqual(detect_location("Siauliai Campus", "UAB TRASIS"), "lithuania")
        self.assertEqual(detect_location("Girteka Park", "Girteka, UAB"), "lithuania")
        self.assertEqual(detect_location("Poznan Campus", "GoTrans"), "poland")
        self.assertEqual(detect_location("Tbilisi", "Girteka Business Services LLC"), "georgia")
        self.assertIn("PL Baze", DEFAULT_GROUPS["poland"])

    def test_tndm_and_girteka_dedicated_use_girteka_onboarding_policy(self):
        for company in ("TNDM", "TNDM Trucking, UAB", "Girteka Dedicated"):
            with self.subTest(company=company):
                self.assertEqual(detect_site("", company), "vilnius")
                self.assertEqual(detect_location("", company), "lithuania")
                self.assertEqual(detect_domain(company), "girteka.eu")
                self.assertEqual(
                    build_email("Test", "User", detect_domain(company)),
                    "Test.User@girteka.eu",
                )

    def test_retired_tndm_domain_cannot_restore_the_old_email_pattern(self):
        self.assertEqual(
            build_email("Test", "User", "tndmtrucking.com"),
            "Test.User@girteka.eu",
        )


class AddressMappingTests(unittest.TestCase):
    def test_gbs_uses_confirmed_full_tbilisi_address(self):
        expected = {
            "street": (
                "Georgia, Tbilisi, Vake District, Ilia Chavchavadze Avenue, "
                "No. 37L, Commercial Space No. 5, Floor 3-4, Block A"
            ),
            "city": "Tbilisi",
            "zip": "0162",
            "country": "GE",
            "office": "Tbilisi",
            "ext15": "SF",
        }
        self.assertEqual(
            detect_company_address("Girteka Business Services LLC", "GBS"),
            expected,
        )

    def test_siauliai_uses_confirmed_campus_address(self):
        expected = {
            "street": "Pročiūnų g. 16",
            "city": "Šiauliai",
            "zip": "77103",
            "country": "LT",
            "office": "Siauliai Campus",
            "ext15": "SF",
        }
        self.assertEqual(
            detect_company_address("UAB TRASIS", "Siauliai Campus"),
            expected,
        )
        self.assertEqual(detect_company_address("Mireli, UAB"), expected)

    def test_girpoltrans_keeps_existing_sady_mapping(self):
        address = detect_company_address(
            "Girpoltrans sp z o.o.",
            "Poznan Campus",
        )
        self.assertEqual(address["street"], "Poznańska 4")
        self.assertEqual(address["city"], "Sady")
        self.assertEqual(address["country"], "PL")

    def test_other_polish_companies_do_not_inherit_sady_address(self):
        for company in ("GoTrans", "ME Trailers Poland", "Classtrucks Poland sp. z o.o."):
            with self.subTest(company=company):
                address = detect_company_address(company, "Poznan Campus")
                self.assertEqual(address["office"], "Poznan Campus")
                self.assertEqual(address["country"], "PL")
                self.assertFalse(address["street"])
                self.assertFalse(address["city"])
                self.assertFalse(address["zip"])
                self.assertTrue(detect_address_warning("Poznan Campus", company))

    def test_explicit_sady_office_applies_full_address_to_any_polish_company(self):
        address = detect_company_address(
            "TransEu Poland Sp.zo.o",
            "Poznańska 4, Sady",
        )
        self.assertEqual(address["street"], "Poznańska 4")
        self.assertEqual(address["city"], "Sady")
        self.assertFalse(
            detect_address_warning("Poznańska 4, Sady", "TransEu Poland Sp.zo.o")
        )


class GeneratedScriptTests(unittest.TestCase):
    SF_ACCOUNT = {
        "username": "TESTSF",
        "dn": "CN=TESTSF,OU=Active_Users_from_SF,DC=girteka,DC=lt",
    }
    OLD_ACCOUNT = {"username": "TESTOLD"}
    OU = "OU=Test,DC=girteka,DC=lt"

    def test_permission_groups_are_filtered_in_all_onboarding_scenarios(self):
        ticket = {
            "office": "Vilnius",
            "company_name": "Girteka Logistics UAB",
            "manager": "Test Manager",
            "position": "Tester",
        }
        groups = [
            "Normal Group",
            "EU Transportas/O=girteka",
            "Disable_USB",
            "VPN_IT_integracijos",
            "GrayList_WillGrow Users",
        ]
        scripts = [
            build_new_joiner_script(
                ticket, self.SF_ACCOUNT, self.OU,
                "Test.User@girteka.eu", "Example#42", groups,
            ),
            build_rejoiner_dual_script(
                ticket, self.SF_ACCOUNT, self.OLD_ACCOUNT, self.OU,
                "Test.User@girteka.eu", "Example#42", groups,
            ),
            build_rejoiner_single_script(
                ticket, self.OLD_ACCOUNT, self.OU,
                "Test.User@girteka.eu", "Example#42", groups,
            ),
        ]
        for script in scripts:
            with self.subTest(script=script[:40]):
                self.assertIn("$groupName = 'Normal Group'", script)
                self.assertIn("$groupName = 'EU Transportas/O=girteka'", script)
                self.assertIn("Get-ADGroup -Filter", script)
                self.assertIn(
                    "Add-ADGroupMember -Identity $targetGroups[0].DistinguishedName",
                    script,
                )
                self.assertNotIn("Disable_USB", script)
                self.assertNotIn("VPN_IT_integracijos", script)
                self.assertNotIn("GrayList_WillGrow Users", script)

    def test_siauliai_fields_are_applied_in_all_scenarios(self):
        ticket = {
            "office": "Siauliai Campus",
            "company_name": "UAB TRASIS",
            "manager": "Jekaterina Šidlauskienė",
            "position": "Administrator",
        }
        scripts = [
            build_new_joiner_script(
                ticket, self.SF_ACCOUNT, self.OU,
                "Test.User@girteka.eu", "Example#42", [],
            ),
            build_rejoiner_dual_script(
                ticket, self.SF_ACCOUNT, self.OLD_ACCOUNT, self.OU,
                "Test.User@girteka.eu", "Example#42", [],
            ),
            build_rejoiner_single_script(
                ticket, self.OLD_ACCOUNT, self.OU,
                "Test.User@girteka.eu", "Example#42", [],
            ),
        ]
        for script in scripts:
            with self.subTest(script=script[:40]):
                self.assertIn("City", script)
                self.assertIn("Šiauliai", script)
                self.assertIn("PostalCode", script)
                self.assertIn("77103", script)
                self.assertIn("Country", script)
                self.assertIn("'LT'", script)

    def test_unknown_polish_street_is_copied_from_sf_for_dual_rejoiner(self):
        ticket = {
            "office": "Poznan Campus",
            "company_name": "GoTrans",
        }
        script = build_rejoiner_dual_script(
            ticket, self.SF_ACCOUNT, self.OLD_ACCOUNT, self.OU,
            "Test.User@girteka.eu", "Example#42", [],
        )
        self.assertIn("$setParams['StreetAddress'] = $sf.StreetAddress", script)
        self.assertIn("$setParams['City'] = $sf.City", script)
        self.assertIn("Office        = 'Poznan Campus'", script)
        self.assertNotIn("Poznańska 4", script)

    def test_email_manager_is_looked_up_by_email_address(self):
        ticket = {
            "office": "Poznańska 4, Sady",
            "company_name": "Girpoltrans Sp. z o.o.",
            "manager": (
                "<a href='mailto:aliaksandra.krzysztofik@girteka.eu'>"
                "aliaksandra.krzysztofik@girteka.eu</a>"
            ),
        }
        script = build_rejoiner_single_script(
            ticket, self.OLD_ACCOUNT, self.OU,
            "Test.User@girteka.eu", "Example#42", [],
        )
        self.assertIn(
            "EmailAddress -eq 'aliaksandra.krzysztofik@girteka.eu'",
            script,
        )

    def test_rejoiner_cleanup_removes_retired_tndm_proxy_addresses(self):
        ticket = {
            "office": "Girteka Park",
            "company_name": "TNDM",
            "manager": "Test Manager",
            "position": "Tester",
        }
        script = build_rejoiner_single_script(
            ticket, self.OLD_ACCOUNT, self.OU,
            "Test.User@girteka.eu", "Example#42", [],
        )
        self.assertIn("Remove addresses from TNDM's retired mail domain", script)
        self.assertIn("@tndmtrucking\\.com$", script)

    def test_default_jql_remains_scoped_to_current_user(self):
        ui_source = (Path(__file__).parents[1] / "ui.py").read_text(encoding="utf-8")
        self.assertIn(
            'assignee = currentUser() AND issuetype = "SF: Employee onboarding"',
            ui_source,
        )


if __name__ == "__main__":
    unittest.main()
