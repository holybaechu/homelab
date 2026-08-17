#!/usr/bin/env python3
"""Fail closed when a dedicated OpenClaw-related LXC allocation is not safe."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import ipaddress
import json
from pathlib import Path
import re
import select
import socket
import struct
import subprocess
import sys
import time
from typing import Callable


class PreflightError(RuntimeError):
    """An allocation conflict or an inability to prove safety."""


@dataclass(frozen=True)
class Allocation:
    vmid: int
    hostname: str
    ip_address: ipaddress.IPv4Interface
    mac_address: str
    datastore_id: str
    required_storage_bytes: int
    role_tag: str = "role-openclaw"
    required_features: frozenset[str] = field(default_factory=frozenset)
    expected_bind_mounts: tuple[str, ...] = ()
    allow_missing_expected_bind_mounts: bool = False
    transitional_bind_mounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuestConfig:
    path: Path
    kind: str
    vmid: int
    text: str


def normalize_mac(value: str) -> str:
    compact = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise ValueError(f"invalid MAC address: {value!r}")
    octets = [compact[index : index + 2] for index in range(0, 12, 2)]
    return ":".join(octets).upper()


def parse_allocation(args: argparse.Namespace) -> Allocation:
    if args.vmid < 100:
        raise ValueError("VMID must be at least 100")
    ip_interface = ipaddress.ip_interface(args.ip_address)
    if not isinstance(ip_interface, ipaddress.IPv4Interface):
        raise ValueError("the LXC must use an IPv4 allocation")
    mac_address = normalize_mac(args.mac_address)
    first_octet = int(mac_address.split(":", 1)[0], 16)
    if first_octet & 1:
        raise ValueError("LXC MAC address must be unicast")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", args.hostname):
        raise ValueError("LXC hostname is not a valid DNS label")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.datastore_id):
        raise ValueError("LXC datastore ID is invalid")
    if args.required_storage_gb <= 0:
        raise ValueError("LXC required storage must be greater than zero")
    if not re.fullmatch(r"role-[a-z0-9-]+", args.role_tag):
        raise ValueError("LXC role tag is invalid")
    required_features = frozenset(args.required_feature)
    unsupported_features = required_features - {"nesting", "keyctl"}
    if unsupported_features:
        raise ValueError(
            "unsupported required LXC feature(s): "
            + ", ".join(sorted(unsupported_features))
        )
    def parse_bind_mounts(values: list[str], label: str) -> tuple[str, ...]:
        bind_mounts = tuple(sorted(values))
        if len(set(bind_mounts)) != len(bind_mounts):
            raise ValueError(f"{label} bind mounts must not contain duplicates")
        for bind_mount in bind_mounts:
            source, separator, destination = bind_mount.partition(",mp=")
            if (
                separator != ",mp="
                or not source.startswith("/")
                or not destination.startswith("/")
                or "," in destination
            ):
                raise ValueError(
                    f"{label} bind mounts must use /source,mp=/destination syntax"
                )
        return bind_mounts

    expected_bind_mounts = parse_bind_mounts(
        args.expected_bind_mount, "expected"
    )
    transitional_bind_mounts = parse_bind_mounts(
        args.transitional_bind_mount, "transitional"
    )
    if transitional_bind_mounts == expected_bind_mounts and transitional_bind_mounts:
        raise ValueError("transitional bind mounts must differ from the expected set")
    if args.allow_missing_expected_bind_mounts and not expected_bind_mounts:
        raise ValueError(
            "allow_missing_expected_bind_mounts requires an expected bind mount"
        )
    return Allocation(
        args.vmid,
        args.hostname,
        ip_interface,
        mac_address,
        args.datastore_id,
        args.required_storage_gb * 1024**3,
        args.role_tag,
        required_features,
        expected_bind_mounts,
        args.allow_missing_expected_bind_mounts,
        transitional_bind_mounts,
    )


def load_guest_configs(config_root: Path) -> list[GuestConfig]:
    if not config_root.is_dir():
        raise PreflightError(f"Proxmox config root is unavailable: {config_root}")
    nodes_root = config_root / "nodes"
    if not nodes_root.is_dir():
        raise PreflightError(f"Proxmox cluster config is unavailable: {nodes_root}")

    candidates: list[tuple[str, Path]] = []
    for kind in ("lxc", "qemu-server"):
        candidates.extend((kind, path) for path in nodes_root.glob(f"*/{kind}/*.conf"))

    configs: list[GuestConfig] = []
    seen: set[str] = set()
    for kind, path in sorted(candidates, key=lambda item: str(item[1])):
        try:
            resolved = str(path.resolve(strict=True))
        except OSError as exc:
            raise PreflightError(f"cannot resolve Proxmox guest config {path}: {exc}") from exc
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            vmid = int(path.stem)
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            raise PreflightError(f"cannot inspect Proxmox guest config {path}: {exc}") from exc
        configs.append(GuestConfig(path, kind, vmid, text))
    return configs


def parse_lxc_fields(config: GuestConfig) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in config.text.splitlines():
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def parse_net_values(value: str) -> dict[str, str]:
    return {
        key.strip(): item_value.strip()
        for item in value.split(",")
        if "=" in item
        for key, item_value in [item.split("=", 1)]
    }


def validate_existing_target(config: GuestConfig, allocation: Allocation) -> None:
    if config.kind != "lxc":
        raise PreflightError(
            f"VMID {allocation.vmid} is occupied by a QEMU VM at {config.path}"
        )

    fields = parse_lxc_fields(config)
    expected = {
        "hostname": allocation.hostname,
        "unprivileged": "1",
    }
    for key, value in expected.items():
        if fields.get(key) != value:
            raise PreflightError(
                f"VMID {allocation.vmid} has unexpected {key}: "
                f"{fields.get(key)!r} (expected {value!r})"
            )

    net0 = parse_net_values(fields.get("net0", ""))
    expected_ip = str(allocation.ip_address)
    actual_mac = normalize_mac(net0.get("hwaddr", "")) if net0.get("hwaddr") else ""
    if net0.get("ip") != expected_ip or actual_mac != allocation.mac_address:
        raise PreflightError(
            f"VMID {allocation.vmid} does not own the exact OpenClaw network identity"
        )

    tags = {
        tag.strip()
        for tag in re.split(r"[;,]", fields.get("tags", ""))
        if tag.strip()
    }
    required_tags = {"managed-by-opentofu", allocation.role_tag}
    if not required_tags.issubset(tags):
        role_label = (
            "OpenClaw" if allocation.role_tag == "role-openclaw" else allocation.role_tag
        )
        raise PreflightError(
            f"VMID {allocation.vmid} is missing its managed {role_label} tags"
        )
    rootfs = fields.get("rootfs", "")
    root_volume = (
        parse_net_values(rootfs).get("volume", "")
        if rootfs.startswith("volume=")
        else rootfs.split(",", 1)[0]
    )
    if not root_volume.startswith(f"{allocation.datastore_id}:"):
        raise PreflightError(
            f"VMID {allocation.vmid} has root volume {root_volume!r}, not "
            f"datastore {allocation.datastore_id!r}"
        )

    feature_values = parse_net_values(fields.get("features", ""))
    enabled_features = frozenset(
        name for name in ("nesting", "keyctl") if feature_values.get(name) == "1"
    )
    if enabled_features != allocation.required_features:
        if not allocation.required_features:
            detail = "forbidden features: " + ", ".join(sorted(enabled_features))
        else:
            detail = (
                "unexpected feature set: "
                + ", ".join(sorted(enabled_features))
                + " (expected "
                + ", ".join(sorted(allocation.required_features))
                + ")"
            )
        raise PreflightError(
            f"VMID {allocation.vmid} has {detail}"
        )
    actual_bind_mounts = tuple(
        sorted(
            value
            for key, value in fields.items()
            if re.fullmatch(r"mp[0-9]+", key)
        )
    )
    valid_transitional_set = (
        bool(allocation.transitional_bind_mounts)
        and actual_bind_mounts == allocation.transitional_bind_mounts
    )
    if actual_bind_mounts != allocation.expected_bind_mounts and not valid_transitional_set:
        if not (
            allocation.allow_missing_expected_bind_mounts
            and len(actual_bind_mounts) == len(set(actual_bind_mounts))
            and set(actual_bind_mounts).issubset(allocation.expected_bind_mounts)
        ):
            if not allocation.expected_bind_mounts:
                detail = "a forbidden bind mount"
            else:
                detail = "an unexpected bind mount set"
            raise PreflightError(f"VMID {allocation.vmid} has {detail}")
    if any(
        re.fullmatch(r"dev[0-9]+", key) and "/dev/net/tun" in value
        for key, value in fields.items()
    ):
        raise PreflightError(f"VMID {allocation.vmid} has forbidden TUN passthrough")


def find_config_claims(
    configs: list[GuestConfig], allocation: Allocation, excluded: set[Path]
) -> list[Path]:
    target_ip = str(allocation.ip_address.ip)
    ip_pattern = re.compile(
        rf"(?<![0-9.]){re.escape(target_ip)}(?:/[0-9]{{1,2}})?(?![0-9.])"
    )
    mac_pattern = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")
    claims: list[Path] = []
    excluded_resolved = {path.resolve() for path in excluded}
    for config in configs:
        if config.path.resolve() in excluded_resolved:
            continue
        network_values = []
        for line in config.text.splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split(":", 1)
            if re.fullmatch(r"(?:net|ipconfig)[0-9]+", key.strip()):
                network_values.append(value)
        network_text = "\n".join(network_values)
        ip_claimed = bool(ip_pattern.search(network_text))
        mac_claimed = any(
            normalize_mac(match.group(0)) == allocation.mac_address
            for match in mac_pattern.finditer(network_text)
        )
        if ip_claimed or mac_claimed:
            claims.append(config.path)
    return claims


def probe_storage(datastore_id: str, required_bytes: int) -> dict[str, object]:
    """Prove the configured container datastore is active and has enough space."""
    # `pvesm status` has a command-specific text formatter and does not accept
    # the otherwise common `--output-format` option on supported PVE releases.
    # Query the corresponding local-node API endpoint through pvesh instead.
    # The per-datastore endpoint returns byte counts and the configuration
    # fields needed to prove that the exact store is enabled for LXC rootfs use.
    status_path = f"/nodes/localhost/storage/{datastore_id}/status"
    try:
        result = subprocess.run(
            [
                "pvesh",
                "get",
                status_path,
                "--output-format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"cannot inspect datastore {datastore_id!r}: {exc}") from exc
    if result.returncode != 0:
        raise PreflightError(
            f"cannot inspect datastore {datastore_id!r}: {result.stderr.strip()}"
        )
    try:
        payload = json.loads(result.stdout)
        row = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        if not isinstance(row, dict):
            raise ValueError("expected one storage status object")
        reported_id = row.get("storage", datastore_id)
        if reported_id != datastore_id:
            raise ValueError(f"unexpected datastore ID {reported_id!r}")
        active = row.get("active")
        if active not in (True, 1, "1", "true", "yes", "active"):
            raise ValueError("datastore is not active")
        enabled = row.get("enabled")
        if enabled not in (True, 1, "1", "true", "yes", "enabled"):
            raise ValueError("datastore is not enabled")
        content = row.get("content")
        if not isinstance(content, str):
            raise ValueError("invalid datastore content types")
        content_types = {item.strip() for item in content.split(",") if item.strip()}
        if "rootdir" not in content_types:
            raise ValueError("datastore does not allow rootdir content")
        available_value = row.get("avail", row.get("available"))
        if isinstance(available_value, bool):
            raise ValueError("invalid available byte count")
        available_bytes = int(available_value)
        if available_bytes < 0:
            raise ValueError("invalid available byte count")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PreflightError(
            f"unexpected status for rootdir datastore {datastore_id!r}: {exc}"
        ) from exc

    if available_bytes < required_bytes:
        raise PreflightError(
            f"datastore {datastore_id!r} has {available_bytes / 1024**3:.2f} GiB "
            f"available; {required_bytes / 1024**3:.2f} GiB is required"
        )
    return {
        "datastore_id": datastore_id,
        "available_bytes": available_bytes,
        "required_additional_bytes": required_bytes,
    }


def route_interface(target_ip: ipaddress.IPv4Address) -> str:
    try:
        result = subprocess.run(
            ["ip", "-json", "route", "get", str(target_ip)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"cannot resolve the LAN interface for {target_ip}: {exc}") from exc
    if result.returncode != 0:
        raise PreflightError(
            f"cannot resolve the LAN interface for {target_ip}: {result.stderr.strip()}"
        )
    try:
        routes = json.loads(result.stdout)
        interface = routes[0]["dev"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise PreflightError(f"unexpected ip route output for {target_ip}") from exc
    if not isinstance(interface, str) or not interface:
        raise PreflightError(f"no LAN interface found for {target_ip}")
    return interface


def _parse_arp_sender(frame: bytes, target_ip: ipaddress.IPv4Address) -> str | None:
    if len(frame) < 42 or frame[12:14] != b"\x08\x06":
        return None
    try:
        hardware, protocol, hardware_len, protocol_len, _opcode, sender_mac, sender_ip, _, _ = (
            struct.unpack("!HHBBH6s4s6s4s", frame[14:42])
        )
    except struct.error:
        return None
    if (hardware, protocol, hardware_len, protocol_len) != (1, 0x0800, 6, 4):
        return None
    if sender_ip != target_ip.packed:
        return None
    return normalize_mac(sender_mac.hex())


def probe_arp(target_ip: ipaddress.IPv4Address) -> tuple[str, set[str]]:
    interface = route_interface(target_ip)
    mac_path = Path("/sys/class/net") / interface / "address"
    try:
        source_mac_text = mac_path.read_text(encoding="ascii").strip()
        source_mac = bytes.fromhex(normalize_mac(source_mac_text).replace(":", ""))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PreflightError(f"cannot read MAC address for {interface}: {exc}") from exc

    ethernet = b"\xff" * 6 + source_mac + struct.pack("!H", 0x0806)
    arp_request = struct.pack(
        "!HHBBH6s4s6s4s",
        1,
        0x0800,
        6,
        4,
        1,
        source_mac,
        b"\x00" * 4,
        b"\x00" * 6,
        target_ip.packed,
    )
    frame = ethernet + arp_request
    responders: set[str] = set()

    try:
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806)) as sock:
            sock.bind((interface, 0))
            sock.setblocking(False)
            for _ in range(3):
                sock.send(frame)
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    ready, _, _ = select.select(
                        [sock], [], [], max(0.0, deadline - time.monotonic())
                    )
                    if not ready:
                        break
                    sender = _parse_arp_sender(sock.recv(65535), target_ip)
                    if sender:
                        responders.add(sender)
    except (OSError, PermissionError) as exc:
        raise PreflightError(f"ARP allocation probe failed on {interface}: {exc}") from exc
    return interface, responders


Probe = Callable[[ipaddress.IPv4Address], tuple[str, set[str]]]
StorageProbe = Callable[[str, int], dict[str, object]]


def preflight(
    allocation: Allocation,
    config_root: Path,
    probe: Probe = probe_arp,
    storage_probe: StorageProbe = probe_storage,
) -> dict[str, object]:
    configs = load_guest_configs(config_root)
    target_configs = [config for config in configs if config.vmid == allocation.vmid]
    if len(target_configs) > 1:
        paths = ", ".join(str(config.path) for config in target_configs)
        raise PreflightError(f"VMID {allocation.vmid} appears more than once: {paths}")

    existing = target_configs[0] if target_configs else None
    if existing:
        validate_existing_target(existing, allocation)

    storage = storage_probe(
        allocation.datastore_id,
        0 if existing is not None else allocation.required_storage_bytes,
    )

    claims = find_config_claims(
        configs, allocation, {existing.path} if existing is not None else set()
    )
    if claims:
        raise PreflightError(
            "OpenClaw IP or MAC is already claimed by: "
            + ", ".join(str(path) for path in claims)
        )

    interface, responders = probe(allocation.ip_address.ip)
    if existing is None and responders:
        raise PreflightError(
            f"OpenClaw IP {allocation.ip_address.ip} answered ARP from "
            + ", ".join(sorted(responders))
        )
    unexpected = responders - {allocation.mac_address}
    if existing is not None and unexpected:
        raise PreflightError(
            f"OpenClaw IP {allocation.ip_address.ip} has unexpected ARP responder(s): "
            + ", ".join(sorted(unexpected))
        )

    return {
        "status": "existing-managed-target" if existing else "allocation-available",
        "vmid": allocation.vmid,
        "ip_address": str(allocation.ip_address),
        "mac_address": allocation.mac_address,
        "interface": interface,
        "arp_responders": sorted(responders),
        "storage": storage,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vmid", required=True, type=int)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--ip-address", required=True)
    parser.add_argument("--mac-address", required=True)
    parser.add_argument("--datastore-id", required=True)
    parser.add_argument("--required-storage-gb", required=True, type=int)
    parser.add_argument("--role-tag", default="role-openclaw")
    parser.add_argument("--required-feature", action="append", default=[])
    parser.add_argument("--expected-bind-mount", action="append", default=[])
    parser.add_argument("--transitional-bind-mount", action="append", default=[])
    parser.add_argument("--allow-missing-expected-bind-mounts", action="store_true")
    parser.add_argument("--config-root", type=Path, default=Path("/etc/pve"))
    return parser


def main(argv: list[str]) -> int:
    try:
        args = build_parser().parse_args(argv[1:])
        allocation = parse_allocation(args)
        result = preflight(allocation, args.config_root)
    except (PreflightError, ValueError) as exc:
        print(f"OpenClaw LXC allocation preflight failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
