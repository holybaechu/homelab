from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "scripts" / "ci" / "deploy-compose-via-ssh.sh"
BASH = shutil.which("bash")
if BASH is None and os.name == "nt":
    candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
    if candidate.is_file():
        BASH = str(candidate)

pytestmark = pytest.mark.skipif(BASH is None, reason="POSIX shell is unavailable")


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _posix(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    assert BASH is not None
    return subprocess.run(
        [BASH, "-lc", 'cygpath -u "$1"', "path", str(path)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    ).stdout.strip()


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    os.chmod(path, 0o755)


@pytest.fixture
def shell_tmp(tmp_path: Path):
    if os.name != "nt":
        yield tmp_path
        return
    base = ROOT / ".pytest-shell"
    base.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="deploy-ssh-", dir=base))
    try:
        yield path
    finally:
        def make_writable(function, target, _error):
            os.chmod(target, stat.S_IRWXU)
            function(target)

        shutil.rmtree(path, onerror=make_writable)
        try:
            base.rmdir()
        except OSError:
            pass


@pytest.fixture
def repository(shell_tmp: Path) -> tuple[Path, str]:
    repo = shell_tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "core.autocrlf", "false")

    compose = repo / "apps" / "compose" / "homelab" / "compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text(
        'name: homelab\nservices:\n  t3code:\n    image: "${T3CODE_IMAGE_REF:?exact digest required}"\n',
        encoding="utf-8",
        newline="\n",
    )
    deployer = repo / "scripts" / "ci" / "deploy_compose_release.py"
    _write_executable(
        deployer,
        """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

with Path(os.environ["FAKE_DEPLOY_LOG"]).open("a", encoding="utf-8") as log:
    log.write(f"deploy|{'|'.join(sys.argv[1:])}\\n")
""",
    )
    immutable_helper = repo / "scripts" / "ci" / "immutable_image_release.py"
    _write_executable(
        immutable_helper,
        "#!/usr/bin/env python3\n# committed immutable image contract helper\n",
    )
    image_build = repo / "apps" / "images" / "t3code" / "Dockerfile"
    image_build.parent.mkdir(parents=True)
    image_build.write_text("FROM scratch\n", encoding="utf-8", newline="\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    sha = _git(repo, "rev-parse", "HEAD")

    # Worktree values must never enter the exact-commit archive.
    compose.write_text("WORKTREE COMPOSE LEAK\n", encoding="utf-8", newline="\n")
    deployer.write_text("WORKTREE DEPLOYER LEAK\n", encoding="utf-8", newline="\n")
    immutable_helper.write_text(
        "WORKTREE IMMUTABLE HELPER LEAK\n", encoding="utf-8", newline="\n"
    )
    return repo, sha


@pytest.fixture
def fake_remote(shell_tmp: Path) -> tuple[dict[str, str], dict[str, Path]]:
    fake_bin = shell_tmp / "fake-bin"
    fake_bin.mkdir()
    ssh_log = shell_tmp / "ssh.log"
    scp_log = shell_tmp / "scp.log"
    deploy_log = shell_tmp / "deploy.log"
    remote_script = shell_tmp / "remote-install.sh"
    archive_capture = shell_tmp / "transferred.tar"
    release_root = shell_tmp / "remote" / "releases"
    current_root = shell_tmp / "remote" / "current"
    state_root = shell_tmp / "remote" / "state"
    runtime_root = shell_tmp / "remote" / "runtime"
    for directory in (release_root, current_root, state_root, runtime_root):
        directory.mkdir(parents=True)
        os.chmod(directory, 0o755 if directory == release_root else 0o700)

    _write_executable(
        fake_bin / "ssh",
        """#!/bin/sh
printf 'CALL' >>"$FAKE_SSH_LOG"
for argument do printf '\\t%s' "$argument" >>"$FAKE_SSH_LOG"; done
printf '\\n' >>"$FAKE_SSH_LOG"
shift
if [ "$#" -eq 1 ]; then
  case "$1" in
    'umask 077; mktemp /tmp/homelab-compose-upload.XXXXXXXXXX.tar') sh -c "$1"; exit ;;
    "rm -f -- "*) sh -c "$1"; exit ;;
  esac
fi
[ "$1" = sh ] && [ "$2" = -s ] && [ "$3" = -- ] || exit 97
remote_command=
for argument do
  remote_command="${remote_command}${remote_command:+ }${argument}"
done
cat >"$FAKE_REMOTE_SCRIPT"
PATH=$(dirname "$0"):$PATH
export PATH
# OpenSSH concatenates command arguments into shell text. All fixture values
# are token-safe; parsing this text deliberately drops trailing empty args.
exec sh -c "$remote_command" <"$FAKE_REMOTE_SCRIPT"
""",
    )
    _write_executable(
        fake_bin / "scp",
        """#!/bin/sh
[ "$#" -eq 2 ] || exit 96
printf '%s\\t%s\\n' "$1" "$2" >>"$FAKE_SCP_LOG"
destination=${2#*:}
cp "$1" "$destination"
cp "$1" "$FAKE_ARCHIVE_CAPTURE"
if [ "${FAKE_CORRUPT_TRANSFER:-0}" = 1 ]; then printf x >>"$destination"; fi
""",
    )
    _write_executable(
        fake_bin / "id",
        '#!/bin/sh\nif [ "${1:-}" = -u ]; then echo 0; else /usr/bin/id "$@"; fi\n',
    )
    _write_executable(
        fake_bin / "stat",
        '#!/bin/sh\nif [ "${1:-}" = -c ] && [ "${2:-}" = %u ]; then echo 0; else /usr/bin/stat "$@"; fi\n',
    )
    _write_executable(fake_bin / "find", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "install",
        """#!/bin/sh
directory=0
mode=0755
path=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -d) directory=1; shift ;;
    -o|-g) shift 2 ;;
    -m) mode=$2; shift 2 ;;
    *) path=$1; shift ;;
  esac
done
[ "$directory" -eq 1 ] && [ -n "$path" ] || exit 95
mkdir -p "$path"
chmod "$mode" "$path"
""",
    )

    paths = {
        "ssh_log": ssh_log,
        "scp_log": scp_log,
        "deploy_log": deploy_log,
        "remote_script": remote_script,
        "archive": archive_capture,
        "release_root": release_root,
        "current_root": current_root,
        "state_root": state_root,
        "runtime_root": runtime_root,
    }
    environment = os.environ.copy()
    environment.update(
        {
            "DOCKER_APPS_HOST": "docker-apps.test",
            "SSH_BIN": _posix(fake_bin / "ssh"),
            "SCP_BIN": _posix(fake_bin / "scp"),
            "FAKE_SSH_LOG": _posix(ssh_log),
            "FAKE_SCP_LOG": _posix(scp_log),
            "FAKE_DEPLOY_LOG": str(deploy_log),
            "FAKE_REMOTE_SCRIPT": _posix(remote_script),
            "FAKE_ARCHIVE_CAPTURE": _posix(archive_capture),
            "RELEASE_ROOT": _posix(release_root),
            "CURRENT_ROOT": _posix(current_root),
            "STATE_ROOT": _posix(state_root),
            "RUNTIME_CONFIG_ROOT": _posix(runtime_root),
            "TMPDIR": _posix(shell_tmp / "tmp"),
        }
    )
    (shell_tmp / "tmp").mkdir()
    return environment, paths


def _run(repo: Path, sha: str, environment: dict[str, str]):
    assert BASH is not None
    return subprocess.run(
        [BASH, _posix(CLIENT), sha],
        cwd=repo,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_packages_and_deploys_only_one_homelab_release(repository, fake_remote):
    repo, sha = repository
    environment, paths = fake_remote

    first = _run(repo, sha, environment)
    assert first.returncode == 0, first.stderr

    installed = paths["release_root"] / sha
    assert "exact digest required" in (
        installed / "apps" / "compose" / "homelab" / "compose.yml"
    ).read_text(encoding="utf-8")
    assert "FAKE_DEPLOY_LOG" in (
        installed / "scripts" / "ci" / "deploy_compose_release.py"
    ).read_text(encoding="utf-8")
    assert "committed immutable image contract helper" in (
        installed / "scripts" / "ci" / "immutable_image_release.py"
    ).read_text(encoding="utf-8")
    assert (installed / ".archive.sha256").read_text(encoding="utf-8").strip()

    with tarfile.open(paths["archive"], "r:") as archive:
        names = set(archive.getnames())
    assert "apps/compose/homelab/compose.yml" in names
    assert all(not name.startswith("scripts/recovery/") for name in names)
    assert "apps/images/t3code/Dockerfile" not in names
    for project in ("platform", "media", "code"):
        assert all(not name.startswith(f"apps/compose/{project}/") for name in names)
    assert paths["deploy_log"].read_text(encoding="utf-8").splitlines()[0].startswith(
        f"deploy|{sha}|"
    )

    second = _run(repo, sha, environment)
    assert second.returncode == 0, second.stderr
    lines = paths["deploy_log"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[1].startswith(f"deploy|{sha}|")
    remote_script = paths["remote_script"].read_text(encoding="utf-8")
    assert "flock -x 9" in remote_script
    assert "flock -u 9" in remote_script
    assert remote_script.index("flock -u 9") < remote_script.index(
        'deployer="$final/scripts/ci/deploy_compose_release.py"'
    )
    assert "root@docker-apps.test:" in paths["scp_log"].read_text(encoding="utf-8")


def test_existing_release_digest_marker_is_immutable(repository, fake_remote):
    repo, sha = repository
    environment, paths = fake_remote
    assert _run(repo, sha, environment).returncode == 0
    marker = paths["release_root"] / sha / ".archive.sha256"
    os.chmod(marker, 0o600)
    marker.write_text(
        "0" * 64 + "\n", encoding="utf-8"
    )

    result = _run(repo, sha, environment)

    assert result.returncode != 0
    assert "different archive digest" in result.stderr
    assert len(paths["deploy_log"].read_text(encoding="utf-8").splitlines()) == 1


def test_forwards_exact_same_build_t3_digest(repository, fake_remote):
    repo, sha = repository
    environment, paths = fake_remote
    image_ref = "ghcr.io/holybaechu/homelab-t3code@sha256:" + "1" * 64
    environment["T3_SOURCE_SHA"] = sha
    environment["T3_IMAGE_REF"] = image_ref

    result = _run(repo, sha, environment)

    assert result.returncode == 0, result.stderr
    arguments = paths["deploy_log"].read_text(encoding="utf-8").strip().split("|")
    assert arguments[:2] == ["deploy", sha]
    assert arguments[-4:] == ["--t3-source-sha", sha, "--t3-image-ref", image_ref]
    with tarfile.open(paths["archive"], "r:") as archive:
        names = set(archive.getnames())
    assert "apps/images/t3code/Dockerfile" not in names
    assert "apps/compose/homelab/Dockerfile" not in names


@pytest.mark.parametrize(
    ("source", "image_ref", "message"),
    [
        (None, "valid", "supplied together"),
        ("valid", None, "supplied together"),
        ("other", "valid", "equal the tested"),
        ("valid", "tag", "T3_IMAGE_REF must be"),
        ("valid", "wrong-repository", "T3_IMAGE_REF must be"),
    ],
)
def test_rejects_incomplete_mutable_or_cross_sha_t3_approval_before_ssh(
    repository, fake_remote, source, image_ref, message
):
    repo, sha = repository
    environment, paths = fake_remote
    values = {
        "valid": "ghcr.io/holybaechu/homelab-t3code@sha256:" + "1" * 64,
        "tag": "ghcr.io/holybaechu/homelab-t3code:latest",
        "wrong-repository": "ghcr.io/other/t3code@sha256:" + "1" * 64,
    }
    if source is not None:
        environment["T3_SOURCE_SHA"] = sha if source == "valid" else "f" * 40
    if image_ref is not None:
        environment["T3_IMAGE_REF"] = values[image_ref]

    result = _run(repo, sha, environment)

    assert result.returncode == 2
    assert message in result.stderr
    assert not paths["ssh_log"].exists()
    assert not paths["scp_log"].exists()


def test_remote_digest_mismatch_stops_before_install(repository, fake_remote):
    repo, sha = repository
    environment, paths = fake_remote
    environment["FAKE_CORRUPT_TRANSFER"] = "1"

    result = _run(repo, sha, environment)

    assert result.returncode != 0
    assert "digest mismatch" in result.stderr
    assert not paths["deploy_log"].exists()
    assert not (paths["release_root"] / sha).exists()


@pytest.mark.parametrize("sha", ["f" * 40, "f" * 64])
def test_requires_the_exact_existing_commit_before_ssh(repository, fake_remote, sha):
    repo, _actual_sha = repository
    environment, paths = fake_remote

    result = _run(repo, sha, environment)

    assert result.returncode == 2
    assert "existing commit" in result.stderr
    assert not paths["ssh_log"].exists()
    assert not paths["scp_log"].exists()


@pytest.mark.parametrize(
    ("sha", "host", "root", "message"),
    [
        ("a" * 39, "docker.test", None, "40 or 64"),
        ("A" * 40, "docker.test", None, "lowercase"),
        ("a" * 40, "", None, "DOCKER_APPS_HOST"),
        ("a" * 40, "host..test", None, "DOCKER_APPS_HOST"),
        ("a" * 40, "-oProxyCommand=x", None, "DOCKER_APPS_HOST"),
        ("a" * 40, "host@attacker", None, "DOCKER_APPS_HOST"),
        ("a" * 40, "docker.test", "/tmp/release/../escape", "remote roots"),
        ("a" * 40, "docker.test", "/opt/homelab/current", "must not overlap"),
    ],
)
def test_rejects_untrusted_identity_before_running_commands(
    tmp_path, sha, host, root, message
):
    environment = os.environ.copy()
    environment.update(
        {
            "DOCKER_APPS_HOST": host,
            "GIT_BIN": "must-not-run",
            "SSH_BIN": "must-not-run",
            "SCP_BIN": "must-not-run",
        }
    )
    if root is not None:
        environment["RELEASE_ROOT"] = root

    result = _run(tmp_path, sha, environment)

    assert result.returncode == 2
    assert message in result.stderr


def test_rejects_archive_symlinks_before_install(repository, fake_remote):
    repo, _old_sha = repository
    environment, paths = fake_remote
    blob = _git(repo, "hash-object", "-w", "--stdin", input_text="../../etc/passwd\n")
    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{blob},apps/compose/homelab/untrusted-link",
    )
    _git(repo, "commit", "-qm", "add archive symlink")
    sha = _git(repo, "rev-parse", "HEAD")

    result = _run(repo, sha, environment)

    assert result.returncode != 0
    assert "unsafe or unexpected archive member" in result.stderr
    assert not (paths["release_root"] / sha).exists()
    assert not paths["deploy_log"].exists()
