"""Run with the pinned Hermes image's real Git, hook API and credential patterns."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT / "kubernetes/apps/hermes"
spec = importlib.util.spec_from_file_location("instruction_history", SOURCE / "instruction_history.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "home"
        self.root.mkdir()
        (self.root / "profiles/coder").mkdir(parents=True)
        self.stock = self.base / "stock"
        self.stock.mkdir()
        self.remote = self.base / "remote.git"
        self.history = module.History(self.root, stock=(self.stock,), remote=str(self.remote))
        self.history.initialize()
        for name, value in [("commit.gpgsign", "false"), ("user.name", "Fixture"),
                            ("user.email", "fixture@example.invalid")]:
            self.history.git("config", name, value)
        self.write("SOUL.md", "Initial personality.\n")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def head(self):
        return self.history.git("rev-parse", "HEAD").stdout.decode().strip()

    def tracked(self):
        return set(self.history.git("ls-tree", "-r", "--name-only", "HEAD").stdout.decode().splitlines())

    def test_scope_stock_and_exclusions(self):
        stock = self.stock / "builtin/SKILL.md"
        stock.parent.mkdir()
        stock.write_text("Bundled instructions.\n")
        self.write("skills/builtin/SKILL.md", stock.read_text())
        self.write("skills/personal/SKILL.md", "A learned procedure.\n")
        self.write("skills/personal/scripts/run.py", "print('hello')\n")
        self.write("config.yaml", "model:\n  default: personal-model\n")
        self.write("memories/USER.md", "Personal preferences.\n")
        self.write("cron/jobs.json", '{"jobs": []}\n')
        self.write("hooks/personal.sh", "echo hello\n")
        self.write("skills/.bundled_manifest", "{}\n")
        self.write("skills/.hub/lock.json", "{}\n")
        self.write("profiles/coder/profile.yaml", "display_name: coder\n")
        for path in ["auth.json", "sessions/chat.json", "cron/executions.db", "cron/ticker_heartbeat",
                     "skills/personal/.env", "skills/personal/token.json",
                     "skills/personal/__pycache__/cache.pyc", "skills/personal/task.lock",
                     "skills/writing-for-agents/SKILL.md", "skills/homelab-services/SKILL.md"]:
            self.write(path, "Not an editable personal instruction.\n")
        self.history.save(push=False)
        self.assertEqual(self.tracked(), {"SOUL.md", "skills/builtin/SKILL.md", "skills/personal/SKILL.md",
                                         "skills/personal/scripts/run.py", "config.yaml", "memories/USER.md",
                                         "cron/jobs.json", "hooks/personal.sh", "profiles/coder/profile.yaml",
                                         "skills/.bundled_manifest", "skills/.hub/lock.json"})
        self.write("skills/builtin/SKILL.md", "My modified procedure.\n")
        self.history.save(push=False)
        self.assertIn("skills/builtin/SKILL.md", self.tracked())

    def test_restore_records_a_new_commit_and_keeps_later_history(self):
        self.history.save(push=False)
        first = self.head()
        self.write("SOUL.md", "Updated personality.\n")
        self.history.save(push=False)
        second = self.head()
        self.history.save(push=False)
        self.assertEqual(second, self.head())
        self.history.git("restore", f"--source={first}", "--worktree", "--", "SOUL.md")
        self.history.save(push=False)
        self.assertEqual((self.root / "SOUL.md").read_text(), "Initial personality.\n")
        self.assertEqual(self.history.git("rev-parse", "HEAD^").stdout.decode().strip(), second)

    def test_deleted_skill_is_recorded_and_recoverable(self):
        file = self.write("skills/personal/SKILL.md", "Keep this procedure.\n")
        self.history.save(push=False)
        first = self.head()
        file.unlink()
        self.history.save(push=False)
        self.assertNotIn("skills/personal/SKILL.md", self.tracked())
        self.history.git("restore", f"--source={first}", "--worktree", "--", "skills/personal/SKILL.md")
        self.assertEqual(file.read_text(), "Keep this procedure.\n")

    def test_embedded_credential_never_enters_index(self):
        self.history.save(push=False)
        first = self.head()
        self.write("skills/personal/SKILL.md", 'api_key = "ghp_' + 'A' * 36 + '"\n')
        with self.assertRaisesRegex(RuntimeError, "credential scan blocked"):
            self.history.save(push=False)
        self.assertEqual(first, self.head())
        self.assertEqual(self.history.git("diff", "--cached", "--name-only").stdout, b"")

    def test_unchanged_public_examples_are_allowed_but_new_credentials_are_not(self):
        stock = self.stock / "builtin/SKILL.md"
        stock.parent.mkdir()
        example = 'api_key = "ghp_' + 'A' * 36 + '"\n'
        stock.write_text(example + "Original procedure.\n")
        self.write("skills/builtin/SKILL.md", example + "Customized procedure.\n")
        self.history.save(push=False)
        self.write("skills/builtin/SKILL.md", 'api_key = "ghp_' + 'B' * 36 + '"\n')
        with self.assertRaisesRegex(RuntimeError, "credential scan blocked"):
            self.history.save(push=False)

    def test_known_runtime_secret_is_blocked_without_pattern_match(self):
        value = "fixture_runtime_value_928374650192837465"
        self.write("skills/personal/SKILL.md", "Remember " + value)
        with patch.dict(os.environ, {"CUSTOM_SERVICE_PASSWORD": value}):
            with self.assertRaisesRegex(RuntimeError, "runtime credential found"):
                self.history.save(push=False)

    def test_symlink_cannot_export_an_outside_file(self):
        outside = self.base / "outside.md"
        outside.write_text("Keep outside the repository.\n")
        link = self.root / "skills/personal/SKILL.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "symlink refused"):
            self.history.save(push=False)

    def test_symlinked_profile_directory_is_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("Keep this outside.\n")
        (self.root / "skills").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "directory symlink refused"):
            self.history.save(push=False)

    def test_restore_of_old_config_keeps_automatic_history_enabled(self):
        subprocess.run(["git", "init", "--bare", str(self.remote)], capture_output=True, check=True)
        for home in [self.root, self.root / "profiles/coder"]:
            (home / "config.yaml").write_text("model:\n  default: original\n")
        self.history.save(push=False)
        self.write("config.yaml", "model:\n  default: updated\n")
        args = ["hermes-instructions", "restore", "HEAD", "--", "config.yaml"]
        with patch.object(module, "ROOT", self.root), patch.object(module, "REMOTE", str(self.remote)), \
                patch.object(sys, "argv", args):
            module.main()
        import hermes_yaml as yaml
        config = yaml.safe_load((self.root / "config.yaml").read_text())
        self.assertEqual(config["model"]["default"], "original")
        self.assertEqual(config["hooks"]["on_session_end"], [{"command": module.HOOK, "timeout": 30}])
        self.assertIn(b"updated", self.history.git("show", "HEAD^:config.yaml").stdout)

    def test_push_failure_keeps_commit_and_next_save_publishes_it(self):
        with self.assertRaisesRegex(RuntimeError, "git push failed"):
            self.history.save()
        saved = self.head()
        subprocess.run(["git", "init", "--bare", str(self.remote)], capture_output=True, check=True)
        self.history.save()
        self.assertEqual(saved, self.head())
        result = subprocess.run(["git", f"--git-dir={self.remote}", "rev-parse", "main"],
                                capture_output=True, check=True)
        self.assertEqual(result.stdout.decode().strip(), saved)

    def test_profile_hooks_preserve_existing_config_and_approve_only_one_command(self):
        for home in [self.root, self.root / "profiles/coder"]:
            home.mkdir(parents=True, exist_ok=True)
            (home / "config.yaml").write_text(
                'hooks:\n  on_session_end:\n    - command: echo existing\n'
                'hooks_auto_accept: false\nmodel:\n  default: retained\n')
        module.configure_hooks(self.root)
        module.configure_hooks(self.root)
        import hermes_yaml as yaml
        for home in [self.root, self.root / "profiles/coder"]:
            config = yaml.safe_load((home / "config.yaml").read_text())
            self.assertFalse(config["hooks_auto_accept"])
            self.assertEqual(config["model"]["default"], "retained")
            self.assertEqual(len(config["hooks"]["on_session_end"]), 2)
            approvals = json.loads((home / "shell-hooks-allowlist.json").read_text())["approvals"]
            self.assertEqual([(entry["event"], entry["command"]) for entry in approvals],
                             [("on_session_end", module.HOOK)])

    def test_startup_seeds_new_souls_and_preserves_edits_and_recorded_deletion(self):
        fresh = self.base / "fresh"
        fresh.mkdir()
        (fresh / "config.yaml").write_text("{}\n")
        script = (
            "import sys,os; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from configure_profiles import reconcile; "
            "os.environ['HERMES_HOME']=sys.argv[2]; os.environ['HOME']=sys.argv[2]; "
            "reconcile(Path(sys.argv[2]),Path(sys.argv[1]))"
        )
        env = {**os.environ, "HOME": str(fresh), "HERMES_HOME": str(fresh),
               "XDG_CONFIG_HOME": str(fresh / "xdg")}
        def configure():
            result = subprocess.run([sys.executable, "-c", script, str(SOURCE), str(fresh)],
                                    env=env, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        configure()
        coder = fresh / "profiles/coder/SOUL.md"
        self.assertEqual((fresh / "SOUL.md").read_text(), (SOURCE / "assistant-soul.md").read_text())
        self.assertEqual(coder.read_text(), (SOURCE / "coder-soul.md").read_text())
        (fresh / "SOUL.md").write_text("Personal assistant instructions.\n")
        coder.write_text("Personal coder instructions.\n")
        import hermes_yaml as yaml
        config = yaml.safe_load((fresh / "config.yaml").read_text())
        config["model"] = {"provider": "personal", "default": "personal-model"}
        config["agent"]["reasoning_effort"] = "low"
        config["mcp_servers"]["personal"] = {"command": "echo"}
        config["mcp_servers"]["t3code"]["enabled"] = True
        (fresh / "config.yaml").write_text(yaml.safe_dump(config))
        (fresh / ".instruction-history.git").mkdir()
        configure()
        self.assertEqual((fresh / "SOUL.md").read_text(), "Personal assistant instructions.\n")
        self.assertEqual(coder.read_text(), "Personal coder instructions.\n")
        config = yaml.safe_load((fresh / "config.yaml").read_text())
        self.assertEqual(config["model"]["default"], "personal-model")
        self.assertEqual(config["agent"]["reasoning_effort"], "low")
        self.assertIn("personal", config["mcp_servers"])
        self.assertFalse(config["mcp_servers"]["t3code"]["enabled"])
        (fresh / "SOUL.md").unlink()
        configure()
        self.assertFalse((fresh / "SOUL.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
