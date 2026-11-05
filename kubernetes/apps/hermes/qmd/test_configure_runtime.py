import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("configure_runtime", Path(__file__).with_name("configure_runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class ConfigureRuntimeTests(unittest.TestCase):
    def test_changes_only_the_supported_default_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "server.js"
            prefix, suffix = "// unrelated source\n", "\n// keep explicit rerank support\n"
            path.write_bytes((prefix + runtime.UPSTREAM + suffix).encode())
            runtime.configure(path)
            once = path.read_bytes()
            self.assertEqual(once.decode(), prefix + runtime.HOMELAB + suffix)
            runtime.configure(path)
            self.assertEqual(path.read_bytes(), once)

    def test_unknown_runtime_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "server.js"
            original = b"// different upstream implementation\n"
            path.write_bytes(original)
            with self.assertRaises(RuntimeError):
                runtime.configure(path)
            self.assertEqual(path.read_bytes(), original)
