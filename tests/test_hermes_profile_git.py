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
        return subprocess.run(["git", "-C", str(self.source), *args], capture_output=True, check=True).stdout

    def chezmoi(self, *args):
        return subprocess.run(["chezmoi", *args], capture_output=True, check=True,
                              env={**os.environ, "HOME": str(self.root)}).stdout

    def write(self, path, content="profile state\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def sync(self, home=None, check=True, action="end"):
        result = subprocess.run([str(self.entry), action], capture_output=True,
                                env={**os.environ, "HERMES_HOME": str(home or self.root)})
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

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
        self.write("skills/old/SKILL.md")
        self.sync()
        (self.root / "SOUL.md").unlink()
        shutil.rmtree(self.root / "skills/old")
        self.write("skills/new/SKILL.md", "new skill\n")
        self.sync()
        managed = self.chezmoi("managed", "--include", "files,symlinks").decode().splitlines()
        self.assertEqual(managed, ["skills/new/SKILL.md"])

    def remote_edit(self, target, content):
        checkout = self.base / "checkout"
        subprocess.run(["git", "clone", "--branch", "main", str(self.remote), str(checkout)],
                       capture_output=True, check=True)
        relative = Path(self.chezmoi("source-path", str(self.root / target)).decode().strip()).relative_to(self.source)
        (checkout / relative).write_text(content)
        for args in (("config", "user.name", "Fixture"), ("config", "user.email", "fixture@example.invalid"),
                     ("config", "commit.gpgsign", "false"), ("add", "-A"),
                     ("commit", "-m", "remote edit"), ("push",)):
            subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, check=True)

    def test_session_start_applies_remote_edits_without_losing_local_changes(self):
        self.write("SOUL.md", "before\n")
        self.write("memories/USER.md", "before\n")
        self.sync()
        self.remote_edit("SOUL.md", "from GitHub\n")
        self.write("memories/USER.md", "from Hermes\n")
        self.sync(action="start")
        self.assertEqual((self.root / "SOUL.md").read_text(), "from GitHub\n")
        self.assertEqual((self.root / "memories/USER.md").read_text(), "from Hermes\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "refs/remotes/origin/main"))

    def test_conflict_stops_before_applying_and_blocks_further_capture(self):
        self.write("SOUL.md", "before\n")
        self.sync()
        self.remote_edit("SOUL.md", "from GitHub\n")
        self.write("SOUL.md", "from Hermes\n")
        self.assertNotEqual(self.sync(action="start", check=False).returncode, 0)
        self.assertEqual((self.root / "SOUL.md").read_text(), "from Hermes\n")
        self.assertTrue(self.git("ls-files", "--unmerged"))
        self.assertNotEqual(self.sync(check=False).returncode, 0)
        self.assertEqual((self.root / "SOUL.md").read_text(), "from Hermes\n")

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
