#!/usr/bin/env python3
"""Render service:group target pairs for parallel Ansible deploys."""

from __future__ import annotations

import sys

from homelab_topology import load_containers, service_names


def render_targets() -> str:
    return " ".join(f"{name}:svc_{name}" for name in service_names(load_containers()))


def main() -> int:
    sys.stdout.write(render_targets())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
