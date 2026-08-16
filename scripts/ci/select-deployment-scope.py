#!/usr/bin/env python3
"""Select the smallest safe production deployment for a GitHub event."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")

NO_DEPLOY_PREFIXES = (
    "docs/",
    "tests/",
)
NO_DEPLOY_PATHS = {
    ".github/workflows/ci.yml",
    ".github/dependabot.yml",
    "requirements-dev.txt",
    "renovate.json",
}

OPENCLAW_PREFIXES = (
    "infra/ansible/roles/openclaw_native/",
    "infra/ansible/roles/openclaw_ctf_executor/",
    "infra/ansible/roles/openclaw_ctf_transport/",
    "apps/openclaw-ctf-kali/",
)
OPENCLAW_PATHS = {
    "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml",
    "infra/ansible/inventory/prod/group_vars/svc_ctf_executor.yml",
}

ARCANE_WORKLOAD_PREFIXES = {
    "platform": "apps/compose/platform/",
    "media": "apps/compose/media/",
    "code": "apps/compose/code/",
}

# These paths need the full pre-site safety checks or Ansible handler semantics.
# The tracked route participates in the OpenClaw ownership tuple, while Arcane's
# normal Git sync cannot force-recreate Traefik for static configuration drift.
FULL_DEPLOYMENT_PATHS = {
    "apps/compose/platform/dynamic/routes.yml",
    "apps/compose/platform/traefik.yml",
}


def classify_paths(paths: list[str]) -> str:
    changed = [path for path in paths if path]
    if not changed:
        return "none"
    if all(
        path in NO_DEPLOY_PATHS or path.startswith(NO_DEPLOY_PREFIXES)
        for path in changed
    ):
        return "none"
    if any(path in FULL_DEPLOYMENT_PATHS for path in changed):
        return "full"
    if all(
        path in OPENCLAW_PATHS or path.startswith(OPENCLAW_PREFIXES)
        for path in changed
    ):
        return "openclaw"
    if changed and all(
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


def deployment_scope(repo_root: Path) -> tuple[str, list[str], str]:
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "repository_dispatch":
        promoted = os.environ.get("OPENCLAW_PROMOTED_COMMIT", "").lower()
        if not COMMIT_SHA.fullmatch(promoted):
            return "full", [], ""
        return "openclaw", [], promoted
    if event != "push":
        return "full", [], ""

    before = os.environ.get("GITHUB_EVENT_BEFORE", "")
    current = os.environ.get("GITHUB_SHA", "")
    if (
        not COMMIT_SHA.fullmatch(before)
        or not COMMIT_SHA.fullmatch(current)
        or before == "0" * 40
    ):
        return "full", [], ""

    try:
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", before, current, "--"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return "full", [], ""

    paths = result.stdout.splitlines()
    return classify_paths(paths), paths, ""


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: select-deployment-scope.py GITHUB_OUTPUT")

    repo_root = Path(__file__).resolve().parents[2]
    scope, paths, promoted_commit = deployment_scope(repo_root)
    projects = select_arcane_projects(paths) if scope == "arcane" else []
    openclaw_components = "gateway" if scope == "openclaw" and promoted_commit else "all"
    output_path = Path(sys.argv[1])
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"deployment_scope={scope}\n")
        output.write(f"arcane_projects={','.join(projects)}\n")
        output.write(f"openclaw_setup_commit={promoted_commit}\n")
        output.write(f"openclaw_components={openclaw_components}\n")

    print(f"Deployment scope: {scope}")
    if paths:
        print("Changed paths:")
        for path in paths:
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
