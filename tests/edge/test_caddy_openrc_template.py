from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_caddy_openrc_exports_environment_from_confd():
    template = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "caddy"
        / "templates"
        / "caddy.openrc.j2"
    ).read_text(encoding="utf-8")

    assert "export CLOUDFLARE_DNS_API_TOKEN" in template
    assert "export XDG_DATA_HOME" in template
    assert "export XDG_CONFIG_HOME" in template
    assert "export HOME" in template

