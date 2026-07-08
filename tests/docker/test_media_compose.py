from tests.helpers import REPO_ROOT


def test_qbittorrent_uses_gluetun_network_namespace_and_no_direct_ports():
    compose = (REPO_ROOT / "apps" / "compose" / "media" / "compose.yml").read_text(encoding="utf-8")
    qbittorrent = compose.split("  qbittorrent:", maxsplit=1)[1].split("  copyparty:", maxsplit=1)[0]
    gluetun = compose.split("  gluetun:", maxsplit=1)[1].split("  qbittorrent:", maxsplit=1)[0]

    assert "network_mode: service:gluetun" in qbittorrent
    assert "ports:" not in qbittorrent
    assert "traefik.http.routers.qbt.rule=Host(`qbt.home.hchu.me`)" in gluetun
    assert "traefik.http.services.qbt.loadbalancer.server.port=8080" in gluetun


def test_media_storage_mount_modes_preserve_public_seeding_contract():
    compose = (REPO_ROOT / "apps" / "compose" / "media" / "compose.yml").read_text(encoding="utf-8")
    qbittorrent = compose.split("  qbittorrent:", maxsplit=1)[1].split("  copyparty:", maxsplit=1)[0]
    copyparty = compose.split("  copyparty:", maxsplit=1)[1]

    assert "/downloads:/downloads:rw" in qbittorrent
    assert "/public:/public:rw" in qbittorrent
    assert "/public:/srv/public:rw" in copyparty
    assert "/shared-readonly:/srv/shared-readonly:ro" in copyparty
    assert "/downloads/complete:/srv/downloads:ro" in copyparty


def test_copyparty_password_hashing_is_not_introduced():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "apps" / "compose" / "media").iterdir()
        if path.is_file()
    )

    assert "password_hash" not in combined
    assert "COPYPARTY_PASSWORD_HASH_SALT" not in combined
    assert '\"password\"' in combined
