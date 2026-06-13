from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[2]


def render_adguard_config():
    env = Environment(
        loader=FileSystemLoader(
            REPO_ROOT / "infra" / "ansible" / "roles" / "adguard" / "templates"
        ),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("AdGuardHome.yaml.j2")
    return template.render(
        adguard_admin_password_hash="hash",
        adguard_admin_username="holybaechu",
        adguard_cert_domain="dns.hchu.me",
        adguard_dns_port=53,
        adguard_dot_port=853,
        adguard_https_port=443,
        adguard_admin_port=80,
        adguard_tls_dir="/opt/adguardhome/tls",
        adguard_work_dir="/opt/adguardhome/work",
        adguard_trusted_proxies=["192.168.0.4/32"],
        adguard_dns_filters=[
            {
                "enabled": True,
                "url": "https://adguardteam.github.io/HostlistsRegistry/assets/filter_48.txt",
                "name": "HaGeZi's Pro Blocklist",
                "id": 48,
            }
        ],
        edge_ip="192.168.0.4",
        homelab_private_domain="home.hchu.me",
    )


def test_adguard_baseline_config_uses_current_schema_for_bootstrap_dns():
    rendered = render_adguard_config()

    assert "schema_version: 34" in rendered
    assert "bootstrap_dns:\n    - 1.1.1.1\n    - 9.9.9.9" in rendered
    assert "bootstrap_dns:\n    - - 1.1.1.1" not in rendered


def test_adguard_baseline_config_uses_configured_admin_username():
    rendered = render_adguard_config()

    assert '  - name: "holybaechu"' in rendered
    assert "  - name: admin" not in rendered


def test_adguard_baseline_config_rewrites_private_home_zone_to_edge():
    rendered = render_adguard_config()

    assert "rewrites_enabled: true" in rendered
    assert "rewrites:" in rendered
    assert "domain: '*.home.hchu.me'" in rendered
    assert "answer: 192.168.0.4" in rendered
    assert "enabled: true" in rendered


def test_adguard_web_session_ttl_keeps_successful_login_session_alive():
    rendered = render_adguard_config()

    assert "session_ttl: 720h" in rendered
    assert "session_ttl: 0s" not in rendered


def test_adguard_trusts_configured_caddy_proxy_for_client_ips():
    rendered = render_adguard_config()

    dns_index = rendered.index("dns:\n")
    filtering_index = rendered.index("filtering:\n")
    trusted_proxy_index = rendered.index("  trusted_proxies:\n")

    assert dns_index < trusted_proxy_index < filtering_index
    assert "  trusted_proxies:\n    - 192.168.0.4/32" in rendered


def test_adguard_uses_hagezi_pro_blocklist_instead_of_default_filter():
    rendered = render_adguard_config()

    assert "HaGeZi's Pro Blocklist" in rendered
    assert "https://adguardteam.github.io/HostlistsRegistry/assets/filter_48.txt" in rendered
    assert "id: 48" in rendered
    assert "AdGuard DNS filter" not in rendered
    assert "filter_1.txt" not in rendered
