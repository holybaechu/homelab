#!/usr/bin/env python3
"""Plan, audit, and reconcile the three topology-owned Proxmox LXCs.

The live Proxmox configuration is the only runtime state.  A normal apply may
create a missing container, grow a root disk, and change fields that Proxmox can
reconcile without discarding the root filesystem.  Removing mounts/devices or
recreating a container requires an exact VMID confirmation on the command line.
Every apply exports the pre-mutation ``pct config`` output first.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence


class ReconcileError(RuntimeError):
    """The requested reconciliation cannot proceed safely."""


MANAGED_HOSTS_PATH = ("all", "children", "debian", "hosts")
VALID_UNITS = {"tailnet", "apps-host", "openclaw-host"}
SAFE_FIELDS = {
    "cores",
    "memory",
    "swap",
    "onboot",
    "startup",
    "description",
    "tags",
    "nameserver",
    "searchdomain",
}
RESTART_FIELDS = {"hostname", "net0", "features"}
REPLACEMENT_FIELDS = {"ostype", "unprivileged", "rootfs_datastore"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEVICE_RE = re.compile(r"^dev[0-9]+$")
MOUNT_RE = re.compile(r"^mp[0-9]+$")
MAC_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
SIZE_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>[KMGT]?)$", re.I)


@dataclass(frozen=True)
class Change:
    field: str
    before: object
    after: object
    risk: str
    operation: str = "set"

    def as_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "risk": self.risk,
            "operation": self.operation,
        }


@dataclass(frozen=True)
class ContainerPlan:
    name: str
    vmid: int
    exists: bool
    changes: tuple[Change, ...]

    @property
    def changed(self) -> bool:
        return not self.exists or bool(self.changes)

    @property
    def risks(self) -> set[str]:
        return {change.risk for change in self.changes}

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "vmid": self.vmid,
            "exists": self.exists,
            "changed": self.changed,
            "changes": [change.as_dict() for change in self.changes],
        }


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReconcileError(f"{label} must be an object")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconcileError(f"{label} must be a non-empty string")
    return value.strip()


def _require_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ReconcileError(f"{label} must be an integer >= {minimum}")
    return value


def topology_from_document(
    topology: object,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    topology = _require_dict(topology, "topology")
    cursor: object = topology
    for component in MANAGED_HOSTS_PATH:
        parent = _require_dict(cursor, ".".join(MANAGED_HOSTS_PATH))
        if component not in parent:
            raise ReconcileError(
                f"topology lacks {'.'.join(MANAGED_HOSTS_PATH)}"
            )
        cursor = parent[component]
    hosts = _require_dict(cursor, "managed LXC hosts")
    all_vars = _require_dict(topology["all"].get("vars"), "all.vars")
    validate_topology(all_vars, hosts)
    return all_vars, hosts


def load_topology(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        topology = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconcileError(f"cannot load topology {path}: {exc}") from exc

    return topology_from_document(topology)


def validate_topology(
    all_vars: Mapping[str, object], hosts: Mapping[str, Mapping[str, object]]
) -> None:
    if set(hosts) != {"tailnet", "docker_apps", "openclaw"}:
        raise ReconcileError("topology must contain exactly tailnet, docker_apps, and openclaw")

    bridge = _require_string(all_vars.get("pve_bridge"), "all.vars.pve_bridge")
    datastore = _require_string(
        all_vars.get("pve_root_datastore_id"), "all.vars.pve_root_datastore_id"
    )
    if not KEY_RE.fullmatch(bridge) or not re.fullmatch(r"[A-Za-z0-9_.-]+", datastore):
        raise ReconcileError("PVE bridge or root datastore has an invalid identifier")
    nameservers = all_vars.get("pve_lxc_nameservers")
    if not isinstance(nameservers, list) or not nameservers:
        raise ReconcileError("all.vars.pve_lxc_nameservers must be a non-empty list")
    for address in nameservers:
        ipaddress.ip_address(_require_string(address, "PVE LXC nameserver"))
    _require_string(all_vars.get("pve_lxc_searchdomain"), "all.vars.pve_lxc_searchdomain")

    unique: dict[str, set[object]] = {
        field: set()
        for field in ("vmid", "hostname", "ansible_host", "mac_address", "startup_order")
    }
    for name, raw_host in hosts.items():
        host = _require_dict(raw_host, f"host {name}")
        vmid = _require_int(host.get("vmid"), f"{name}.vmid", minimum=100)
        unit = _require_string(host.get("deployment_unit"), f"{name}.deployment_unit")
        expected_unit = {
            "tailnet": "tailnet",
            "docker_apps": "apps-host",
            "openclaw": "openclaw-host",
        }[name]
        if unit != expected_unit or unit not in VALID_UNITS:
            raise ReconcileError(f"{name}.deployment_unit must be {expected_unit}")
        hostname = _require_string(host.get("hostname"), f"{name}.hostname")
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname) is None:
            raise ReconcileError(f"{name}.hostname is invalid")
        _require_string(host.get("description"), f"{name}.description")
        address = _require_string(host.get("ansible_host"), f"{name}.ansible_host")
        prefix = _require_int(host.get("prefix_length"), f"{name}.prefix_length", minimum=1)
        if prefix > 32 or ipaddress.ip_address(address).version != 4:
            raise ReconcileError(f"{name} must use a valid IPv4 address and prefix")
        gateway = ipaddress.ip_address(
            _require_string(host.get("gateway"), f"{name}.gateway")
        )
        if gateway.version != 4:
            raise ReconcileError(f"{name}.gateway must be IPv4")
        mac = _require_string(host.get("mac_address"), f"{name}.mac_address").upper()
        if not MAC_RE.fullmatch(mac):
            raise ReconcileError(f"{name}.mac_address is invalid")
        order = _require_int(host.get("startup_order"), f"{name}.startup_order", minimum=1)
        for field, value in (
            ("vmid", vmid),
            ("hostname", hostname),
            ("ansible_host", address),
            ("mac_address", mac),
            ("startup_order", order),
        ):
            if value in unique[field]:
                raise ReconcileError(f"duplicate {field}: {value}")
            unique[field].add(value)

        for field, minimum in (
            ("root_disk_gb", 1),
            ("cores", 1),
            ("memory_mb", 1),
            ("swap_mb", 0),
        ):
            _require_int(host.get(field), f"{name}.{field}", minimum=minimum)
        if host.get("unprivileged") is not True:
            raise ReconcileError(f"{name}.unprivileged must be true")
        if _require_string(host.get("os_type"), f"{name}.os_type") != "debian":
            raise ReconcileError(f"{name}.os_type must be debian")
        _require_string(host.get("template_file_id"), f"{name}.template_file_id")
        tags = host.get("lxc_tags")
        if not isinstance(tags, list) or not tags or any(
            not isinstance(tag, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]+", tag) is None
            for tag in tags
        ):
            raise ReconcileError(f"{name}.lxc_tags must be a non-empty string list")
        features = _require_dict(host.get("lxc_features"), f"{name}.lxc_features")
        if any(not KEY_RE.fullmatch(str(key)) or type(value) is not bool for key, value in features.items()):
            raise ReconcileError(f"{name}.lxc_features must contain boolean feature flags")
        devices = _require_dict(host.get("lxc_devices"), f"{name}.lxc_devices")
        if any(
            not DEVICE_RE.fullmatch(str(key))
            or not isinstance(value, str)
            or not value
            or "\n" in value
            for key, value in devices.items()
        ):
            raise ReconcileError(f"{name}.lxc_devices has an invalid declaration")
        mounts = _require_dict(host.get("lxc_mounts"), f"{name}.lxc_mounts")
        for key, value in mounts.items():
            if not MOUNT_RE.fullmatch(str(key)):
                raise ReconcileError(f"{name}.lxc_mounts has an invalid key")
            mount = _require_dict(value, f"{name}.lxc_mounts.{key}")
            if set(mount) != {"source", "target", "source_owner", "source_group", "source_mode"}:
                raise ReconcileError(f"{name}.lxc_mounts.{key} has unexpected fields")
            source = PurePosixPath(_require_string(mount["source"], f"{name}.{key}.source"))
            target = PurePosixPath(_require_string(mount["target"], f"{name}.{key}.target"))
            if not source.is_absolute() or not target.is_absolute() or ".." in source.parts + target.parts:
                raise ReconcileError(f"{name}.lxc_mounts.{key} paths must be absolute and normalized")
            _require_int(mount["source_owner"], f"{name}.{key}.source_owner")
            _require_int(mount["source_group"], f"{name}.{key}.source_group")
            if re.fullmatch(r"0[0-7]{3}", str(mount["source_mode"])) is None:
                raise ReconcileError(f"{name}.lxc_mounts.{key}.source_mode is invalid")


def parse_pct_config(text: str) -> dict[str, str]:
    config: dict[str, str] = {}
    for raw_line in text.splitlines():
        if not raw_line or raw_line[0].isspace() or ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        if re.fullmatch(r"[a-z][a-z0-9_]*", key):
            config[key] = value.strip()
    return config


def parse_options(value: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for piece in filter(None, (part.strip() for part in value.split(","))):
        key, separator, raw = piece.partition("=")
        options[key] = raw if separator else "1"
    return options


def normalize_tags(value: str | Sequence[str]) -> tuple[str, ...]:
    pieces = value if not isinstance(value, str) else re.split(r"[;,]", value)
    return tuple(sorted(str(piece).strip() for piece in pieces if str(piece).strip()))


def normalize_net(value: str) -> dict[str, str]:
    result = parse_options(value)
    if "hwaddr" in result:
        result["hwaddr"] = result["hwaddr"].upper()
    return result


def size_gib(value: str) -> float:
    match = SIZE_RE.fullmatch(value.strip())
    if match is None:
        raise ReconcileError(f"unsupported Proxmox disk size: {value}")
    number = float(match.group("value"))
    factors = {"": 1 / (1024**3), "K": 1 / (1024**2), "M": 1 / 1024, "G": 1, "T": 1024}
    return number * factors[match.group("unit").upper()]


def desired_config(
    all_vars: Mapping[str, object], host: Mapping[str, object]
) -> dict[str, object]:
    features = {
        str(key): "1" if value else "0"
        for key, value in _require_dict(host["lxc_features"], "lxc_features").items()
    }
    mounts = {
        str(key): f"{value['source']},mp={value['target']}"
        for key, value in _require_dict(host["lxc_mounts"], "lxc_mounts").items()
    }
    return {
        "hostname": host["hostname"],
        "ostype": host["os_type"],
        "unprivileged": "1" if host["unprivileged"] else "0",
        "cores": str(host["cores"]),
        "memory": str(host["memory_mb"]),
        "swap": str(host["swap_mb"]),
        "onboot": "1",
        "startup": f"order={host['startup_order']},up=15,down=15",
        "description": host["description"],
        "tags": normalize_tags(host["lxc_tags"]),
        "nameserver": " ".join(str(value) for value in all_vars["pve_lxc_nameservers"]),
        "searchdomain": all_vars["pve_lxc_searchdomain"],
        "net0": {
            "name": "veth0",
            "type": "veth",
            "bridge": str(all_vars["pve_bridge"]),
            "hwaddr": str(host["mac_address"]).upper(),
            "ip": f"{host['ansible_host']}/{host['prefix_length']}",
            "gw": str(host["gateway"]),
        },
        "features": features,
        "devices": {str(key): str(value) for key, value in host["lxc_devices"].items()},
        "mounts": mounts,
        "rootfs_datastore": str(all_vars["pve_root_datastore_id"]),
        "rootfs_size_gb": int(host["root_disk_gb"]),
    }


def _risk_for_field(field: str) -> str:
    if field in SAFE_FIELDS:
        return "safe"
    if field in RESTART_FIELDS:
        return "restart"
    if field in REPLACEMENT_FIELDS:
        return "replacement"
    raise AssertionError(f"unknown managed field: {field}")


def inspect_mount_sources(host: Mapping[str, object]) -> tuple[Change, ...]:
    """Report topology-owned bind-source metadata without changing the host."""

    changes: list[Change] = []
    mounts = _require_dict(host["lxc_mounts"], "lxc_mounts")
    for key, raw_mount in sorted(mounts.items()):
        mount = _require_dict(raw_mount, f"lxc_mounts.{key}")
        path = Path(str(mount["source"]))
        expected = {
            "owner": int(mount["source_owner"]),
            "group": int(mount["source_group"]),
            "mode": str(mount["source_mode"]),
        }
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            before: object = None
        else:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ReconcileError(
                    f"mount source {path} must be a regular directory, not a symlink or other file"
                )
            before = {
                "owner": metadata.st_uid,
                "group": metadata.st_gid,
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
            }
        if before != expected:
            changes.append(
                Change(
                    f"mount_source:{key}",
                    before,
                    expected,
                    "safe",
                    "reconcile",
                )
            )
    return tuple(changes)


def plan_container(
    name: str,
    host: Mapping[str, object],
    all_vars: Mapping[str, object],
    current_text: str | None,
    mount_changes: Iterable[Change] = (),
) -> ContainerPlan:
    vmid = int(host["vmid"])
    planned_mount_changes = tuple(mount_changes)
    if current_text is None:
        return ContainerPlan(
            name=name,
            vmid=vmid,
            exists=False,
            changes=(
                Change("container", None, "present", "create", "create"),
                *planned_mount_changes,
            ),
        )

    current = parse_pct_config(current_text)
    desired = desired_config(all_vars, host)
    changes: list[Change] = []
    for field in (
        "hostname",
        "ostype",
        "unprivileged",
        "cores",
        "memory",
        "swap",
        "onboot",
        "startup",
        "description",
        "nameserver",
        "searchdomain",
    ):
        before: object = current.get(field, "")
        after = desired[field]
        if field == "startup":
            before = parse_options(str(before))
            after = parse_options(str(after))
        if before != after:
            changes.append(Change(field, before, after, _risk_for_field(field)))

    before_tags = normalize_tags(current.get("tags", ""))
    if before_tags != desired["tags"]:
        changes.append(Change("tags", before_tags, desired["tags"], "safe"))
    before_net = normalize_net(current.get("net0", ""))
    if before_net != desired["net0"]:
        changes.append(Change("net0", before_net, desired["net0"], "restart"))
    before_features = parse_options(current.get("features", ""))
    after_features = desired["features"]
    if before_features != after_features:
        removed = set(before_features).difference(after_features)
        disabled = {
            key for key in set(before_features).intersection(after_features)
            if before_features[key] == "1" and after_features[key] != "1"
        }
        risk = "destructive" if removed or disabled else "restart"
        changes.append(Change("features", before_features, after_features, risk))

    for category, pattern in (("devices", DEVICE_RE), ("mounts", MOUNT_RE)):
        wanted = desired[category]
        observed = {key: value for key, value in current.items() if pattern.fullmatch(key)}
        for key in sorted(set(observed) | set(wanted)):
            before = observed.get(key)
            after = wanted.get(key)
            if before == after:
                continue
            risk = "restart" if before is None else "destructive"
            operation = "delete" if after is None else "set"
            changes.append(Change(key, before, after, risk, operation))

    rootfs = current.get("rootfs", "")
    datastore, separator, details = rootfs.partition(":")
    if not separator:
        changes.append(Change("rootfs_datastore", rootfs, desired["rootfs_datastore"], "replacement"))
    else:
        if datastore != desired["rootfs_datastore"]:
            changes.append(
                Change("rootfs_datastore", datastore, desired["rootfs_datastore"], "replacement")
            )
        size = parse_options(details).get("size")
        if size is None:
            changes.append(Change("rootfs_size", None, desired["rootfs_size_gb"], "replacement"))
        else:
            current_size = size_gib(size)
            wanted_size = float(desired["rootfs_size_gb"])
            if abs(current_size - wanted_size) > 0.001:
                risk = "safe" if wanted_size > current_size else "replacement"
                changes.append(Change("rootfs_size", current_size, wanted_size, risk, "grow"))

    changes.extend(planned_mount_changes)
    return ContainerPlan(name, vmid, True, tuple(changes))


class PctRunner:
    def run(self, argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(argv), check=False, capture_output=True, text=True, encoding="utf-8"
        )
        if check and completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostics"
            raise ReconcileError(f"command failed ({shlex.join(argv)}): {detail}")
        return completed

    def config(self, vmid: int) -> str | None:
        result = self.run(["pct", "config", str(vmid)], check=False)
        if result.returncode == 0:
            return result.stdout
        combined = (result.stderr + result.stdout).lower()
        if "does not exist" in combined or "not exist" in combined:
            return None
        raise ReconcileError(f"cannot inspect VMID {vmid}: {result.stderr.strip()}")

    def running(self, vmid: int) -> bool:
        result = self.run(["pct", "status", str(vmid)])
        return "status: running" in result.stdout


def export_config(export_dir: Path, name: str, vmid: int, current: str | None) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = export_dir / f"{timestamp}-{vmid}-{name}.conf"
    temporary = export_dir / f".{target.name}.tmp"
    temporary.write_text(current if current is not None else "ABSENT\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return target


def _format_options(value: Mapping[str, object]) -> str:
    return ",".join(f"{key}={value[key]}" for key in sorted(value))


def _set_command(vmid: int, change: Change) -> list[str]:
    field = change.field
    if change.operation == "delete":
        return ["pct", "set", str(vmid), "--delete", field]
    value = change.after
    if field in {"startup", "net0", "features"}:
        value = _format_options(value)  # type: ignore[arg-type]
    elif field == "tags":
        value = ";".join(value)  # type: ignore[arg-type]
    return ["pct", "set", str(vmid), f"--{field}", str(value)]


def ensure_mount_sources(host: Mapping[str, object]) -> None:
    mounts = _require_dict(host["lxc_mounts"], "lxc_mounts")
    for key, raw_mount in sorted(mounts.items()):
        mount = _require_dict(raw_mount, f"lxc_mounts.{key}")
        path = Path(str(mount["source"]))
        for parent in path.parents:
            if parent == Path("/"):
                break
            try:
                parent_metadata = parent.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(parent_metadata.st_mode):
                raise ReconcileError(f"mount source parent {parent} must not be a symlink")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            path.mkdir(parents=True, mode=int(str(mount["source_mode"]), 8))
            metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ReconcileError(
                f"mount source {path} must be a regular directory, not a symlink or other file"
            )
        owner = int(mount["source_owner"])
        group = int(mount["source_group"])
        mode = int(str(mount["source_mode"]), 8)
        if metadata.st_uid != owner or metadata.st_gid != group:
            os.chown(path, owner, group)
        if stat.S_IMODE(metadata.st_mode) != mode:
            os.chmod(path, mode)


def create_container(
    runner: PctRunner,
    name: str,
    host: Mapping[str, object],
    all_vars: Mapping[str, object],
) -> None:
    desired = desired_config(all_vars, host)
    vmid = int(host["vmid"])
    ensure_mount_sources(host)
    argv = [
        "pct", "create", str(vmid), str(host["template_file_id"]),
        "--hostname", str(host["hostname"]),
        "--description", str(host["description"]),
        "--ostype", str(host["os_type"]),
        "--unprivileged", "1",
        "--cores", str(host["cores"]),
        "--memory", str(host["memory_mb"]),
        "--swap", str(host["swap_mb"]),
        "--rootfs", f"{desired['rootfs_datastore']}:{host['root_disk_gb']}",
        "--net0", _format_options(desired["net0"]),  # type: ignore[arg-type]
        "--features", _format_options(desired["features"]),  # type: ignore[arg-type]
        "--nameserver", str(desired["nameserver"]),
        "--searchdomain", str(desired["searchdomain"]),
        "--onboot", "1",
        "--startup", _format_options(parse_options(str(desired["startup"]))),
        "--tags", ";".join(desired["tags"]),  # type: ignore[arg-type]
    ]
    for key, value in sorted(desired["devices"].items()):  # type: ignore[union-attr]
        argv.extend((f"--{key}", str(value)))
    for key, value in sorted(desired["mounts"].items()):  # type: ignore[union-attr]
        argv.extend((f"--{key}", str(value)))
    runner.run(argv)
    runner.run(["pct", "start", str(vmid)])


def apply_container(
    runner: PctRunner,
    plan: ContainerPlan,
    host: Mapping[str, object],
    all_vars: Mapping[str, object],
    *,
    allow_destructive: set[int],
    allow_replacement: set[int],
) -> None:
    if not plan.exists:
        create_container(runner, plan.name, host, all_vars)
        return

    replacement = "replacement" in plan.risks
    destructive = "destructive" in plan.risks
    if replacement and plan.vmid not in allow_replacement:
        raise ReconcileError(
            f"VMID {plan.vmid} requires replacement; rerun with --allow-replacement {plan.vmid}"
        )
    if destructive and plan.vmid not in allow_destructive:
        raise ReconcileError(
            f"VMID {plan.vmid} has destructive drift; rerun with --allow-destructive {plan.vmid}"
        )
    if replacement:
        if runner.running(plan.vmid):
            runner.run(["pct", "shutdown", str(plan.vmid), "--timeout", "60"], check=False)
            if runner.running(plan.vmid):
                runner.run(["pct", "stop", str(plan.vmid)])
        runner.run(["pct", "destroy", str(plan.vmid), "--purge", "1"])
        create_container(runner, plan.name, host, all_vars)
        return

    restart_changes = [change for change in plan.changes if change.risk in {"restart", "destructive"}]
    was_running = runner.running(plan.vmid) if restart_changes else False
    if was_running:
        result = runner.run(
            ["pct", "shutdown", str(plan.vmid), "--timeout", "60"], check=False
        )
        if result.returncode and runner.running(plan.vmid):
            runner.run(["pct", "stop", str(plan.vmid)])
    try:
        ensure_mount_sources(host)
        for change in plan.changes:
            if change.field == "rootfs_size":
                runner.run(
                    ["pct", "resize", str(plan.vmid), "rootfs", f"{int(change.after)}G"]
                )
            elif change.field.startswith("mount_source:"):
                continue
            elif change.field != "container":
                runner.run(_set_command(plan.vmid, change))
    finally:
        # Restore availability even if a later pct set/resize operation fails.
        if was_running:
            runner.run(["pct", "start", str(plan.vmid)])


def build_plan(
    runner: PctRunner,
    all_vars: Mapping[str, object],
    hosts: Mapping[str, Mapping[str, object]],
    mount_inspector: Callable[[Mapping[str, object]], Iterable[Change]] = inspect_mount_sources,
) -> tuple[list[ContainerPlan], dict[int, str | None]]:
    current = {int(host["vmid"]): runner.config(int(host["vmid"])) for host in hosts.values()}
    plans = [
        plan_container(
            name,
            host,
            all_vars,
            current[int(host["vmid"])],
            mount_inspector(host),
        )
        for name, host in hosts.items()
    ]
    return plans, current


def result_payload(command: str, plans: Iterable[ContainerPlan], exports: Iterable[Path]) -> dict[str, object]:
    materialized = tuple(plans)
    return {
        "version": 1,
        "command": command,
        "changed": any(plan.changed for plan in materialized),
        "containers": [plan.as_dict() for plan in materialized],
        "exports": [str(path) for path in exports],
    }


def parse_confirmations(values: Sequence[str], label: str) -> set[int]:
    confirmed: set[int] = set()
    for value in values:
        if not value.isdecimal() or int(value) < 100:
            raise ReconcileError(f"{label} values must be exact numeric VMIDs")
        confirmed.add(int(value))
    return confirmed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "audit", "apply"))
    topology = parser.add_mutually_exclusive_group(required=True)
    topology.add_argument("--topology", type=Path)
    topology.add_argument("--topology-json")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--allow-destructive", action="append", default=[], metavar="VMID")
    parser.add_argument("--allow-replacement", action="append", default=[], metavar="VMID")
    parser.add_argument(
        "--protect-control-vmid", action="append", default=[], metavar="VMID"
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate a complete apply, including risk confirmations, without mutation",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: PctRunner | None = None,
    mount_inspector: Callable[[Mapping[str, object]], Iterable[Change]] = inspect_mount_sources,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.preflight_only and args.command != "apply":
            raise ReconcileError("--preflight-only is valid only with apply")
        if args.topology is not None:
            all_vars, hosts = load_topology(args.topology)
        else:
            try:
                document = json.loads(args.topology_json)
            except json.JSONDecodeError as exc:
                raise ReconcileError(f"cannot decode inline topology: {exc}") from exc
            all_vars, hosts = topology_from_document(document)
        executor = runner or PctRunner()
        plans, current = build_plan(
            executor, all_vars, hosts, mount_inspector=mount_inspector
        )
        exports: list[Path] = []
        if args.command == "apply":
            destructive = parse_confirmations(args.allow_destructive, "--allow-destructive")
            replacement = parse_confirmations(args.allow_replacement, "--allow-replacement")
            protected = parse_confirmations(
                args.protect_control_vmid, "--protect-control-vmid"
            )
            managed_vmids = {plan.vmid for plan in plans}
            unknown_confirmations = (destructive | replacement | protected).difference(
                managed_vmids
            )
            if unknown_confirmations:
                raise ReconcileError(
                    "manual confirmations contain unmanaged VMIDs: "
                    + ",".join(str(value) for value in sorted(unknown_confirmations))
                )
            for plan in plans:
                disruptive = plan.risks.intersection(
                    {"restart", "destructive", "replacement"}
                )
                if plan.exists and plan.vmid in protected and disruptive:
                    raise ReconcileError(
                        f"VMID {plan.vmid} carries the active control path and "
                        "requires an out-of-band apply for connectivity-affecting drift"
                    )
            # Fail closed for the complete topology before exporting or mutating
            # the first container.  This prevents a partially applied run merely
            # because a later VMID needs a manual risk confirmation.
            for plan in plans:
                if "replacement" in plan.risks and plan.vmid not in replacement:
                    raise ReconcileError(
                        f"VMID {plan.vmid} requires replacement; rerun with "
                        f"--allow-replacement {plan.vmid}"
                    )
                if "destructive" in plan.risks and plan.vmid not in destructive:
                    raise ReconcileError(
                        f"VMID {plan.vmid} has destructive drift; rerun with "
                        f"--allow-destructive {plan.vmid}"
                    )
            if not args.preflight_only:
                for plan in plans:
                    if plan.changed:
                        exports.append(
                            export_config(
                                args.export_dir,
                                plan.name,
                                plan.vmid,
                                current[plan.vmid],
                            )
                        )
                        apply_container(
                            executor,
                            plan,
                            hosts[plan.name],
                            all_vars,
                            allow_destructive=destructive,
                            allow_replacement=replacement,
                        )
        payload = result_payload(args.command, plans, exports)
        print(json.dumps(payload, sort_keys=True))
        if args.command == "audit" and payload["changed"]:
            return 1
        return 0
    except (OSError, ReconcileError, ValueError) as exc:
        print(f"pve-lxc-reconcile: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
