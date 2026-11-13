"""Credential-free launcher checks; Windows ACLs and cloud access are not exercised."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
LAUNCHER = ROOT / "infra/backup/Review-Adoption.ps1"


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7.4+ required")
class BackupAdoptionTests(unittest.TestCase):
    def run_review(self, plan, artifact=None, show_exit=0, fixture_identity=False):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            private = base / "private"
            private.mkdir()
            if artifact:
                (private / artifact).write_text("preserve-me")
            source = LAUNCHER.read_text()
            if fixture_identity:
                # A producer-shape control, never a claim of successful B2 adoption.
                source = source.replace('b2_bucket.backup', 'terraform_data.backup')
                source = source.replace('unchanged B2 metadata', 'unchanged builtin fixture')
            # Only native Windows host/ACL boundaries are bypassed on Linux.
            source = source.replace(
                "if (-not $IsWindows) { Stop-Review 'Run this handoff on Windows, not a remote agent.' }", ""
            )
            start = source.index("$acl = Get-Acl")
            end = source.index("# Reject inherited switches", start)
            source = source[:start] + source[end:]
            script = base / "source/infra/backup/Review-Adoption.ps1"
            script.parent.mkdir(parents=True)
            script.write_text(source)
            (base / "response.json").write_text(json.dumps(plan))
            native = base / "tofu"
            native.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "base = pathlib.Path(__file__).parent\n"
                "with (base / 'calls.jsonl').open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if 'version' in sys.argv: print('{\"terraform_version\":\"1.13.2\"}')\n"
                "elif 'show' in sys.argv:\n"
                " print((base / 'response.json').read_text())\n"
                " sys.exit(int(os.environ['SHOW_EXIT']))\n"
            )
            native.chmod(0o700)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('TF_', 'OP_', 'B2_'))}
            env.update(PATH=str(base) + os.pathsep + os.environ['PATH'],
                       OP_ACCOUNT="fake-account", B2_APPLICATION_KEY_ID="fake-id",
                       B2_APPLICATION_KEY="fake-key", TF_VAR_state_passphrase="fake-passphrase-for-offline-tests-only",
                       SHOW_EXIT=str(show_exit))
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(script), "-PrivateDirectory", str(private)],
                env=env, capture_output=True, text=True,
            )
            calls = base / "calls.jsonl"
            self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            if artifact:
                self.assertEqual((private / artifact).read_text(), "preserve-me")
            return result

    def candidate(self):
        # Deliberately synthetic rejection inputs, NOT evidence of a valid B2 plan.
        return {"format_version": "1.2", "terraform_version": "1.13.2", "errored": False,
                "resource_changes": [{"address": "b2_bucket.backup", "mode": "managed", "change": {
                    "actions": ["no-op"], "importing": {"id": "8a6f23878ab0beb4a3120e11"}}}]}

    def test_invalid_schema_status_and_identity_are_refused(self):
        cases = {}
        for key, value in (("errored", True), ("errored", "false"), ("format_version", "9.0"),
                           ("terraform_version", "1.13.3")):
            plan = self.candidate()
            plan[key] = value
            cases[f"{key}-{value}"] = plan
        cases['root-array'] = [self.candidate()]
        for field in ("format_version", "terraform_version", "errored"):
            plan = self.candidate()
            del plan[field]
            cases['missing-' + field] = plan
        plan = self.candidate()
        plan['resource_changes'][0]['change']['actions'] = 'no-op'
        cases['scalar-actions'] = plan
        plan = self.candidate()
        plan['resource_changes'][0]['change']['importing'] = [{"id": "8a6f23878ab0beb4a3120e11"}]
        cases['array-import'] = plan
        for field, value in (("mode", ["managed"]), ("address", ["b2_bucket.backup"])):
            plan = self.candidate()
            plan['resource_changes'][0][field] = value
            cases['array-' + field] = plan
        plan = self.candidate()
        plan['resource_changes'][0]['change']['actions'] = [["no-op"]]
        cases['nested-array-actions'] = plan
        plan = self.candidate()
        plan['resource_changes'][0]['change']['importing']['id'] = ["8a6f23878ab0beb4a3120e11"]
        cases['array-import-id'] = plan
        for address in ("B2_BUCKET.BACKUP", "b2_bucket.backup\ufeff", "terraform_data.backup"):
            plan = self.candidate()
            plan['resource_changes'][0]['address'] = address
            cases[address] = plan
        for action in (["create"], ["update"], ["delete", "create"], []):
            plan = self.candidate()
            plan['resource_changes'][0]['change']['actions'] = action
            cases[str(action)] = plan
        for mode in ('data', 'MANAGED'):
            plan = self.candidate()
            plan['resource_changes'][0]['mode'] = mode
            cases[mode] = plan
        plan = self.candidate()
        plan['resource_changes'].append(copy.deepcopy(plan['resource_changes'][0]))
        cases['duplicate'] = plan
        plan = self.candidate()
        plan['resource_changes'][0]['change']['importing']['id'] = 'wrong-bucket'
        cases['wrong-id'] = plan
        for name, plan in cases.items():
            with self.subTest(name=name):
                result = self.run_review(plan)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('PASS:', result.stdout)

    @unittest.skipUnless(shutil.which('tofu'), 'Pinned OpenTofu required')
    def test_real_encrypted_import_producer_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            original = (ROOT / 'infra/backup/main.tf').read_text()
            # Reuse the exact encryption and sensitive/ephemeral input declarations.
            config = 'terraform {\n  required_version = "= 1.13.2"\n'
            config += original[original.index('  backend "local"'):original.index('\nvariable "provision_scaleway"')]
            config += '\nresource "terraform_data" "backup" {}\nimport {\n'
            config += '  to = terraform_data.backup\n  id = "8a6f23878ab0beb4a3120e11"\n}\n'
            (base / 'main.tf').write_text(config)
            (base / 'cli.tfrc').write_text('disable_checkpoint = true\n')
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('TF_', 'OP_', 'B2_', 'SCW_'))}
            env.update(HOME=str(base), TF_CLI_CONFIG_FILE=str(base / 'cli.tfrc'),
                       TF_VAR_state_passphrase='fake-passphrase-for-offline-tests-only', CHECKPOINT_DISABLE='1')
            def tofu(*args):
                return subprocess.run(['tofu', '-chdir=' + directory, *args],
                                      env=env, capture_output=True, text=True)
            self.assertEqual(json.loads(tofu('version', '-json').stdout)['terraform_version'], '1.13.2')
            self.assertEqual(tofu('init', '-input=false').returncode, 0)
            saved = str(base / 'import.tfplan')
            self.assertEqual(tofu('plan', '-input=false', '-detailed-exitcode', '-out=' + saved).returncode, 2)
            self.assertEqual(json.loads(Path(saved).read_text())['encryption_version'], 'v0')
            shown = tofu('show', '-json', saved)
            self.assertEqual(shown.returncode, 0)
            plan = json.loads(shown.stdout)
            self.assertEqual(plan['format_version'], '1.2')
            self.assertIs(plan['errored'], False)
            self.assertNotIn('complete', plan)
            change = plan['resource_changes'][0]['change']
            self.assertEqual(change['actions'], ['no-op'])
            self.assertEqual(change['importing']['id'], '8a6f23878ab0beb4a3120e11')
            for values in (plan['prior_state']['values'], plan['planned_values']):
                self.assertEqual(values['root_module']['resources'][0]['address'], 'terraform_data.backup')
            self.assertFalse((base / 'backup.tfstate').exists())
            env['TF_ENCRYPTION'] = ('method "unencrypted" "probe" {}\n'
                                    'state {\n method = method.unencrypted.probe\n enforced = false\n}\n'
                                    'plan {\n method = method.unencrypted.probe\n enforced = false\n}\n')
            override = base / 'override.tfplan'
            unencrypted = tofu('plan', '-input=false', '-out=' + str(override))
            self.assertNotEqual(unencrypted.returncode, 0)
            self.assertFalse(override.exists())
            del env['TF_ENCRYPTION']
            env['TF_VAR_state_passphrase'] = 'wrong-fake-passphrase-for-offline-tests'
            refused = tofu('show', '-json', saved)
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse(refused.stdout.strip())
            result = self.run_review(plan)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unexpected B2 import', result.stderr)
            control = self.run_review(plan, fixture_identity=True)
            self.assertEqual(control.returncode, 0, control.stderr)
            self.assertIn('terraform_data.backup: no-op', control.stdout)

    def test_native_failure_cannot_pass(self):
        result = self.run_review(self.candidate(), show_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Plan decryption failed', result.stderr)
        plan_call = next(call for call in self.calls if 'plan' in call)
        self.assertIn('-var=provision_scaleway=false', plan_call)
        self.assertIn('-var=provision_runtime_credentials=false', plan_call)
        self.assertIn('-detailed-exitcode', plan_call)

    def test_existing_artifacts_are_preserved_before_native_calls(self):
        for artifact in ('adoption.tfplan', 'backup.tfstate', 'tofu-data',
                         'backup.tfstate.backup', '.backup.tfstate.lock.info'):
            with self.subTest(artifact=artifact):
                result = self.run_review({}, artifact=artifact)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls, [])
                self.assertIn('Existing artifacts found', result.stderr)


if __name__ == '__main__':
    unittest.main()
