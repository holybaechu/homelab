"""Keep all-port Pod access behind Personal's Headscale admission boundary."""
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return json.loads(subprocess.check_output(
        ['yq', '-o=json', '.', str(ROOT / path)]))


class TailnetAccessTests(unittest.TestCase):
    def test_personal_is_the_only_headscale_entitlement_binding(self):
        config = read_yaml('kubernetes/identity/headscale/config.yaml')
        self.assertEqual(config['oidc']['allowed_groups'], ['user'])
        entries = read_yaml('kubernetes/identity/blueprints/homelab.yaml')['entries']
        entitlement = next(entry for entry in entries
                           if entry.get('id') == 'headscale-user-entitlement')
        self.assertEqual(entitlement['identifiers'], {
            'app': 'headscale-app', 'name': 'user'})
        bindings = [entry for entry in entries
                    if entry['model'] == 'authentik_policies.policybinding'
                    and entry['identifiers']['target'] == entitlement['id']]
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]['attrs'], {
            'group': 'personal', 'policy': None, 'user': None})

    def test_only_member_devices_get_all_port_pod_access(self):
        policy = json.loads((ROOT / 'kubernetes/identity/headscale/policy.hujson').read_text())
        grants = [grant for grant in policy['grants']
                  if '${POD_CIDR}' in grant['dst'] or '*' in grant['dst']]
        self.assertTrue(grants)
        for grant in grants:
            self.assertEqual(grant['src'], ['autogroup:member'])
            self.assertEqual(grant['ip'], ['*'])
        self.assertEqual(policy['autoApprovers']['routes']['${POD_CIDR}'], ['tag:gateway'])


if __name__ == '__main__':
    unittest.main()
