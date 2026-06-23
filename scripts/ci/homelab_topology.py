#!/usr/bin/env python3
"""Shared helpers for the committed homelab container topology."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY = ROOT / "infra" / "opentofu" / "envs" / "prod" / "containers.auto.tfvars"


def _parse_value(body: str, key: str) -> str:
    match = re.search(rf'^\s*{key}\s*=\s*"?([^"\n]+)"?\s*$', body, re.MULTILINE)
    if not match:
        raise ValueError(f"missing {key} in container block")
    return match.group(1)


def load_containers(path: Path = TOPOLOGY) -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    containers: dict[str, dict[str, Any]] = {}
    for match in re.finditer(
        r"^\s{2}([a-z0-9_-]+)\s*=\s*\{(.*?)^\s{2}\}",
        text,
        re.MULTILINE | re.DOTALL,
    ):
        name, body = match.groups()
        containers[name] = {
            "hostname": _parse_value(body, "hostname"),
            "os_type": _parse_value(body, "os_type"),
            "ip_address": _parse_value(body, "ip_address").split("/", 1)[0],
            "startup_order": int(_parse_value(body, "startup_order")),
        }

    if not containers:
        raise ValueError(f"no containers found in {path}")

    duplicate_orders = sorted(
        order
        for order in {container["startup_order"] for container in containers.values()}
        if sum(1 for container in containers.values() if container["startup_order"] == order) > 1
    )
    if duplicate_orders:
        raise ValueError(f"duplicate startup_order values: {duplicate_orders}")

    return containers


def service_names(containers: dict[str, dict[str, Any]] | None = None) -> list[str]:
    containers = containers or load_containers()
    return sorted(containers, key=lambda name: containers[name]["startup_order"])


def expected_lxc_count(path: Path = TOPOLOGY) -> int:
    return len(load_containers(path))
