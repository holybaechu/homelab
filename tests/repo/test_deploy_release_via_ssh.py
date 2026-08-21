from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


WRAPPER = REPO_ROOT / "scripts/ci/deploy-release-via-ssh.sh"
DIGEST = "a" * 64


def posix_shell() -> str:
    shell = shutil.which("sh")
    if shell is not None:
        return shell
    for candidate in (
        Path("C:/Program Files/Git/bin/sh.exe"),
        Path("C:/Program Files/Git/usr/bin/sh.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    pytest.skip("POSIX sh is unavailable")


def shell_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    drive, remainder = os.path.splitdrive(str(resolved))
    return f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"


def shell_environment_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def write_tool(path: Path, source: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + source, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def transport(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "transport.log"
    local_stage = tmp_path / "upload-stage"
    local_stage.mkdir()

    ssh = write_tool(
        tools / "ssh",
        r'''
{
  printf 'ssh'
  for arg do printf '\t%s' "$arg"; done
  printf '\n'
} >> "$FAKE_LOG"
case "${2:-}" in
  *"mktemp -d"*) printf '%s\n' "$FAKE_REMOTE_ROOT" ;;
  *"/usr/local/libexec/homelab-release"*)
    [ "${FAKE_REMOTE_FAILURE:-}" != launcher ] || exit 41
    ;;
esac
''',
    )
    scp = write_tool(
        tools / "scp",
        r'''
{
  printf 'scp'
  for arg do printf '\t%s' "$arg"; done
  printf '\n'
} >> "$FAKE_LOG"
[ "${FAKE_REMOTE_FAILURE:-}" != scp ] || exit 42
''',
    )
    sha256 = write_tool(
        tools / "sha256sum",
        f"printf '%s  %s\\n' '{DIGEST}' \"$1\"\n",
    )
    mktemp = write_tool(
        tools / "mktemp",
        'printf \'%s\\n\' "$FAKE_LOCAL_STAGE"\n',
    )

    env = os.environ.copy()
    env.update(
        {
            "SSH_BIN": shell_path(ssh),
            "SCP_BIN": shell_path(scp),
            "SHA256_BIN": shell_path(sha256),
            "MKTEMP_BIN": shell_path(mktemp),
            "FAKE_LOG": shell_environment_path(log),
            "FAKE_LOCAL_STAGE": shell_environment_path(local_stage),
        }
    )
    return env, log, local_stage


def run_wrapper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [posix_shell(), shell_path(WRAPPER), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def calls(log: Path, command: str) -> list[list[str]]:
    return [
        line.split("\t")[1:]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.split("\t", 1)[0] == command
    ]


def test_deploy_routes_apps_and_uploads_release_and_secrets_once(
    tmp_path: Path, transport: tuple[dict[str, str], Path, Path]
) -> None:
    env, log, local_stage = transport
    bundle = tmp_path / "apps.tar"
    secrets = tmp_path / "apps.json"
    bundle.write_bytes(b"release")
    secrets.write_text("{}", encoding="utf-8")
    env.update(
        {
            "DOCKER_APPS_HOST": "apps.internal",
            "FAKE_REMOTE_ROOT": "/tmp/homelab-apps-deploy.ABC123",
        }
    )

    result = run_wrapper(env, "deploy", "apps", shell_path(bundle), shell_path(secrets))

    assert result.returncode == 0, result.stderr
    assert f"Deployed apps bundle {DIGEST} to root@apps.internal" in result.stdout
    uploads = calls(log, "scp")
    assert len(uploads) == 1
    assert [Path(value).name for value in uploads[0][:-1]] == ["release.tar", "secrets.json"]
    assert uploads[0][-1] == "root@apps.internal:/tmp/homelab-apps-deploy.ABC123/"
    remote = calls(log, "ssh")
    assert all(call[0] == "root@apps.internal" for call in remote)
    assert any(
        "/usr/local/libexec/homelab-release deploy --target 'apps'" in call[1]
        and f"--sha256 '{DIGEST}'" in call[1]
        for call in remote
    )
    assert any("rm -rf -- '/tmp/homelab-apps-deploy.ABC123'" in call[1] for call in remote)
    assert not local_stage.exists()


def test_secret_sync_routes_openclaw_and_uploads_only_the_bundle_once(
    tmp_path: Path, transport: tuple[dict[str, str], Path, Path]
) -> None:
    env, log, local_stage = transport
    secrets = tmp_path / "openclaw.json"
    secrets.write_text("{}", encoding="utf-8")
    env.update(
        {
            "OPENCLAW_HOST": "openclaw.internal",
            "FAKE_REMOTE_ROOT": "/tmp/homelab-openclaw-sync-secrets.XYZ789",
        }
    )

    result = run_wrapper(env, "sync-secrets", "openclaw", shell_path(secrets))

    assert result.returncode == 0, result.stderr
    assert "Synchronized openclaw component secrets on root@openclaw.internal" in result.stdout
    uploads = calls(log, "scp")
    assert len(uploads) == 1
    assert Path(uploads[0][0]).name == "secrets.json"
    assert len(uploads[0]) == 2
    assert uploads[0][-1] == (
        "root@openclaw.internal:/tmp/homelab-openclaw-sync-secrets.XYZ789/"
    )
    remote = calls(log, "ssh")
    activation = next(
        call[1]
        for call in remote
        if "/usr/local/libexec/homelab-release" in call[1]
    )
    assert "sync-secrets --target 'openclaw'" in activation
    assert "--archive" not in activation
    assert "--sha256" not in activation
    assert not local_stage.exists()


def test_remote_activation_failure_still_cleans_both_staging_directories(
    tmp_path: Path, transport: tuple[dict[str, str], Path, Path]
) -> None:
    env, log, local_stage = transport
    bundle = tmp_path / "apps.tar"
    secrets = tmp_path / "apps.json"
    bundle.write_bytes(b"release")
    secrets.write_text("{}", encoding="utf-8")
    env.update(
        {
            "DOCKER_APPS_HOST": "apps.internal",
            "FAKE_REMOTE_ROOT": "/tmp/homelab-apps-deploy.FAIL01",
            "FAKE_REMOTE_FAILURE": "launcher",
        }
    )

    result = run_wrapper(env, "deploy", "apps", shell_path(bundle), shell_path(secrets))

    assert result.returncode == 2
    assert "remote release activation failed" in result.stderr
    assert len(calls(log, "scp")) == 1
    remote = calls(log, "ssh")
    assert any("rm -rf -- '/tmp/homelab-apps-deploy.FAIL01'" in call[1] for call in remote)
    assert not local_stage.exists()


def test_wrapper_shell_syntax_when_sh_is_available() -> None:
    result = subprocess.run(
        [posix_shell(), "-n", shell_path(WRAPPER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
