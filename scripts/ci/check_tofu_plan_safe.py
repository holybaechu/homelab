#!/usr/bin/env python3
"""Fail CI when an OpenTofu plan contains destructive actions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_plan(argv: list[str]) -> dict[str, Any]:
    if len(argv) > 2:
        raise SystemExit("usage: check_tofu_plan_safe.py [PLAN_JSON]")

    if len(argv) == 2:
        with Path(argv[1]).open(encoding="utf-8") as handle:
            return json.load(handle)

    return json.load(sys.stdin)


def _destructive_changes(plan: dict[str, Any]) -> list[str]:
    destructive = []
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if "delete" in actions:
            address = resource.get("address", "<unknown>")
            destructive.append(f"{address}: {','.join(actions)}")
    return destructive


def main(argv: list[str]) -> int:
    plan = _load_plan(argv)
    destructive = _destructive_changes(plan)

    if not destructive:
        print("OpenTofu plan safety check passed: no destructive actions.")
        return 0

    print("OpenTofu plan contains destructive actions:", file=sys.stderr)
    for change in destructive:
        print(f"- {change}", file=sys.stderr)

    if _truthy(os.environ.get("ALLOW_TOFU_DESTROY")):
        print("ALLOW_TOFU_DESTROY is set; allowing destructive plan.", file=sys.stderr)
        return 0

    print(
        "Refusing to continue. Set ALLOW_TOFU_DESTROY=true only for an intentional "
        "manual destroy workflow.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
