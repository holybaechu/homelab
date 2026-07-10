from tests.helpers import REPO_ROOT


def read(name: str) -> str:
    return (REPO_ROOT / "apps" / "compose" / "platform" / name).read_text(encoding="utf-8")


def test_traefik_replaces_caddy_and_defaults_to_no_container_exposure():
    compose = read("compose.yml")
    static = read("traefik.yml")

    assert "traefik:v3" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in compose
    assert "exposedByDefault: false" in static
    assert "certResolver: cloudflare" in static


def test_private_routes_and_headers_preserve_edge_policy():
    dynamic = read("dynamic.yml")

    assert "192.168.0.0/24" in dynamic
    assert "100.64.0.0/10" in dynamic
    assert "adguard.home.hchu.me" in dynamic
    assert "pve.home.hchu.me" in dynamic
    assert "customFrameOptionsValue: SAMEORIGIN" in dynamic
    assert "/etc/ssl/certs/homelab-pve-root-ca.pem" in dynamic
    assert "dns.hchu.me" not in dynamic
    assert "dns-query" not in dynamic


def test_adguard_uses_host_network_only_for_plain_dns_and_private_admin():
    compose = read("compose.yml")
    template = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/templates/AdGuardHome.yaml.j2"
    ).read_text(encoding="utf-8")

    assert "network_mode: host" in compose
    assert "port: {{ adguard_dns_port }}" in template
    assert "address: 0.0.0.0:{{ adguard_admin_port }}" in template
    assert "enabled: false" in template.split("tls:", 1)[1]
    assert "port_dns_over_tls" not in template
