from tests.helpers import render_role_template


def render_adguard_nftables():
    return render_role_template(
        "adguard",
        "nftables.conf.j2",
        adguard_admin_port=80,
        adguard_dns_port=53,
        adguard_dot_port=853,
        adguard_https_port=443,
        edge_ip="192.168.0.4",
        homelab_lan_cidr="192.168.0.0/24",
        homelab_tailscale_cidr="100.64.0.0/10",
    )


def test_adguard_nftables_allows_ssh_and_limits_dns_service_ports():
    rendered = render_adguard_nftables()

    assert "type filter hook input priority 0; policy drop;" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } tcp dport 22 accept" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } udp dport 53 accept" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } tcp dport 53 accept" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } tcp dport 853 accept" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } udp dport 853 accept" in rendered
    assert "ip saddr 192.168.0.4 tcp dport 443 accept" in rendered
    assert "tcp dport 53 accept" not in rendered.replace(
        "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } tcp dport 53 accept", ""
    )


def test_adguard_nftables_limits_admin_http_to_edge_and_tailnet():
    rendered = render_adguard_nftables()

    assert "ip saddr 192.168.0.4 tcp dport 80 accept" in rendered
    assert "ip saddr 100.64.0.0/10 tcp dport 80 accept" in rendered
    assert "tcp dport 80 reject" in rendered
