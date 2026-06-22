from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[2]


def render_adguard_nftables():
    env = Environment(
        loader=FileSystemLoader(
            REPO_ROOT / "infra" / "ansible" / "roles" / "adguard" / "templates"
        ),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("nftables.conf.j2")
    return template.render(
        adguard_admin_port=80,
        adguard_dns_port=53,
        adguard_dot_port=853,
        adguard_https_port=443,
        edge_ip="192.168.0.4",
        homelab_lan_cidr="192.168.0.0/24",
        homelab_tailscale_cidr="100.64.0.0/10",
    )


def test_adguard_nftables_allows_ssh_and_dns_service_ports():
    rendered = render_adguard_nftables()

    assert "type filter hook input priority 0; policy drop;" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } tcp dport 22 accept" in rendered
    assert "udp dport 53 accept" in rendered
    assert "tcp dport 53 accept" in rendered
    assert "tcp dport 443 accept" in rendered
    assert "tcp dport 853 accept" in rendered
    assert "udp dport 853 accept" in rendered


def test_adguard_nftables_limits_admin_http_to_edge_and_tailnet():
    rendered = render_adguard_nftables()

    assert "ip saddr 192.168.0.4 tcp dport 80 accept" in rendered
    assert "ip saddr 100.64.0.0/10 tcp dport 80 accept" in rendered
    assert "tcp dport 80 reject" in rendered
