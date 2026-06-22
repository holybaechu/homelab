from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alpine_lxc_bootstrap_opens_ssh_through_active_nftables():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "pve_lxc_access_bootstrap"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "Allow SSH through active Alpine nftables firewall" in tasks
    assert "rc-service nftables status" in tasks
    assert "nft list chain inet filter input" in tasks
    assert "ip saddr {{ homelab_lan_cidr }} tcp dport 22 accept" in tasks
    assert "ip saddr {{ homelab_tailscale_cidr }} tcp dport 22 accept" in tasks
