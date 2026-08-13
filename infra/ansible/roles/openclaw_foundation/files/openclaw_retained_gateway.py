#!/usr/bin/env python3
"""Fail-closed verifier for the retained Docker OpenClaw rollback Gateway."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CHECKPOINT_SCHEMA = "homelab-openclaw-retained-gateway-v1"
COMPOSE_SHA256 = "e4f0f963584bec3516b1961749c017137efa8e61ce565463d9426875f4d60dd5"
AT_FDCWD = -100
RENAME_NOREPLACE = 1
HEALTHCHECK_TEST = [
    "CMD",
    "node",
    "-e",
    "fetch('http://127.0.0.1:18789/healthz').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))",
]
PINNED_IMAGE_ENTRYPOINT = ["tini", "-s", "--"]
PINNED_IMAGE_COMMAND = ["node", "openclaw.mjs", "gateway"]
PINNED_IMAGE_WORKING_DIRECTORY = "/app"
PINNED_IMAGE_USER = "node"
PINNED_IMAGE_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "NODE_VERSION": "24.16.0",
    "YARN_VERSION": "1.22.22",
    "COREPACK_HOME": "/usr/local/share/corepack",
    "PLAYWRIGHT_BROWSERS_PATH": "/home/node/.cache/ms-playwright",
    "NODE_ENV": "production",
}
COMPOSE_ENVIRONMENT = {
    "HOME": "/home/node",
    "OPENCLAW_HOME": "/home/node",
    "OPENCLAW_STATE_DIR": "/home/node/.openclaw",
    "OPENCLAW_CONFIG_DIR": "/etc/openclaw",
    "OPENCLAW_CONFIG_PATH": "/etc/openclaw/openclaw.json",
    "OPENCLAW_WORKSPACE_DIR": "/home/node/.openclaw/workspace",
    "OPENCLAW_DISABLE_BONJOUR": "1",
    "TERM": "xterm-256color",
    "TZ": "Asia/Seoul",
}
MASKED_PATHS = frozenset(
    {
        "/proc/acpi",
        "/proc/asound",
        "/proc/interrupts",
        "/proc/kcore",
        "/proc/keys",
        "/proc/latency_stats",
        "/proc/sched_debug",
        "/proc/scsi",
        "/proc/timer_list",
        "/proc/timer_stats",
        "/sys/devices/virtual/powercap",
        "/sys/firmware",
    }
)
READONLY_PATHS = frozenset(
    {
        "/proc/bus",
        "/proc/fs",
        "/proc/irq",
        "/proc/sys",
        "/proc/sysrq-trigger",
    }
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(rb"^[0-9a-fA-F]{64}\n$")
IMAGE = re.compile(r"^ghcr\.io/openclaw/openclaw:[^@\s]+@sha256:[0-9a-f]{64}$")
SENSITIVE_COMPONENTS = frozenset(
    {
        b"state",
        b"runtime",
        b"auth",
        b"auth-profile-secrets",
        b"secrets",
        b"credentials",
        b"sessions",
        b"logs",
        b"cache",
        b"tmp",
        b"temp",
    }
)
UNSAFE_GIT_CONFIG = (
    re.compile(rb"^include"),
    re.compile(rb"^extensions\.worktreeconfig$"),
    re.compile(
        rb"^core\.(fsmonitor|hookspath|attributesfile|worktree|editor|askpass|sshcommand|gitproxy|pager)$"
    ),
    re.compile(rb"^sequence\.editor$"),
    re.compile(rb"^credential\."),
    re.compile(rb"^diff\.external$"),
    re.compile(rb"^diff\..*\.command$"),
    re.compile(rb"^filter\..*\.(clean|smudge|process|required)$"),
    re.compile(rb"^merge\..*\.driver$"),
    re.compile(rb"^gpg\..*\.program$"),
    re.compile(rb"^remote\..*\.(promisor|partialclonefilter)$"),
    re.compile(rb"^extensions\.partialclone$"),
    re.compile(rb"^core\.alternaterefscommand$"),
)


class ContractError(RuntimeError):
    """A retained rollback invariant was not satisfied."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def reject_json_constant(value: str) -> None:
    raise ContractError(f"non-standard JSON constant rejected: {value}")


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, f"duplicate JSON key rejected: {key}")
        value[key] = item
    return value


