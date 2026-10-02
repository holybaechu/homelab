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
        "/^\\.github\\/workflows\\/(?:apps|infra)\\.yml$/"
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




def test_tailscale_action_tracks_published_linux_packages_instead_of_all_git_tags():
    config = json.loads(read("renovate.json"))
    manager = next(item for item in config["customManagers"]
                   if item.get("depNameTemplate") == "tailscale/tailscale")
    assert manager["datasourceTemplate"] == "custom.tailscale-linux"
    source = config["customDatasources"]["tailscale-linux"]
    assert source["defaultRegistryUrlTemplate"] == "https://pkgs.tailscale.com/stable/?mode=json"
    assert source["format"] == "json"
    # GitHub can publish a tag for a different platform without a Linux build.
    # Only the artifact feed's TarballsVersion may select the runner version.
    assert source["transformTemplates"] == ['{"releases":[{"version":TarballsVersion}]}']
    expression = re.sub(r"\(\?<([A-Za-z][A-Za-z0-9_]*)>", r"(?P<\1>", manager["matchStrings"][0])
    for path in (".github/workflows/apps.yml", ".github/workflows/infra.yml"):
        matches = list(re.finditer(expression, read(path)))
        assert len(matches) == 1
