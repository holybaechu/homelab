#!/usr/bin/env python3
"""Promote validated live OpenClaw skills through an automatically merged PR."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ALLOWED_SUPPORT = {"assets", "examples", "references", "scripts", "templates"}
MAX_FILE_BYTES = 200_000
MAX_SKILL_BYTES = 1_000_000
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


class SyncError(RuntimeError):
    pass


class Deferred(SyncError):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SyncError(f"missing required environment: {name}")
    return value


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if completed.returncode != 0:
        raise SyncError(f"command failed ({argv[0]}): rc={completed.returncode}")
    return completed.stdout.strip()


def read_token(path: Path) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SyncError("GitHub credential is not a single-linked regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SyncError("GitHub credential permissions are too broad")
    payload = path.read_bytes()
    if not 20 <= len(payload) <= 4097 or b"\x00" in payload:
        raise SyncError("GitHub credential has an invalid size or encoding")
    token = payload.decode("utf-8").removesuffix("\n")
    if not token or any(character.isspace() for character in token):
        raise SyncError("GitHub credential contains whitespace")
    return token


def read_skill_file(path: Path, token: str, expected_uid: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SyncError(f"unsupported skill file type: {path}")
    if info.st_uid != expected_uid or info.st_mode & stat.S_IWOTH:
        raise SyncError(f"unsafe skill ownership or mode: {path}")
    if info.st_size > MAX_FILE_BYTES:
        raise SyncError(f"skill file exceeds {MAX_FILE_BYTES} bytes: {path}")
    payload = path.read_bytes()
    if b"\x00" in payload:
        raise SyncError(f"skill file contains a NUL byte: {path}")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SyncError(f"skill file is not UTF-8: {path}") from error
    if token in text:
        raise SyncError(f"skill file contains the promotion credential: {path}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise SyncError(f"skill file resembles a credential: {path}")
    return payload


def collect_skill(skill: Path, token: str, expected_uid: int) -> dict[Path, bytes]:
    info = skill.lstat()
    if not stat.S_ISDIR(info.st_mode) or skill.is_symlink() or info.st_uid != expected_uid:
        raise SyncError(f"unsafe skill directory: {skill}")
    if not SKILL_NAME.fullmatch(skill.name):
        raise SyncError(f"invalid skill name: {skill.name}")
    files: dict[Path, bytes] = {}
    total = 0
    for current, directories, filenames in os.walk(skill, followlinks=False):
        base = Path(current)
        for directory in directories:
            child = base / directory
            relative = child.relative_to(skill)
            child_info = child.lstat()
            if (
                not stat.S_ISDIR(child_info.st_mode)
                or child.is_symlink()
                or child_info.st_uid != expected_uid
                or relative.parts[0] not in ALLOWED_SUPPORT
            ):
                raise SyncError(f"unsupported skill directory: {child}")
        for filename in filenames:
            child = base / filename
            relative = child.relative_to(skill)
            if relative != Path("SKILL.md") and (
                len(relative.parts) < 2 or relative.parts[0] not in ALLOWED_SUPPORT
            ):
                raise SyncError(f"unsupported skill file: {child}")
            payload = read_skill_file(child, token, expected_uid)
            files[relative] = payload
            total += len(payload)
    if Path("SKILL.md") not in files:
        raise SyncError(f"skill has no SKILL.md: {skill}")
    if total > MAX_SKILL_BYTES:
        raise SyncError(f"skill exceeds {MAX_SKILL_BYTES} bytes: {skill}")
    return files


def collect_roots(
    roots: dict[str, Path], token: str, expected_uid: int
) -> dict[Path, dict[Path, bytes]]:
    collected: dict[Path, dict[Path, bytes]] = {}
    for target_prefix, root in roots.items():
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or root.is_symlink() or info.st_uid != expected_uid:
            raise SyncError(f"unsafe managed skill root: {root}")
        for skill in sorted(root.iterdir()):
            collected[Path(target_prefix) / skill.name] = collect_skill(
                skill, token, expected_uid
            )
    return collected


def safe_directory(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for component in relative.parts:
        current /= component
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise SyncError(f"repository path is not a regular directory: {current}")
        else:
            current.mkdir(mode=0o755)


def apply_collected(repo: Path, collected: dict[Path, dict[Path, bytes]]) -> None:
    for relative_root in (Path("workspaces/main/skills"), Path("workspaces/ctf/skills")):
        target_root = repo / relative_root
        safe_directory(target_root.parent, repo)
        if target_root.exists() or target_root.is_symlink():
            if target_root.is_symlink() or not target_root.is_dir():
                raise SyncError(f"repository skill root is unsafe: {target_root}")
            shutil.rmtree(target_root)
    for relative_skill, files in collected.items():
        parent = repo / relative_skill.parent
        safe_directory(parent, repo)
        target = repo / relative_skill
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise SyncError(f"repository skill target is unsafe: {target}")
            shutil.rmtree(target)
        target.mkdir(mode=0o755)
        for relative_file, payload in files.items():
            destination = target / relative_file
            safe_directory(destination.parent, target)
            destination.write_bytes(payload)
            destination.chmod(0o644)


def request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "openclaw-skill-sync/1",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            decoded: object = json.loads(body) if body else {}
        except json.JSONDecodeError:
            decoded = {}
        return error.code, decoded


def find_or_create_pr(api: str, owner: str, branch: str, sha: str, token: str) -> int:
    head = urllib.parse.quote(f"{owner}:{branch}", safe="")
    status, payload = request_json("GET", f"{api}/pulls?state=open&head={head}", token)
    if status != 200 or not isinstance(payload, list):
        raise SyncError("cannot query existing skill-promotion pull requests")
    if payload:
        return int(payload[0]["number"])
    status, payload = request_json(
        "POST",
        f"{api}/pulls",
        token,
        {
            "title": f"Promote autonomous OpenClaw skills ({sha[:12]})",
            "head": branch,
            "base": "main",
            "body": "Automated, validated promotion of runtime OpenClaw skill files.",
        },
    )
    if status != 201 or not isinstance(payload, dict):
        raise SyncError("cannot create the skill-promotion pull request")
    return int(payload["number"])


def wait_for_workflow(api: str, sha: str, token: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        query = urllib.parse.urlencode(
            {"event": "pull_request", "head_sha": sha, "per_page": 100}
        )
        status, payload = request_json("GET", f"{api}/actions/runs?{query}", token)
        if status != 200 or not isinstance(payload, dict):
            raise SyncError("cannot inspect the skill-promotion workflow")
        runs = payload.get("workflow_runs")
        if isinstance(runs, list) and runs:
            if all(run.get("status") == "completed" for run in runs):
                allowed = {"success", "neutral", "skipped"}
                if not all(run.get("conclusion") in allowed for run in runs):
                    raise Deferred("skill-promotion workflow did not pass")
                return
        time.sleep(15)
    raise Deferred("skill-promotion workflow is still pending")


def merge_pr(api: str, number: int, sha: str, token: str) -> None:
    status, payload = request_json(
        "PUT",
        f"{api}/pulls/{number}/merge",
        token,
        {"sha": sha, "merge_method": "squash"},
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("merged") is not True:
        raise Deferred("skill-promotion pull request is not mergeable yet")


def main() -> int:
    state = Path(require_env("OPENCLAW_SKILL_SYNC_STATE_ROOT"))
    state_info = state.lstat()
    if not stat.S_ISDIR(state_info.st_mode) or state.is_symlink():
        raise SyncError("skill-sync state root is unsafe")
    token_path = Path(require_env("OPENCLAW_SKILL_SYNC_GITHUB_TOKEN_FILE"))
    token = read_token(token_path)
    repository = require_env("OPENCLAW_SKILL_SYNC_REPOSITORY")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository):
        raise SyncError("invalid GitHub repository identifier")
    owner = repository.split("/", 1)[0]
    clone_url = f"https://github.com/{repository}.git"
    api = f"https://api.github.com/repos/{repository}"
    timeout = int(os.environ.get("OPENCLAW_SKILL_SYNC_CHECK_TIMEOUT", "900"))
    roots = {
        "workspaces/main/skills": Path(require_env("OPENCLAW_SKILL_SYNC_MAIN_ROOT")),
        "workspaces/ctf/skills": Path(require_env("OPENCLAW_SKILL_SYNC_CTF_ROOT")),
    }
    expected_uid = int(require_env("OPENCLAW_SKILL_SYNC_SKILL_UID"))
    if expected_uid < 1:
        raise SyncError("invalid managed skill owner UID")
    collected = collect_roots(roots, token, expected_uid)

    git_env = dict(os.environ)
    git_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": require_env("OPENCLAW_SKILL_SYNC_ASKPASS"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
    )
    temporary_root = state / "tmp"
    temporary_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="promotion-", dir=temporary_root) as temporary:
        repo = Path(temporary) / "repo"
        run(["git", "clone", "--depth", "1", "--branch", "main", "--no-tags", clone_url, str(repo)], env=git_env)
        apply_collected(repo, collected)
        changed = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repo, env=git_env)
        if not changed:
            print("skill promotion: repository already matches live skills")
            return 0
        names = run(["git", "diff", "--name-only", "--no-ext-diff"], cwd=repo, env=git_env).splitlines()
        names += run(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, env=git_env).splitlines()
        allowed = ("workspaces/main/skills/", "workspaces/ctf/skills/")
        if not names or any(not name.startswith(allowed) for name in names):
            raise SyncError("promotion diff contains an unmanaged path")
        run(["git", "add", "-A", "--", "workspaces"], cwd=repo, env=git_env)
        patch = run(["git", "diff", "--cached", "--binary", "--no-ext-diff"], cwd=repo, env=git_env)
        digest = hashlib.sha256(patch.encode()).hexdigest()
        branch = f"automation/skill-sync-{digest[:16]}"
        run(["git", "checkout", "-b", branch], cwd=repo, env=git_env)
        run(["git", "-c", "user.name=OpenClaw Skill Sync", "-c", "user.email=openclaw-skill-sync@users.noreply.github.com", "commit", "-m", "Promote autonomous OpenClaw skills"], cwd=repo, env=git_env)
        local_sha = run(["git", "rev-parse", "HEAD"], cwd=repo, env=git_env)
        remote_line = run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], cwd=repo, env=git_env)
        if remote_line:
            sha = remote_line.split()[0]
        else:
            run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo, env=git_env)
            sha = local_sha
        number = find_or_create_pr(api, owner, branch, sha, token)
        wait_for_workflow(api, sha, token, timeout)
        merge_pr(api, number, sha, token)
        request_json("DELETE", f"{api}/git/refs/heads/{urllib.parse.quote(branch, safe='')}", token)
        print(f"skill promotion: merged pull request {number} at {sha[:12]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Deferred as error:
        print(f"skill promotion deferred: {error}", file=sys.stderr)
        raise SystemExit(75)
    except (OSError, SyncError, ValueError) as error:
        print(f"skill promotion failed: {error}", file=sys.stderr)
        raise SystemExit(1)
