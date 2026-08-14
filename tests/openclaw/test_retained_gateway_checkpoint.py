import hashlib
import importlib.util
import itertools
import json
import sys
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined

from tests.helpers import REPO_ROOT


HELPER = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "openclaw_foundation"
    / "files"
    / "openclaw_retained_gateway.py"
)
ROLE = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "roles"
    / "openclaw_foundation"
    / "tasks"
    / "main.yml"
)
VALIDATE = REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml"
FENCE = (
    REPO_ROOT
    / "infra"
    / "ansible"
    / "playbooks"
    / "fence-openclaw-docker-before-native.yml"
)
FENCE_ASSETS = FENCE.with_name("fence-openclaw-retained-assets.yml")
COMPOSE = REPO_ROOT / "apps" / "compose" / "openclaw" / "compose.yml"


def foundation_config() -> dict:
    return {
        "secrets": {
            "providers": {
                "gateway_token_file": {
                    "source": "file",
                    "path": "/run/secrets/openclaw_gateway_token",
                    "mode": "singleValue",
                }
            }
        },
        "gateway": {
            "mode": "local",
            "port": 18789,
            "bind": "lan",
            "auth": {
                "mode": "token",
                "token": {
                    "source": "file",
                    "provider": "gateway_token_file",
                    "id": "value",
                },
            },
            "controlUi": {
                "allowedOrigins": [
                    "http://127.0.0.1:18789",
                    "http://localhost:18789",
                ]
            },
        },
    }


def rollback_config() -> dict:
    value = foundation_config()
    value["gateway"]["auth"].update(
        {
            "allowTailscale": False,
            "rateLimit": {
                "maxAttempts": 10,
                "windowMs": 60000,
                "lockoutMs": 300000,
                "exemptLoopback": True,
            },
        }
    )
    value["gateway"].update(
        {
            "controlUi": {
                "enabled": True,
                "allowedOrigins": ["https://openclaw.home.hchu.me"],
            },
            "terminal": {"enabled": False},
            "trustedProxies": [],
            "allowRealIpFallback": False,
            "tailscale": {"mode": "off", "resetOnExit": False},
        }
    )
    return value


