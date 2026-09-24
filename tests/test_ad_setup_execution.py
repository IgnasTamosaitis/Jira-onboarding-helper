import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from ad_automation import (
    build_new_joiner_script, build_rejoiner_dual_script, build_rejoiner_single_script, run_ps,
)
from ad_setup_execution import setup_result_status
from ad_ui import ADSetupWindow
from storage import TaskStorage, TASK_SCHEMA_VERSION


class CompletionTests(unittest.TestCase):
    def test_only_verified_clean_result_can_complete(self):
        cases = [
            ('OK', '', 0, 'Not completed'),
            ('AD_SETUP_VERIFIED:OTHER', '', 0, 'Not completed'),
            ('AD_SETUP_VERIFIED:USER', 'failure', 0, 'Not completed'),
            ('AD_SETUP_VERIFIED:USER', '', 1, 'Not completed'),
            ('WARNING: Manager lookup failed\nAD_SETUP_VERIFIED:USER', '', 0, 'Not completed'),
            ('WARNING: Group Restricted: Access denied\nAD_SETUP_VERIFIED:USER', '', 0, 'Completed'),
            ('AD_SETUP_VERIFIED:USER\nUnexpected trailing output', '', 0, 'Not completed'),
            ('AD_SETUP_READBACK:{}\nAD_SETUP_VERIFIED:USER', '', 0, 'Completed'),
        ]
        for output, error, code, expected in cases:
            with self.subTest(output=output, error=error, code=code):
                self.assertEqual(setup_result_status(output, error, code, 'USER'), expected)

    def test_failed_retry_clears_old_completion_without_touching_other_tasks(self):
        with patch('storage._load', return_value={
            '__task_schema_version': TASK_SCHEMA_VERSION,
            '__ad_setup_123': {'completed_at': 'previous', 'account': 'USER'},
            '123': [True, True, False, True, False],
        }), patch('storage._save'):
            storage = TaskStorage()
            storage.mark_ad_setup_incomplete('123', 'Not completed')
        self.assertFalse(storage.ad_setup_done('123'))
        self.assertEqual(storage.get('123'), [False, True, False, True, False])
        self.assertEqual(storage._data['__ad_setup_123']['account'], 'USER')

    def test_window_records_failure_instead_of_completion(self):
        window = Mock(ticket={'id': '123'})
        ADSetupWindow._apply_run_result(window, 'Not completed', 'Group failed')
        window._mark_setup_completed.assert_not_called()
        window.storage.mark_ad_setup_incomplete.assert_called_once_with('123', 'Not completed')
        window._write_audit_log.assert_called_once_with('Not completed', 'Group failed')


