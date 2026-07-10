from pathlib import Path
import re

from tests.helpers import REPO_ROOT


def container_body(text: str, name: str) -> str:
    match = re.search(rf"^  {name} = \{{(?P<body>.*?)^  \}}", text, re.MULTILINE | re.DOTALL)
    assert match
    return match.group("body")


def value(body: str, key: str) -> int:
    match = re.search(rf"^\s+{key}\s+=\s+(\d+)$", body, re.MULTILINE)
    assert match
    return int(match.group(1))


def test_two_lxcs_match_consolidated_capacity_plan():
    text = (REPO_ROOT / "infra/opentofu/envs/prod/containers.auto.tfvars").read_text(encoding="utf-8")
    expected = {
        "tailnet": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "docker_apps": {"root_disk_gb": 32, "cores": 6, "memory_mb": 8192},
    }
    for name, sizing in expected.items():
        body = container_body(text, name)
        for key, wanted in sizing.items():
            assert value(body, key) == wanted


def test_homelab_data_lv_size_is_preserved():
    text = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml").read_text(encoding="utf-8")
    assert re.search(r"^homelab_data_lv_size: 896G$", text, re.MULTILINE)
