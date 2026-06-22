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
