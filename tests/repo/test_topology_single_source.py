import ipaddress
import json
import os
import re
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


TOPOLOGY_PATH = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"
MANAGED_SERVICES = ("tailnet", "docker_apps", "openclaw")
HOST_FIELDS = {
    "ansible_host",
    "vmid",
    "hostname",
    "description",
    "lxc_tags",
    "template_file_id",
    "os_type",
    "prefix_length",
    "mac_address",
    "gateway",
    "root_disk_gb",
    "cores",
    "memory_mb",
    "swap_mb",
    "startup_order",
    "lxc_root_options",
}


def load_topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def managed_hosts() -> dict[str, dict]:
    return load_topology()["all"]["children"]["debian"]["hosts"]


def test_topology_is_a_complete_static_ansible_inventory():
    topology = load_topology()
    assert set(topology) == {"all"}
    assert set(topology["all"]) == {"vars", "children"}
    assert topology["all"]["vars"] == {"ansible_user": "root"}

    children = topology["all"]["children"]
    assert set(children) == {
        "pve_hosts",
        "debian",
        "svc_tailnet",
        "svc_docker_apps",
        "svc_openclaw",
    }
    assert children["pve_hosts"] == {
        "hosts": {"pve": {"ansible_host": "192.168.0.2"}}
    }
    assert tuple(children["debian"]["hosts"]) == MANAGED_SERVICES
    for service in MANAGED_SERVICES:
        assert children[f"svc_{service}"] == {"hosts": {service: {}}}

    ansible_config = (REPO_ROOT / "infra/ansible/ansible.cfg").read_text(
        encoding="utf-8"
    )
    assert "inventory = inventory/prod/topology.json" in ansible_config


def test_managed_host_schema_and_identifiers_are_valid_and_unique():
    hosts = managed_hosts()
    expected_identities = {
        "tailnet": (111, "tailnet", "192.168.0.4", "02:00:00:BA:EC:04"),
        "docker_apps": (
            110,
            "docker-apps",
            "192.168.0.3",
            "02:00:00:BA:EC:03",
        ),
        "openclaw": (118, "openclaw", "192.168.0.5", "02:00:00:BA:EC:05"),
    }

    for name, host in hosts.items():
        assert set(host) == HOST_FIELDS
        assert (
            host["vmid"],
            host["hostname"],
            host["ansible_host"],
            host["mac_address"],
        ) == expected_identities[name]
        assert type(host["vmid"]) is int and host["vmid"] >= 100
        assert type(host["prefix_length"]) is int
        assert 1 <= host["prefix_length"] <= 32
        assert ipaddress.ip_address(host["ansible_host"]).version == 4
        assert ipaddress.ip_address(host["gateway"]).version == 4
        assert re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", host["mac_address"])
        assert host["os_type"] == "debian"
        assert host["description"]
        assert host["lxc_tags"] == [
            "homelab",
            "managed-by-opentofu",
            f"role-{name.replace('_', '-')}",
        ]
        assert re.fullmatch(
            r"local:vztmpl/debian-13-standard_[^/]+_amd64\.tar\.zst",
            host["template_file_id"],
        )
        for field in (
            "root_disk_gb",
            "cores",
            "memory_mb",
            "swap_mb",
            "startup_order",
        ):
            assert type(host[field]) is int
        assert host["root_disk_gb"] > 0
        assert host["cores"] > 0
        assert host["memory_mb"] > 0
        assert host["swap_mb"] >= 0
        assert host["startup_order"] > 0

        root_options = host["lxc_root_options"]
        assert isinstance(root_options, dict)
        assert isinstance(root_options["settings"], list)
        assert root_options["settings"]
        for setting in root_options["settings"]:
            assert set(setting) == {"description", "pattern", "pct_args"}
            assert all(isinstance(value, str) and value for value in setting.values())
        for setting in root_options.get("absent_settings", []):
            assert set(setting) == {
                "description",
                "pattern",
                "delete_matching_keys",
            }
            assert type(setting["delete_matching_keys"]) is bool
        assert all(
            isinstance(path, str) and path
            for path in root_options.get("bind_mount_sources", [])
        )

    for field in ("vmid", "hostname", "ansible_host", "mac_address", "startup_order"):
        values = [host[field] for host in hosts.values()]
        assert len(values) == len(set(values)), f"duplicate {field}: {values}"
    assert sorted(host["startup_order"] for host in hosts.values()) == [1, 2, 3]


@pytest.mark.skipif(os.name == "nt", reason="Ansible's controller CLI requires POSIX")
def test_ansible_inventory_cli_loads_topology_groups_and_hostvars():
    executable = shutil.which("ansible-inventory")
    if executable is None:
        pytest.skip("ansible-inventory is not installed")
    result = subprocess.run(
        [executable, "-i", str(TOPOLOGY_PATH), "--list"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    inventory = json.loads(result.stdout)
    assert set(inventory["debian"]["hosts"]) == set(MANAGED_SERVICES)
    assert inventory["svc_tailnet"]["hosts"] == ["tailnet"]
    assert inventory["svc_docker_apps"]["hosts"] == ["docker_apps"]
    assert inventory["svc_openclaw"]["hosts"] == ["openclaw"]
    assert inventory["_meta"]["hostvars"]["docker_apps"]["vmid"] == 110


def test_ansible_aliases_derive_from_inventory_without_duplicate_topology_lists():
    text = (
        REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml"
    ).read_text(encoding="utf-8")
    assert "tailnet_ip: \"{{ hostvars['tailnet'].ansible_host }}\"" in text
    assert "docker_apps_ip: \"{{ hostvars['docker_apps'].ansible_host }}\"" in text
    for duplicate in (
        "openclaw_lxc_allocation:",
        "pve_lxc_root_options:",
        "pve_lxc_access_bootstrap:",
    ):
        assert duplicate not in text


def test_opentofu_decodes_the_inventory_and_retired_sources_are_deleted():
    main = (REPO_ROOT / "infra/opentofu/envs/prod/main.tf").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r'jsondecode\(\s*file\("\$\{path\.module\}/\.\./\.\./\.\./ansible/'
        r'inventory/prod/topology\.json"\)\s*\)',
        main,
    )
    assert "local.production_topology.all.children.debian.hosts" in main
    assert 'ip_address = "${host.ansible_host}/${host.prefix_length}"' in main
    assert "tags             = each.value.lxc_tags" in main
    assert "for_each = local.containers" in main
    assert "var.containers" not in main
    assert "removed {" not in main

    variables = (REPO_ROOT / "infra/opentofu/envs/prod/variables.tf").read_text(
        encoding="utf-8"
    )
    assert 'variable "containers"' not in variables

    retired_sources = (
        "infra/opentofu/envs/prod/containers.auto.tfvars",
        "infra/ansible/inventory/prod/hosts.yml",
        "scripts/ci/homelab_topology.py",
        "scripts/ci/render_ansible_inventory.py",
        "scripts/ci/render_ansible_targets.py",
    )
    for path in retired_sources:
        assert not (REPO_ROOT / path).exists(), path

    guard = (REPO_ROOT / "scripts/ci/check_tofu_plan_safe.py").read_text(
        encoding="utf-8"
    )
    assert "topology.json" in guard
    assert "homelab_topology" not in guard
