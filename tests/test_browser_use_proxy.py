import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest


INITIALIZE = pathlib.Path(__file__).resolve().parents[1] / 'kubernetes/apps/browser-use-proxy/runtime/initialize.py'
spec = importlib.util.spec_from_file_location('proxy_initialize', INITIALIZE)
assert spec is not None and spec.loader is not None
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


class ProxyVerifierTests(unittest.TestCase):
    def initialize(self, data, raw=False):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / 'source.json'
            target = pathlib.Path(directory) / 'passwd'
            source.write_text(data if raw else json.dumps(data))
            result = subprocess.run([sys.executable, '-I', '-B', str(INITIALIZE), str(source), str(target)], capture_output=True)
            return result, target.read_text() if target.exists() else None, stat.S_IMODE(target.stat().st_mode) if target.exists() else None

    def source(self):
        return dict(host='proxy.hchu.me', port=443, protocol='https', username='fixture-user', password='AZaz09_-' * 8)

    def test_private_verifier_has_no_plaintext_or_output(self):
        source = self.source()
        result, verifier, mode = self.initialize(source)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))
        assert verifier is not None
        self.assertRegex(verifier, r'\A"fixture-user:CR:\$3\$[0-9a-f]{32}\$[./0-9A-Za-z]{22}"\n\Z')
        self.assertNotEqual(verifier, self.initialize(source)[1])
        self.assertNotIn(source['password'], verifier)
        self.assertEqual(mode, 0o640)
        self.assertEqual(len(verifier.splitlines()), 1)

    def test_rejects_invalid_contract_without_output_or_verifier(self):
        invalid = [None, [], 'not-an-object', {**self.source(), 'extra': 'unreviewed'}]
        for key, value in [('host', 'other.example'), ('port', '443'), ('port', 443.0), ('port', True), ('port', 80), ('protocol', 'http'), ('username', 'bad:name'), ('username', 'user\n'), ('username', '$(touch injected)'), ('username', '"user'), ('username', None), ('password', ''), ('password', 'x' * 63), ('password', 'x' * 65), ('password', 'x' * 63 + '\n'), ('password', 'x' * 63 + '\x00'), ('password', 'x' * 63 + '$'), ('password', ['array'])]:
            invalid.append({**self.source(), key: value})
        for data in invalid:
            with self.subTest(case=invalid.index(data)):
                result, verifier, mode = self.initialize(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((result.stdout, result.stderr), (b'', b''))
                self.assertIsNone(verifier)

    def test_rejects_malformed_or_multiple_json_documents(self):
        valid = json.dumps(self.source())
        for raw in ('', '{', valid + '\n' + valid, '{}\n' + valid, valid.replace('"port": 443', '"port": 80, "port": 443'), ' ' * 4097 + valid):
            result, verifier, _ = self.initialize(raw, raw=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.stdout, result.stderr), (b'', b''))
            self.assertIsNone(verifier)


    def test_official_3proxy_crypt_synthetic_vectors(self):
        # Cross-checked against the attested official 1.0.3 DEB's 3proxy_crypt.
        for password, salt, expected in [
            ('A' * 64, 'fixture-salt', 'CR:$3$fixture-salt$wqUIb9FHiswzEf7G9wmVc0'),
            ('AZaz09_-' * 8, '0123456789abcdef' * 2, 'CR:$3$0123456789abcdef0123456789abcdef$Z1muF828FxpCeb87mC3Sh1'),
            ('fixture-only-not-a-secret', 'salt', 'CR:$3$salt$Vkm8KTfytwtfDqDefWmQr.'),
        ]:
            self.assertEqual(initializer.verifier(password, salt), expected)

    def test_existing_target_and_symlink_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / 'source'
            source.write_text(json.dumps(self.source()))
            original = pathlib.Path(directory) / 'original'
            original.write_text('preserve')
            link = pathlib.Path(directory) / 'link'
            link.symlink_to(original)
            for target in (original, link):
                with self.assertRaises(OSError):
                    initializer.initialize(source, target)
            self.assertEqual(original.read_text(), 'preserve')

    @unittest.skipUnless(pathlib.Path('/proc/self/cmdline').exists(), 'Linux process inspection')
    def test_source_bytes_are_not_in_process_arguments_or_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / 'source'
            target = pathlib.Path(directory) / 'target'
            os.mkfifo(source)
            with subprocess.Popen([sys.executable, '-I', '-B', str(INITIALIZE), str(source), str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                with source.open('w') as stream:
                    proc = pathlib.Path('/proc') / str(child.pid)
                    for name in ('cmdline', 'environ'):
                        self.assertNotIn(('AZaz09_-' * 8).encode(), (proc / name).read_bytes())
                    stream.write(json.dumps(self.source()))
                self.assertEqual(child.communicate(timeout=5), (b'', b''))
                self.assertEqual(child.returncode, 0)


if __name__ == '__main__':
    unittest.main()
