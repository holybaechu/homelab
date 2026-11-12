"""Exercise the home ignore policy and small native-Git sync hook."""

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
        self.remote = self.base / "remote.git"
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        subprocess.run(["git", "init", "--bare", str(self.remote)], capture_output=True, check=True)
        self.git("remote", "add", "origin", str(self.remote))
        shutil.copyfile(SOURCE / "home.gitignore", self.root / ".gitignore")
        self.entry = self.base / "profile-sync"
        shutil.copyfile(SOURCE / "profile-sync.sh", self.entry)
        self.entry.chmod(0o555)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, check=True).stdout

    def write(self, path, content="profile state\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def sync(self, home=None, check=True):
        return subprocess.run([str(self.entry)], capture_output=True, check=check,
                              env={**os.environ, "HERMES_HOME": str(home or self.root)})

    def test_policy_tracks_profiles_without_credentials_or_runtime_data(self):
        included = {"SOUL.md", "config.yaml", "memories/USER.md", "cron/jobs.json",
                    "skills/custom/SKILL.md", "skills/.bundled_manifest", "skills/.hub/lock.json",
                    "hooks/custom.sh", "scripts/task.py", "profiles/coder/config.yaml",
                    "profiles/coder/memories/MEMORY.md", "workspace/.hermes.md",
                    "skills/writing-for-agents/SKILL.md", "skills/writing-for-agents/LICENSE",
                    "profiles/coder/skills/writing-for-agents/SKILL.md",
                    "skills/homelab-services/SKILL.md"}
        excluded = {".env", "auth.json", "google_token.json", "state.db", "sessions/chat.json",
                    "workspace/repo/file.py", "workspace/config.yaml", "cron/executions.db", "cron/ticker_heartbeat",
                    "skills/custom/.env", "skills/custom/token.json", "skills/custom/key.pem",
                    "skills/custom/node_modules/library.js", "skills/.usage.json", "skills/.locks/x.lock",
                    "plugins/langfuse/plugin.py",
                    "profiles/coder/auth.json", "profiles/coder/.env", "profiles/coder/workspace/file.py"}
        for path in included | excluded:
            self.write(path)
        self.sync()
        self.assertEqual(set(self.git("ls-files").decode().splitlines()), included | {".gitignore"})

    def test_coder_and_default_share_the_same_git_root(self):
        self.write("SOUL.md")
        self.sync()
        self.write("profiles/coder/memories/MEMORY.md")
        self.sync(self.root / "profiles/coder")
        self.sync()
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), b"2")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "refs/remotes/origin/main"))

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
