from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_downloads_vpn_installs_resolvconf_provider_for_wg_quick_dns():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- openresolv" in tasks

