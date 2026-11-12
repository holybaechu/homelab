  #!/opt/hermes/.venv/bin/python
"""Record persistent Hermes profile changes with Git; exclude credentials and runtime data."""

import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path("/opt/data")
REMOTE = "https://github.com/holybaechu/hermes-instructions.git"
HOOK = "/opt/coder/bin/hermes-instructions save"
STOCK = (Path("/opt/hermes/skills"), Path("/opt/hermes/optional-skills"))
SKILL_ROOTS = (Path("skills"), Path("profiles/coder/skills"))
MOUNTED = {"writing-for-agents", "homelab-services", "instruction-history"}
SKILL_METADATA = {"skills/.bundled_manifest", "skills/.curator_state", "skills/.hub/lock.json"}
MANAGED_PLUGINS = {"advanced-delegation", "langfuse"}
SKIP_DIRS = {"__pycache__", "node_modules", "venv"}
SKIP_NAMES = {"auth.json", "credentials.json", "token.json", "tokens.json"}
SKIP_SUFFIXES = {".lock", ".jsonl", ".pyc", ".pem", ".key", ".p12", ".db", ".sqlite"}


def allowed(path: Path) -> bool:
    from hermes_cli.profiles import _DEFAULT_EXPORT_INCLUDE_ROOT, profile_path_is_private

    roots = (_DEFAULT_EXPORT_INCLUDE_ROOT - {"sessions"}) | {
        "hooks", "skins", "profile.yaml", "ui-preferences.json",
    }
    relative = path
    if path.is_relative_to("profiles/coder"):
        relative = path.relative_to("profiles/coder")
    parts = relative.parts
    if not parts or parts[0] not in roots or profile_path_is_private(parts):
        return False
    if relative.as_posix() in SKILL_METADATA:
        return True
    if parts[0] == "cron" and parts != ("cron", "jobs.json"):
        return False
    if len(parts) > 1 and (parts[0] == "skills" and parts[1] in MOUNTED
                          or parts[0] == "plugins" and parts[1] in MANAGED_PLUGINS):
        return False
    return not any((part.startswith(".") and part != ".cursorrules") or part in SKIP_DIRS
                   for part in parts) and path.name not in SKIP_NAMES and path.suffix not in SKIP_SUFFIXES


