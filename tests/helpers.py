import os
from pathlib import Path
import shutil

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


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
