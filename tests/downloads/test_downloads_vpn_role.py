from tests.helpers import REPO_ROOT


def test_downloads_vpn_installs_resolvconf_provider_for_wg_quick_dns():
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "- openresolv" in tasks


def test_qbittorrent_uid_is_rejected_before_general_dns_allow():
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    reject_rule = nftables.index("meta skuid {{ qbittorrent_uid }} reject")
    dns_allow = nftables.index("udp dport 53 accept")
    lan_allow = nftables.index("ip daddr {{ homelab_lan_cidr }} accept")

    assert reject_rule < dns_allow
    assert reject_rule < lan_allow


def test_qbittorrent_webui_responses_are_allowed_before_uid_reject():
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    edge_response = "ct state established meta skuid {{ qbittorrent_uid }} ip daddr {{ edge_ip }} tcp sport {{ qbittorrent_webui_port }} accept"
    tailnet_response = "ct state established meta skuid {{ qbittorrent_uid }} ip daddr {{ homelab_tailscale_cidr }} tcp sport {{ qbittorrent_webui_port }} accept"
    reject_rule = nftables.index("meta skuid {{ qbittorrent_uid }} reject")

    assert edge_response in nftables
    assert tailnet_response in nftables
    assert nftables.index(edge_response) < reject_rule
    assert nftables.index(tailnet_response) < reject_rule


def test_qbittorrent_webui_input_is_limited_to_edge_and_tailscale():
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    assert "chain input" in nftables
    assert "ip saddr {{ edge_ip }} tcp dport {{ qbittorrent_webui_port }} accept" in nftables
    assert "ip saddr {{ homelab_tailscale_cidr }} tcp dport {{ qbittorrent_webui_port }} accept" in nftables
    assert "tcp dport {{ qbittorrent_webui_port }} reject" in nftables


def test_proton_server_list_is_pinned_to_an_immutable_commit():
    downloads_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_downloads.yml"
    ).read_text(encoding="utf-8")

    assert "refs/heads/main" not in downloads_vars
    assert "raw.githubusercontent.com/qdm12/gluetun-servers/" in downloads_vars
    assert "14277e92ce8291eb4515cd9af0dfad383a23f145" in downloads_vars



def test_downloads_vpn_uses_default_drop_firewall_policy():
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    assert "type filter hook input priority 0; policy drop;" in nftables
    assert "type filter hook output priority 0; policy drop;" in nftables
    assert "ct state invalid drop" in nftables


def test_vuetorrent_download_is_checksum_pinned_and_versioned():
    downloads_vars = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "inventory"
        / "prod"
        / "group_vars"
        / "svc_downloads.yml"
    ).read_text(encoding="utf-8")
    tasks = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "qbittorrent"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")

    assert "qbittorrent_vuetorrent_sha256:" in downloads_vars
    assert 'checksum: "sha256:{{ qbittorrent_vuetorrent_sha256 }}"' in tasks
    assert "qbittorrent_vuetorrent_version" in downloads_vars
    assert "qbittorrent_vuetorrent_root_current" in tasks


def test_downloads_vpn_allows_ssh_before_default_drop():
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    assert "ip saddr { {{ homelab_lan_cidr }}, {{ homelab_tailscale_cidr }} } tcp dport 22 accept" in nftables


def test_qbittorrent_kill_switch_runs_before_broad_established_and_handshake_allows():
    nftables = (
        REPO_ROOT
        / "infra"
        / "ansible"
        / "roles"
        / "downloads_vpn"
        / "templates"
        / "nftables.conf.j2"
    ).read_text(encoding="utf-8")

    output_chain = nftables.split("chain output", maxsplit=1)[1]
    reject_rule = output_chain.index("meta skuid {{ qbittorrent_uid }} reject")
    broad_established = output_chain.index("ct state established,related accept")
    handshake = output_chain.index("meta skuid != {{ qbittorrent_uid }} udp dport 51820 accept")
    assert reject_rule < broad_established
    assert reject_rule < handshake
