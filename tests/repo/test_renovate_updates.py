import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

OPERATIONAL_UPDATE_FILES = [
    ".github/workflows/ci.yml",
    ".github/workflows/cd.yml",
    "scripts/ci/install-tools.sh",
    "infra/ansible/inventory/prod/group_vars/dns.yml",
    "infra/ansible/inventory/prod/group_vars/downloads.yml",
    "infra/ansible/roles/copyparty/tasks/main.yml",
]

RENOVATE_MANAGED_DEPENDENCIES = [
    "tailscale/tailscale",
    "opentofu/opentofu",
    "VueTorrent/VueTorrent",
    "AdguardTeam/AdGuardHome",
    "copyparty",
]


def _read(relative_path):
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_operational_dependency_files_do_not_use_latest_aliases():
    contents = "\n".join(_read(path) for path in OPERATIONAL_UPDATE_FILES)

    assert "ubuntu-latest" not in contents
    assert "version: latest" not in contents
    assert "releases/latest/download" not in contents


def test_nonstandard_runtime_pins_have_renovate_metadata():
    contents = "\n".join(_read(path) for path in OPERATIONAL_UPDATE_FILES)

    for dep_name in RENOVATE_MANAGED_DEPENDENCIES:
        assert f"depName={dep_name}" in contents


def test_renovate_has_regex_manager_for_inline_dependency_metadata():
    config = json.loads(_read("renovate.json"))

    assert any(
        manager.get("customType") == "regex"
        and any("renovate: datasource=" in match for match in manager["matchStrings"])
        for manager in config.get("customManagers", [])
    )
