import yaml

from tests.helpers import REPO_ROOT


PACKAGE = REPO_ROOT / "apps" / "compose" / "homelab"


def read(name: str) -> str:
    return (PACKAGE / name).read_text(encoding="utf-8")


def test_traefik_defaults_to_no_container_exposure_and_uses_one_owned_network():
    model = yaml.safe_load(read("compose.yml"))
    static = read("traefik.yml")
    service = model["services"]["traefik"]

    assert service["image"].startswith("traefik:v3")
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in service["volumes"]
    assert "exposedByDefault: false" in static
    assert "certResolver: cloudflare" in static
    assert (
        "./generated/traefik/routes.yml:/etc/traefik/dynamic/routes.yml:ro"
        in service["volumes"]
    )
    assert "directory: /etc/traefik/dynamic" in static
    assert model["networks"]["proxy"] == {"name": "homelab_proxy"}


def test_private_routes_and_headers_preserve_edge_policy():
    dynamic = read("config/routes.yml.tmpl")

    assert "192.168.0.0/24" in dynamic
    assert "100.64.0.0/10" in dynamic
    assert "adguard.home.hchu.me" in dynamic
    assert "rule: Host(`openclaw.home.hchu.me`)" in dynamic
    assert "middlewares: [private-only, secure-headers]" in dynamic
    assert "pve.home.hchu.me" in dynamic
    assert "customFrameOptionsValue: SAMEORIGIN" in dynamic
    assert "/etc/ssl/certs/homelab-pve-root-ca.pem" in dynamic
    assert "dns.hchu.me" not in dynamic
    assert "dns-query" not in dynamic


def test_adguard_static_policy_is_package_owned_and_plain_dns_only():
    model = yaml.safe_load(read("compose.yml"))
    service = model["services"]["adguard"]
    template_text = read("config/AdGuardHome.yaml.tmpl")
    template = yaml.safe_load(
        template_text.replace("@@ADGUARD_ADMIN_USERNAME@@", '"admin"')
        .replace("@@ADGUARD_ADMIN_PASSWORD_HASH@@", '"$2y$10$' + '.' * 53 + '"')
        .replace("@@APPS_HOST@@", "192.0.2.10")
    )

    assert service["network_mode"] == "host"
    assert (
        "./generated/adguard/AdGuardHome.yaml:/opt/adguardhome/conf/AdGuardHome.yaml:ro"
        in service["volumes"]
    )
    assert template["dns"]["port"] == 53
    assert template["http"]["address"] == "0.0.0.0:3000"
    assert template["tls"]["enabled"] is False
    assert template["filtering"]["safe_search"]["enabled"] is False
    assert template["filtering"]["rewrites"] == [
        {"domain": "*.home.hchu.me", "answer": "192.0.2.10", "enabled": True}
    ]
