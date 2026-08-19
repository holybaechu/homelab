import json
import re

from tests.helpers import REPO_ROOT


def managed_hosts() -> dict[str, dict]:
    topology = json.loads(
        (
            REPO_ROOT / "infra/ansible/inventory/prod/topology.json"
        ).read_text(encoding="utf-8")
    )
    return topology["all"]["children"]["debian"]["hosts"]


def test_three_lxcs_match_capacity_plan():
    expected = {
        "tailnet": {
            "root_disk_gb": 4,
            "cores": 1,
            "memory_mb": 512,
            "swap_mb": 0,
            "startup_order": 1,
        },
        "docker_apps": {
            "root_disk_gb": 32,
            "cores": 6,
            "memory_mb": 8192,
            "swap_mb": 2048,
            "startup_order": 2,
        },
        "openclaw": {
            "root_disk_gb": 96,
            "cores": 8,
            "memory_mb": 12288,
            "swap_mb": 3072,
            "startup_order": 3,
        },
    }
    hosts = managed_hosts()
    assert set(hosts) == set(expected)
    assert {
        name: {field: hosts[name][field] for field in sizing}
        for name, sizing in expected.items()
    } == expected


def test_homelab_data_lv_size_is_preserved():
    text = (
        REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml"
    ).read_text(encoding="utf-8")
    assert re.search(r"^homelab_data_lv_size: 896G$", text, re.MULTILINE)
