import json
import os
from pathlib import PurePosixPath
import re
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


TOPOLOGY_PATH = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"


def load_topology() -> dict:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def managed_hosts() -> dict[str, dict]:
    return load_topology()["all"]["children"]["debian"]["hosts"]


def test_each_special_mount_or_device_is_owned_by_only_one_lxc() -> None:
    hosts = managed_hosts()
    device_owners = [name for name, host in hosts.items() if host["lxc_devices"]]
    mount_owners = [name for name, host in hosts.items() if host["lxc_mounts"]]

    assert device_owners == ["tailnet"]
    assert set(mount_owners) == {"docker_apps"}
    for name in mount_owners:
        for mount in hosts[name]["lxc_mounts"].values():
            assert PurePosixPath(mount["source"]).is_absolute()
            assert PurePosixPath(mount["target"]).is_absolute()
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
