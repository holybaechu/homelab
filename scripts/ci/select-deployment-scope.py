#!/usr/bin/env python3
"""Select the smallest safe production deployment for a GitHub event."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")

ARCANE_WORKLOAD_PREFIXES = {
    "platform": "apps/compose/platform/",
    "media": "apps/compose/media/",
    "code": "apps/compose/code/",
    "openclaw": "apps/compose/openclaw/",
}

# These files need the full Ansible handler's force-recreate/build semantics.
# Arcane's normal Git sync redeploy does not force-recreate a container whose
# bind-mounted static configuration changed.
FULL_DEPLOYMENT_PATHS = {
    "apps/compose/platform/traefik.yml",
}


def classify_paths(paths: list[str]) -> str:
    changed = [path for path in paths if path]
    if changed and not any(path in FULL_DEPLOYMENT_PATHS for path in changed) and all(
        any(path.startswith(prefix) for prefix in ARCANE_WORKLOAD_PREFIXES.values())
        for path in changed
    ):
        return "arcane"
    return "full"


def select_arcane_projects(paths: list[str]) -> list[str]:
    """Return affected Arcane projects in deterministic dependency order."""

    return [
        project
        for project, prefix in ARCANE_WORKLOAD_PREFIXES.items()
        if any(path.startswith(prefix) for path in paths)
    ]


def deployment_scope(repo_root: Path) -> tuple[str, list[str]]:
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return "full", []

    before = os.environ.get("GITHUB_EVENT_BEFORE", "")
    current = os.environ.get("GITHUB_SHA", "")
    if (
        not COMMIT_SHA.fullmatch(before)
        or not COMMIT_SHA.fullmatch(current)
        or before == "0" * 40
    ):
        return "full", []

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", before, current, "--"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return "full", []

    paths = result.stdout.splitlines()
    return classify_paths(paths), paths


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: select-deployment-scope.py GITHUB_OUTPUT")

    repo_root = Path(__file__).resolve().parents[2]
    scope, paths = deployment_scope(repo_root)
    projects = select_arcane_projects(paths) if scope == "arcane" else []
    output_path = Path(sys.argv[1])
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"deployment_scope={scope}\n")
        output.write(f"arcane_projects={','.join(projects)}\n")

    print(f"Deployment scope: {scope}")
    if paths:
        print("Changed paths:")
        for path in paths:
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