def json_type_exact(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            json_type_exact(actual[key], item) for key, item in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            json_type_exact(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def environment_map(entries: Any, description: str) -> dict[str, str]:
    require(isinstance(entries, list), f"{description} environment is not a list")
    require(
        all(isinstance(value, str) and "=" in value for value in entries),
        f"{description} environment is malformed",
    )
    pairs = [value.split("=", 1) for value in entries]
    result = dict(pairs)
    require(len(result) == len(pairs), f"{description} environment has duplicate keys")
    return result


def validate_runtime_shape(
    container_config: dict[str, Any],
    image_config: dict[str, Any],
    deployment_revision: str,
) -> None:
    require(
        image_config.get("User") == PINNED_IMAGE_USER,
        "pinned image user differs from the audited release",
    )
    require(
        image_config.get("Entrypoint") == PINNED_IMAGE_ENTRYPOINT
        and container_config.get("Entrypoint") == PINNED_IMAGE_ENTRYPOINT,
        "retained container or pinned image entrypoint drifted",
    )
    require(
        image_config.get("Cmd") == PINNED_IMAGE_COMMAND
        and container_config.get("Cmd") == PINNED_IMAGE_COMMAND,
        "retained container or pinned image command drifted",
    )
    require(
        image_config.get("WorkingDir") == PINNED_IMAGE_WORKING_DIRECTORY
        and container_config.get("WorkingDir") == PINNED_IMAGE_WORKING_DIRECTORY,
        "retained container or pinned image working directory drifted",
    )

    image_environment = environment_map(image_config.get("Env"), "pinned image")
    require(
        image_environment == PINNED_IMAGE_ENVIRONMENT,
        "pinned image environment differs from the audited release",
    )
    expected_environment = dict(PINNED_IMAGE_ENVIRONMENT)
    expected_environment.update(COMPOSE_ENVIRONMENT)
    expected_environment["OPENCLAW_CONFIG_REVISION"] = deployment_revision
    actual_environment = environment_map(
        container_config.get("Env") or [], "retained container"
    )
    require(
        actual_environment == expected_environment,
        "retained container environment drifted from the image and Compose assets",
    )
    require(
        not any(key.startswith("OPENCLAW_GATEWAY_TOKEN") for key in actual_environment),
        "retained container exposes a Gateway credential through its environment",
    )

    healthcheck = container_config.get("Healthcheck")
    require(isinstance(healthcheck, dict), "retained healthcheck is absent or malformed")
    expected_healthcheck = {
        "Test": HEALTHCHECK_TEST,
        "Interval": 30_000_000_000,
        "Timeout": 5_000_000_000,
        "Retries": 5,
        "StartPeriod": 20_000_000_000,
    }
    require(
        set(healthcheck) in (
            set(expected_healthcheck),
            set(expected_healthcheck) | {"StartInterval"},
        ),
        "retained healthcheck keys drifted",
    )
    require(
        all(
            json_type_exact(healthcheck.get(key), value)
            for key, value in expected_healthcheck.items()
        ),
        "retained healthcheck contract drifted",
    )
    if "StartInterval" in healthcheck:
        require(
            type(healthcheck["StartInterval"]) is int
            and healthcheck["StartInterval"] == 0,
            "retained healthcheck start interval drifted",
        )


def validate_tmpfs(tmpfs: Any) -> None:
    require(isinstance(tmpfs, dict), "retained tmpfs configuration is malformed")
    require(set(tmpfs) == {"/tmp"}, "retained tmpfs destinations drifted")
    options = str(tmpfs["/tmp"]).split(",")
    require(all(options) and len(options) == len(set(options)), "retained tmpfs options are malformed")
    option_set = set(options)
    sizes = option_set & {"size=32m", "size=32768k", "size=33554432"}
    expected = {"rw", "noexec", "nosuid", "nodev", "uid=1000", "gid=1000", "mode=1777"}
    require(
        len(sizes) == 1 and option_set - sizes == expected,
        "retained tmpfs hardening drifted",
    )


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    allowed: tuple[int, ...] = (0,),
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in allowed:
        raise ContractError(f"command failed ({argv[0]} {argv[1]}): rc={result.returncode}")
    return result


def checked_lstat(
    path: Path,
    *,
    kind: str,
    uid: int,
    gid: int,
    mode: int,
    links: int | None = None,
) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ContractError(f"required path is unavailable: {path}") from exc
    expected_kind = stat.S_ISREG if kind == "file" else stat.S_ISDIR
    require(expected_kind(metadata.st_mode), f"unexpected path type: {path}")
    require(not stat.S_ISLNK(metadata.st_mode), f"symlink rejected: {path}")
    require(metadata.st_uid == uid and metadata.st_gid == gid, f"unexpected owner: {path}")
    require(stat.S_IMODE(metadata.st_mode) == mode, f"unexpected mode: {path}")
    if links is not None:
        require(metadata.st_nlink == links, f"unexpected link count: {path}")
    return metadata


def read_regular(
    path: Path, *, uid: int, gid: int, mode: int, maximum: int = 1 << 20
) -> bytes:
    before = checked_lstat(path, kind="file", uid=uid, gid=gid, mode=mode, links=1)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractError(f"could not open protected file: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        require(
            stat.S_ISREG(opened.st_mode)
            and opened.st_uid == uid
            and opened.st_gid == gid
            and stat.S_IMODE(opened.st_mode) == mode
            and opened.st_nlink == 1
            and (opened.st_dev, opened.st_ino, opened.st_size)
            == (before.st_dev, before.st_ino, before.st_size),
            f"protected file changed during open: {path}",
        )
        require(opened.st_size <= maximum, f"protected file is unexpectedly large: {path}")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        require(len(payload) <= maximum, f"protected file is unexpectedly large: {path}")
        after = os.fstat(descriptor)
        require(
            stat.S_ISREG(after.st_mode)
            and after.st_uid == uid
            and after.st_gid == gid
            and stat.S_IMODE(after.st_mode) == mode
            and after.st_nlink == 1
            and (after.st_dev, after.st_ino, after.st_size)
            == (opened.st_dev, opened.st_ino, opened.st_size),
            f"protected file changed while read: {path}",
        )
        return bytes(payload)
    finally:
        os.close(descriptor)


def git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LITERAL_PATHSPECS": "1",
        }
    )
    return environment


