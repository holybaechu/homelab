#!/usr/bin/env python3
"""Render prod Ansible inventory from OpenTofu container topology."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY = ROOT / "infra" / "opentofu" / "envs" / "prod" / "containers.auto.tfvars"
INVENTORY = ROOT / "infra" / "ansible" / "inventory" / "prod" / "hosts.yml"
SERVICE_ORDER = ["edge", "dns", "tailnet", "downloads", "files", "minecraft", "hermes"]


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
        }
    missing = [name for name in SERVICE_ORDER if name not in containers]
    if missing:
        raise ValueError(f"missing containers: {', '.join(missing)}")
    return containers


def render_inventory(containers: dict[str, dict[str, Any]]) -> str:
    lines = [
        "all:",
        "  vars:",
        "    ansible_user: root",
        "  children:",
        "    pve_hosts:",
        "      hosts:",
        "        pve:",
        "          ansible_host: 192.168.0.2",
    ]
    for os_type in ("alpine", "debian"):
        lines += [f"    {os_type}:", "      hosts:"]
        for name in SERVICE_ORDER:
            if containers[name]["os_type"] == os_type:
                lines += [f"        {name}:", f"          ansible_host: {containers[name]['ip_address']}"]
    for name in SERVICE_ORDER:
        lines += [f"    svc_{name}:", "      hosts:", f"        {name}:"]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if committed inventory differs")
    parser.add_argument("--output", type=Path, help="write rendered inventory to this path")
    args = parser.parse_args(argv[1:])

    rendered = render_inventory(load_containers())
    if args.check:
        current = INVENTORY.read_text(encoding="utf-8")
        if current != rendered:
            print("Ansible inventory is not in sync with containers.auto.tfvars", file=sys.stderr)
            return 1
        return 0
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
