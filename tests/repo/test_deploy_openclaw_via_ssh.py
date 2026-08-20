from pathlib import Path
import os
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts/ci/deploy-openclaw-via-ssh.sh"
BASH = shutil.which("bash")
if BASH is None and os.name == "nt":
    candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
    if candidate.is_file():
        BASH = str(candidate)

pytestmark = pytest.mark.skipif(BASH is None, reason="POSIX shell is unavailable")


def _posix(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    assert BASH is not None
    return subprocess.run(
        [BASH, "-lc", 'cygpath -u "$1"', "path", str(path)],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _write_tool(path: Path, body: str) -> None:
    path.write_text(
        "#!/bin/sh\nset -eu\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o755)


def test_direct_openclaw_uploader_has_no_build_or_ansible_toolchain() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\nset -eu\n")
    assert "deploy_openclaw_release.py" in text
    assert "openclaw_release.py" in text
    assert "readyz" in text
    assert "legacy-recovery" not in text
    assert "deployment_source_sha" in text
    assert "flock -n 9" in text
    assert "secrets.token_hex" in text
    deploy = '  deploy \\\n'
    audit = '  audit\n'
    assert text.count(deploy) == 1
    assert text.count(audit) == 1
    assert text.index(deploy) < text.index(audit)
    assert text.count('"$@"') == 2
    for forbidden in ("docker build", "npm ", "ansible", "tofu", "pip install", "setup-python"):
        assert forbidden not in text
    subprocess.run([BASH, "-n", _posix(SCRIPT)], check=True)


def test_uploader_transfers_one_exact_release_and_invokes_preinstalled_deployer(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.log"
    ssh = tmp_path / "ssh"
    scp = tmp_path / "scp"
    python = tmp_path / "python"
    _write_tool(
        ssh,
        'printf "ssh:%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "${2-}" = sh ]; then cat >/dev/null; fi\n',
    )
    _write_tool(scp, 'printf "scp:%s\\n" "$*" >> "$CALL_LOG"\n')
    _write_tool(
        python,
        'printf "python:%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "${1-}" = -c ]; then\n'
        '  case "${2-}" in *secrets.token_hex*) printf "%s\\n" 0123456789abcdef01234567 ;; '
        '*) exec "' + _posix(Path(os.sys.executable)) + '" "$@" ;; esac\n'
        'elif [ "${2-}" = verify ]; then\n'
        '  printf \'{"deployment_source_sha":"%s"}\\n\' "' + ("a" * 40) + '"\n'
        'fi\n',
    )
    release = tmp_path / "release.json"
    runtime = tmp_path / "runtime.tar"
    config = tmp_path / "config.tar"
    for path in (release, runtime, config):
        path.write_text("fixture", encoding="utf-8")
    env = {
        **os.environ,
        "OPENCLAW_HOST": "192.168.0.5",
        "SSH_BIN": _posix(ssh),
        "SCP_BIN": _posix(scp),
        "PYTHON_BIN": _posix(python),
        "CALL_LOG": _posix(log),
    }
    result = subprocess.run(
        [
            BASH, _posix(SCRIPT), "a" * 40,
            _posix(release), _posix(runtime), _posix(config),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "python:scripts/ci/openclaw_release.py verify" in calls
    assert calls.count("scp:") == 3
    assert "root@192.168.0.5" in calls
    assert f"/opt/openclaw/incoming/{'a' * 40}-0123456789abcdef01234567" in calls


def test_uploader_rejects_manifest_for_a_different_source_sha_before_ssh(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls.log"
    python = tmp_path / "python"
    _write_tool(
        python,
        'printf "python:%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "${2-}" = verify ]; then printf \'{"deployment_source_sha":"%s"}\\n\' "' + ("b" * 40) + '"; '
        'else exec "' + _posix(Path(os.sys.executable)) + '" "$@"; fi\n',
    )
    release, runtime, config = (tmp_path / name for name in ("release", "runtime", "config"))
    for path in (release, runtime, config):
        path.write_text("fixture", encoding="utf-8")
    result = subprocess.run(
        [
            BASH, _posix(SCRIPT), "a" * 40,
            _posix(release), _posix(runtime), _posix(config),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "OPENCLAW_HOST": "192.168.0.5",
            "PYTHON_BIN": _posix(python),
            "CALL_LOG": _posix(log),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "deployment_source_sha differs" in result.stderr
    assert "ssh:" not in log.read_text(encoding="utf-8")


def test_uploader_rejects_nonexact_sha_before_any_remote_call(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("release", "runtime", "config")]
    for path in paths:
        path.write_text("x", encoding="utf-8")
    result = subprocess.run(
        [BASH, _posix(SCRIPT), "A" * 40, *(_posix(path) for path in paths)],
        cwd=REPO_ROOT,
        env={**os.environ, "OPENCLAW_HOST": "192.168.0.5"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "exact lowercase 40-hex" in result.stderr