def load_helper():
    spec = importlib.util.spec_from_file_location("openclaw_retained_gateway", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def task_by_name(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def assert_bounded_fail_closed_retry(task: dict) -> None:
    register = task["register"]
    assert task["retries"] == 3
    assert task["delay"] == 2
    assert task["until"] == f"{register}.rc == 0"
    assert "failed_when" not in task
    assert "ignore_errors" not in task
    assert task["no_log"] is True


def identity(**updates):
    value = {
        "schema": "homelab-openclaw-retained-gateway-v1",
        "container_id": "a" * 64,
        "container_created": "2026-08-13T00:00:00.000000000Z",
        "image_id": "sha256:" + "b" * 64,
        "image_ref": "ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:" + "c" * 64,
    }
    value.update(updates)
    return value


def runtime_configs():
    helper = load_helper()
    image = {
        "User": helper.PINNED_IMAGE_USER,
        "Entrypoint": list(helper.PINNED_IMAGE_ENTRYPOINT),
        "Cmd": list(helper.PINNED_IMAGE_COMMAND),
        "WorkingDir": helper.PINNED_IMAGE_WORKING_DIRECTORY,
        "Env": [
            f"{key}={value}"
            for key, value in helper.PINNED_IMAGE_ENVIRONMENT.items()
        ],
    }
    revision = "d" * 40
    environment = helper.environment_map(image["Env"], "test image")
    environment.update(helper.COMPOSE_ENVIRONMENT)
    environment["OPENCLAW_CONFIG_REVISION"] = revision
    container = {
        "Entrypoint": list(image["Entrypoint"]),
        "Cmd": list(image["Cmd"]),
        "WorkingDir": image["WorkingDir"],
        "Env": [f"{key}={value}" for key, value in environment.items()],
        "Healthcheck": {
            "Test": list(helper.HEALTHCHECK_TEST),
            "Interval": 30_000_000_000,
            "Timeout": 5_000_000_000,
            "Retries": 5,
            "StartPeriod": 20_000_000_000,
            "StartInterval": 0,
        },
    }
    return helper, image, container, revision


def live_host_runtime_config() -> dict:
    helper = load_helper()
    return {
        "PidMode": "",
        "IpcMode": "private",
        "CgroupnsMode": "private",
        "UTSMode": "",
        "UsernsMode": "",
        "Runtime": "runc",
        "AutoRemove": False,
        "PublishAllPorts": False,
        "Init": None,
        "ExtraHosts": [],
        "GroupAdd": None,
        "Dns": None,
        "DnsOptions": None,
        "DnsSearch": None,
        "DeviceCgroupRules": None,
        "Cgroup": "",
        "Sysctls": None,
        "LogConfig": {
            "Type": "json-file",
            "Config": {"max-file": "3", "max-size": "10m"},
        },
        "MaskedPaths": sorted(helper.MASKED_PATHS),
        "ReadonlyPaths": sorted(helper.READONLY_PATHS),
    }


def test_checkpoint_is_canonical_and_rejects_identity_shape_drift():
    helper = load_helper()
    payload = helper.checkpoint_bytes(identity())

    assert payload.endswith(b"\n")
    assert payload == (
        json.dumps(identity(), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    with pytest.raises(helper.ContractError):
        helper.checkpoint_bytes(identity(extra="unexpected"))
    with pytest.raises(helper.ContractError):
        helper.checkpoint_bytes(identity(container_id="short"))
    with pytest.raises(helper.ContractError):
        helper.checkpoint_bytes(identity(image_id="sha256:" + "z" * 64))


def test_config_modes_accept_only_the_two_exact_retained_contracts():
    helper = load_helper()
    foundation = json.dumps(foundation_config()).encode()
    rollback = json.dumps(rollback_config()).encode()

    helper.validate_config(
        foundation, "openclaw.home.hchu.me", 18789, "foundation-or-rollback"
    )
    helper.validate_config(
        rollback, "openclaw.home.hchu.me", 18789, "foundation-or-rollback"
    )
    helper.validate_config(
        rollback, "openclaw.home.hchu.me", 18789, "rollback"
    )
    with pytest.raises(helper.ContractError):
        helper.validate_config(
            foundation, "openclaw.home.hchu.me", 18789, "rollback"
        )

    first_phase2_live = foundation_config()
    assert first_phase2_live["gateway"]["controlUi"]["allowedOrigins"] == [
        "http://127.0.0.1:18789",
        "http://localhost:18789",
    ]
    helper.validate_config(
        json.dumps(first_phase2_live).encode(),
        "openclaw.home.hchu.me",
        18789,
        "foundation-or-rollback",
    )

    drift = rollback_config()
    drift["gateway"]["trustedProxies"] = ["192.168.0.3"]
    with pytest.raises(helper.ContractError):
        helper.validate_config(
            json.dumps(drift).encode(),
            "openclaw.home.hchu.me",
            18789,
            "rollback",
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("gateway", "port"), 18789.0),
        (("gateway", "auth", "allowTailscale"), 0),
        (("gateway", "controlUi", "enabled"), 1),
        (("gateway", "auth", "rateLimit", "maxAttempts"), True),
    ],
)
def test_config_contract_rejects_json_values_equal_only_by_python_coercion(
    path, value
):
    helper = load_helper()
    drift = rollback_config()
    target = drift
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value

    with pytest.raises(helper.ContractError):
        helper.validate_config(
            json.dumps(drift).encode(),
            "openclaw.home.hchu.me",
            18789,
            "rollback",
        )


def test_config_parser_rejects_duplicate_keys_and_non_standard_constants():
    helper = load_helper()
    duplicate = json.dumps(rollback_config(), separators=(",", ":")).replace(
        '"port":18789', '"port":18789,"port":18789', 1
    )
    with pytest.raises(helper.ContractError, match="duplicate JSON key"):
        helper.validate_config(
            duplicate.encode(), "openclaw.home.hchu.me", 18789, "rollback"
        )

    non_standard = json.dumps(rollback_config(), separators=(",", ":")).replace(
        '"port":18789', '"port":NaN', 1
    )
    with pytest.raises(helper.ContractError, match="non-standard JSON constant"):
        helper.validate_config(
            non_standard.encode(), "openclaw.home.hchu.me", 18789, "rollback"
        )


def test_pinned_retained_compose_asset_hash_matches_the_public_manifest():
    helper = load_helper()
    assert hashlib.sha256(COMPOSE.read_bytes()).hexdigest() == helper.COMPOSE_SHA256


def test_exact_retained_runtime_shape_accepts_the_image_and_compose_overlay():
    helper, image, container, revision = runtime_configs()
    helper.validate_runtime_shape(container, image, revision)
    helper.validate_tmpfs(
        {
            "/tmp": (
                "rw,noexec,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=1777"
            )
        }
    )


def test_exact_host_runtime_shape_accepts_the_live_docker_defaults():
    helper = load_helper()
    live = live_host_runtime_config()

    helper.validate_host_runtime_shape(live)
    del live["Init"]
    helper.validate_host_runtime_shape(live)


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("PidMode", "host"),
        ("IpcMode", "host"),
        ("CgroupnsMode", "host"),
        ("UTSMode", "host"),
        ("UsernsMode", "host"),
        ("Runtime", "crun"),
        ("AutoRemove", True),
        ("PublishAllPorts", True),
        ("Init", False),
        ("ExtraHosts", ["attacker:127.0.0.1"]),
        ("GroupAdd", []),
        ("Dns", []),
        ("DnsOptions", []),
        ("DnsSearch", []),
        ("DeviceCgroupRules", []),
        ("Cgroup", "/attacker"),
        ("Sysctls", {"net.ipv4.ip_forward": "1"}),
        (
            "LogConfig",
            {
                "Type": "syslog",
                "Config": {"max-file": "3", "max-size": "10m"},
            },
        ),
    ],
)
def test_exact_host_runtime_shape_rejects_every_boundary_mutation(field, mutation):
    helper = load_helper()
    drift = live_host_runtime_config()
    drift[field] = mutation

    with pytest.raises(helper.ContractError):
        helper.validate_host_runtime_shape(drift)


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        (field, mutation)
        for field in ("MaskedPaths", "ReadonlyPaths")
        for mutation in ("cleared", "extra", "duplicate", "non_list", "non_string")
    ],
)
def test_exact_host_kernel_path_boundary_rejects_every_mutation(field, mutation):
    helper = load_helper()
    drift = live_host_runtime_config()
    original = drift[field]
    if mutation == "cleared":
        drift[field] = []
    elif mutation == "extra":
        drift[field] = [*original, "/attacker"]
    elif mutation == "duplicate":
        drift[field] = [*original, original[0]]
    elif mutation == "non_list":
        drift[field] = tuple(original)
    else:
        drift[field] = [*original[:-1], 1]

    with pytest.raises(helper.ContractError):
        helper.validate_host_runtime_shape(drift)


