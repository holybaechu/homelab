"""Keep T3's shared Git identity separate from broader assistant access."""
import json
from pathlib import Path
import subprocess
import unittest


class T3CredentialBoundaryTests(unittest.TestCase):
    def test_root_only_sources_and_private_runtime_boundary(self):
        rendered = subprocess.check_output(
            ['kustomize', 'build', 'kubernetes/apps/t3'],
            cwd=Path(__file__).resolve().parents[1])
        documents = subprocess.check_output(
            ['yq', '-N', '-o=json', '-I=0', '.'], input=rendered)
        deployment = next(d for d in map(json.loads, documents.splitlines())
                          if d['kind'] == 'Deployment')
        pod = deployment['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertNotIn('fsGroup', pod['securityContext'])
        volumes = {v['name']: v for v in pod['volumes']}
        projection = volumes['coder-source']['projected']
        self.assertEqual(projection['defaultMode'], 0o400)
        self.assertEqual({s['secret']['name']: {i['key'] for i in s['secret']['items']}
                          for s in projection['sources']}, {
                              'github-connect': {'credential'},
                              'signing-connect': {'private-key', 'public-key'}})
        self.assertEqual(volumes['coder-credentials']['emptyDir']['medium'], 'Memory')
        for name, volume in volumes.items():
            self.assertNotIn('secret', volume)
            self.assertNotIn('hostPath', volume)
            if name != 'coder-source':
                self.assertNotIn('projected', volume)
        runtime = pod['containers'][0]
        mounts = {m['name']: m for m in runtime['volumeMounts']}
        self.assertNotIn('coder-source', mounts)
        self.assertNotIn('git-bootstrap', mounts)
        self.assertTrue(mounts['coder-credentials']['readOnly'])
        self.assertEqual(pod['securityContext']['runAsUser'], 10000)
        initializers = pod['initContainers']
        self.assertEqual(initializers[0]['securityContext']['runAsUser'], 0)
        self.assertFalse(initializers[0]['securityContext']['runAsNonRoot'])
        for container in [runtime, *initializers]:
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
            for env in container.get('env', []):
                self.assertNotIn('valueFrom', env)
        for container in initializers[1:]:
            self.assertNotIn('coder-source', {m['name'] for m in container['volumeMounts']})
        self.assertEqual({i['key'] for i in volumes['git-bootstrap']['configMap']['items']}, {
            'coder-configure-git.sh', 'coder-configure-git.py', 'coder-tools.sha256',
            'coder-gh.sh', 'coder-gitconfig'})


if __name__ == '__main__':
    unittest.main()
