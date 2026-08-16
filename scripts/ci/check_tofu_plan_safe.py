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
OPENCLAW_STAGE_ADDRESS = (
    f'module.target_lxc["openclaw"]{LXC_RESOURCE_SUFFIX}'
)
APPROVED_ADDITIVE_TARGETS = {
    OPENCLAW_STAGE_ADDRESS: 118,
}

OPENCLAW_STAGE_TAGS = frozenset(
    {"homelab", "managed-by-opentofu", "role-openclaw"}
)
OPENCLAW_TEMPLATE_FILE_ID = (
    "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
)
_MISSING = object()


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


def _is_approved_additive_target(resource: dict[str, Any]) -> bool:
    address = resource.get("address", "")
    expected = APPROVED_ADDITIVE_TARGETS.get(address)
    if expected is None or _actions(resource) != ["create"]:
        return False

    change = resource.get("change", {})
    after_vmid = (change.get("after") or {}).get("vm_id")
    return after_vmid == expected


class _ContractError(ValueError):
    """An approved stage field is missing, malformed, unknown, or unexpected."""


def _unknown_child(unknown: Any, key: str, context: str) -> Any:
    """Return an after_unknown child while rejecting malformed ancestors."""

    if unknown is _MISSING or unknown is False:
        return _MISSING
    if unknown is True:
        raise _ContractError(f"{context} is unknown")
    if type(unknown) is not dict:
        raise _ContractError(f"{context} has malformed after_unknown metadata")
    return unknown.get(key, _MISSING)


def _known_scalar_marker(marker: Any, context: str) -> None:
    if marker is _MISSING or marker is False:
        return
    if marker is True:
        raise _ContractError(f"{context} is unknown")
    raise _ContractError(f"{context} has malformed after_unknown metadata")


def _require_scalar(
    values: dict[str, Any],
    unknown: Any,
    key: str,
    expected: str | int | bool,
    context: str,
) -> None:
    field = f"{context}.{key}"
    if key not in values:
        raise _ContractError(f"{field} is missing")

    _known_scalar_marker(_unknown_child(unknown, key, context), field)
    actual = values[key]
    if type(actual) is not type(expected) or actual != expected:
        raise _ContractError(f"{field} must equal {expected!r}")


def _normalize_unknown_block(marker: Any, context: str) -> dict[str, Any]:
    if marker is _MISSING or marker is False:
        return {}
    if marker is True:
        raise _ContractError(f"{context} is unknown")
    if (
        type(marker) is list
        and len(marker) == 1
        and type(marker[0]) is dict
    ):
        return marker[0]
    raise _ContractError(f"{context} has malformed after_unknown metadata")