def live_mount_contract():
    config = Path("/opt/homelab-compose/openclaw-setup/config/openclaw.json")
    state = Path("/srv/homelab/docker-apps/openclaw/state")
    auth = Path("/srv/homelab/docker-apps/openclaw/auth-profile-secrets")
    token = Path("/opt/homelab-control/openclaw/secrets/gateway_token")
    specifications = [
        (str(config), "/etc/openclaw/openclaw.json", False),
        (str(state), "/home/node/.openclaw", True),
        (str(auth), "/home/node/.config/openclaw", True),
        (str(token), "/run/secrets/openclaw_gateway_token", False),
    ]
    host_mounts = []
    for source, target, writable in specifications:
        mount = {"Type": "bind", "Source": source, "Target": target}
        if not writable:
            mount["ReadOnly"] = True
        mount["BindOptions"] = {}
        host_mounts.append(mount)
    realized_mounts = [
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
    return config, state, auth, token, host_mounts, realized_mounts


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_exact_requested_and_realized_mount_contract_accepts_every_realized_order(
    order,
):
    helper = load_helper()
    config, state, auth, token, host_mounts, realized = live_mount_contract()
    realized = [realized[index] for index in order]

    helper.validate_mount_contract(
        host_mounts, realized, config, state, auth, token
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "host_type",
        "host_source",
        "host_target",
        "host_read_only",
        "host_bind_options",
        "host_writable_read_only_key",
        "host_order",
        "realized_type",
        "realized_source",
        "realized_destination",
        "realized_mode",
        "realized_rw",
        "realized_rw_type",
        "realized_propagation",
        "realized_extra_key",
        "realized_missing_key",
        "realized_duplicate",
        "realized_missing",
        "realized_extra",
        "realized_outer_type",
        "realized_entry_type",
        "realized_destination_type",
    ],
)
def test_exact_requested_and_realized_mount_contract_rejects_every_drift(mutation):
    helper = load_helper()
    config, state, auth, token, host_mounts, realized = live_mount_contract()
    if mutation == "host_type":
        host_mounts[0]["Type"] = "volume"
    elif mutation == "host_source":
        host_mounts[0]["Source"] = "/attacker"
    elif mutation == "host_target":
        host_mounts[0]["Target"] = "/attacker"
    elif mutation == "host_read_only":
        host_mounts[0]["ReadOnly"] = False
    elif mutation == "host_bind_options":
        host_mounts[0]["BindOptions"] = {"Propagation": "rshared"}
    elif mutation == "host_writable_read_only_key":
        host_mounts[1]["ReadOnly"] = False
    elif mutation == "host_order":
        host_mounts[0], host_mounts[1] = host_mounts[1], host_mounts[0]
    elif mutation == "realized_type":
        realized[0]["Type"] = "volume"
    elif mutation == "realized_source":
        realized[0]["Source"] = "/attacker"
    elif mutation == "realized_destination":
        realized[0]["Destination"] = "/attacker"
    elif mutation == "realized_mode":
        realized[0]["Mode"] = "ro"
    elif mutation == "realized_rw":
        realized[0]["RW"] = True
    elif mutation == "realized_rw_type":
        realized[0]["RW"] = 0
    elif mutation == "realized_propagation":
        realized[0]["Propagation"] = "rshared"
    elif mutation == "realized_extra_key":
        realized[0]["Name"] = "unexpected"
    elif mutation == "realized_missing_key":
        realized[0].pop("Propagation")
    elif mutation == "realized_duplicate":
        realized[1] = dict(realized[0])
    elif mutation == "realized_missing":
        realized.pop()
    elif mutation == "realized_extra":
        realized.append(
            {
                "Type": "bind",
                "Source": "/attacker",
                "Destination": "/attacker",
                "Mode": "",
                "RW": False,
                "Propagation": "rprivate",
            }
        )
    elif mutation == "realized_outer_type":
        realized = tuple(realized)
    elif mutation == "realized_entry_type":
        realized[0] = "malformed"
    else:
        realized[0]["Destination"] = ["/etc/openclaw/openclaw.json"]

    with pytest.raises(helper.ContractError):
        helper.validate_mount_contract(
            host_mounts, realized, config, state, auth, token
        )


def test_apparmor_profile_accepts_only_the_exact_live_empty_value():
    helper = load_helper()
    helper.validate_apparmor_profile("")

    for drift in (None, "unconfined", "docker-default", False):
        with pytest.raises(helper.ContractError):
            helper.validate_apparmor_profile(drift)


@pytest.mark.parametrize(
    "mutation", ["cmd", "healthcheck", "environment", "image_user"]
)
def test_exact_retained_runtime_shape_rejects_mutation(mutation):
    helper, image, container, revision = runtime_configs()
    if mutation == "cmd":
        container["Cmd"] = ["true"]
    elif mutation == "healthcheck":
        container["Healthcheck"]["Retries"] = 1
    elif mutation == "environment":
        container["Env"].append("NODE_OPTIONS=--require=/tmp/attack.js")
    else:
        image["User"] = "root"

    with pytest.raises(helper.ContractError):
        helper.validate_runtime_shape(container, image, revision)


@pytest.mark.parametrize(
    "options",
    [
        "rw,exec,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=1777",
        "rw,noexec,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=1777,exec",
        "rw,noexec,nosuid,nodev,size=32m,size=32768k,uid=1000,gid=1000,mode=1777",
    ],
)
def test_exact_tmpfs_contract_rejects_contradictory_or_extra_options(options):
    helper = load_helper()
    with pytest.raises(helper.ContractError):
        helper.validate_tmpfs({"/tmp": options})


@pytest.mark.parametrize(
    "record",
    [
        b"120000 " + b"a" * 40 + b" 0\tconfig/link\0",
        b"160000 " + b"a" * 40 + b" 0\tvendor/submodule\0",
        b"100644 " + b"a" * 40 + b" 0\t.env\nprod\0",
        b"100644 " + b"a" * 40 + b" 0\tnested/auth/token\0",
        b"100644 " + b"a" * 40 + b" 0\tstate/session.json\0",
        b"100644 " + b"a" * 40 + b" 0\tconfig/openclaw.json",
    ],
)
def test_git_index_classifier_rejects_unsafe_byte_records(record):
    helper = load_helper()
    with pytest.raises(helper.ContractError):
        helper.validate_tracked_paths(record)