def git(setup: Path, arguments: list[str], *, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
    return run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "diff.external=",
            "-c",
            "pager.status=false",
            *arguments,
        ],
        cwd=setup,
        allowed=allowed,
        environment=git_environment(),
    )


def validate_tracked_paths(payload: bytes) -> tuple[bytes, ...]:
    if not payload:
        return ()
    require(payload.endswith(b"\0"), "Git path list has invalid framing")
    records = tuple(payload[:-1].split(b"\0"))
    require(all(records), "Git path list contains an empty path")
    for record in records:
        try:
            header, path = record.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ", 2)
        except ValueError as exc:
            raise ContractError("Git index record has invalid framing") from exc
        require(mode in {b"100644", b"100755"}, "symlink or submodule rejected from private Git index")
        require(re.fullmatch(rb"[0-9a-f]{40,64}", object_id) is not None, "invalid Git object ID")
        require(stage == b"0", "unmerged private Git index entry rejected")
        if path == b".env.example":
            continue
        components = path.split(b"/")
        require(all(components), "invalid empty Git path component")
        require(
            not any(
                component.startswith(b".env") or component in SENSITIVE_COMPONENTS
                for component in components
            ),
            "sensitive path is tracked by the private repository",
        )
    return records


def validate_private_repository(setup: Path, config: Path, token: Path) -> None:
    require(config == setup / "config" / "openclaw.json", "config path escaped private setup root")
    try:
        git_config = (setup / ".git" / "config").lstat()
    except OSError as exc:
        raise ContractError("private repository config is unavailable") from exc
    require(
        stat.S_ISREG(git_config.st_mode)
        and not stat.S_ISLNK(git_config.st_mode)
        and git_config.st_uid == 0
        and git_config.st_gid == 0
        and git_config.st_nlink == 1
        and stat.S_IMODE(git_config.st_mode) in {0o600, 0o644},
        "private repository config has unsafe metadata",
    )
    for forbidden in (setup / ".gitmodules", setup / ".git" / "info" / "attributes"):
        require(not os.path.lexists(forbidden), f"forbidden Git control file exists: {forbidden}")
    for root, directories, files in os.walk(setup, followlinks=False):
        root_path = Path(root)
        for name in (*directories, *files):
            candidate = root_path / name
            require(not candidate.is_symlink(), f"symlink rejected from private repository: {candidate}")
            if name == ".gitattributes":
                raise ContractError("private repository attributes files are forbidden")
    for alternates in (
        setup / ".git" / "objects" / "info" / "alternates",
        setup / ".git" / "objects" / "info" / "http-alternates",
    ):
        require(not os.path.lexists(alternates), "Git object alternates are forbidden")

    local_config = git(setup, ["config", "--local", "--name-only", "--get-regexp", ".*"], allowed=(0, 1))
    for name in local_config.stdout.lower().splitlines():
        require(not any(pattern.search(name) for pattern in UNSAFE_GIT_CONFIG), "unsafe local Git configuration")
    require(not git(setup, ["remote"]).stdout.strip(), "private repository must have no deployment remote")
    require(git(setup, ["symbolic-ref", "--short", "HEAD"]).stdout == b"main\n", "private repository must be on main")
    revision = git(setup, ["rev-parse", "--verify", "HEAD"]).stdout.strip()
    require(re.fullmatch(rb"[0-9a-f]{40,64}", revision) is not None, "invalid private repository revision")
    require(
        not git(setup, ["status", "--porcelain=v1", "-z", "--ignored", "--no-ahead-behind"]).stdout,
        "private repository must be clean and contain no ignored runtime material",
    )
    git(setup, ["diff", "--quiet", "--no-ext-diff", "--no-textconv"])
    git(setup, ["diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv"])
    git(setup, ["ls-files", "--error-unmatch", "--", "config/openclaw.json"])
    validate_tracked_paths(git(setup, ["ls-files", "--stage", "-z"]).stdout)
    token_search = git(
        setup,
        ["grep", "--quiet", "--fixed-strings", "--no-textconv", "-f", str(token), "--"],
        allowed=(0, 1),
    )
    require(token_search.returncode == 1, "Gateway token is present in tracked private content")


