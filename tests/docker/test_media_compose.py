from tests.helpers import REPO_ROOT


def test_qbittorrent_uses_gluetun_namespace_and_native_port_forwarding():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(encoding="utf-8")
    qbittorrent = compose.split("  qbittorrent:", 1)[1].split("  copyparty:", 1)[0]
    gluetun = compose.split("  gluetun:", 1)[1].split("  qbittorrent:", 1)[0]

    assert "network_mode: service:gluetun" in qbittorrent
    assert "ports:" not in qbittorrent
    assert 'VPN_PORT_FORWARDING: "on"' in gluetun
    assert 'PORT_FORWARD_ONLY: "on"' in gluetun
    assert "HEALTH_SMALL_CHECK_TYPE: dns" in gluetun
    assert 'HEALTH_TARGET_ADDRESSES: "cloudflare.com:443,github.com:443"' in gluetun
    assert "VPN_PORT_FORWARDING_UP_COMMAND" in gluetun
    assert "/api/v2/app/setPreferences" in gluetun


def test_shared_data_uses_bind_mounts_and_opaque_state_uses_volumes():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(encoding="utf-8")

    assert "/srv/homelab/downloads:/downloads:rw" in compose
    assert "/srv/homelab/copyparty/public:/public:rw" in compose
    assert "/srv/homelab/downloads/complete:/srv/downloads:ro" in compose
    assert "gluetun_data:/gluetun" in compose
    assert "/srv/homelab/docker-apps/copyparty:/config/state:rw" in compose


def test_custom_wireguard_and_natpmp_roles_are_not_deployed():
    site = (REPO_ROOT / "infra/ansible/playbooks/site.yml").read_text(encoding="utf-8")
    assert "downloads_vpn" not in site
    assert "qbittorrent\n" not in site
    assert "proton-natpmp" not in site


def test_copyparty_history_uses_the_backed_up_state_mount():
    config = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/templates/copyparty.conf.j2").read_text(encoding="utf-8")
    assert "hist: /config/state" in config
