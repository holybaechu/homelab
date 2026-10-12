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


def test_workflow_python_updates_stay_within_ansible_controller_support():
    config = json.loads(read("renovate.json"))
    rules = [rule for rule in config["packageRules"]
             if "actions/python-versions" in rule.get("matchPackageNames", [])]
    assert len(rules) == 1
    assert rules[0]["matchManagers"] == ["github-actions"]
    assert rules[0]["allowedVersions"] == "<3.15"


def test_qbittorrent_updates_accept_application_tags_and_reject_legacy_os_tags():
    config = json.loads(read("renovate.json"))
    rule = next(rule for rule in config["packageRules"]
                if "lscr.io/linuxserver/qbittorrent" in rule.get("matchPackageNames", []))
    assert rule["matchDatasources"] == ["docker"]
    assert rule["versioning"].startswith("regex:")
    expression = re.sub(r"\(\?<([A-Za-z][A-Za-z0-9_]*)>", r"(?P<\1>",
                        rule["versioning"].removeprefix("regex:"))
    for tag, accepted in (
        ("5.2.4", True),
        ("5.3.0", True),
        ("6.0.0", True),
        ("20.04.1", False),
        ("14.3.9.99202110311443-7435-01519b5e7ubuntu20.04.1-ls166", False),
        ("5.2.4_v2.0.15-ls479", False),
        ("libtorrentv1", False),
    ):
        assert (re.fullmatch(expression, tag) is not None) is accepted, tag