def validate_config(payload: bytes, hostname: str, port: int, config_state: str) -> None:
    try:
        document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("retained Docker config is not valid JSON") from exc
    require(isinstance(document, dict), "retained Docker config must be an object")
    expected_token = {"source": "file", "provider": "gateway_token_file", "id": "value"}
    expected_provider = {
        "source": "file",
        "path": "/run/secrets/openclaw_gateway_token",
        "mode": "singleValue",
    }
    foundation = {
        "secrets": {"providers": {"gateway_token_file": expected_provider}},
        "gateway": {
            "mode": "local",
            "port": port,
            "bind": "lan",
            "auth": {"mode": "token", "token": expected_token},
            "controlUi": {
                "allowedOrigins": [
                    "http://127.0.0.1:18789",
                    "http://localhost:18789",
                ]
            },
        },
    }
    rollback = {
        "secrets": {"providers": {"gateway_token_file": expected_provider}},
        "gateway": {
            "mode": "local",
            "port": port,
            "bind": "lan",
            "auth": {
                "mode": "token",
                "token": expected_token,
                "allowTailscale": False,
                "rateLimit": {
                    "maxAttempts": 10,
                    "windowMs": 60000,
                    "lockoutMs": 300000,
                    "exemptLoopback": True,
                },
            },
            "controlUi": {"enabled": True, "allowedOrigins": [f"https://{hostname}"]},
            "terminal": {"enabled": False},
            "trustedProxies": [],
            "allowRealIpFallback": False,
            "tailscale": {"mode": "off", "resetOnExit": False},
        },
    }
    require(config_state in {"foundation-or-rollback", "rollback"}, "unknown retained config state")
    allowed = (rollback,) if config_state == "rollback" else (foundation, rollback)
    require(
        any(json_type_exact(document, candidate) for candidate in allowed),
        f"retained Docker config is not an exact {config_state} contract",
    )


def require_assets(arguments: argparse.Namespace) -> bytes:
    uid = arguments.uid
    gid = arguments.gid
    checked_lstat(arguments.compose_root, kind="directory", uid=uid, gid=gid, mode=0o755)
    checked_lstat(arguments.setup_root, kind="directory", uid=0, gid=gid, mode=0o750)
    checked_lstat(arguments.setup_root / ".git", kind="directory", uid=0, gid=0, mode=0o700)
    checked_lstat(arguments.runtime_root, kind="directory", uid=uid, gid=gid, mode=0o700)
    checked_lstat(arguments.state_root, kind="directory", uid=uid, gid=gid, mode=0o700)
    checked_lstat(arguments.state_root / "workspace", kind="directory", uid=uid, gid=gid, mode=0o700)
    checked_lstat(arguments.auth_root, kind="directory", uid=uid, gid=gid, mode=0o700)
    checked_lstat(arguments.control_root, kind="directory", uid=0, gid=0, mode=0o755)
    checked_lstat(arguments.token.parent, kind="directory", uid=0, gid=gid, mode=0o750)
    compose_payload = read_regular(
        arguments.compose_root / "compose.yml", uid=uid, gid=gid, mode=0o644
    )
    require(
        hashlib.sha256(compose_payload).hexdigest() == COMPOSE_SHA256,
        "retained Compose manifest differs from the pinned rollback asset",
    )
    environment_payload = read_regular(
        arguments.compose_root / ".env", uid=uid, gid=gid, mode=0o600
    )
    config_payload = read_regular(arguments.config, uid=0, gid=gid, mode=0o640)
    token_payload = read_regular(arguments.token, uid=uid, gid=gid, mode=0o600, maximum=65)
    require(TOKEN.fullmatch(token_payload) is not None, "retained Gateway token has invalid shape")
    if arguments.require_expected_token:
        expected = sys.stdin.buffer.read()
        require(TOKEN.fullmatch(expected) is not None, "expected Gateway token input has invalid shape")
        require(token_payload == expected, "retained Gateway token differs from current deployment secret")
    validate_config(config_payload, arguments.hostname, arguments.port, arguments.config_state)
    for manifest in (
        "compose.yaml",
        "compose.override.yml",
        "compose.override.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.override.yml",
        "docker-compose.override.yaml",
    ):
        require(not os.path.lexists(arguments.compose_root / manifest), "alternate retained Compose manifest exists")
        require(
            not os.path.lexists(arguments.setup_root / manifest),
            "deployment manifest exists in private repository",
        )
    validate_private_repository(arguments.setup_root, arguments.config, arguments.token)
    environment_match = re.fullmatch(
        rb"PUID=(\d+)\nPGID=(\d+)\nTZ=Asia/Seoul\n"
        rb"OPENCLAW_CONFIG_REVISION=([0-9a-f]{40,64})\n",
        environment_payload,
    )
    require(
        environment_match is not None
        and int(environment_match.group(1)) == uid
        and int(environment_match.group(2)) == gid,
        "retained deployment environment differs from the pinned rollback contract",
    )
    arguments.deployment_revision = environment_match.group(3).decode("ascii")
    return token_payload


