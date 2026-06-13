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
        adguard_cert_domain="dns.hchu.me",
        adguard_dns_port=53,
        adguard_dot_port=853,
        adguard_https_port=443,
        adguard_admin_port=80,
        adguard_tls_dir="/opt/adguardhome/tls",
        adguard_work_dir="/opt/adguardhome/work",
        edge_ip="192.168.0.4",
        homelab_private_domain="home.hchu.me",
    )


def test_adguard_baseline_config_uses_current_schema_for_bootstrap_dns():
    rendered = render_adguard_config()

    assert "schema_version: 34" in rendered
    assert "bootstrap_dns:\n    - 1.1.1.1\n    - 9.9.9.9" in rendered
    assert "bootstrap_dns:\n    - - 1.1.1.1" not in rendered


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