def test_git_index_classifier_allows_only_regular_non_sensitive_paths():
    helper = load_helper()
    manifest = b"".join(
        b"100644 " + b"a" * 40 + b" 0\t" + path + b"\0"
        for path in (
            b".env.example",
            b"README.md",
            b"config/openclaw.json",
            b"nested/authentication/README",
        )
    )
    assert len(helper.validate_tracked_paths(manifest)) == 4


def test_rollback_alias_may_be_free_or_self_owned_but_never_collide(monkeypatch):
    helper = load_helper()
    container_id = "a" * 64

    for owners in ([], [container_id]):
        monkeypatch.setattr(helper, "rollback_alias_owners", lambda: owners)
        helper.require_rollback_alias_available(container_id)

    for owners in (["b" * 64], [container_id, container_id]):
        monkeypatch.setattr(helper, "rollback_alias_owners", lambda: owners)
        with pytest.raises(helper.ContractError):
            helper.require_rollback_alias_available(container_id)


def live_endpoint(container_id: str, proxy: bool, running: bool = True) -> dict:
    if proxy:
        aliases = ["openclaw-rollback"]
        dns_names = [
            "openclaw-openclaw-gateway-1",
            "openclaw-rollback",
            container_id[:12],
        ]
    else:
        aliases = ["openclaw-openclaw-gateway-1", "openclaw-gateway"]
        dns_names = [
            "openclaw-openclaw-gateway-1",
            "openclaw-gateway",
            container_id[:12],
        ]
    return {
        "Aliases": aliases,
        "DNSNames": dns_names,
        "IPAMConfig": None,
        "DriverOpts": None,
        "Links": None,
        "GwPriority": 0,
        "MacAddress": "02:42:ac:14:00:05" if running else "",
    }


def test_exact_retained_container_dns_identity_accepts_only_the_live_shape():
    helper = load_helper()
    container_id = "a" * 64

    helper.validate_container_dns_identity(
        "/openclaw-openclaw-gateway-1",
        container_id[:12],
        container_id,
        live_endpoint(container_id, proxy=False),
        live_endpoint(container_id, proxy=True),
        True,
    )
    helper.validate_container_dns_identity(
        "/openclaw-openclaw-gateway-1",
        container_id[:12],
        container_id,
        live_endpoint(container_id, proxy=False, running=False),
        None,
        False,
    )


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("name", "/openclaw-openclaw-gateway-renamed"),
        ("hostname", "attacker"),
        ("default_aliases", ["openclaw-gateway"]),
        (
            "default_dns_names",
            ["openclaw-openclaw-gateway-1", "openclaw-gateway", "wrong-short-id"],
        ),
        ("proxy_aliases", ["openclaw-rollback", "poisoned-route"]),
        ("proxy_aliases", ["openclaw-rollback", "openclaw-rollback"]),
        ("proxy_aliases", None),
        ("proxy_aliases", "openclaw-rollback"),
        ("proxy_aliases", ["openclaw-rollback", 1]),
        (
            "proxy_dns_names",
            ["openclaw-openclaw-gateway-1", "openclaw-rollback", "wrong-short-id"],
        ),
        ("default_ipam", {"IPv4Address": "172.20.0.50"}),
        ("default_driver_opts", {}),
        ("default_links", []),
        ("default_gw_priority", 1),
        ("default_mac", ""),
        ("proxy_ipam", {"IPv4Address": "172.20.0.50"}),
        ("proxy_driver_opts", {}),
        ("proxy_links", []),
        ("proxy_gw_priority", 1),
        ("proxy_mac", ""),
        ("proxy_mac", "not-a-mac"),
        ("proxy_mac", "ff:ff:ff:ff:ff:ff"),
        ("proxy_mac", "00:00:00:00:00:00"),
    ],
)
def test_exact_retained_container_dns_identity_rejects_every_drift(mutation, value):
    helper = load_helper()
    container_id = "a" * 64
    name = "/openclaw-openclaw-gateway-1"
    hostname = container_id[:12]
    default_network = live_endpoint(container_id, proxy=False)
    proxy_network = live_endpoint(container_id, proxy=True)
    if mutation == "name":
        name = value
    elif mutation == "hostname":
        hostname = value
    elif mutation == "default_aliases":
        default_network["Aliases"] = value
    elif mutation == "default_dns_names":
        default_network["DNSNames"] = value
    elif mutation == "proxy_aliases":
        proxy_network["Aliases"] = value
    elif mutation == "proxy_dns_names":
        proxy_network["DNSNames"] = value
    elif mutation.startswith("default_"):
        field = {
            "default_ipam": "IPAMConfig",
            "default_driver_opts": "DriverOpts",
            "default_links": "Links",
            "default_gw_priority": "GwPriority",
            "default_mac": "MacAddress",
        }[mutation]
        default_network[field] = value
    else:
        field = {
            "proxy_ipam": "IPAMConfig",
            "proxy_driver_opts": "DriverOpts",
            "proxy_links": "Links",
            "proxy_gw_priority": "GwPriority",
            "proxy_mac": "MacAddress",
        }[mutation]
        proxy_network[field] = value

    with pytest.raises(helper.ContractError):
        helper.validate_container_dns_identity(
            name,
            hostname,
            container_id,
            default_network,
            proxy_network,
            True,
        )


