"""Exercise real chezmoi capture, remote updates, and recovery boundaries."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "kubernetes/apps/hermes"


class ProfileGitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "home"
        self.root.mkdir()
        self.env = {**os.environ, "HOME": str(self.root),
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            self.env.pop(name, None)
        self.source = self.root / ".local/share/chezmoi"
        self.source.mkdir(parents=True)
        config = self.root / ".config/chezmoi"
        config.mkdir(parents=True)
        shutil.copyfile(SOURCE / "chezmoi.toml", config / "chezmoi.toml")
        shutil.copyfile(SOURCE / "home.chezmoiignore", self.source / ".chezmoiignore")
        self.remote = self.base / "remote.git"
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "push.autoSetupRemote", "true")
        subprocess.run(["git", "init", "--bare", str(self.remote)], capture_output=True, check=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.entry = self.base / "profile-sync"
        shutil.copyfile(SOURCE / "profile-sync.sh", self.entry)
        self.entry.chmod(0o555)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.source), *args], capture_output=True, check=True, env=self.env).stdout

    def chezmoi(self, *args):
        return subprocess.run(["chezmoi", *args], capture_output=True, check=True,
                              env=self.env).stdout

    def write(self, path, content="profile state\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def sync(self, home=None, check=True, action="end"):
        env = {**self.env, "HERMES_HOME": str(home or self.root),
               "HOME": str(self.base / "shell-home"),
               **{name: str(self.base / "unused-xdg") for name in
                  ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME")}}
        result = subprocess.run([str(self.entry), action], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=20, env=env)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"", "native hooks expect JSON or empty stdout")
        return result

    def test_boot_permission_drift_keeps_config_private_at_both_boundaries(self):
        self.write("config.yaml", "fixture: before\n")
        target = self.root / "config.yaml"
        target.chmod(0o600)
        self.sync()
        source = self.source / "private_config.yaml"
        self.assertTrue(source.is_file())
        for action in ("start", "end"):
            with self.subTest(action=action):
                target.chmod(0o640)
                target.write_text(f"fixture: {action}\n")
                self.sync(action=action)
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(source.read_text(), target.read_text())
                self.assertFalse((self.source / "config.yaml").exists())
                saved = self.git("rev-parse", "HEAD")
                self.sync(action=action)
                self.assertEqual(self.git("rev-parse", "HEAD"), saved)
                self.assertEqual(self.git("rev-parse", "refs/remotes/origin/main"), saved)

    def test_other_private_files_fail_safely_if_their_permissions_drift(self):
        self.write("memories/USER.md")
        target = self.root / "memories/USER.md"
        target.chmod(0o600)
        self.sync()
        source = Path(self.chezmoi("source-path", str(target)).decode().strip())
        target.chmod(0o640)
        target.write_text("new live memory\n")
        self.assertNotEqual(self.sync(action="start", check=False).returncode, 0)
        self.assertTrue(source.name.startswith("private_"))
        self.assertEqual(source.read_text(), "profile state\n")
        self.assertEqual(target.read_text(), "new live memory\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_policy_tracks_assistant_without_retired_profiles_or_runtime_data(self):
        included = {"SOUL.md", "config.yaml", "memories/USER.md", "cron/jobs.json",
                    "skills/custom/SKILL.md", "skills/.bundled_manifest", "skills/.hub/lock.json",
                    "hooks/custom.sh", "scripts/task.py", "workspace/.hermes.md",
                    "skills/writing-for-agents/SKILL.md", "skills/writing-for-agents/LICENSE",
                    "skills/homelab-services/SKILL.md"}
        excluded = {".env", "auth.json", "google_token.json", "state.db", "sessions/chat.json",
                    "workspace/repo/file.py", "workspace/config.yaml", "cron/executions.db", "cron/ticker_heartbeat",
                    "skills/custom/.env", "skills/custom/token.json", "skills/custom/key.pem",
                    "skills/custom/node_modules/library.js", "skills/.usage.json", "skills/.locks/x.lock",
                    "plugins/langfuse/plugin.py",
                    "profiles/coder/config.yaml", "profiles/coder/memories/MEMORY.md",
                    "profiles/coder/skills/writing-for-agents/SKILL.md",
                    "profiles/coder/auth.json", "profiles/coder/.env", "profiles/coder/workspace/file.py"}
        for path in included | excluded:
            self.write(path)
        plugin = self.root / "profiles/coder/plugins/langfuse"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.symlink_to(self.root / "plugins/langfuse", target_is_directory=True)
        self.sync()
        managed = set(self.chezmoi("managed", "--include", "files,symlinks").decode().splitlines())
        self.assertEqual(managed, included)
        for path in excluded:
            self.assertTrue((self.root / path).is_file())
        self.assertTrue(plugin.is_symlink())
        self.sync(action="start")
        for path in excluded:
            self.assertTrue((self.root / path).is_file())

    def test_retiring_profile_sources_does_not_delete_or_recapture_live_data(self):
        policy = self.source / ".chezmoiignore"
        policy.write_text(policy.read_text() + "\n!profiles/\n!profiles/coder/\n!profiles/coder/SOUL.md\n")
        self.write("SOUL.md")
        self.write("profiles/coder/SOUL.md", "retained instructions\n")
        self.write("profiles/coder/state.db", "retained history\n")
        self.sync()
        target = self.root / "profiles/coder/SOUL.md"
        source = Path(self.chezmoi("source-path", str(target)).decode().strip())
        self.assertTrue(source.is_file())
        shutil.copyfile(SOURCE / "home.chezmoiignore", policy)
        source.unlink()
        self.chezmoi("apply", "--force")
        self.sync()
        self.assertEqual(target.read_text(), "retained instructions\n")
        self.assertEqual((target.parent / "state.db").read_text(), "retained history\n")
        managed = self.chezmoi("managed", "--include", "files,symlinks").decode().splitlines()
        self.assertFalse(any(path.startswith("profiles/") for path in managed))

    def test_new_skills_and_deleted_files_are_recorded(self):
        self.write("SOUL.md")
        self.write("skills/old/nested/note with space.md")
        self.sync()
        (self.root / "SOUL.md").unlink()
        shutil.rmtree(self.root / "skills/old")
        self.write("skills/new/SKILL.md", "new skill\n")
        self.sync(action="start")
        managed = self.chezmoi("managed", "--include", "files,symlinks").decode().splitlines()
        self.assertEqual(managed, ["skills/new/SKILL.md"])
        self.assertFalse((self.root / "skills/old").exists())
        saved = self.git("rev-parse", "HEAD")
        self.sync(action="start")
        self.assertEqual(self.git("rev-parse", "HEAD"), saved)

    def remote_edit(self, target, content):
        checkout = self.base / "checkout"
        subprocess.run(["git", "clone", "--branch", "main", str(self.remote), str(checkout)],
                       capture_output=True, check=True, env=self.env)
        relative = Path(self.chezmoi("source-path", str(self.root / target)).decode().strip()).relative_to(self.source)
        (checkout / relative).write_text(content)
        for args in (("config", "user.name", "Fixture"), ("config", "user.email", "fixture@example.invalid"),
                     ("config", "commit.gpgsign", "false"), ("add", "-A"),
                     ("commit", "-m", "remote edit"), ("push",)):
            subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, check=True, env=self.env)

    def test_session_start_applies_remote_edits_without_losing_local_changes(self):
        self.write("SOUL.md", "before\n")
        self.write("memories/USER.md", "before\n")
        self.write("skills/old/SKILL.md")
        self.sync()
        self.remote_edit("SOUL.md", "from GitHub\n")
        self.write("memories/USER.md", "from Hermes\n")
        self.write("skills/new/SKILL.md", "new live reference\n")
        link = self.root / "skills/new/reference.md"
        link.symlink_to("not-installed.md")
        self.sync(action="start")
        self.assertEqual((self.root / "SOUL.md").read_text(), "from GitHub\n")
        self.assertEqual((self.root / "memories/USER.md").read_text(), "from Hermes\n")
        self.assertEqual((self.root / "skills/new/SKILL.md").read_text(), "new live reference\n")
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.readlink(), Path("not-installed.md"))
        self.assertIn("skills/new/reference.md", self.chezmoi("managed").decode().splitlines())
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "refs/remotes/origin/main"))

    def test_conflict_stops_before_applying_and_blocks_further_capture(self):
        self.write("SOUL.md", "before\n")
        self.write("memories/USER.md")
        self.sync()
        self.remote_edit("SOUL.md", "from GitHub\n")
        self.write("SOUL.md", "from Hermes\n")
        self.assertNotEqual(self.sync(action="start", check=False).returncode, 0)
        self.assertEqual((self.root / "SOUL.md").read_text(), "from Hermes\n")
        self.assertTrue(self.git("ls-files", "--unmerged"))
        saved = self.git("rev-parse", "HEAD")
        unmerged = self.git("ls-files", "--stage")
        diff = self.git("diff", "HEAD")
        self.write("SOUL.md", "newer live content\n")
        self.write("skills/new/SKILL.md")
        (self.root / "memories/USER.md").unlink()
        for action in ("start", "end"):
            self.assertNotEqual(self.sync(action=action, check=False).returncode, 0)
            self.assertEqual(self.git("rev-parse", "HEAD"), saved)
            self.assertEqual(self.git("ls-files", "--stage"), unmerged)
            self.assertEqual(self.git("diff", "HEAD"), diff)
            self.assertEqual((self.root / "SOUL.md").read_text(), "newer live content\n")

    def test_capture_keeps_native_git_commit_signing(self):
        key = self.base / "signing-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       capture_output=True, check=True)
        signers = self.base / "allowed-signers"
        signers.write_text('fixture@example.invalid namespaces="git" ' +
                           key.with_suffix(".pub").read_text())
        for name, value in {"gpg.format": "ssh", "user.signingkey": str(key),
                            "gpg.ssh.allowedSignersFile": str(signers),
                            "commit.gpgsign": "true"}.items():
            self.git("config", name, value)
        self.write("memories/USER.md")
        for action in ("end", "start"):
            self.write("SOUL.md", f"signed {action}\n")
            self.sync(action=action)
            self.git("verify-commit", "HEAD")
            if action == "end":
                self.remote_edit("memories/USER.md", "remote memory\n")

    def test_failed_push_retries_without_losing_or_duplicating_the_commit(self):
        self.write("SOUL.md")
        offline = self.base / "offline.git"
        self.remote.rename(offline)
        self.assertNotEqual(self.sync(check=False).returncode, 0)
        saved = self.git("rev-parse", "HEAD")
        offline.rename(self.remote)
        self.sync()
        self.assertEqual(self.git("rev-parse", "HEAD"), saved)
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/main"), saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
