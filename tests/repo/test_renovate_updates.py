import json
import re

import yaml

from scripts.ci import select_deployment_components as selector
from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_operational_dependencies_do_not_use_latest_aliases():
    paths = [
        ".github/workflows/ci.yml",
        "scripts/ci/install-tools.sh",
        *[str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "apps/compose").rglob("compose.yml")],
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
    workflows = read(".github/workflows/ci.yml")
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
        "topology",
    ):
        assert marker in manager_text
    assert "download.proxmox.com/images/system" in datasource_text




def test_vuetorrent_mod_manager_tracks_official_semver():
    config = json.loads(read("renovate.json"))
    manager = next(
        manager
        for manager in config["customManagers"]
        if manager.get("depNameTemplate")
        == "ghcr.io/vuetorrent/vuetorrent-lsio-mod"
    )

    assert manager["managerFilePatterns"] == [
        "/^apps\\/compose\\/homelab\\/compose\\.yml$/"
    ]
    assert manager["matchStrings"] == [
        "DOCKER_MODS: ghcr.io/vuetorrent/vuetorrent-lsio-mod:"
        "(?<currentValue>\\d+\\.\\d+\\.\\d+)"
    ]
    assert manager["datasourceTemplate"] == "docker"
    assert manager["versioningTemplate"] == "semver"


def test_custom_managers_reference_only_current_workflow_and_compose_paths():
    manager_text = json.dumps(json.loads(read("renovate.json"))["customManagers"])
    assert "workflows\\\\/ci\\\\.yml" in manager_text
    assert "compose\\\\/homelab\\\\/compose" in manager_text
    assert "workflows\\\\/cd\\\\.yml" not in manager_text
    assert "compose\\\\/media\\\\/compose" not in manager_text


def test_metube_image_uses_explicit_calendar_versioning():
    config = json.loads(read("renovate.json"))
    rule = next(
        rule
        for rule in config["packageRules"]
        if rule.get("matchPackageNames") == ["ghcr.io/alexta69/metube"]
    )

    assert rule["matchDatasources"] == ["docker"]
    assert rule["versioning"] == (
        r"regex:^(?<major>\d{4})\.(?<minor>\d{2})\.(?<patch>\d{2})$"
    )


def test_direct_requirements_and_collections_are_exactly_pinned():
    requirement_lines = [
        line.strip()
        for line in read("requirements-dev.txt").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirement_lines
    assert all(
        re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^<>=!~;\s]+", line)
        for line in requirement_lines
    )

    galaxy = yaml.safe_load(read("infra/ansible/requirements.yml"))
    collections = galaxy["collections"]
    assert collections
    assert all(re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", item["name"]) for item in collections)
    assert all(
        isinstance(item.get("version"), str)
        and re.fullmatch(r"\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?", item["version"])
        for item in collections
    )


def test_opentofu_updates_remain_in_deployment_path_scope():
    selection = selector.classify_paths([".opentofu-version"])

    assert selection.components == ("tofu", "bootstrap")


def test_openclaw_image_updates_always_require_review():
    config = json.loads(read("renovate.json"))
    rule = next(
        rule
        for rule in config["packageRules"]
        if rule.get("description") == "Require review for OpenClaw"
    )

    assert rule["matchDatasources"] == ["docker"]
    assert rule["matchPackageNames"] == ["ghcr.io/openclaw/openclaw"]
    assert rule["automerge"] is False
    assert rule["platformAutomerge"] is False
