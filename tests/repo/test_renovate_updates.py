import json
import re

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_operational_dependencies_do_not_use_latest_aliases():
    paths = [
        ".github/workflows/ci.yml",
        ".github/workflows/cd.yml",
        "scripts/ci/install-tools.sh",
        *[str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "apps/compose").rglob("compose.yml")],
        "apps/compose/hermes/Dockerfile",
    ]
    contents = "\n".join(read(path) for path in paths)
    assert "ubuntu-latest" not in contents
    assert "version: latest" not in contents
    assert ":latest" not in contents


def test_renovate_uses_builtin_compose_and_dockerfile_managers():
    config = json.loads(read("renovate.json"))
    assert "config:recommended" in config["extends"]
    assert all(
        "svc_(edge|dns|downloads)" not in pattern
        for manager in config.get("customManagers", [])
        for pattern in manager.get("managerFilePatterns", [])
    )


def test_ansible_install_is_pinned_and_opentofu_lockfile_is_tracked():
    assert "ansible==" in read("requirements-deploy.txt")
    assert "requirements-deploy.txt" in read(".github/workflows/ci.yml")
    assert ".terraform.lock.hcl" not in read(".gitignore")


def test_action_sha_pins_keep_release_comments_for_renovate():
    workflows = read(".github/workflows/ci.yml") + read(".github/workflows/cd.yml")
    action_lines = [line.strip() for line in workflows.splitlines() if "uses:" in line]
    sha_lines = [line for line in action_lines if re.search(r"@[0-9a-f]{40}\b", line)]

    assert sha_lines
    assert all(re.search(r"\s#\s+v?\d+(?:\.\d+){0,2}$", line) for line in sha_lines)


def test_nonstandard_version_surfaces_have_focused_managers():
    config = json.loads(read("renovate.json"))
    manager_text = json.dumps(config.get("customManagers", []))
    datasource_text = json.dumps(config.get("customDatasources", {}))

    for marker in (
        ".opentofu-version",
        "tailscale/tailscale",
        "vuetorrent-lsio-mod",
        "containers.auto.tfvars",
        "OP_CLI_VERSION",
    ):
        assert marker in manager_text
    assert "download.proxmox.com/images/system" in datasource_text
    assert "app-updates.agilebits.com/product_history/CLI2" in datasource_text


def test_onepassword_cli_manager_tracks_upstream_semver():
    config = json.loads(read("renovate.json"))
    manager = next(
        manager
        for manager in config["customManagers"]
        if manager.get("depNameTemplate") == "onepassword-cli"
    )
    datasource = config["customDatasources"]["onepassword-cli"]
    package_rule = next(
        rule
        for rule in config["packageRules"]
        if rule.get("matchDatasources") == ["custom.onepassword-cli"]
    )

    assert manager["managerFilePatterns"] == [
        "/^apps\\/compose\\/hermes\\/Dockerfile$/"
    ]
    assert manager["matchStrings"] == [
        "ARG OP_CLI_VERSION=(?<currentValue>\\d+\\.\\d+\\.\\d+)"
    ]
    assert manager["datasourceTemplate"] == "custom.onepassword-cli"
    assert manager["versioningTemplate"] == "semver"
    assert datasource == {
        "defaultRegistryUrlTemplate": (
            "https://app-updates.agilebits.com/product_history/CLI2"
        ),
        "format": "html",
    }
    assert package_rule["extractVersion"] == (
        ".*op_linux_amd64_v(?<version>\\d+\\.\\d+\\.\\d+)\\.zip$"
    )


def test_vuetorrent_mod_manager_tracks_official_semver():
    config = json.loads(read("renovate.json"))
    manager = next(
        manager
        for manager in config["customManagers"]
        if manager.get("depNameTemplate")
        == "ghcr.io/vuetorrent/vuetorrent-lsio-mod"
    )

    assert manager["managerFilePatterns"] == [
        "/^apps\\/compose\\/media\\/compose\\.yml$/"
    ]
    assert manager["matchStrings"] == [
        "DOCKER_MODS: ghcr.io/vuetorrent/vuetorrent-lsio-mod:"
        "(?<currentValue>\\d+\\.\\d+\\.\\d+)"
    ]
    assert manager["datasourceTemplate"] == "docker"
    assert manager["versioningTemplate"] == "semver"


def test_direct_requirements_are_exact_and_local_hermes_tag_is_constant():
    requirements = read("requirements-dev.txt").splitlines()
    collection = read("infra/ansible/requirements.yml")
    compose = read("apps/compose/hermes/compose.yml")

    assert requirements == ["pytest==9.1.1", "Jinja2==3.1.6", "PyYAML==6.0.3"]
    assert 'version: "13.2.0"' in collection
    assert "image: homelab/hermes-agent:local" in compose
    assert "homelab/hermes-agent:2026" not in compose


def test_opentofu_updates_trigger_cd():
    assert '      - ".opentofu-version"' in read(".github/workflows/cd.yml")
