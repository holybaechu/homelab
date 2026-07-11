from tests.helpers import REPO_ROOT


def test_tailnet_lxc_disables_tailscale_dns_acceptance():
    inventory = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_tailnet.yml"
    ).read_text(encoding="utf-8")
    role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "tailscale_gateway"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "tailscale_accept_dns: false" in inventory
    assert "--accept-dns={{ tailscale_accept_dns | default(false) | lower }}" in role


def test_tailnet_uses_ipv4_only_underlay_when_public_ipv6_is_unroutable():
    role = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "tailscale_gateway"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "net.ipv6.conf.all.disable_ipv6=1" in role
    assert "net.ipv6.conf.default.disable_ipv6=1" in role
    assert "sysctl -w" in role
    assert "net.ipv4.ip_forward=1" in role
    assert "net.ipv6.conf.all.forwarding=1" not in role
