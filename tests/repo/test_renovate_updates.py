import json
import re

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def workflow_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / ".github/workflows").glob("*.yml"))
    )


def test_operational_dependencies_do_not_use_floating_latest_aliases():
    compose_files = sorted((REPO_ROOT / "apps/compose").rglob("compose.yml"))
    contents = workflow_text() + "\n" + "\n".join(
        path.read_text(encoding="utf-8") for path in compose_files
    )
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


def test_action_sha_pins_keep_release_comments_for_renovate():
    action_lines = [
        line.strip() for line in workflow_text().splitlines() if "uses:" in line
    ]
    sha_lines = [line for line in action_lines if re.search(r"@[0-9a-f]{40}\b", line)]

    assert action_lines == sha_lines
    assert all(re.search(r"\s#\s+v?\d+(?:\.\d+){0,2}$", line) for line in sha_lines)


def test_openclaw_dockerfile_bases_are_locally_digest_pinned():
    dockerfiles = sorted((REPO_ROOT / "infra/openclaw").glob("*/Dockerfile"))
    assert len(dockerfiles) == 2
    for dockerfile in dockerfiles:
        lines = dockerfile.read_text(encoding="utf-8").splitlines()
        references = [line.split()[1] for line in lines if line.startswith("FROM ")]
        assert references
        assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in references)
        assert not any(line.startswith("ARG ") and "REF" in line for line in lines)


def test_nonstandard_versions_have_only_focused_managers():
    config = json.loads(read("renovate.json"))
    managers = config["customManagers"]
    by_dependency = {manager["depNameTemplate"]: manager for manager in managers}

    assert set(by_dependency) == {
        "tailscale/tailscale",
        "ghcr.io/vuetorrent/vuetorrent-lsio-mod",
        "proxmox-debian-13",
    }
    assert by_dependency["tailscale/tailscale"]["managerFilePatterns"] == [
        "/^\\.github\\/workflows\\/(?:apps|infra|openclaw)\\.yml$/"
    ]
    assert by_dependency["ghcr.io/vuetorrent/vuetorrent-lsio-mod"]["managerFilePatterns"] == [
        "/^apps\\/compose\\/homelab\\/compose\\.yml$/"
    ]
    assert by_dependency["proxmox-debian-13"]["managerFilePatterns"] == [
        "/^infra\\/ansible\\/inventory\\/prod\\/topology\\.json$/"
    ]
    assert (
        config["customDatasources"]["proxmox-debian-13"]["defaultRegistryUrlTemplate"]
        == "https://download.proxmox.com/images/system/"
    )


def test_vuetorrent_mod_manager_tracks_official_semver():
    config = json.loads(read("renovate.json"))
    manager = next(
        item
        for item in config["customManagers"]
        if item.get("depNameTemplate") == "ghcr.io/vuetorrent/vuetorrent-lsio-mod"
    )

    assert manager["matchStrings"] == [
        "DOCKER_MODS: ghcr.io/vuetorrent/vuetorrent-lsio-mod:"
        "(?<currentValue>\\d+\\.\\d+\\.\\d+)"
        "@(?<currentDigest>sha256:[0-9a-f]{64})"
    ]
    assert manager["datasourceTemplate"] == "docker"
    assert manager["versioningTemplate"] == "semver"


def test_metube_image_uses_explicit_calendar_versioning():
    config = json.loads(read("renovate.json"))
    rule = next(
        item
        for item in config["packageRules"]
        if item.get("matchPackageNames") == ["ghcr.io/alexta69/metube"]
    )

    assert rule["matchDatasources"] == ["docker"]
    assert rule["versioning"] == (
        r"regex:^(?<major>\d{4})\.(?<minor>\d{2})\.(?<patch>\d{2})$"
    )


def test_direct_python_requirements_are_exactly_pinned():
    for filename in ("requirements-dev.txt", "requirements-deploy.txt"):
        requirement_lines = [
            line.strip()
            for line in read(filename).splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-r "))
        ]
        assert requirement_lines
        assert all(
            re.fullmatch(
                r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^<>=!~;\s]+",
                line,
            )
            for line in requirement_lines
        )


def test_openclaw_image_updates_always_require_review():
    config = json.loads(read("renovate.json"))
    rule = next(
        item
        for item in config["packageRules"]
        if item.get("description") == "Require review for OpenClaw"
    )

    assert rule["matchDatasources"] == ["docker"]
    assert rule["matchPackageNames"] == ["ghcr.io/openclaw/openclaw"]
    assert rule["automerge"] is False
    assert rule["platformAutomerge"] is False
