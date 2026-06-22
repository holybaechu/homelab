from pathlib import Path
import re




from tests.helpers import REPO_ROOT
def tfvars_container_body(tfvars_text: str, name: str) -> str:
    match = re.search(
        rf"^  {name} = \{{(?P<body>.*?)^  \}}",
        tfvars_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name} container block not found"
    return match.group("body")


def numeric_value(container_body: str, key: str) -> int:
    match = re.search(rf"^\s+{key}\s+=\s+(\d+)$", container_body, re.MULTILINE)
    assert match is not None, f"{key} not found"
    return int(match.group(1))


def assert_container_sizing(tfvars_path: Path):
    tfvars_text = tfvars_path.read_text(encoding="utf-8")

    expected = {
        "edge": {"root_disk_gb": 6, "cores": 1, "memory_mb": 512},
        "downloads": {"root_disk_gb": 8, "cores": 2, "memory_mb": 1024},
        "files": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "dns": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "tailnet": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "minecraft": {"root_disk_gb": 32, "cores": 4, "memory_mb": 4096},
        "hermes": {"root_disk_gb": 16, "cores": 2, "memory_mb": 2048},
    }

    for name, values in expected.items():
        body = tfvars_container_body(tfvars_text, name)
        for key, expected_value in values.items():
            assert numeric_value(body, key) == expected_value


def test_tracked_lxc_resource_sizing_matches_capacity_plan():
    assert_container_sizing(
        REPO_ROOT
        / "infra"
        / "opentofu"
        / "envs"
        / "prod"
        / "containers.auto.tfvars"
    )


def test_homelab_data_lv_size_matches_capacity_plan():
    all_vars_path = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml"
    )
    all_vars_text = all_vars_path.read_text(encoding="utf-8")

    assert re.search(r"^homelab_data_lv_size: 896G$", all_vars_text, re.MULTILINE)
