import json
import pathlib
import stat
import subprocess
import tempfile
import unittest


INITIALIZE = pathlib.Path(__file__).resolve().parents[1] / 'kubernetes/apps/browser-use-proxy/runtime/initialize.sh'


class ProxyVerifierTests(unittest.TestCase):
    def initialize(self, data, raw=False):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / 'source.json'
            target = pathlib.Path(directory) / 'passwd'
            source.write_text(data if raw else json.dumps(data))
            result = subprocess.run(['sh', str(INITIALIZE), str(source), str(target)], capture_output=True)
            return result, target.read_text() if target.exists() else None, stat.S_IMODE(target.stat().st_mode) if target.exists() else None

    def source(self):
        return dict(host='proxy.hchu.me', port=443, protocol='https', username='fixture-user', password='fixture-only-password-123456789')

    def test_private_verifier_has_no_plaintext_or_output(self):
        source = self.source()
        result, verifier, mode = self.initialize(source)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))
        assert verifier is not None
        self.assertTrue(verifier.startswith('fixture-user:$6$'))
        self.assertNotIn(source['password'], verifier)
        self.assertEqual(mode, 0o640)
        self.assertEqual(len(verifier.splitlines()), 1)

    def test_rejects_invalid_contract_without_output_or_verifier(self):
        invalid = [None, [], 'not-an-object', {**self.source(), 'extra': 'unreviewed'}]
        for key, value in [('host', 'other.example'), ('port', '443'), ('port', 80), ('protocol', 'http'), ('username', 'bad:name'), ('username', 'user\n'), ('username', '$(touch injected)'), ('username', None), ('password', ''), ('password', 'x' * 129), ('password', 'x' * 24 + '\n'), ('password', 'x' * 24 + '\x00'), ('password', ['array'])]:
            invalid.append({**self.source(), key: value})
        for data in invalid:
            with self.subTest(case=invalid.index(data)):
                result, verifier, mode = self.initialize(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((result.stdout, result.stderr), (b'', b''))
                self.assertIsNone(verifier)

    def test_rejects_malformed_or_multiple_json_documents(self):
        valid = json.dumps(self.source())
        for raw in ('', '{', valid + '\n' + valid, '{}\n' + valid):
            result, verifier, _ = self.initialize(raw, raw=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.stdout, result.stderr), (b'', b''))
            self.assertIsNone(verifier)


if __name__ == '__main__':
    unittest.main()
