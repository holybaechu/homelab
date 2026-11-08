"""Offline safety fixtures executed through real PowerShell and a fake native kubectl."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PWSH = os.environ.get("PWSH") or shutil.which("pwsh")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-eso-dns01.ps1"
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
mode = os.environ['FIXTURE']
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps(args) + '\n')
name = 'eso-retirement-check'
solver = [{'dns01': {'cloudflare': {'apiTokenSecretRef': {'name': 'cloudflare-connect', 'key': 'credential'}}}, 'selector': {'dnsZones': ['hchu.me']}}]
issuer = {'kind': 'ClusterIssuer', 'metadata': {'name': 'letsencrypt', 'uid': 'production'}, 'spec': {'acme': {'solvers': solver}}}
def fail(message):
    print(message + ' PRIVATE-BODY-SENTINEL', file=sys.stderr)
    sys.exit(17)
if args[0] == 'create':
    obj = json.load(sys.stdin)
    if mode == 'create-failure' or (mode == 'certificate-create-failure' and obj['kind'] == 'Certificate'):
        fail('failed')
    sys.exit(0)
if args[0] in ('delete', 'wait'):
    if args[0] == 'delete' and '--raw' in args:
        options = json.load(sys.stdin)
        expected = '22345678-1234-1234-1234-123456789abc' if 'clusterissuers/' in args[2] else '12345678-1234-1234-1234-123456789abc'
        assert options == {'apiVersion':'v1', 'kind':'DeleteOptions', 'preconditions':{'uid':expected}}
        if mode == 'uid-conflict':
            fail('Conflict')
    sys.exit(0)
if '--ignore-not-found' in args:
    if args[1:3] == ['secret', name+'-tls'] and '--no-headers' in args and mode.startswith('tls-'):
        owners = '<none> <none> <none> <none>' if mode == 'tls-ownerless' else 'cert-manager.io/v1 Certificate '+name+' 32345678-1234-1234-1234-123456789abc'
        if mode == 'tls-unreviewed':
            owners = 'cert-manager.io/v1 Certificate PRIVATE-BODY-SENTINEL 32345678-1234-1234-1234-123456789abc'
        print('12345678-1234-1234-1234-123456789abc 2026-11-08T10:05:00Z '+owners+' <none> kubernetes.io/tls')
    if mode.startswith('collision-') and args[1] == mode.split('-')[1] and args[2] == mode.split('-', 2)[2]:
        print('resource/' + args[2])
    if mode in ('forbidden', 'unauthorized', 'transport', 'timeout'):
        fail({'forbidden':'Forbidden', 'unauthorized':'Unauthorized', 'transport':'Unable to connect', 'timeout':'deadline exceeded'}[mode])
    sys.exit(0)
if args[1] == 'secret':
    metadata = ['12345678-1234-1234-1234-123456789abc', '2026-11-08T09:58:49Z', '<none>', '<none>']
    if mode == 'wrong-account-time':
        metadata[1] = '2026-11-08T09:59:49Z'
    if mode == 'owned-account':
        metadata[2] = 'PRIVATE-BODY-SENTINEL'
    if mode == 'deleting-account':
        metadata[3] = '2026-11-08T10:00:00Z'
    print(' '.join(metadata))
    sys.exit(0)
if args[1] == 'clusterissuer':
    if args[2] == name:
        issuer['metadata'].update(name=name, uid='22345678-1234-1234-1234-123456789abc', creationTimestamp='2026-11-08T09:58:49Z')
        issuer['spec']['acme'].update(server='https://acme-staging-v02.api.letsencrypt.org/directory', privateKeySecretRef={'name': name+'-account'})
        if mode == 'wrong-staging':
            issuer['spec']['acme']['server'] = 'https://production.invalid'
        if mode == 'wrong-issuer-time':
            issuer['metadata']['creationTimestamp'] = '2026-11-08T09:59:49Z'
        if mode == 'different-solver':
            issuer['spec']['acme']['solvers'][0]['dns01']['cloudflare']['email'] = 'PRIVATE-BODY-SENTINEL'
        if mode == 'deleting-issuer':
            issuer['metadata']['deletionTimestamp'] = '2026-11-08T10:00:00Z'
    elif mode == 'invalid-json':
        print('PRIVATE-BODY-SENTINEL not-json')
        sys.exit(0)
    elif mode == 'issuer-exit':
        fail('Forbidden')
    elif mode == 'wrong-projection':
        issuer['spec']['acme']['solvers'][0]['selector']['dnsZones'] = ['other.invalid']
    print(json.dumps(issuer))
elif args[1] == 'certificate':
    print(json.dumps({'kind':'Certificate', 'metadata':{'name':name, 'namespace':'cert-manager'}, 'status':{'revision':1, 'conditions':[{'type':'Ready', 'status':'True'}]}}))
else:
    if mode == 'challenge-read-failure' and args[1] == 'challenges.acme.cert-manager.io':
        print('{}')
    else:
        items = []
        if mode == 'existing-consumer' and args[1] == 'orders.acme.cert-manager.io':
            items = [{'kind':'Order', 'apiVersion':'acme.cert-manager.io/v1', 'metadata':{'name':'test-order', 'namespace':'cert-manager'}, 'spec':{'issuerRef':{'name':name}}}]
        if mode == 'shared-account' and args[1] == 'issuers.cert-manager.io':
            items = [{'kind':'Issuer', 'apiVersion':'cert-manager.io/v1', 'metadata':{'name':'other-issuer', 'namespace':'cert-manager'}, 'spec':{'acme':{'privateKeySecretRef':{'name':name+'-account'}}}}]
        item_kind = {'challenges.acme.cert-manager.io':'Challenge', 'orders.acme.cert-manager.io':'Order', 'certificates.cert-manager.io':'Certificate', 'issuers.cert-manager.io':'Issuer', 'clusterissuers.cert-manager.io':'ClusterIssuer'}[args[1]]
        api = 'acme.cert-manager.io/v1' if item_kind in ('Challenge', 'Order') else 'cert-manager.io/v1'
        if mode in ('generic-nonempty', 'typed-nonempty') and item_kind in ('Challenge', 'Order'):
            items = [{'kind':item_kind, 'apiVersion':api, 'metadata':{'name':'unrelated', 'namespace':'cert-manager'}, 'spec':{'issuerRef':{'name':'letsencrypt'}}}]
        obj = {'kind':item_kind+'List' if mode.startswith('typed-') else 'List', 'apiVersion':api if mode.startswith('typed-') else 'v1', 'items':items}
        if mode == 'missing-items':
            del obj['items']
        if mode == 'null-items':
            obj['items'] = None
        if mode == 'object-items':
            obj['items'] = {}
        if mode == 'invalid-item':
            obj['items'] = [{'kind':'PRIVATE-BODY-SENTINEL'}]
        print(json.dumps(obj))
'''


@unittest.skipUnless(PWSH, "Set PWSH to an official PowerShell executable")
class Dns01Tests(unittest.TestCase):
    def run_fixture(self, mode, *flags, shadow=False):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            executable = directory / "kubectl"
            executable.write_text(FAKE)
            executable.chmod(0o755)
            calls = directory / "calls.jsonl"
            env = dict(os.environ, FIXTURE=mode, CALLS=str(calls), PATH=str(directory) + os.pathsep + os.environ["PATH"])
            command = [PWSH, "-NoProfile", "-File", str(SCRIPT), *flags]
            if shadow:
                escaped = str(SCRIPT).replace("'", "''")
                command = [PWSH, "-NoProfile", "-Command", "$global:LASTEXITCODE = 91; function kubectl { throw 'SHADOW-MUST-NOT-RUN' }; & '" + escaped + "' -DiagnoseOnly"]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            self.assertNotIn("PRIVATE-BODY-SENTINEL", output)
            self.assertNotIn("SHADOW-MUST-NOT-RUN", output)
            return result.returncode, output, [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def assert_no_writes(self, calls):
        self.assertFalse(any(call[0] in ("create", "delete") for call in calls), calls)

    def test_all_collisions_block_without_writes(self):
        for kind, name in (("clusterissuer", "eso-retirement-check"), ("certificate", "eso-retirement-check"), ("secret", "eso-retirement-check-account"), ("secret", "eso-retirement-check-tls")):
            with self.subTest(kind=kind, name=name):
                code, output, calls = self.run_fixture(f"collision-{kind}-{name}")
                self.assertNotEqual(code, 0)
                self.assertIn("category=name-collision", output)
                self.assertIn(f"/{kind}/{name}", output)
                self.assertEqual(len(calls), 4)
                self.assert_no_writes(calls)

    def test_native_errors_are_classified_and_redacted(self):
        for mode, category in (("forbidden", "access-denied"), ("unauthorized", "authentication"), ("transport", "transport"), ("timeout", "timeout"), ("issuer-exit", "access-denied")):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode)
                self.assertNotEqual(code, 0)
                self.assertIn(f"category={category} exit=17", output)
                self.assert_no_writes(calls)

    def test_json_and_projection_fail_closed(self):
        for mode, category in (("invalid-json", "invalid-json-object"), ("wrong-projection", "contract-mismatch")):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode)
                self.assertNotEqual(code, 0)
                self.assertIn(f"category={category}", output)
                self.assert_no_writes(calls)

    def test_stale_exit_and_function_shadow_do_not_affect_native_result(self):
        code, output, calls = self.run_fixture("success", shadow=True)
        self.assertEqual(code, 0, output)
        self.assertIn("PASS DNS01-read-only-preflight", output)
        self.assert_no_writes(calls)

    def test_fresh_success_and_cleanup(self):
        code, output, calls = self.run_fixture("success")
        self.assertEqual(code, 0, output)
        self.assertIn("PASS fresh-staging-DNS01", output)
        self.assertIn("PASS staging-DNS01-cleanup", output)
        self.assertEqual(sum(c[0] == 'create' for c in calls), 2)
        deletes = [c[1:3] for c in calls if c[0] == 'delete']
        self.assertEqual(deletes, [['certificate', 'eso-retirement-check'], ['secret', 'eso-retirement-check-tls'], ['clusterissuer', 'eso-retirement-check'], ['secret', 'eso-retirement-check-account']])

    def test_unknown_creates_never_cleanup(self):
        for mode in ("create-failure", "certificate-create-failure"):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode)
                self.assertNotEqual(code, 0)
                self.assertIn("unknown-create-outcome", output)
                self.assertFalse(any(c[0] == 'delete' for c in calls))

    def test_bad_challenge_read_preserves_issuer_account(self):
        code, output, calls = self.run_fixture("challenge-read-failure")
        self.assertNotEqual(code, 0)
        self.assertIn("category=invalid-list", output)
        self.assertEqual([c[1] for c in calls if c[0] == 'delete'], ['certificate'])

    def test_reviewed_resume_and_cleanup(self):
        code, output, calls = self.run_fixture("success", "-ResumeStagingPair")
        self.assertEqual(code, 0, output)
        self.assertIn("PASS reviewed-staging-pair", output)
        self.assertIn("PASS staging-DNS01-cleanup", output)
        self.assertEqual(sum(c[0] == 'create' for c in calls), 1)

    def test_resume_diagnostic_is_read_only(self):
        code, output, calls = self.run_fixture("success", "-ResumeStagingPair", "-DiagnoseOnly")
        self.assertEqual(code, 0, output)
        self.assert_no_writes(calls)

    def test_generic_and_typed_list_empty_and_singleton(self):
        for mode in ('success', 'generic-nonempty', 'typed-empty', 'typed-nonempty'):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode)
                self.assertEqual(code, 0, output)
                self.assertIn('PASS staging-DNS01-cleanup', output)

    def test_invalid_lists_preserve_pair(self):
        for mode in ('missing-items', 'null-items', 'object-items', 'invalid-item'):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode)
                self.assertNotEqual(code, 0)
                self.assertIn('category=invalid-list', output)
                self.assertEqual([c[1] for c in calls if c[0] == 'delete'], ['certificate'])

    def test_cleanup_only_never_issues_and_uses_uid_preconditions(self):
        for mode in ('success', 'tls-owned', 'tls-ownerless', 'generic-nonempty'):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode, '-CleanupOnly')
                self.assertEqual(code, 0, output)
                self.assertIn('PASS staging-DNS01-cleanup', output)
                self.assertFalse(any(c[0] == 'create' for c in calls))
                deletes = [c for c in calls if c[0] == 'delete']
                self.assertEqual(len(deletes), 3 if mode.startswith('tls-') else 2)
                self.assertTrue(all(c[1] == '--raw' for c in deletes))
                self.assertEqual(sum(c[0] == 'get' and '-o' in c and c[c.index('-o')+1] == 'name' for c in calls), 5)

    def test_cleanup_only_rejects_bad_contracts_before_deletes(self):
        for mode in ('wrong-staging', 'wrong-issuer-time', 'wrong-account-time', 'tls-unreviewed', 'existing-consumer', 'shared-account', 'missing-items', 'null-items', 'invalid-item', 'collision-certificate-eso-retirement-check'):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode, '-CleanupOnly')
                self.assertNotEqual(code, 0)
                self.assert_no_writes(calls)

    def test_cleanup_uid_conflict_stops_without_retry(self):
        code, output, calls = self.run_fixture('uid-conflict', '-CleanupOnly')
        self.assertNotEqual(code, 0)
        self.assertEqual(sum(c[0] == 'delete' for c in calls), 1)
        self.assertNotIn('PASS staging-DNS01-cleanup', output)

    def test_cleanup_diagnostic_is_read_only(self):
        code, output, calls = self.run_fixture('success', '-CleanupOnly', '-DiagnoseOnly')
        self.assertEqual(code, 0, output)
        self.assert_no_writes(calls)

    def test_unreviewed_resume_blocks_without_cleanup(self):
        for mode in ("wrong-staging", "wrong-issuer-time", "different-solver", "deleting-issuer", "wrong-account-time", "owned-account", "deleting-account", "existing-consumer", "shared-account"):
            with self.subTest(mode=mode):
                code, output, calls = self.run_fixture(mode, "-ResumeStagingPair")
                self.assertNotEqual(code, 0)
                self.assertIn("FAIL stage=resume/", output)
                self.assert_no_writes(calls)


if __name__ == "__main__":
    unittest.main()