def test_alias_owner_scan_rejects_string_aliases_instead_of_using_substring_membership(
    monkeypatch,
):
    helper = load_helper()
    container_id = "a" * 64

    class Result:
        stdout = (container_id + "\n").encode("ascii")

    monkeypatch.setattr(helper, "run", lambda _command: Result())
    monkeypatch.setattr(
        helper,
        "load_json",
        lambda _command: [
            {
                "Id": container_id,
                "NetworkSettings": {
                    "Networks": {
                        "homelab_proxy": {"Aliases": "openclaw-rollback"}
                    }
                },
            }
        ],
    )

    with pytest.raises(helper.ContractError, match="aliases are malformed"):
        helper.rollback_alias_owners()


@pytest.mark.parametrize(
    ("state", "accepted"),
    [
        ({"Running": False, "Status": "created"}, True),
        ({"Running": False, "Status": "exited"}, True),
        ({"Running": True, "Status": "running"}, False),
        ({"Running": False, "Status": "paused"}, False),
        ({"Running": 0, "Status": "created"}, False),
        ({"Running": False, "Status": None}, False),
        ([], False),
    ],
)
def test_fenced_lifecycle_accepts_only_created_or_exited_nonrunning_containers(
    state, accepted
):
    helper = load_helper()

    if accepted:
        helper.require_fenced_lifecycle_state(state)
    else:
        with pytest.raises(helper.ContractError):
            helper.require_fenced_lifecycle_state(state)


@pytest.mark.parametrize(
    ("container_state", "status", "running", "health", "networks", "accepted"),
    [
        ("fenced", "created", False, None, {"openclaw_default"}, True),
        ("fenced", "exited", False, None, {"openclaw_default"}, True),
        ("fenced", "created", False, None, {"openclaw_default", "rogue"}, False),
        ("fenced", "paused", False, None, {"openclaw_default"}, False),
        ("rollback", "created", False, None, {"openclaw_default"}, True),
        ("rollback", "exited", False, None, {"openclaw_default"}, True),
        ("rollback", "running", True, "starting", {"openclaw_default"}, True),
        (
            "rollback",
            "running",
            True,
            "healthy",
            {"openclaw_default", "homelab_proxy"},
            True,
        ),
        ("rollback", "running", True, "unhealthy", {"openclaw_default"}, False),
        ("rollback", "running", True, "healthy", {"openclaw_default", "rogue"}, False),
        (
            "running",
            "running",
            True,
            "healthy",
            {"openclaw_default", "homelab_proxy"},
            True,
        ),
        ("running", "running", True, "healthy", {"openclaw_default"}, False),
    ],
)
def test_retained_lifecycle_accepts_only_exact_network_and_health_states(
    container_state, status, running, health, networks, accepted
):
    helper = load_helper()
    state = {"Running": running, "Status": status}
    fenced = not running and status in {"created", "exited"}

    def validate():
        if container_state == "fenced":
            helper.require_fenced_lifecycle_state(state)
            helper.require(set(networks) == {"openclaw_default"}, "networks")
        elif container_state == "rollback":
            helper.require(fenced or running, "state")
            if fenced:
                helper.require(set(networks) == {"openclaw_default"}, "networks")
            else:
                helper.require(health in {"starting", "healthy"}, "health")
                helper.require(
                    set(networks)
                    in (
                        {"openclaw_default"},
                        {"openclaw_default", "homelab_proxy"},
                    ),
                    "networks",
                )
        else:
            helper.require(running and health == "healthy", "state")
            helper.require(
                set(networks) == {"openclaw_default", "homelab_proxy"},
                "networks",
            )

    if accepted:
        validate()
    else:
        with pytest.raises(helper.ContractError):
            validate()


