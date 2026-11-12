"""Keep personal state and unrelated hooks intact across the real startup path."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import hermes_yaml as yaml


SOURCE = Path(__file__).resolve().parents[2] / "kubernetes/apps/hermes"


class ProfileStartupTests(unittest.TestCase):
    def test_personal_state_and_other_hooks_survive_chezmoi_hook_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.yaml").write_text("{}\n")
            (home / "SOUL.md").write_text("Personal assistant instructions.\n")
            script = (
                "import sys,os; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from configure_profiles import reconcile; "
                "os.environ['HERMES_HOME']=sys.argv[2]; os.environ['HOME']=sys.argv[2]; "
                "reconcile(Path(sys.argv[2]),Path(sys.argv[1]))"
            )
            env = {**os.environ, "HOME": str(home), "HERMES_HOME": str(home),
                   "XDG_CONFIG_HOME": str(home / "xdg")}

            def configure():
                result = subprocess.run([sys.executable, "-c", script, str(SOURCE), str(home)],
                                        env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode())

            configure()
            coder = home / "profiles/coder/SOUL.md"
            self.assertEqual((home / "SOUL.md").read_text(), "Personal assistant instructions.\n")
            self.assertTrue(coder.is_file())
            coder.write_text("Personal coder instructions.\n")
            config = yaml.safe_load((home / "config.yaml").read_text())
            config["model"] = {"provider": "personal", "default": "personal-model"}
            config["agent"]["reasoning_effort"] = "low"
            config["mcp_servers"]["personal"] = {"command": "echo"}
            config["mcp_servers"]["t3code"]["enabled"] = True
            config["hooks"]["on_session_end"].append({"command": "echo retained"})
            config["hooks"]["on_session_end"].append({"command": "/opt/coder/bin/hermes-profile-sync"})
            config["hooks"]["on_session_start"].append({"command": "echo start retained"})
            (home / "config.yaml").write_text(yaml.safe_dump(config))
            subprocess.run(["git", "init", str(home)], capture_output=True, check=True)
            configure()
            self.assertEqual((home / "SOUL.md").read_text(), "Personal assistant instructions.\n")
            self.assertEqual(coder.read_text(), "Personal coder instructions.\n")
            config = yaml.safe_load((home / "config.yaml").read_text())
            self.assertEqual(config["model"]["default"], "personal-model")
            self.assertEqual(config["agent"]["reasoning_effort"], "low")
            self.assertIn("personal", config["mcp_servers"])
            self.assertFalse(config["mcp_servers"]["t3code"]["enabled"])
            self.assertEqual(len(config["hooks"]["on_session_end"]), 2)
            self.assertEqual(len(config["hooks"]["on_session_start"]), 2)
            self.assertIn({"command": "echo start retained"}, config["hooks"]["on_session_start"])
            self.assertEqual(config["hooks"]["on_session_start"][-1]["command"],
                             "/opt/coder/bin/hermes-profile-sync start")
            self.assertIn({"command": "echo retained"}, config["hooks"]["on_session_end"])
            self.assertFalse(config.get("hooks_auto_accept", False))
            (home / "SOUL.md").unlink()
            configure()
            self.assertFalse((home / "SOUL.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
