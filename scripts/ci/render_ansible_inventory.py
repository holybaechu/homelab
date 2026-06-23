#!/usr/bin/env python3
"""Render prod Ansible inventory from OpenTofu container topology."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from homelab_topology import ROOT, load_containers, service_names

INVENTORY = ROOT / "infra" / "ansible" / "inventory" / "prod" / "hosts.yml"


def render_inventory(containers: dict[str, dict[str, object]]) -> str:
    names = service_names(containers)
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
        for name in names:
            if containers[name]["os_type"] == os_type:
                lines += [f"        {name}:", f"          ansible_host: {containers[name]['ip_address']}"]
    for name in names:
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