def test_checkpoint_is_seeded_only_after_native_fence_and_required_before_rollback_start():
    tasks = yaml.safe_load(ROLE.read_text(encoding="utf-8"))
    install = task_by_name(
        tasks, "Install the retained OpenClaw rollback verifier after validated cutover"
    )
    assert install["when"] == (
        "openclaw_native_cutover_marker.stat.exists | default(false)"
    )
    assert install["ansible.builtin.copy"]["owner"] == "root"
    assert install["ansible.builtin.copy"]["group"] == "root"
    assert install["ansible.builtin.copy"]["mode"] == "0755"
    assert install["ansible.builtin.copy"]["follow"] is False
    inspect_directories = task_by_name(
        tasks, "Inspect the retained verifier executable path after validated cutover"
    )
    assert inspect_directories["ansible.builtin.stat"]["follow"] is False
    assert inspect_directories["loop"] == [
        "/usr/local",
        "/usr/local/libexec",
        "{{ openclaw_retained_gateway_verifier_path }}",
    ]
    assert_directories = task_by_name(
        tasks, "Require a real root-owned retained verifier executable path"
    )
    executable_directory = task_by_name(
        tasks, "Create the root-owned local executable directory after validated cutover"
    )
    assert tasks.index(inspect_directories) < tasks.index(assert_directories)
    assert tasks.index(assert_directories) < tasks.index(executable_directory)
    assert tasks.index(executable_directory) < tasks.index(install)
    assert executable_directory["ansible.builtin.file"] == {
        "path": "/usr/local/libexec",
        "state": "directory",
        "owner": "root",
        "group": "root",
        "mode": "0755",
        "follow": False,
    }
    inspect_install = task_by_name(
        tasks, "Inspect the installed retained OpenClaw rollback verifier"
    )
    inspect_source = task_by_name(
        tasks, "Inspect the bundled retained OpenClaw rollback verifier"
    )
    assert_install = task_by_name(
        tasks, "Require the exact installed retained OpenClaw rollback verifier"
    )
    assert tasks.index(install) < tasks.index(inspect_source)
    assert tasks.index(inspect_source) < tasks.index(inspect_install)
    assert tasks.index(inspect_install) < tasks.index(assert_install)
    assert inspect_source["delegate_to"] == "localhost"
    assert inspect_source["become"] is False
    assert inspect_source["ansible.builtin.stat"]["path"] == (
        "{{ role_path }}/files/openclaw_retained_gateway.py"
    )
    assert inspect_source["ansible.builtin.stat"]["checksum_algorithm"] == "sha256"
    assert inspect_install["ansible.builtin.stat"]["checksum_algorithm"] == "sha256"
    installed_requirements = assert_install["ansible.builtin.assert"]["that"]
    assert any("stat.isreg" in requirement for requirement in installed_requirements)
    assert any("stat.islnk" in requirement for requirement in installed_requirements)
    assert any("stat.nlink" in requirement for requirement in installed_requirements)
    assert any("stat.checksum" in requirement for requirement in installed_requirements)

    preserve = task_by_name(
        tasks, "Preserve the validated native cutover and hold Docker Gateway stopped"
    )["block"]
    running = task_by_name(
        preserve, "Inspect whether the retained Docker OpenClaw Gateway is running"
    )
    stop = task_by_name(preserve, "Stop the retained Docker OpenClaw Gateway")
    disconnect = task_by_name(
        preserve, "Disconnect the stopped rollback Gateway from the proxy network"
    )
    proof = task_by_name(
        preserve,
        "Require retained rollback assets and their immutable identity checkpoint",
    )
    assert preserve.index(running) < preserve.index(stop) < preserve.index(disconnect)
    assert stop["when"] == (
        "openclaw_retained_gateway_running.stdout | trim | length > 0"
    )
    assert stop["changed_when"] is True
    assert preserve.index(disconnect) < preserve.index(proof)
    assert proof["ansible.builtin.command"]["argv"][1:4] == [
        "require",
        "--container-state",
        "fenced",
    ]
    assert "--require-expected-token" in proof["ansible.builtin.command"]["argv"]
    assert proof["no_log"] is True
    assert proof["when"] == "hostvars['openclaw'].openclaw_native_activate | bool"
    assert proof["changed_when"] is False
    assert_bounded_fail_closed_retry(proof)
    assert all(
        "seed" not in task.get("ansible.builtin.command", {}).get("argv", [])
        for task in preserve
    )

    rollback = task_by_name(
        tasks, "Activate the exact retained Docker Gateway for tracked rollback"
    )["block"]
    checkpoint = task_by_name(
        rollback,
        "Require the exact retained rollback assets and immutable identity checkpoint",
    )
    start = task_by_name(rollback, "Start only the exact retained Docker Gateway")
    attach = task_by_name(
        rollback, "Attach the retained Gateway to the proxy with its unique rollback alias"
    )
    assert rollback.index(checkpoint) < rollback.index(start) < rollback.index(attach)
    assert checkpoint["ansible.builtin.command"]["argv"][1:4] == [
        "require",
        "--container-state",
        "rollback",
    ]
    assert "--require-expected-token" in checkpoint["ansible.builtin.command"]["argv"]
    assert_bounded_fail_closed_retry(checkpoint)


def test_full_validation_rechecks_checkpoint_in_native_and_rollback_states():
    plays = yaml.safe_load(VALIDATE.read_text(encoding="utf-8"))
    docker_play = next(play for play in plays if play["hosts"] == "svc_docker_apps")
    task = task_by_name(
        docker_play["tasks"],
        "Validate retained rollback assets and their immutable identity checkpoint",
    )
    inspect_boundary = task_by_name(
        docker_play["tasks"],
        "Inspect the retained rollback verifier executable boundary",
    )
    assert_boundary = task_by_name(
        docker_play["tasks"],
        "Validate the exact retained rollback verifier executable boundary",
    )
    source_boundary = task_by_name(
        docker_play["tasks"],
        "Inspect the bundled retained rollback verifier for validation",
    )
    assert docker_play["tasks"].index(inspect_boundary) < docker_play["tasks"].index(
        source_boundary
    ) < docker_play["tasks"].index(assert_boundary)
    assert docker_play["tasks"].index(assert_boundary) < docker_play["tasks"].index(task)
    assert inspect_boundary["ansible.builtin.stat"]["follow"] is False
    assert inspect_boundary["ansible.builtin.stat"]["checksum_algorithm"] == "sha256"
    assert inspect_boundary["loop"] == [
        "/usr/local",
        "/usr/local/libexec",
        "{{ openclaw_retained_gateway_verifier_path }}",
    ]
    boundary_requirements = assert_boundary["ansible.builtin.assert"]["that"]
    assert any("results[2].stat.isreg" in value for value in boundary_requirements)
    assert any("results[2].stat.islnk" in value for value in boundary_requirements)
    assert any("results[2].stat.nlink" in value for value in boundary_requirements)
    assert any("stat.checksum" in value for value in boundary_requirements)
    argv = task["ansible.builtin.command"]["argv"]
    assert argv[1] == "require"
    assert "'running' if openclaw_docker_rollback_activate" in argv[3]
    assert "else 'fenced'" in argv[3]
    assert task["when"] == [
        "openclaw_native_cutover_marker_validation.stat.exists | default(false)",
        "(hostvars['openclaw'].openclaw_native_activate | bool) or "
        "(openclaw_docker_rollback_activate | bool)",
    ]
    assert_bounded_fail_closed_retry(task)


