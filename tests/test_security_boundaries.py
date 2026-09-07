import inspect
import re
import unittest

from ad_automation import generate_password
from ad_ui import ADSetupWindow
from snipeit_client import SnipeITClient


class PasswordSafetyTests(unittest.TestCase):
    def test_generated_password_meets_the_wizard_requirements(self):
        password = generate_password()

        self.assertEqual(len(password), 10)
        self.assertRegex(password, r"[A-Z]")
        self.assertRegex(password, r"[a-z]")
        self.assertRegex(password, r"[0-9]")
        self.assertRegex(password, r"[#@!]")

    def test_ad_wizard_uses_generated_password_instead_of_a_fixed_value(self):
        source = inspect.getsource(ADSetupWindow._fill)

        self.assertIn("generate_password()", source)
        self.assertIsNone(re.search(r"Welcome123|Initial123", source))


class SnipeITReadOnlyTests(unittest.TestCase):
    def test_client_has_no_inventory_mutation_request(self):
        source = inspect.getsource(SnipeITClient)

        self.assertIsNone(
            re.search(r"\.\s*(post|put|patch|delete)\s*\(", source, re.IGNORECASE)
        )


if __name__ == "__main__":
    unittest.main()
