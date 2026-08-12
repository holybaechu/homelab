import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts/ci/verify-compose-container-identities.sh"


def _posix_shell() -> str:
    shell = shutil.which("sh")
    if shell and os.name != "nt":
        return shell

    git_shell = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
    )
    if not os.path.exists(git_shell):
        pytest.skip("POSIX shell is unavailable")
    return git_shell


def _shell_path(path: Path) -> str:
    value = str(path)
    if os.name != "nt":
        return value
    drive, remainder = os.path.splitdrive(value)
    return f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"


def _fake_docker(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu

case "$1" in
  info)
    exit 0
    ;;
  ps)
    case "$*" in
      *project=platform*) printf '%s\\n' platform-1 platform-oneoff ;;
      *project=media*) printf '%s\\n' media-1 ;;
      *project=code*) printf '%s\\n' code-1 ;;
      *project=openclaw*) printf '%s\\n' openclaw-1 ;;
      *) exit 1 ;;
    esac
    ;;
  inspect)
    format="$3"
    container="$4"
    case "$format" in
      *com.docker.compose.oneoff*)
        if [ "$container" = platform-oneoff ]; then
          echo True
        else
          echo False
        fi
        ;;
      *State.Health*)
        project="${container%%-*}"
        [ "$project" = openclaw ] && project=openclaw
        health=healthy
        if [ "${FAKE_UNHEALTHY:-0}" = 1 ] && [ "$project" = openclaw ]; then
          health=unhealthy
        fi
        printf '%s|service|/%s|running|%s\\n' "$project" "$container" "$health"
        ;;
      *)
        project="${container%%-*}"
        [ "$project" = openclaw ] && project=openclaw
        printf '%s\\tservice\\t/%s\\tsha256:%s\\t2026-08-12T00:00:00Z\\t2026-08-12T00:00:01Z%s\\t0\\tsha256:image-%s\\texample/%s:stable\\n' \\
          "$project" "$container" "$container" "${FAKE_STARTED_SUFFIX:-}" \\
          "$container" "$project"
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)
    return fake_bin


def _run(
    shell: str,
    fake_bin: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(fake_bin), env["PATH"]])
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [shell, _shell_path(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def test_identity_helper_snapshots_and_verifies_long_lived_containers(tmp_path):
    shell = _posix_shell()
    fake_bin = _fake_docker(tmp_path)

    snapshot = _run(shell, fake_bin, "snapshot")
    assert snapshot.returncode == 0, snapshot.stderr
    assert snapshot.stdout.startswith("project\tservice\tname\tcontainer_id")
    assert "platform-oneoff" not in snapshot.stdout
    for project in ("platform", "media", "code", "openclaw"):
        assert f"{project}\tservice" in snapshot.stdout

    baseline = tmp_path / "baseline.tsv"
    baseline.write_text(snapshot.stdout, encoding="utf-8", newline="\n")
    verified = _run(shell, fake_bin, "verify", _shell_path(baseline))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "identities match the baseline" in verified.stdout
    assert "running and healthy" in verified.stdout


def test_identity_helper_rejects_restart_and_unhealthy_state(tmp_path):
    shell = _posix_shell()
    fake_bin = _fake_docker(tmp_path)
    snapshot = _run(shell, fake_bin, "snapshot")
    assert snapshot.returncode == 0, snapshot.stderr
    baseline = tmp_path / "baseline.tsv"
    baseline.write_text(snapshot.stdout, encoding="utf-8", newline="\n")

    changed = _run(
        shell,
        fake_bin,
        "verify",
        _shell_path(baseline),
        extra_env={"FAKE_STARTED_SUFFIX": "-changed"},
    )
    assert changed.returncode == 1
    assert "identities changed" in changed.stderr

    unhealthy = _run(
        shell,
        fake_bin,
        "health",
        extra_env={"FAKE_UNHEALTHY": "1"},
    )
    assert unhealthy.returncode == 1
    assert "health is unhealthy" in unhealthy.stderr