def test_pre_site_fence_verifies_before_native_mutation_and_always_cleans_up():
    play = yaml.safe_load(FENCE.read_text(encoding="utf-8"))[0]
    checkpoint_stat = task_by_name(
        play["tasks"],
        "Inspect the retained Gateway identity checkpoint bootstrap boundary",
    )
    verifier_stat = task_by_name(
        play["tasks"],
        "Inspect the persistent retained Gateway verifier bootstrap boundary",
    )
    transaction = task_by_name(
        play["tasks"], "Verify retained rollback assets before any native-primary mutation"
    )
    allocate = task_by_name(
        transaction["block"], "Allocate an ephemeral retained OpenClaw rollback verifier"
    )
    install = task_by_name(
        transaction["block"], "Install the ephemeral retained OpenClaw rollback verifier"
    )
    include = task_by_name(
        transaction["block"], "Run the retained OpenClaw pre-site ownership transaction"
    )
    cleanup = task_by_name(
        transaction["always"], "Remove the ephemeral retained OpenClaw rollback verifier"
    )
    assert allocate["ansible.builtin.tempfile"]["path"] == "/run"
    assert install["ansible.builtin.copy"]["dest"] == (
        "{{ openclaw_pre_site_retained_gateway_verifier.path }}"
    )
    assert include["ansible.builtin.include_tasks"] == (
        "fence-openclaw-retained-assets.yml"
    )
    assert cleanup["ansible.builtin.file"]["state"] == "absent"
    assert checkpoint_stat["ansible.builtin.stat"]["follow"] is False
    assert verifier_stat["ansible.builtin.stat"]["follow"] is False
    assert play["tasks"].index(checkpoint_stat) < play["tasks"].index(transaction)
    assert play["tasks"].index(verifier_stat) < play["tasks"].index(transaction)

    tasks = yaml.safe_load(FENCE_ASSETS.read_text(encoding="utf-8"))
    container = task_by_name(
        tasks,
        "Inspect whether the retained Gateway container exists before native-primary fencing",
    )
    inspect = task_by_name(
        tasks,
        "Inspect whether the retained Gateway is running before native-primary fencing",
    )
    bootstrap_boundary = task_by_name(
        tasks,
        "Require a pristine retained Gateway bootstrap boundary before recovery",
    )
    recovery_preflight = task_by_name(
        tasks,
        "Preflight exact retained rollback assets before cold recovery",
    )
    create = task_by_name(
        tasks,
        "Create an absent retained Docker Gateway fenced for initial identity seeding",
    )
    pre_stop = task_by_name(
        tasks,
        "Require a checkpoint-matching running rollback Gateway before stopping it",
    )
    stop = task_by_name(
        tasks, "Stop the retained Docker Gateway before native-primary reconciliation"
    )
    stopped_require = task_by_name(
        tasks, "Re-require the exact stopped Gateway after leaving rollback"
    )
    checkpoint = task_by_name(
        tasks,
        "Bootstrap the stopped retained identity checkpoint once or require it thereafter",
    )
    rollback = task_by_name(
        tasks, "Require the exact retained identity checkpoint before tracked rollback"
    )
    assert tasks.index(container) < tasks.index(inspect) < tasks.index(bootstrap_boundary)
    assert tasks.index(bootstrap_boundary) < tasks.index(recovery_preflight) < tasks.index(create)
    assert tasks.index(create) < tasks.index(pre_stop) < tasks.index(stop)
    assert container["ansible.builtin.command"]["argv"] == [
        "docker",
        "compose",
        "ps",
        "--all",
        "-q",
        "openclaw-gateway",
    ]
    assert container["changed_when"] is False
    bootstrap_requirements = bootstrap_boundary["ansible.builtin.assert"]["that"]
    for requirement in (
        "openclaw_native_primary_source_hold.stat.exists | default(false)",
        "openclaw_native_primary_source_hold.stat.isreg | default(false)",
        "not (openclaw_native_primary_source_hold.stat.islnk | default(false))",
        "not (openclaw_pre_site_retained_gateway_checkpoint.stat.exists | default(false))",
        "not (openclaw_pre_site_retained_gateway_checkpoint.stat.islnk | default(false))",
        "not (openclaw_pre_site_persistent_retained_gateway_verifier.stat.exists | default(false))",
        "not (openclaw_pre_site_persistent_retained_gateway_verifier.stat.islnk | default(false))",
    ):
        assert requirement in bootstrap_requirements
    assert "length == 0" in bootstrap_boundary["when"][-1]
    assert bootstrap_boundary["no_log"] is True
    assert recovery_preflight["ansible.builtin.command"]["argv"][1:4] == [
        "preflight",
        "--container-state",
        "fenced",
    ]
    assert "--require-expected-token" in recovery_preflight["ansible.builtin.command"]["argv"]
    assert_bounded_fail_closed_retry(recovery_preflight)
    assert create["ansible.builtin.command"]["argv"] == [
        "docker",
        "compose",
        "create",
        "openclaw-gateway",
    ]
    create_conditions = "\n".join(create["when"])
    for requirement in (
        "openclaw_native_primary_source_hold.stat.exists",
        "openclaw_pre_site_retained_gateway_container.stdout | trim | length == 0",
        "not (openclaw_pre_site_retained_gateway_checkpoint.stat.exists",
        "not (openclaw_pre_site_persistent_retained_gateway_verifier.stat.exists",
    ):
        assert requirement in create_conditions
    assert "running.stdout" not in create_conditions
    assert "start" not in " ".join(create["ansible.builtin.command"]["argv"])
    assert tasks.index(stop) < tasks.index(stopped_require)
    assert "length > 0" in pre_stop["when"][-1]
    assert "length > 0" in stop["when"][-1]
    assert stop["changed_when"] is True
    assert "length > 0" in stopped_require["when"][-1]
    assert "length == 0" in checkpoint["when"][-1]
    mode = checkpoint["ansible.builtin.command"]["argv"][1]
    assert "'seed'" in mode and "else 'require'" in mode
    assert "openclaw_pre_site_retained_gateway_checkpoint.stat.exists" in mode
    assert "openclaw_pre_site_persistent_retained_gateway_verifier.stat.exists" in mode
    assert "stat.islnk" in mode
    assert pre_stop["ansible.builtin.command"]["argv"][3] == "rollback"
    assert stopped_require["ansible.builtin.command"]["argv"][3] == "fenced"
    assert checkpoint["ansible.builtin.command"]["argv"][3] == "fenced"
    assert rollback["ansible.builtin.command"]["argv"][5] == "rollback"
    for verifier_task in (pre_stop, stopped_require, checkpoint, rollback):
        assert_bounded_fail_closed_retry(verifier_task)


