from tests.helpers import REPO_ROOT


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_dns_compose_runs_adguard_and_exposes_dns_ports():
    compose = _read("apps/compose/dns/compose.yml")

    assert "adguard/adguardhome" in compose
    assert '"53:53/tcp"' in compose
    assert '"53:53/udp"' in compose
    assert '"853:853/tcp"' in compose
    assert '"853:853/udp"' in compose
    assert "/srv/docker-apps/adguard/work:/opt/adguardhome/work:rw" in compose
    assert "/srv/docker-apps/adguard/conf:/opt/adguardhome/conf:rw" in compose
    assert "/srv/docker-apps/adguard/tls:/opt/adguardhome/tls:ro" in compose


def test_dns_compose_routes_adguard_ui_privately_through_traefik():
    compose = _read("apps/compose/dns/compose.yml")

    assert "traefik.http.routers.adguard.rule=Host(`adguard.home.hchu.me`)" in compose
    assert "private-only@file,secure-headers@file" in compose
    assert "traefik.http.services.adguard.loadbalancer.server.port=3000" in compose
    assert "dns.hchu.me" not in compose
    assert "dns-query" not in compose


def test_docker_adguard_template_preserves_dns_policy_and_tls_name():
    template = _read("infra/ansible/roles/docker_compose_project/templates/adguardhome.yaml.j2")
    vars_file = _read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")

    assert "server_name: {{ adguard_cert_domain }}" in template
    assert "certificate_path: {{ adguard_container_tls_dir }}/fullchain.pem" in template
    assert "answer: {{ docker_apps_ip }}" in template
    assert "tls://1.1.1.1" in vars_file
    assert "tls://1.0.0.1" in vars_file
    assert "adguard_cert_domain: adguard.home.hchu.me" in vars_file
    assert "dns.hchu.me" not in template
