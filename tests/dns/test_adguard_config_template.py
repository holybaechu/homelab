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
    )


def test_adguard_baseline_config_uses_current_schema_for_bootstrap_dns():
    rendered = render_adguard_config()

    assert "schema_version: 34" in rendered
    assert "bootstrap_dns:\n    - 1.1.1.1\n    - 9.9.9.9" in rendered
    assert "bootstrap_dns:\n    - - 1.1.1.1" not in rendered
