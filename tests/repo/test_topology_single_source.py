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
DEPLOYMENT_UNITS = {"tailnet", "apps-host", "openclaw-host"}
REQUIRED_HOST_FIELDS = {
    "ansible_host",
    "deployment_unit",
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
    "unprivileged",
    "lxc_features",
    "lxc_devices",
    "lxc_mounts",
}


def load_topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def managed_hosts() -> dict[str, dict]:
    return load_topology()["all"]["children"]["debian"]["hosts"]


def test_inventory_preserves_exactly_the_three_runtime_boundaries() -> None:
    topology = load_topology()
    children = topology["all"]["children"]
    hosts = managed_hosts()

    assert set(hosts) == set(MANAGED_SERVICES)
    assert {host["deployment_unit"] for host in hosts.values()} == DEPLOYMENT_UNITS
    assert set(children["debian"]["hosts"]) == set(hosts)
    for service in hosts:
        assert set(children[f"svc_{service}"]["hosts"]) == {service}


def test_managed_host_schema_and_routable_identities_are_valid_and_unique() -> None:
    hosts = managed_hosts()
    for host in hosts.values():
        assert set(host) == REQUIRED_HOST_FIELDS
        assert ipaddress.ip_address(host["ansible_host"]).version == 4
        assert ipaddress.ip_address(host["gateway"]).version == 4
        assert re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", host["mac_address"])
        assert re.fullmatch(
            r"local:vztmpl/debian-13-standard_[^/]+_amd64\.tar\.zst",
            host["template_file_id"],
        )
        assert host["os_type"] == "debian"
        assert host["unprivileged"] is True
        assert all(
            host[field] > 0
            for field in ("vmid", "root_disk_gb", "cores", "memory_mb")
        )
        assert host["swap_mb"] >= 0
        assert isinstance(host["lxc_devices"], dict)
        assert isinstance(host["lxc_mounts"], dict)

    for field in ("vmid", "hostname", "ansible_host", "mac_address", "startup_order"):
        values = [host[field] for host in hosts.values()]
        assert len(values) == len(set(values)), f"duplicate {field}: {values}"
    assert sorted(host["startup_order"] for host in hosts.values()) == [1, 2, 3]


def test_each_special_mount_or_device_is_owned_by_only_one_lxc() -> None:
    hosts = managed_hosts()
    device_owners = [name for name, host in hosts.items() if host["lxc_devices"]]
    mount_owners = [name for name, host in hosts.items() if host["lxc_mounts"]]

    assert device_owners == ["tailnet"]
    assert set(mount_owners) == {"docker_apps", "openclaw"}
    for name in mount_owners:
        for mount in hosts[name]["lxc_mounts"].values():
            assert os.path.isabs(mount["source"])
            assert os.path.isabs(mount["target"])
            assert re.fullmatch(r"0[0-7]{3}", mount["source_mode"])


@pytest.mark.skipif(os.name == "nt", reason="Ansible's controller CLI requires POSIX")
def test_ansible_inventory_cli_derives_groups_and_hostvars_from_topology() -> None:
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
    expected = managed_hosts()
    assert set(inventory["debian"]["hosts"]) == set(expected)
    for service, desired in expected.items():
        assert inventory[f"svc_{service}"]["hosts"] == [service]
        assert inventory["_meta"]["hostvars"][service]["vmid"] == desired["vmid"]
