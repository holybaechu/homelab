from tests.helpers import REPO_ROOT


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_traefik_disables_default_exposure_and_public_doh_route():
    static = _read("apps/compose/traefik/traefik.yml")
    dynamic = _read("apps/compose/traefik/dynamic.yml")

    assert "exposedByDefault: false" in static
    assert "dns.hchu.me" not in dynamic
    assert "dns-query" not in dynamic


def test_traefik_private_only_and_header_policies_match_caddy_contracts():
    dynamic = _read("apps/compose/traefik/dynamic.yml")

    assert "private-only" in dynamic
    assert "192.168.0.0/24" in dynamic
    assert "100.64.0.0/10" in dynamic
    assert "adguard.home.hchu.me" in dynamic
    assert "serverName: adguard.home.hchu.me" in dynamic
    assert "pve.home.hchu.me" in dynamic
    assert "customFrameOptionsValue: SAMEORIGIN" in dynamic

    router_headers = dynamic.split("router-headers:", maxsplit=1)[1].split("routers:", maxsplit=1)[0]
    assert "contentTypeNosniff" not in router_headers


def test_traefik_preserves_pve_ca_mount_for_verified_upstream_tls():
    compose = _read("apps/compose/traefik/compose.yml")
    dynamic = _read("apps/compose/traefik/dynamic.yml")

    assert "/etc/ssl/certs/homelab-pve-root-ca.pem:/etc/ssl/certs/homelab-pve-root-ca.pem:ro" in compose
    assert "rootCAs:" in dynamic
    assert "/etc/ssl/certs/homelab-pve-root-ca.pem" in dynamic
