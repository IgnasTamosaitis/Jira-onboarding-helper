import json
import shutil
import unittest
from unittest.mock import patch

from ad_automation import get_account_enabled_status, _parse_account_search_output, run_ps
from ui import MainWindow


class BuddyAccountStatusTests(unittest.TestCase):
    def output(self, enabled):
        return 'AD_ACCOUNT_STATUS:' + json.dumps({
            'enabled': enabled, 'dn': 'CN=Dmytro,OU=Users,DC=example,DC=com',
            'name': 'Dmytro Ahibailov'})

    def test_warnings_do_not_turn_enabled_buddy_into_disabled(self):
        output = 'WARNING: Module warning\n' + self.output(True) + '\nWARNING: trailing warning'
        with patch('ad_automation.run_ps', return_value=(output, '', 0)):
            buddy = MainWindow._resolve_detected_buddy({'name': 'x17315'})
        self.assertFalse(buddy['disabled'])
        self.assertEqual(buddy['display_name'], 'Dmytro Ahibailov')

    def test_explicit_false_remains_disabled(self):
        with patch('ad_automation.run_ps', return_value=(self.output(False), '', 0)):
            self.assertIs(get_account_enabled_status('x17315')[0], False)

    def test_unreadable_status_never_means_disabled(self):
        for output, code in [(self.output(None), 0), (self.output('False'), 0),
                             ('WARNING: AD unavailable', 0), ('AD_ACCOUNT_STATUS:{', 0),
                             (self.output(True), 1)]:
            with self.subTest(output=output, code=code):
                with patch('ad_automation.run_ps', return_value=(output, '', code)):
                    self.assertIsNone(get_account_enabled_status('x17315')[0])

    def test_search_rejects_unknown_status(self):
        output = '\n'.join(f'x17315|{value}|Title|CN=Dmytro,OU=Users,DC=example,DC=com'
                           for value in ('', 'unknown', 'true'))
        accounts = _parse_account_search_output(output)
        self.assertEqual(len(accounts), 1)
        self.assertTrue(accounts[0]['enabled'])

    @unittest.skipUnless(shutil.which('powershell'), 'Windows PowerShell required')
    def test_generated_script_with_real_powershell_warning(self):
        def simulate(script, timeout):
            harness = '''
function Import-Module { Write-Warning 'Simulated module warning' }
function Get-ADUser {
    [pscustomobject]@{Enabled=$true; DistinguishedName='CN=Dmytro,OU=Users,DC=example,DC=com'; Name='Dmytro Ahibailov'}
}
'''
            return run_ps(harness + script, timeout=timeout)
        with patch('ad_automation.run_ps', side_effect=simulate):
            self.assertIs(get_account_enabled_status('x17315')[0], True)
