"""Exercise the administrator script in real PowerShell with a synthetic native kubectl."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/bootstrap-hermes-browser-vault.ps1"
PWSH = os.environ.get("PWSH_BINARY") or shutil.which("pwsh")
TOKEN = "synthetic-bootstrap-token"

FAKE = '''import json, os, pathlib, sys
root = pathlib.Path(os.environ['BOOTSTRAP_FIXTURE'])
args = sys.argv[1:]
with (root/'argv').open('a') as file: file.write(json.dumps(args)+'\\n')
scenario = (root/'scenario').read_text()
state = root/'state'
if 'get' in args:
    if scenario == 'get_failure':
        print('synthetic-bootstrap-token', file=sys.stderr); sys.exit(1)
    if state.exists():
        obj = json.loads(state.read_text())
        if scenario == 'readback_mismatch': obj['data']['connect-token'] = 'wrong-synthetic'
        print(json.dumps(obj))
elif 'apply' in args:
    obj = json.load(sys.stdin)
    with (root/'writes').open('a') as file: file.write('write\\n')
    state.write_text(json.dumps(obj))
    if scenario == 'apply_unknown':
        print('synthetic-bootstrap-token', file=sys.stderr); sys.exit(1)
    print('secret updated')
else:
    sys.exit(2)
'''


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PWSH, "Real PowerShell required")
        self.temp = tempfile.TemporaryDirectory(prefix="bootstrap-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        kubectl = self.root / "kubectl"
        kubectl.write_text("#!" + sys.executable + "\n" + FAKE)
        kubectl.chmod(0o755)
        (self.root / "scenario").write_text("normal")
        (self.root / "token").write_text(TOKEN)
        (self.root / "token").chmod(0o600)

    def run_script(self):
        result = subprocess.run([PWSH, "-NoProfile", "-File", str(SCRIPT), "-TokenFile", str(self.root / "token")],
                                env={**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                                     "BOOTSTRAP_FIXTURE": str(self.root)}, capture_output=True, text=True, timeout=60)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        if (self.root / "argv").exists():
            self.assertNotIn(TOKEN, (self.root / "argv").read_text())
        return result

    def test_create_verify_and_idempotent_resume(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exact read-back verified", result.stdout)
        state = json.loads((self.root / "state").read_text())
        self.assertEqual(set(state["data"]), {"connect-token"})
        self.assertEqual(state["metadata"], {"name": "hermes-browser-vault", "namespace": "homelab"})
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.root / "writes").read_text(), "write\n")

    def test_unknown_apply_outcome_is_not_retried_and_resume_reads_actual_state(self):
        (self.root / "scenario").write_text("apply_unknown")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("apply_unknown_outcome_on_failure", result.stderr)
        self.assertEqual((self.root / "writes").read_text(), "write\n")
        (self.root / "scenario").write_text("normal")
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.root / "writes").read_text(), "write\n")

    def test_denied_read_and_invalid_input_make_no_writes(self):
        (self.root / "scenario").write_text("get_failure")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("read_existing", result.stderr)
        self.assertFalse((self.root / "writes").exists())
        (self.root / "token").write_text(TOKEN + "\n")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("read_token", result.stderr)
        self.assertFalse((self.root / "writes").exists())

    def test_collision_and_failed_readback_are_not_success(self):
        obj = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
               "metadata": {"name": "hermes-browser-vault", "namespace": "homelab", "ownerReferences": [{"uid": "synthetic"}]},
               "data": {"connect-token": "synthetic"}}
        (self.root / "state").write_text(json.dumps(obj))
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse((self.root / "writes").exists())
        (self.root / "state").unlink()
        (self.root / "scenario").write_text("readback_mismatch")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify_readback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