def _require_single_block(
    values: dict[str, Any], unknown: Any, key: str, context: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    field = f"{context}.{key}"
    if key not in values:
        raise _ContractError(f"{field} is missing")

    block = values[key]
    if not (
        type(block) is list
        and len(block) == 1
        and type(block[0]) is dict
    ):
        raise _ContractError(f"{field} must be exactly one object")

    unknown_block = _normalize_unknown_block(
        _unknown_child(unknown, key, context), field
    )
    return block[0], unknown_block


def _require_exact_tags(
    values: dict[str, Any], unknown: Any, context: str
) -> None:
    field = f"{context}.tags"
    if "tags" not in values:
        raise _ContractError(f"{field} is missing")

    tags = values["tags"]
    if (
        type(tags) is not list
        or any(type(tag) is not str for tag in tags)
        or len(tags) != len(OPENCLAW_STAGE_TAGS)
        or frozenset(tags) != OPENCLAW_STAGE_TAGS
    ):
        raise _ContractError(
            f"{field} must contain exactly {sorted(OPENCLAW_STAGE_TAGS)!r}"
        )

    marker = _unknown_child(unknown, "tags", context)
    if marker is _MISSING or marker is False or marker == []:
        return
    if marker is True or (
        type(marker) is list and any(value is True for value in marker)
    ):
        raise _ContractError(f"{field} is unknown")
    if not (
        type(marker) is list
        and len(marker) == len(tags)
        and all(value is False for value in marker)
    ):
        raise _ContractError(f"{field} has malformed after_unknown metadata")


def _openclaw_stage_contract_errors(resource: dict[str, Any]) -> list[str]:
    """Validate the known create-time values for the dedicated OpenClaw LXC."""

    errors: list[str] = []
    change = resource.get("change")
    if type(change) is not dict:
        return ["change is missing or malformed"]

    after = change.get("after", _MISSING)
    if type(after) is not dict:
        return ["change.after is missing or malformed"]

    after_unknown = change.get("after_unknown", {})
    if after_unknown is True:
        return ["change.after is unknown"]
    if type(after_unknown) is not dict:
        return ["change.after_unknown is malformed"]

    def check(function: Any, *args: Any) -> None:
        try:
            function(*args)
        except _ContractError as exc:
            errors.append(str(exc))

    for key, expected in (
        ("vm_id", 118),
        ("unprivileged", True),
        ("started", True),
        ("start_on_boot", True),
    ):
        check(_require_scalar, after, after_unknown, key, expected, "change.after")
    check(_require_exact_tags, after, after_unknown, "change.after")

    try:
        initialization, initialization_unknown = _require_single_block(
            after, after_unknown, "initialization", "change.after"
        )
        check(
            _require_scalar,
            initialization,
            initialization_unknown,
            "hostname",
            "openclaw",
            "change.after.initialization",
        )
        ip_config, ip_config_unknown = _require_single_block(
            initialization,
            initialization_unknown,
            "ip_config",
            "change.after.initialization",
        )
        ipv4, ipv4_unknown = _require_single_block(
            ip_config,
            ip_config_unknown,
            "ipv4",
            "change.after.initialization.ip_config",
        )
        check(
            _require_scalar,
            ipv4,
            ipv4_unknown,
            "address",
            "192.168.0.5/24",
            "change.after.initialization.ip_config.ipv4",
        )
        check(
            _require_scalar,
            ipv4,
            ipv4_unknown,
            "gateway",
            "192.168.0.1",
            "change.after.initialization.ip_config.ipv4",
        )
    except _ContractError as exc:
        errors.append(str(exc))

    try:
        network, network_unknown = _require_single_block(
            after, after_unknown, "network_interface", "change.after"
        )
        check(
            _require_scalar,
            network,
            network_unknown,
            "name",
            "veth0",
            "change.after.network_interface",
        )
        check(
            _require_scalar,
            network,
            network_unknown,
            "mac_address",
            "02:00:00:BA:EC:05",
            "change.after.network_interface",
        )
    except _ContractError as exc:
        errors.append(str(exc))

    for block_name, fields in (
        ("cpu", (("cores", 4),)),
        ("memory", (("dedicated", 4096), ("swap", 1024))),
        ("disk", (("size", 32),)),
        ("startup", (("order", 3),)),
        (
            "operating_system",
            (("type", "debian"), ("template_file_id", OPENCLAW_TEMPLATE_FILE_ID)),
        ),
    ):
        try:
            block, block_unknown = _require_single_block(
                after, after_unknown, block_name, "change.after"
            )
            for key, expected in fields:
                check(
                    _require_scalar,
                    block,
                    block_unknown,
                    key,
                    expected,
                    f"change.after.{block_name}",
                )
        except _ContractError as exc:
            errors.append(str(exc))

    return errors


def _is_approved_openclaw_stage_target(resource: dict[str, Any]) -> bool:
    return (
        resource.get("address") == OPENCLAW_STAGE_ADDRESS
        and _actions(resource) == ["create"]
        and not _openclaw_stage_contract_errors(resource)
    )


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
        if not address.startswith("module.target_lxc[") or not address.endswith(LXC_RESOURCE_SUFFIX):
            continue
        if _actions(resource) == ["create"]:
            create_only.append(address)
    return create_only


def _expected_lxc_count() -> int:
    configured = os.environ.get("TOFU_EXPECTED_LXC_COUNT")
    if configured:
        return int(configured)
    return expected_lxc_count()


def _stage_only_unapproved_changes(plan: dict[str, Any]) -> list[str]:
    """Return every action outside the one-time additive OpenClaw stage."""

    unapproved = []
    for resource in _resource_changes(plan):
        actions = _actions(resource)
        if actions == ["no-op"] or _is_approved_openclaw_stage_target(resource):
            continue
        address = resource.get("address", "<unknown>")
        rendered_actions = ",".join(actions) if actions else "<missing>"
        if address == OPENCLAW_STAGE_ADDRESS and actions == ["create"]:
            details = "; ".join(_openclaw_stage_contract_errors(resource))
            unapproved.append(
                f"{address}: {rendered_actions} (invalid contract: {details})"
            )
        else:
            unapproved.append(f"{address}: {rendered_actions}")
    return unapproved


def main(argv: list[str]) -> int:
    plan = _load_plan(argv)

    if _truthy(os.environ.get("OPENCLAW_NATIVE_STAGE_ONLY")):
        unapproved_stage_changes = _stage_only_unapproved_changes(plan)
        if unapproved_stage_changes:
            print(
                "OpenTofu plan contains changes outside the additive OpenClaw stage:",
                file=sys.stderr,
            )
            for change in unapproved_stage_changes:
                print(f"- {change}", file=sys.stderr)
            print(
                "Refusing to modify or create any resource except the exact VMID 118 "
                "OpenClaw LXC while OPENCLAW_NATIVE_STAGE_ONLY is set.",
                file=sys.stderr,
            )
            return 1

    approved_additive_targets = [
        resource.get("address", "<unknown>")
        for resource in _resource_changes(plan)
        if _is_approved_additive_target(resource)
    ]
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

    for address in approved_additive_targets:
        vmid = APPROVED_ADDITIVE_TARGETS[address]
        print(f"Approved additive dedicated OpenClaw target: {address} -> {vmid}.")

    create_only_lxcs = _create_only_lxc_changes(plan)
    invalid_additive_targets = [
        resource.get("address", "<unknown>")
        for resource in _resource_changes(plan)
        if resource.get("address") in APPROVED_ADDITIVE_TARGETS
        and _actions(resource) == ["create"]
        and not _is_approved_additive_target(resource)
    ]
    if invalid_additive_targets:
        print("OpenTofu plan uses an unexpected VMID for the dedicated OpenClaw target:", file=sys.stderr)
        for address in invalid_additive_targets:
            print(f"- {address}: expected VMID {APPROVED_ADDITIVE_TARGETS[address]}", file=sys.stderr)
        return 1

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

    if _truthy(os.environ.get("OPENCLAW_NATIVE_STAGE_ONLY")):
        print(
            "OpenTofu plan safety check passed: additive OpenClaw stage only."
        )
    else:
        print("OpenTofu plan safety check passed: no unapproved destructive actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