def load_json(command: list[str], *, cwd: Path | None = None) -> Any:
    try:
        return json.loads(
            run(command, cwd=cwd).stdout,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON from {command[0]} {command[1]}") from exc


def require_rollback_network() -> None:
    networks = load_json(["docker", "network", "inspect", "homelab_proxy"])
    require(isinstance(networks, list) and len(networks) == 1, "rollback proxy network inspect is ambiguous")
    network = networks[0]
    require(network.get("Name") == "homelab_proxy", "rollback proxy network name drifted")
    require(network.get("Driver") == "bridge", "rollback proxy network driver drifted")
    require(network.get("Scope") == "local", "rollback proxy network scope drifted")
    require(network.get("Internal") is False, "rollback proxy network became internal")


def require_default_network() -> None:
    networks = load_json(["docker", "network", "inspect", "openclaw_default"])
    require(isinstance(networks, list) and len(networks) == 1, "retained default network inspect is ambiguous")
    network = networks[0]
    require(network.get("Name") == "openclaw_default", "retained default network name drifted")
    require(network.get("Driver") == "bridge", "retained default network driver drifted")
    require(network.get("Scope") == "local", "retained default network scope drifted")
    require(network.get("Internal") is False, "retained default network became internal")


def rollback_alias_owners() -> list[str]:
    members = run(["docker", "ps", "--all", "--quiet", "--filter", "network=homelab_proxy"]).stdout.splitlines()
    if not members:
        return []
    inspected = load_json(["docker", "inspect", *[member.decode("ascii") for member in members]])
    require(
        isinstance(inspected, list) and len(inspected) == len(members),
        "rollback proxy member inspect is ambiguous",
    )
    alias_owners = []
    for member in inspected:
        member_id = member.get("Id")
        require(
            isinstance(member_id, str) and HEX_64.fullmatch(member_id) is not None,
            "rollback proxy member identity is invalid",
        )
        network = (member.get("NetworkSettings", {}).get("Networks") or {}).get("homelab_proxy")
        require(isinstance(network, dict), "rollback proxy member attachment is malformed")
        aliases = network.get("Aliases")
        if aliases is None:
            aliases = []
        require(type(aliases) is list, "rollback proxy member aliases are malformed")
        require(
            all(type(alias) is str for alias in aliases),
            "rollback proxy member aliases contain a non-string value",
        )
        if "openclaw-rollback" in aliases:
            alias_owners.append(member_id)
    return alias_owners


def require_rollback_alias_available(container_id: str) -> None:
    owners = rollback_alias_owners()
    require(
        owners in ([], [container_id]),
        "rollback proxy alias is duplicated or owned by another container",
    )


def require_rollback_alias_unique(container_id: str) -> None:
    require(
        rollback_alias_owners() == [container_id],
        "rollback proxy alias is absent, duplicated, or misowned",
    )


def require_endpoint_names(
    endpoint: object,
    expected_aliases: list[str],
    expected_dns_names: list[str],
    label: str,
    running: bool,
) -> None:
    require(isinstance(endpoint, dict), f"{label} attachment is malformed")
    aliases = endpoint.get("Aliases")
    dns_names = endpoint.get("DNSNames")
    require(type(aliases) is list, f"{label} aliases are malformed")
    require(type(dns_names) is list, f"{label} DNS names are malformed")
    require(
        all(type(alias) is str for alias in aliases),
        f"{label} aliases contain a non-string value",
    )
    require(
        all(type(name) is str for name in dns_names),
        f"{label} DNS names contain a non-string value",
    )
    require(aliases == expected_aliases, f"{label} aliases drifted")
    require(dns_names == expected_dns_names, f"{label} DNS names drifted")
    require(endpoint.get("IPAMConfig") is None, f"{label} static IPAM config drifted")
    require(endpoint.get("DriverOpts") is None, f"{label} driver options drifted")
    require(endpoint.get("Links") is None, f"{label} links drifted")
    require(
        type(endpoint.get("GwPriority")) is int and endpoint.get("GwPriority") == 0,
        f"{label} Gateway priority drifted",
    )
    mac_address = endpoint.get("MacAddress")
    if running:
        require(
            type(mac_address) is str
            and re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac_address)
            is not None,
            f"{label} MAC address is malformed",
        )
        octets = bytes.fromhex(mac_address.replace(":", ""))
        require(any(octets), f"{label} MAC address is zero")
        require(octets[0] & 1 == 0, f"{label} MAC address is multicast")
    else:
        require(mac_address == "", f"{label} stopped MAC address drifted")


