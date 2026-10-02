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
