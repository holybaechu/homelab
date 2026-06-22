from tests.helpers import REPO_ROOT


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


def test_caddy_build_is_reproducible_and_version_sensitive():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "caddy"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    edge_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_edge.yml"
    ).read_text(encoding="utf-8")

    assert "github.com/caddy-dns/cloudflare@v" in edge_vars
    assert "caddy_build_output" in tasks
    assert "--output {{ caddy_build_output }}" in tasks
    assert "creates: \"{{ caddy_build_output }}\"" in tasks
    assert "xcaddy-{{ xcaddy_version }}.stamp" in tasks
    assert "creates: /tmp/caddy" not in tasks


def test_caddy_installs_proxmox_root_ca_for_upstream_tls():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "caddy"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "Read Proxmox root CA for verified upstream TLS" in tasks
    assert "/etc/pve/pve-root-ca.pem" in tasks
    assert "/etc/ssl/certs/homelab-pve-root-ca.pem" in tasks