def validate_container_dns_identity(
    container_name: object,
    hostname: object,
    container_id: str,
    default_network: object,
    rollback_network: object,
    running: bool,
) -> None:
    require(
        container_name == "/openclaw-openclaw-gateway-1",
        "retained container name drifted",
    )
    short_id = container_id[:12]
    require(hostname == short_id, "retained container hostname drifted")
    require_endpoint_names(
        default_network,
        ["openclaw-openclaw-gateway-1", "openclaw-gateway"],
        ["openclaw-openclaw-gateway-1", "openclaw-gateway", short_id],
        "retained default network",
        running,
    )
    if rollback_network is None:
        return
    require_endpoint_names(
        rollback_network,
        ["openclaw-rollback"],
        ["openclaw-openclaw-gateway-1", "openclaw-rollback", short_id],
        "rollback proxy",
        True,
    )


def require_exact_unique_string_set(
    value: object,
    expected: frozenset[str],
    description: str,
) -> None:
    require(type(value) is list, f"{description} is not a list")
    require(
        all(type(item) is str for item in value),
        f"{description} contains a non-string value",
    )
    require(len(value) == len(set(value)), f"{description} contains duplicates")
    require(set(value) == expected, f"{description} drifted")


def validate_host_runtime_shape(host: dict) -> None:
    """Require the exact live Docker namespace and runtime boundary."""
    require(host.get("PidMode") == "", "retained PID namespace mode drifted")
    require(host.get("IpcMode") == "private", "retained IPC namespace mode drifted")
    require(
        host.get("CgroupnsMode") == "private",
        "retained cgroup namespace mode drifted",
    )
    require(host.get("UTSMode") == "", "retained UTS namespace mode drifted")
    require(host.get("UsernsMode") == "", "retained user namespace mode drifted")
    require(host.get("Runtime") == "runc", "retained OCI runtime drifted")
    require(host.get("AutoRemove") is False, "retained auto-remove policy drifted")
    require(
        host.get("PublishAllPorts") is False,
        "retained automatic port publication policy drifted",
    )
    require(host.get("Init") is None, "retained init policy drifted")
    require(host.get("ExtraHosts") == [], "retained extra-host policy drifted")
    require(host.get("GroupAdd") is None, "retained supplemental groups drifted")
    require(host.get("Dns") is None, "retained DNS servers drifted")
    require(host.get("DnsOptions") is None, "retained DNS options drifted")
    require(host.get("DnsSearch") is None, "retained DNS search domains drifted")
    require(
        host.get("DeviceCgroupRules") is None,
        "retained device cgroup rules drifted",
    )
    require(host.get("Cgroup") == "", "retained cgroup parent drifted")
    require(host.get("Sysctls") is None, "retained sysctls drifted")
    require(
        json_type_exact(
            host.get("LogConfig"),
            {
                "Type": "json-file",
                "Config": {"max-file": "3", "max-size": "10m"},
            },
        ),
        "retained logging contract drifted",
    )
    require_exact_unique_string_set(
        host.get("MaskedPaths"), MASKED_PATHS, "retained masked paths"
    )
    require_exact_unique_string_set(
        host.get("ReadonlyPaths"), READONLY_PATHS, "retained read-only paths"
    )


def validate_mount_contract(
    host_mounts: object,
    realized_mounts: object,
    config_path: Path,
    state_root: Path,
    auth_root: Path,
    token_path: Path,
) -> None:
    specifications = [
        (str(config_path), "/etc/openclaw/openclaw.json", False),
        (str(state_root), "/home/node/.openclaw", True),
        (str(auth_root), "/home/node/.config/openclaw", True),
        (str(token_path), "/run/secrets/openclaw_gateway_token", False),
    ]
    expected_host_mounts = []
    for source, target, writable in specifications:
        mount = {
            "Type": "bind",
            "Source": source,
            "Target": target,
        }
        if not writable:
            mount["ReadOnly"] = True
        mount["BindOptions"] = {}
        expected_host_mounts.append(mount)
    expected_realized_mounts = [
        {
            "Type": "bind",
            "Source": source,
            "Destination": target,
            "Mode": "",
            "RW": writable,
            "Propagation": "rprivate",
        }
        for source, target, writable in specifications
    ]
    require(
        json_type_exact(host_mounts, expected_host_mounts),
        "retained requested mount contract drifted",
    )
    require(
        json_type_exact(realized_mounts, expected_realized_mounts),
        "retained realized mount contract drifted",
    )


def validate_apparmor_profile(profile: object) -> None:
    require(profile == "", "retained AppArmor profile drifted")


