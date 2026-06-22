import json
from tests.helpers import REPO_ROOT

OPERATIONAL_UPDATE_FILES = [
    ".github/workflows/ci.yml",
    ".github/workflows/cd.yml",
    "scripts/ci/install-tools.sh",
    "infra/ansible/inventory/prod/group_vars/svc_edge.yml",
    "infra/ansible/inventory/prod/group_vars/svc_dns.yml",
    "infra/ansible/inventory/prod/group_vars/svc_downloads.yml",
    "infra/ansible/roles/copyparty/tasks/main.yml",
]

RENOVATE_MANAGED_DEPENDENCIES = [
    "opentofu/opentofu",
    "caddyserver/caddy",
    "caddyserver/xcaddy",
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


def test_renovate_regex_manager_scans_edge_inventory():
    config = json.loads(_read("renovate.json"))
    regex_managers = [
        manager
        for manager in config.get("customManagers", [])
        if manager.get("customType") == "regex"
    ]

    assert any(
        "infra\\/ansible\\/inventory\\/prod\\/group_vars\\/svc_(edge|dns|downloads)\\.yml"
        in pattern
        for manager in regex_managers
        for pattern in manager.get("managerFilePatterns", [])
    )


def test_tailscale_binary_comment_is_not_marked_as_renovate_managed():
    contents = "\n".join(_read(path) for path in OPERATIONAL_UPDATE_FILES)

    assert "depName=tailscale/tailscale" not in contents


def test_ansible_install_is_renovate_managed_requirement():
    requirements = _read("requirements-deploy.txt")
    ci_workflow = _read(".github/workflows/ci.yml")
    install_tools = _read("scripts/ci/install-tools.sh")

    assert "ansible==" in requirements
    assert "requirements-deploy.txt" in ci_workflow
    assert "requirements-deploy.txt" in install_tools
    assert "pip install -r requirements-dev.txt ansible" not in ci_workflow
    assert "pip install ansible" not in install_tools


def test_opentofu_lockfile_is_not_ignored():
    gitignore = _read(".gitignore")

    assert ".terraform.lock.hcl" not in gitignore
