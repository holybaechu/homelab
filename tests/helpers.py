import json
import os
from pathlib import Path
import shutil

import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def task_with_module(tasks, module, **arguments):
    matches = [
        task for task in tasks
        if module in task
        and all(task[module].get(key) == value for key, value in arguments.items())
    ]
    assert len(matches) == 1, (module, arguments)
    return matches[0]


def render_ansible(value, **variables):
    environment = NativeEnvironment()
    environment.filters["bool"] = bool
    environment.filters["from_json"] = json.loads
    return environment.from_string(str(value)).render(omit=None, **variables)


def task_enabled(task, **variables):
    conditions = task.get("when", [])
    if not isinstance(conditions, list):
        conditions = [conditions]
    return all(
        render_ansible("{{ " + str(condition) + " }}", **variables) is True
        for condition in conditions
    )


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
