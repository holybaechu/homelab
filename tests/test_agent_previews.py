"""Keep agent preview ingress behind the tailnet boundary."""
import json
from pathlib import Path
import subprocess
import unittest


class AgentPreviewBoundaryTests(unittest.TestCase):
    def test_preview_hosts_have_no_ingress_allowlist_bypass(self):
        for app in ('t3', 'hermes'):
            with self.subTest(app=app):
                rendered = subprocess.check_output(
                    ['kustomize', 'build', f'kubernetes/apps/{app}'],
                    cwd=Path(__file__).resolve().parents[1])
                documents = subprocess.check_output(
                    ['yq', '-N', '-o=json', '-I=0', '.'], input=rendered)
                hostname = f'preview.{app}.${{DOMAIN}}'
                routes = []
                for document in map(json.loads, documents.splitlines()):
                    if document['kind'] != 'IngressRoute':
                        continue
                    for route in document['spec']['routes']:
                        if hostname in route['match']:
                            self.assertEqual(document['spec']['entryPoints'], ['websecure'])
                            self.assertTrue(document['spec']['tls'].get('secretName'))
                            self.assertEqual(route['middlewares'][0], {
                                'name': 'tailnet-only', 'namespace': 'traefik'})
                            routes.append(route)
                self.assertTrue(routes, f'No preview ingress found for {app}')


if __name__ == '__main__':
    unittest.main()
