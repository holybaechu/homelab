import os
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


def _posix_shell_command(script: str) -> list[str]:
    shell = shutil.which("sh")
    if shell and os.name != "nt":
        return [shell, script]

    git_shell = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
    )
    if not os.path.exists(git_shell):
        pytest.skip("POSIX shell is unavailable")
    drive, remainder = os.path.splitdrive(script)
    git_path = f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"
    return [git_shell, git_path]


def test_vuetorrent_validator_reports_every_internal_diagnostic_before_failing():
    script = str(REPO_ROOT / "tests/docker/test_vuetorrent_validation.sh")
    env = os.environ.copy()
    if os.name == "nt":
        git_root = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git")
        env["PATH"] = os.pathsep.join(
            [
                os.path.join(git_root, "usr", "bin"),
                os.path.join(git_root, "mingw64", "bin"),
                env["PATH"],
            ]
        )
    result = subprocess.run(
        _posix_shell_command(script),
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