@pytest.mark.parametrize(
    (
        "checkpoint_exists",
        "checkpoint_islnk",
        "verifier_exists",
        "verifier_islnk",
        "expected_mode",
    ),
    [
        (False, False, False, False, "seed"),
        (False, False, True, False, "require"),
        (True, False, False, False, "require"),
        (True, False, True, False, "require"),
        (False, True, False, False, "require"),
        (False, False, False, True, "require"),
    ],
)
def test_one_time_checkpoint_bootstrap_mode_for_all_existence_tuples(
    checkpoint_exists,
    checkpoint_islnk,
    verifier_exists,
    verifier_islnk,
    expected_mode,
):
    tasks = yaml.safe_load(FENCE_ASSETS.read_text(encoding="utf-8"))
    task = task_by_name(
        tasks,
        "Bootstrap the stopped retained identity checkpoint once or require it thereafter",
    )
    template = Environment(undefined=StrictUndefined).from_string(
        task["ansible.builtin.command"]["argv"][1]
    )
    mode = template.render(
        openclaw_pre_site_retained_gateway_checkpoint={
            "stat": {"exists": checkpoint_exists, "islnk": checkpoint_islnk}
        },
        openclaw_pre_site_persistent_retained_gateway_verifier={
            "stat": {"exists": verifier_exists, "islnk": verifier_islnk}
        },
    ).strip()

    assert mode == expected_mode


def test_helper_enforces_full_runtime_hardening_and_mount_contract():
    source = HELPER.read_text(encoding="utf-8")
    for contract in (
        "ReadonlyRootfs",
        "RestartPolicy",
        "CapDrop",
        "SecurityOpt",
        "Privileged",
        "CapAdd",
        "Devices",
        "DeviceRequests",
        "PidMode",
        "IpcMode",
        "CgroupnsMode",
        "UTSMode",
        "UsernsMode",
        "Runtime",
        "AutoRemove",
        "PublishAllPorts",
        "ExtraHosts",
        "GroupAdd",
        "DnsOptions",
        "DnsSearch",
        "MaskedPaths",
        "ReadonlyPaths",
        "/openclaw-openclaw-gateway-1",
        "openclaw-rollback",
        "openclaw_default",
        "PortBindings",
        '"127.0.0.1"',
        '"/etc/openclaw/openclaw.json"',
        '"/home/node/.openclaw"',
        '"/home/node/.config/openclaw"',
        '"/run/secrets/openclaw_gateway_token"',
        "com.docker.compose.project",
        "com.docker.compose.service",
        "com.getarcaneapp.arcane.updater",
    ):
        assert contract in source


def test_helper_never_overwrites_or_removes_the_checkpoint():
    source = HELPER.read_text(encoding="utf-8")
    seed = source.split("def seed_checkpoint", 1)[1].split("def parse_arguments", 1)[0]

    assert "publish_checkpoint(temporary_name, path)" in seed
    assert "RENAME_NOREPLACE" in source
    assert "require_checkpoint(path, candidate)" in seed
    assert "os.replace" not in seed
    assert "os.rename" not in seed
    assert "os.unlink(path)" not in seed


def test_checkpoint_publication_handles_success_collision_and_error(monkeypatch):
    helper = load_helper()

    class FakeRename:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            return self.result

    class FakeLibc:
        def __init__(self, result):
            self.renameat2 = FakeRename(result)

    monkeypatch.setattr(helper.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc(0))
    helper.rename_noreplace("source", Path("destination"))

    monkeypatch.setattr(helper.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc(-1))
    monkeypatch.setattr(helper.ctypes, "get_errno", lambda: helper.errno.EEXIST)
    with pytest.raises(FileExistsError):
        helper.rename_noreplace("source", Path("destination"))

    monkeypatch.setattr(helper.ctypes, "get_errno", lambda: helper.errno.EIO)
    with pytest.raises(helper.ContractError):
        helper.rename_noreplace("source", Path("destination"))


def test_checkpoint_rerun_recovers_after_publication_before_directory_fsync(
    monkeypatch, tmp_path
):
    helper = load_helper()
    checkpoint = tmp_path / "retained-gateway-identity.json"
    candidate = helper.checkpoint_bytes(identity())
    temporary = tmp_path / ".retained-gateway-identity.test"
    temporary.write_bytes(candidate)

    def simulated_atomic_publication(source, destination):
        Path(destination).write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    monkeypatch.setattr(helper, "rename_noreplace", simulated_atomic_publication)

    def interrupted_fsync(_path):
        raise OSError("simulated interruption after atomic publication")

    monkeypatch.setattr(helper, "fsync_directory", interrupted_fsync)
    with pytest.raises(OSError, match="simulated interruption"):
        helper.publish_checkpoint(str(temporary), checkpoint)

    assert checkpoint.read_bytes() == candidate
    assert checkpoint.stat().st_nlink == 1
    assert not list(tmp_path.glob(".retained-gateway-identity.*"))

    monkeypatch.setattr(
        helper,
        "require_checkpoint",
        lambda path, expected: (
            None if path.read_bytes() == expected else pytest.fail("checkpoint drift")
        ),
    )
    assert helper.seed_checkpoint(checkpoint, candidate) is False
