#!/usr/bin/env python3
"""Fail CI when an OpenTofu plan contains unsafe production actions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from homelab_topology import expected_lxc_count

LXC_RESOURCE_SUFFIX = ".proxmox_virtual_environment_container.this"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_plan(argv: list[str]) -> dict[str, Any]:
    if len(argv) > 2:
        raise SystemExit("usage: check_tofu_plan_safe.py [PLAN_JSON]")

    if len(argv) == 2:
        with Path(argv[1]).open(encoding="utf-8") as handle:
            return json.load(handle)

    return json.load(sys.stdin)


def _resource_changes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return list(plan.get("resource_changes", []))


def _actions(resource: dict[str, Any]) -> list[str]:
    return list(resource.get("change", {}).get("actions", []))


def _destructive_changes(plan: dict[str, Any]) -> list[str]:
    destructive = []
    for resource in _resource_changes(plan):
        actions = _actions(resource)
        if "delete" in actions:
            address = resource.get("address", "<unknown>")
            destructive.append(f"{address}: {','.join(actions)}")
    return destructive


def _create_only_lxc_changes(plan: dict[str, Any]) -> list[str]:
    create_only = []
    for resource in _resource_changes(plan):
        address = resource.get("address", "")
        if not address.startswith("module.lxc[") or not address.endswith(LXC_RESOURCE_SUFFIX):
            continue
        if _actions(resource) == ["create"]:
            create_only.append(address)
    return create_only


def _expected_lxc_count() -> int:
    configured = os.environ.get("TOFU_EXPECTED_LXC_COUNT")
    if configured:
        return int(configured)
    return expected_lxc_count()


def main(argv: list[str]) -> int:
    plan = _load_plan(argv)
    destructive = _destructive_changes(plan)

    if destructive:
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

    create_only_lxcs = _create_only_lxc_changes(plan)
    expected_lxcs = _expected_lxc_count()
    if len(create_only_lxcs) >= expected_lxcs and not _truthy(
        os.environ.get("ALLOW_EMPTY_STATE_BOOTSTRAP")
    ):
        print("OpenTofu plan appears to be a create-only LXC bootstrap plan:", file=sys.stderr)
        for address in create_only_lxcs:
            print(f"- {address}: create-only", file=sys.stderr)
        print(
            "Refusing to continue because this often means the production remote state "
            "bucket/key/endpoint is wrong. Set ALLOW_EMPTY_STATE_BOOTSTRAP=true only "
            "for an intentional first bootstrap.",
            file=sys.stderr,
        )
        return 1

    if create_only_lxcs and _truthy(os.environ.get("ALLOW_EMPTY_STATE_BOOTSTRAP")):
        print("ALLOW_EMPTY_STATE_BOOTSTRAP is set; allowing create-only LXC plan.", file=sys.stderr)

    print("OpenTofu plan safety check passed: no destructive actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
