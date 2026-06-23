from tests.helpers import REPO_ROOT, render_role_template


def render_copyparty_nftables():
    return render_role_template(
        "copyparty",
        "nftables.conf.j2",
        copyparty_listen_port=3923,
        edge_ip="192.168.0.4",
        homelab_lan_cidr="192.168.0.0/24",
        homelab_tailscale_cidr="100.64.0.0/10",
    )


def test_copyparty_nftables_allows_ssh_but_limits_service_port_to_edge_and_tailscale():
    rendered = render_copyparty_nftables()

    assert "type filter hook input priority 0; policy drop;" in rendered
    assert "ip saddr { 192.168.0.0/24, 100.64.0.0/10 } tcp dport 22 accept" in rendered
    assert "ip saddr 192.168.0.4 tcp dport 3923 accept" in rendered
    assert "ip saddr 100.64.0.0/10 tcp dport 3923 accept" in rendered
    assert "ip saddr 192.168.0.0/24 tcp dport 3923 accept" not in rendered
    assert "tcp dport 3923 reject" in rendered


def test_copyparty_role_installs_and_enables_nftables():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "copyparty" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert "- nftables" in tasks
    assert "Install Copyparty nftables config" in tasks
    assert "src: nftables.conf.j2" in tasks
    assert "dest: /etc/nftables.nft" in tasks
    assert "validate: nft -c -f %s" in tasks
    assert "Enable Copyparty nftables" in tasks