class History:
    def __init__(self, root: Path, *, stock=STOCK, remote=REMOTE):
        self.root = root
        self.directory = root / ".instruction-history.git"
        self.stock = stock
        self.remote = remote

    def git(self, *args, data=None, check=True):
        result = subprocess.run(
            ["git", f"--git-dir={self.directory}", f"--work-tree={self.root}", *args],
            cwd=self.root, input=data, capture_output=True, timeout=20,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if check and result.returncode:
            raise RuntimeError(f"git {args[0]} failed; local files are preserved")
        return result

    def initialize(self):
        if self.directory.exists():
            origin = self.git("remote", "get-url", "origin").stdout.decode().strip()
            if origin != self.remote:
                raise RuntimeError("instruction history has an unexpected remote")
            return
        result = subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(self.directory)],
            capture_output=True, timeout=20,
        )
        if result.returncode:
            raise RuntimeError("cannot initialize instruction history")
        self.git("config", "core.bare", "false")
        self.git("config", "core.worktree", str(self.root))
        self.git("config", "status.showUntrackedFiles", "no")
        (self.directory / "info/exclude").write_text("*\n")
        self.git("remote", "add", "origin", self.remote)

    def original(self, path: Path) -> bytes | None:
        for base in SKILL_ROOTS:
            if path.is_relative_to(base):
                relative = path.relative_to(base)
                for source in self.stock:
                    candidate = source / relative
                    if candidate.is_file():
                        return candidate.read_bytes()
        relative = path.relative_to("profiles/coder") if path.is_relative_to("profiles/coder") else path
        candidate = Path("/opt/hermes") / relative
        if candidate.is_file():
            return candidate.read_bytes()
        return None

    def candidates(self):
        paths = []
        for home in (self.root, self.root / "profiles/coder"):
            for base in home.iterdir():
                if base.is_file():
                    paths.append(base.relative_to(self.root))
                elif base.is_dir() and allowed((base / "SKILL.md").relative_to(self.root)):
                    if base.is_symlink():
                        raise RuntimeError(f"profile directory symlink refused: {str(base.relative_to(self.root))!r}")
                    for directory, dirs, files in os.walk(base, followlinks=False):
                        dirs[:] = [d for d in dirs if allowed((Path(directory) / d / "SKILL.md").relative_to(self.root))]
                        paths.extend((Path(directory) / name).relative_to(self.root) for name in files)
            cron = home / "cron/jobs.json"
            if cron.is_file():
                paths.append(cron.relative_to(self.root))
            paths.extend((home / name).relative_to(self.root) for name in SKILL_METADATA
                         if (home / name).is_file())
        selected = {}
        for relative in sorted(set(paths)):
            if not allowed(relative):
                continue
            path = self.root / relative
            if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
                raise RuntimeError(f"instruction symlink refused: {str(relative)!r}")
            if not path.is_file():
                continue
            content = path.read_bytes()
            selected[str(relative)] = (content, "100755" if path.stat().st_mode & 0o111 else "100644")
        return selected

    def check_credentials(self, path: Path, content: bytes):
        from tools.skills_guard import _COMPILED_THREAT_PATTERNS

        source = self.original(path) or b""
        examples = set(source.decode("utf-8-sig", errors="replace").splitlines())
        for line in content.decode("utf-8-sig", errors="replace").splitlines():
            if line in examples:
                continue
            if any(category == "credential_exposure" and pattern.search(line)
                   for pattern, _, _, category, _ in _COMPILED_THREAT_PATTERNS):
                raise RuntimeError(f"credential scan blocked: {str(path)!r}")
        for name, value in os.environ.items():
            if (re.search(r"TOKEN|KEY|SECRET|PASSWORD", name)
                    and not name.endswith(("_FILE", "_PATH", "_REF"))
                    and len(value) >= 16 and value.encode() in content):
                raise RuntimeError(f"runtime credential found: {str(path)!r}")

    def save(self, *, push=True):
        with (self.root / ".instruction-history.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.initialize()
            tracked = {}
            for entry in self.git("ls-files", "--stage", "-z").stdout.split(b"\0"):
                if entry:
                    fields, path = entry.decode().split("\t", 1)
                    if not allowed(Path(path)):
                        raise RuntimeError("instruction index contains an out-of-scope path")
                    tracked[path] = tuple(fields.split()[:2])
            selected = self.candidates()
            changes = {}
            for path, (content, mode) in selected.items():
                oid = self.git("hash-object", "--stdin", data=content).stdout.decode().strip()
                if tracked.get(path) != (mode, oid):
                    self.check_credentials(Path(path), content)
                    changes[path] = (content, mode)
            for path, (content, mode) in changes.items():
                oid = self.git("hash-object", "-w", "--stdin", data=content).stdout.decode().strip()
                self.git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
            for path in tracked.keys() - selected.keys():
                self.git("update-index", "--force-remove", "--", path)
            diff = self.git("diff", "--cached", "--quiet", check=False)
            if diff.returncode == 1:
                self.git("commit", "--quiet", "-m", "chore(hermes): record profile changes")
            elif diff.returncode:
                raise RuntimeError("cannot compare instruction index")
            if push:
                head = self.git("rev-parse", "HEAD").stdout
                published = self.git("rev-parse", "--verify", "refs/remotes/origin/main", check=False)
                if published.returncode or published.stdout != head:
                    self.git("push", "--quiet", "origin", "HEAD:main")


def configure_hooks(root: Path):
    import hermes_yaml as yaml
    from agent.shell_hooks import _record_approval
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from utils import atomic_yaml_write

    for home in (root, root / "profiles/coder"):
        path = home / "config.yaml"
        config = yaml.safe_load(path.read_text()) or {}
        entries = config.setdefault("hooks", {}).setdefault("on_session_end", [])
        entries[:] = [entry for entry in entries if entry.get("command") != HOOK]
        entries.append({"command": HOOK, "timeout": 30})
        atomic_yaml_write(path, config, sort_keys=False)
        token = set_hermes_home_override(home)
        try:
            _record_approval("on_session_end", HOOK)
        finally:
            reset_hermes_home_override(token)


def main():
    os.umask(0o077)
    history = History(ROOT, remote=REMOTE)
    args = sys.argv[1:] or ["save"]
    if args == ["save"]:
        history.save()
    elif args[0] == "restore":
        if len(args) < 4 or args[2] != "--":
            raise RuntimeError("use restore <commit> -- <path> [<path> ...]")
        target = history.git("rev-parse", "--verify", "--end-of-options", f"{args[1]}^{{commit}}")
        commit = target.stdout.decode().strip()
        history.save(push=False)
        history.git("restore", f"--source={commit}", "--worktree", "--", *args[3:])
        try:
            configure_hooks(ROOT)
        except Exception:
            raise RuntimeError("restored files are preserved; cannot re-register history hooks") from None
        history.save()
        print("Restored and recorded requested profile paths.")
    elif args[0] in {"diff", "show"}:
        from agent.redact import redact_sensitive_text

        result = history.git(*args, check=False)
        sys.stdout.write(redact_sensitive_text(result.stdout.decode(errors="replace"), force=True))
        sys.stderr.write(redact_sensitive_text(result.stderr.decode(errors="replace"), force=True))
        sys.exit(result.returncode)
    elif args[0] not in {"log", "status"}:
        raise RuntimeError("use save, status, log, diff, show or restore")
    else:
        os.execvp("git", ["git", f"--git-dir={history.directory}", f"--work-tree={ROOT}", *args])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Instruction history: {error}", file=sys.stderr)
        sys.exit(1)