def require_container(arguments: argparse.Namespace) -> dict[str, str]:
    ids = run(
        ["docker", "compose", "ps", "--all", "-q", "openclaw-gateway"],
        cwd=arguments.compose_root,
    ).stdout.splitlines()
    require(len(ids) == 1, "exactly one retained Gateway container is required")
    container_id = ids[0].decode("ascii")
    require(HEX_64.fullmatch(container_id) is not None, "retained container ID is not full-length hexadecimal")
    containers = load_json(["docker", "inspect", container_id])
    require(isinstance(containers, list) and len(containers) == 1, "retained container inspect is ambiguous")
    container = containers[0]
    require(container.get("Id") == container_id, "retained container identity drifted during inspect")
    validate_apparmor_profile(container.get("AppArmorProfile"))

    rendered_images = run(["docker", "compose", "config", "--images"], cwd=arguments.compose_root).stdout.splitlines()
    require(len(rendered_images) == 1, "retained Compose project must render exactly one image")
    rendered_image = rendered_images[0].decode("utf-8")
    require(
        rendered_image == arguments.expected_image,
        "retained Compose image differs from the pinned rollback image",
    )
    require(IMAGE.fullmatch(rendered_image) is not None, "retained image reference is not immutable")
    images = load_json(["docker", "image", "inspect", rendered_image])
    require(isinstance(images, list) and len(images) == 1, "retained image inspect is ambiguous")
    image_id = images[0].get("Id")
    image_config = images[0].get("Config") or {}
    require(isinstance(image_config, dict), "pinned image config is malformed")
    require(isinstance(image_id, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "invalid retained image ID")
    require(container.get("Image") == image_id, "retained container does not use the pinned local image")

    config = container.get("Config") or {}
    host = container.get("HostConfig") or {}
    state = container.get("State") or {}
    labels = config.get("Labels") or {}
    require(config.get("Image") == rendered_image, "retained container image reference drifted")
    require(config.get("User") == f"{arguments.uid}:{arguments.gid}", "retained container user drifted")
    validate_runtime_shape(config, image_config, arguments.deployment_revision)
    require(labels.get("com.docker.compose.project") == "openclaw", "retained Compose project label drifted")
    require(labels.get("com.docker.compose.service") == "openclaw-gateway", "retained Compose service label drifted")
    require(labels.get("com.getarcaneapp.arcane.updater") == "false", "retained updater policy drifted")
    require(not any(key.startswith("traefik.") for key in labels), "retained container has Traefik labels")
    validate_host_runtime_shape(host)
    require(host.get("ReadonlyRootfs") is True, "retained root filesystem is writable")
    require(host.get("Privileged") is False, "retained container became privileged")
    require((host.get("RestartPolicy") or {}).get("Name") == "unless-stopped", "retained restart policy drifted")
    require(not (host.get("CapAdd") or []), "retained container gained capabilities")
    require(set(host.get("CapDrop") or []) == {"ALL"}, "retained capability policy drifted")
    require(set(host.get("SecurityOpt") or []) == {"no-new-privileges:true"}, "retained security options drifted")
    require(host.get("NetworkMode") == "openclaw_default", "retained container network mode drifted")
    require(not (host.get("Devices") or []), "retained container gained host devices")
    require(not (host.get("DeviceRequests") or []), "retained container gained device requests")
    require(
        host.get("PortBindings")
        == {f"{arguments.port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(arguments.port)}]},
        "retained loopback port binding drifted",
    )
    validate_tmpfs(host.get("Tmpfs"))

    validate_mount_contract(
        host.get("Mounts"),
        container.get("Mounts"),
        arguments.config,
        arguments.state_root,
        arguments.auth_root,
        arguments.token,
    )

    networks = (container.get("NetworkSettings") or {}).get("Networks") or {}
    default_network = networks.get("openclaw_default")
    rollback_network = networks.get("homelab_proxy")
    running = state.get("Running") is True and state.get("Status") == "running"
    stopped = state.get("Running") is False and state.get("Status") == "exited"
    validate_container_dns_identity(
        container.get("Name"),
        config.get("Hostname"),
        container_id,
        default_network,
        rollback_network,
        running,
    )
    require_default_network()
    require_rollback_network()
    if arguments.container_state == "stopped":
        require(stopped, "retained Gateway must be stopped before checkpoint seeding")
        require(set(networks) == {"openclaw_default"}, "stopped retained Gateway network set drifted")
    elif arguments.container_state == "rollback":
        require(stopped or running, "retained rollback Gateway has an invalid lifecycle state")
        if stopped:
            require(set(networks) == {"openclaw_default"}, "stopped rollback Gateway network set drifted")
        else:
            require(
                (state.get("Health") or {}).get("Status") in {"starting", "healthy"},
                "running rollback Gateway has a non-resumable health state",
            )
            require(
                set(networks) in ({"openclaw_default"}, {"openclaw_default", "homelab_proxy"}),
                "running rollback Gateway network set drifted",
            )
            # A tracked rollback can be interrupted after `docker compose start`
            # and before its proxy alias is attached. The role proves alias
            # ownership immediately before attach; final validation uses the
            # stricter `running` state below.
        require_rollback_alias_available(container_id)
    else:
        require(running, "retained rollback Gateway must be running")
        require((state.get("Health") or {}).get("Status") == "healthy", "retained rollback Gateway is unhealthy")
        require(
            set(networks) == {"openclaw_default", "homelab_proxy"},
            "running rollback Gateway network set drifted",
        )
        require_rollback_alias_unique(container_id)

    created = container.get("Created")
    require(isinstance(created, str) and created and "\0" not in created, "invalid retained container creation time")
    return {
        "schema": CHECKPOINT_SCHEMA,
        "container_id": container_id,
        "container_created": created,
        "image_id": image_id,
        "image_ref": rendered_image,
    }


def checkpoint_bytes(identity: dict[str, str]) -> bytes:
    required = {"schema", "container_id", "container_created", "image_id", "image_ref"}
    require(set(identity) == required, "retained identity checkpoint keys drifted")
    require(identity["schema"] == CHECKPOINT_SCHEMA, "retained identity checkpoint schema drifted")
    require(HEX_64.fullmatch(identity["container_id"]) is not None, "invalid checkpoint container ID")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", identity["image_id"]) is not None, "invalid checkpoint image ID")
    require(IMAGE.fullmatch(identity["image_ref"]) is not None, "invalid checkpoint image reference")
    require(bool(identity["container_created"]), "invalid checkpoint creation time")
    return (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def require_checkpoint(path: Path, candidate: bytes) -> None:
    payload = read_regular(path, uid=0, gid=0, mode=0o600, maximum=2048)
    require(payload == candidate, "retained Gateway identity checkpoint does not match live assets")


def rename_noreplace(source: str, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    require(renameat2 is not None, "Linux renameat2 is required for checkpoint publication")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD,
        os.fsencode(source),
        AT_FDCWD,
        os.fsencode(destination),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), destination)
    raise ContractError(f"atomic checkpoint publication failed: errno={error}")


def fsync_directory(path: Path) -> None:
    directory_descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def publish_checkpoint(source: str, destination: Path) -> None:
    rename_noreplace(source, destination)
    fsync_directory(destination.parent)


def seed_checkpoint(path: Path, candidate: bytes) -> bool:
    if os.path.lexists(path):
        require_checkpoint(path, candidate)
        return False
    parent = path.parent
    checked_lstat(parent, kind="directory", uid=0, gid=0, mode=0o755)
    previous_umask = os.umask(0o077)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".retained-gateway-identity.", dir=parent)
        try:
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, 0, 0)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(candidate)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                publish_checkpoint(temporary_name, path)
            except FileExistsError:
                require_checkpoint(path, candidate)
                return False
            return True
        finally:
            if temporary_name is not None and os.path.lexists(temporary_name):
                os.unlink(temporary_name)
    finally:
        os.umask(previous_umask)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "require"))
    parser.add_argument("--container-state", choices=("stopped", "rollback", "running"), required=True)
    parser.add_argument(
        "--config-state",
        choices=("foundation-or-rollback", "rollback"),
        required=True,
    )
    parser.add_argument("--compose-root", type=Path, required=True)
    parser.add_argument("--setup-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--auth-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--token", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--gid", type=int, required=True)
    parser.add_argument("--require-expected-token", action="store_true")
    arguments = parser.parse_args()
    require(
        arguments.checkpoint
        == arguments.control_root / "retained-gateway-identity.json",
        "checkpoint path escaped control root",
    )
    require(arguments.runtime_root != arguments.setup_root, "runtime and setup roots must remain separated")
    require(arguments.state_root == arguments.runtime_root / "state", "state root escaped runtime root")
    require(arguments.token.parent.parent == arguments.control_root, "token path escaped control root")
    require(arguments.port == 18789, "unexpected retained Gateway port")
    return arguments


def main() -> int:
    try:
        arguments = parse_arguments()
        require_assets(arguments)
        identity = require_container(arguments)
        candidate = checkpoint_bytes(identity)
        if arguments.mode == "seed":
            require(arguments.container_state == "stopped", "checkpoint seeding requires a stopped Gateway")
            changed = seed_checkpoint(arguments.checkpoint, candidate)
        else:
            require_checkpoint(arguments.checkpoint, candidate)
            changed = False
        print(f"changed={'true' if changed else 'false'}")
        print(f"container_id={identity['container_id']}")
        return 0
    except ContractError as exc:
        print(f"retained OpenClaw rollback contract rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