@unittest.skipUnless(shutil.which('powershell'), 'Windows PowerShell required for AD script simulation')
class GeneratedSetupExecutionTests(unittest.TestCase):
    def test_runner_loads_native_security_cmdlets_without_prompting(self):
        output, error, code = run_ps("(Get-Command Get-Credential -ErrorAction Stop).ModuleName")
        self.assertEqual(code, 0, error)
        self.assertEqual(output, 'Microsoft.PowerShell.Security')

    def run_setup(self, scenario, arrange='', buddy='BUDDY', groups=None):
        args = ('OU=Target,DC=example,DC=com', 'target@example.com', ['Normal Group'] if groups is None else groups)
        ticket = {'manager': 'Manager', 'company_name': 'Example', 'office': 'Vilnius', 'position': 'Jira title'}
        options = {'buddy_sam': buddy, 'ext_attrs': {'extensionAttribute14': 'Template'}}
        if scenario == 'new':
            script = build_new_joiner_script(ticket, {'username': 'SF'}, *args, **options)
        elif scenario == 'dual':
            script = build_rejoiner_dual_script(ticket, {'username': 'SF'}, {'username': 'OLD'}, *args, **options)
        else:
            script = build_rejoiner_single_script(ticket, {'username': 'OLD'}, *args, **options)
        harness = Path(__file__).with_name('ad_setup_fake_directory.ps1').read_text(encoding='utf-8')
        # Every AD command, credential prompt and password check is replaced by
        # an in-memory double before executing the actual generated script.
        full = harness + '\n' + arrange + '\n$caught=$null\ntry {\n' + script + r'''
} catch { $caught=$_.Exception.Message }
$result=@{error=$caught; mutations=$script:mutations; users=$script:users; deleted=@($script:deleted)}
Write-Host ('HARNESS_RESULT:' + ($result | ConvertTo-Json -Depth 8 -Compress))
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'simulation.ps1'
            path.write_text(full, encoding='utf-8-sig')
            result = subprocess.run(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', str(path)],
                capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        line = next((line for line in result.stdout.splitlines() if line.startswith('HARNESS_RESULT:')), '')
        self.assertTrue(line, result.stdout + result.stderr)
        return json.loads(line.removeprefix('HARNESS_RESULT:')), result.stdout

    def test_buddy_fills_blank_sf_role_fields(self):
        for scenario in ('new', 'dual'):
            with self.subTest(scenario=scenario):
                state, output = self.run_setup(scenario, "$script:users.SF.Title=$null; $script:users.SF.Description=$null; $script:users.SF.Department=$null")
                self.assertIsNone(state['error'], output)
                target = state['users']['SF' if scenario == 'new' else 'OLD']
                self.assertEqual(target['Title'], 'Template title')
                self.assertEqual(target['Description'], 'Distinct template description')
                self.assertEqual(target['Department'], 'Template department')
                self.assertEqual([p for p in target['proxyAddresses'] if p.startswith('SMTP:')], ['SMTP:target@example.com'])
                self.assertIn('AD_SETUP_VERIFIED:', output)
                # Ordinary buddy selection must never reassign the buddy's team.
                self.assertEqual(state['users']['EXISTING']['Manager'], state['users']['BUDDY']['DistinguishedName'])

    def test_role_missing_from_both_sources_stops_before_any_write(self):
        for scenario in ('new', 'dual', 'single'):
            with self.subTest(scenario=scenario):
                state, output = self.run_setup(scenario, "$script:users.BUDDY.Department=$null; $script:users.SF.Department=$null; $script:users.OLD.Department=$null")
                self.assertIn('Required role field is empty: Department', state['error'])
                self.assertEqual(state['mutations'], 0)
                self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_without_buddy_incomplete_sf_does_not_silently_pass(self):
        state, _ = self.run_setup('new', "$script:users.SF.Title=$null", buddy='')
        self.assertIn('Required role field is empty: Title', state['error'])
        self.assertEqual(state['mutations'], 0)

    def test_group_denial_is_advisory_in_all_setup_scenarios(self):
        for scenario in ('new', 'dual', 'single'):
            with self.subTest(scenario=scenario):
                state, output = self.run_setup(scenario, '$script:denyGroup=$true')
                self.assertIsNone(state['error'], output)
                self.assertIn('Insufficient access rights', output)
                self.assertEqual(state['deleted'], ['SF'] if scenario == 'dual' else [])
                account = 'SF' if scenario == 'new' else 'OLD'
                # The simulation adds HARNESS_RESULT after the real script output.
                execution = output.split('HARNESS_RESULT:', 1)[0].strip()
                self.assertEqual(setup_result_status(execution, '', 0, account), 'Completed')
                report_line = next(line for line in output.splitlines() if line.startswith('AD_SETUP_READBACK:'))
                report = json.loads(report_line.split(':', 1)[1])
                self.assertEqual(report['failures'], [])
                self.assertTrue(report['group_warnings'])

    def test_group_advisory_does_not_hide_attribute_failure(self):
        state, output = self.run_setup('dual', "$script:denyGroup=$true; $script:dropWrite='Department'; $script:users.OLD.Department='Old'")
        self.assertIn('Attribute mismatch: Department', state['error'])
        self.assertEqual(state['deleted'], [])
        self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_readback_mismatch_retains_sf(self):
        state, output = self.run_setup('dual', "$script:users.OLD.Department='Previous department'; $script:dropWrite='Department'")
        self.assertIn('Attribute mismatch: Department', state['error'])
        self.assertEqual(state['deleted'], [])
        self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_sf_reporting_links_preserved_before_deletion(self):
        state, output = self.run_setup('dual')
        self.assertIsNone(state['error'], output)
        self.assertEqual(state['deleted'], ['SF'])
        self.assertEqual(state['users']['REPORT']['Manager'], state['users']['OLD']['DistinguishedName'])
        self.assertEqual(state['users']['REPORT']['extensionAttribute10'], 'target@example.com')

    def test_delete_denial_is_not_completed(self):
        state, output = self.run_setup('dual', '$script:denyDelete=$true')
        self.assertIn('SF deletion denied', state['error'])
        self.assertIn('SF', state['users'])
        self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_password_validation_failure_retains_sf(self):
        state, output = self.run_setup('dual', '$script:denyPassword=$true')
        self.assertIn('Password validation failed', state['error'])
        self.assertEqual(state['deleted'], [])
        self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_changed_sf_data_is_retained(self):
        state, output = self.run_setup('dual', '$script:changeSf=$true')
        self.assertIn('SF data changed during setup: Company', state['error'])
        self.assertEqual(state['deleted'], [])
        self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_cancelled_signin_never_writes(self):
        state, output = self.run_setup('new', '$script:cancelSignIn=$true')
        self.assertIn('Administrator sign-in cancelled', state['error'])
        self.assertEqual(state['mutations'], 0)
        self.assertNotIn('AD_SETUP_VERIFIED:', output)

    def test_escaped_comma_in_account_name_does_not_fail_ou_check(self):
        state, output = self.run_setup('new', '$script:escapedName=$true')
        self.assertIsNone(state['error'], output)
        self.assertIn('AD_SETUP_VERIFIED:SF', output)

    def test_populated_sf_wins_over_conflicting_buddy(self):
        for scenario in ('new', 'dual'):
            with self.subTest(scenario=scenario):
                state, output = self.run_setup(scenario)
                self.assertIsNone(state['error'], output)
                target = state['users']['SF' if scenario == 'new' else 'OLD']
                self.assertEqual(target['Title'], 'SF title')
                self.assertEqual(target['Description'], 'SF description')
                self.assertEqual(target['Department'], 'SF department')
                source_line = next(line for line in output.splitlines() if line.startswith('AD_SETUP_ROLE_SOURCES:'))
                self.assertEqual(set(json.loads(source_line.split(':', 1)[1]).values()), {'SF'})

    def test_partial_sf_blanks_are_resolved_per_field_and_audited(self):
        for scenario in ('new', 'dual'):
            with self.subTest(scenario=scenario):
                state, output = self.run_setup(scenario, "$script:users.SF.Department='   '")
                self.assertIsNone(state['error'], output)
                target = state['users']['SF' if scenario == 'new' else 'OLD']
                self.assertEqual(target['Title'], 'SF title')
                self.assertEqual(target['Description'], 'SF description')
                self.assertEqual(target['Department'], 'Template department')
                source_line = next(line for line in output.splitlines() if line.startswith('AD_SETUP_ROLE_SOURCES:'))
                self.assertEqual(json.loads(source_line.split(':', 1)[1]), {
                    'Title': 'SF', 'Description': 'SF', 'Department': 'Buddy: BUDDY',
                })

    def test_complete_sf_does_not_depend_on_buddy_role_data(self):
        state, output = self.run_setup('dual', "$script:users.Remove('BUDDY')")
        self.assertIsNone(state['error'], output)
        self.assertEqual(state['users']['OLD']['Department'], 'SF department')

    def test_single_rejoiner_without_sf_keeps_jira_role_and_buddy_department(self):
        state, output = self.run_setup('single')
        self.assertIsNone(state['error'], output)
        self.assertEqual(state['users']['OLD']['Title'], 'Jira title')
        self.assertEqual(state['users']['OLD']['Description'], 'Jira title')
        self.assertEqual(state['users']['OLD']['Department'], 'Template department')


if __name__ == '__main__':
    unittest.main()
