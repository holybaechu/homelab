import re

import yaml
from jinja2 import Template

from tests.helpers import REPO_ROOT


def _service_block(compose: str, service: str, next_service: str) -> str:
    return compose.split(f"  {service}:", 1)[1].split(f"  {next_service}:", 1)[0]


def test_existing_qbittorrent_is_direct_and_new_instance_uses_gluetun():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )
    gluetun = _service_block(compose, "gluetun", "qbittorrent")
    direct = _service_block(compose, "qbittorrent", "qbittorrent-vpn")
    vpn = _service_block(compose, "qbittorrent-vpn", "copyparty")

    assert "network_mode:" not in direct
    assert "depends_on:" not in direct
    assert "- proxy" in direct
    assert 'TORRENTING_PORT: "${QBT_DIRECT_PEER_PORT}"' in direct
    assert '"${QBT_DIRECT_PEER_PORT}:${QBT_DIRECT_PEER_PORT}/tcp"' in direct
    assert '"${QBT_DIRECT_PEER_PORT}:${QBT_DIRECT_PEER_PORT}/udp"' in direct
    assert (
        "traefik.http.routers.qbt.rule=Host(`public.qbt.home.hchu.me`)" in direct
    )

    assert "network_mode: service:gluetun" in vpn
    assert "condition: service_healthy" in vpn
    assert "ports:" not in vpn
    assert "/srv/homelab/docker-apps/qbittorrent-vpn:/config:rw" in vpn
    assert "traefik.http.routers.qbt-vpn.rule=Host(`qbt.home.hchu.me`)" in gluetun

    assert 'VPN_PORT_FORWARDING: "on"' in gluetun
    assert 'PORT_FORWARD_ONLY: "on"' in gluetun
    assert "HEALTH_SMALL_CHECK_TYPE: dns" in gluetun
    assert 'HEALTH_TARGET_ADDRESSES: "cloudflare.com:443,github.com:443"' in gluetun
    assert "VPN_PORT_FORWARDING_UP_COMMAND" in gluetun
    assert "/api/v2/app/setPreferences" in gluetun


def test_qbittorrent_containers_do_not_receive_the_vpn_secret_environment():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )
    direct = _service_block(compose, "qbittorrent", "qbittorrent-vpn")
    vpn = _service_block(compose, "qbittorrent-vpn", "copyparty")

    for service in (direct, vpn):
        assert "env_file:" not in service
        for variable in ("PUID", "PGID", "TZ"):
            assert f'{variable}: "${{{variable}}}"' in service
        assert "WIREGUARD_PRIVATE_KEY" not in service


def test_shared_data_and_isolated_state_use_the_intended_mounts():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )

    assert compose.count("/srv/homelab/downloads:/downloads:rw") == 2
    assert compose.count("/srv/homelab/copyparty/public:/public:rw") == 2
    assert "/srv/homelab/docker-apps/qbittorrent:/config:rw" in compose
    assert "/srv/homelab/docker-apps/qbittorrent-vpn:/config:rw" in compose
    assert "/srv/homelab/downloads/complete:/srv/downloads:ro" in compose
    assert "gluetun_data:/gluetun" in compose
    assert "/srv/homelab/docker-apps/copyparty:/config/state:rw" in compose


def test_custom_wireguard_and_natpmp_roles_are_not_deployed():
    site = (REPO_ROOT / "infra/ansible/playbooks/site.yml").read_text(
        encoding="utf-8"
    )
    assert "downloads_vpn" not in site
    assert "qbittorrent\n" not in site
    assert "proton-natpmp" not in site


def test_copyparty_history_uses_the_backed_up_state_mount():
    config = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/templates/copyparty.conf.j2"
    ).read_text(encoding="utf-8")
    assert "hist: /config/state" in config


def test_both_qbittorrent_instances_use_vuetorrent_and_managed_network_settings():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )
    config = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/templates/qBittorrent.conf.j2"
    ).read_text(encoding="utf-8")
    variables = (
        REPO_ROOT
        / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    ).read_text(encoding="utf-8")
    parsed_variables = yaml.safe_load(variables)
    direct = _service_block(compose, "qbittorrent", "qbittorrent-vpn")
    vpn = _service_block(compose, "qbittorrent-vpn", "copyparty")

    mod_versions = []
    for service in (direct, vpn):
        match = re.search(
            r"^\s+DOCKER_MODS: ghcr\.io/vuetorrent/vuetorrent-lsio-mod:(\d+\.\d+\.\d+)$",
            service,
            re.MULTILINE,
        )
        assert match
        mod_versions.append(match.group(1))
        assert ":latest" not in service
    assert len(set(mod_versions)) == 1

    assert "WebUI\\AlternativeUIEnabled=true" in config
    assert "WebUI\\RootFolder=/vuetorrent/public" in config
    assert "qbittorrent_instance.connection_interface" in config
    assert "Connection\\PortRangeMin={{ qbittorrent_instance.peer_port }}" in config
    assert "connection_interface: \"\"" in variables
    assert "connection_interface: tun0" in variables
    assert "qbittorrent_direct_peer_port: 6881" in variables
    assert (
        parsed_variables["qbittorrent_instances"][0]["peer_port"]
        == parsed_variables["qbittorrent_direct_peer_port"]
    )

    template = Template(config)
    shared = {
        "qbittorrent_webui_username": "test-user",
        "qbittorrent_webui_password_pbkdf2": "test-hash",
    }
    direct_config = template.render(
        **shared,
        qbittorrent_instance={"connection_interface": "", "peer_port": 6881},
    )
    vpn_config = template.render(
        **shared,
        qbittorrent_instance={"connection_interface": "tun0", "peer_port": 0},
    )
    assert "Connection\\Interface=tun0" not in direct_config
    assert "Connection\\PortRangeMin=6881" in direct_config
    assert "Connection\\Interface=tun0" in vpn_config
    assert "Connection\\PortRangeMin=0" in vpn_config


def test_metube_is_private_browser_downloader_with_managed_cleanup():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )
    metube = compose.split("  metube:", 1)[1].split("\nnetworks:", 1)[0]
    tasks = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    ).read_text(encoding="utf-8")
    env_template = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/templates/media.env.j2"
    ).read_text(encoding="utf-8")

    assert re.search(
        r"^\s+image: ghcr\.io/alexta69/metube:\d{4}\.\d{2}\.\d{2}$",
        metube,
        re.MULTILINE,
    )
    assert ":latest" not in metube
    assert "env_file: .env" in metube
    assert 'DELETE_FILE_ON_TRASHCAN: "true"' in metube
    assert "/srv/homelab/copyparty/downloads:/downloads:rw" in metube
    assert "ports:" not in metube
    assert "- proxy" in metube
    assert "traefik.http.routers.metube.rule=Host(`metube.home.hchu.me`)" in metube
    assert "traefik.http.routers.metube.entrypoints=websecure" in metube
    assert (
        "traefik.http.routers.metube.middlewares="
        "private-only@file,secure-headers@file"
    ) in metube
    assert "traefik.http.services.metube.loadbalancer.server.port=8081" in metube

    assert "{path: /srv/homelab/copyparty/downloads}" in tasks
    for variable in ("PUID", "PGID", "TZ"):
        assert f"{variable}=" in env_template


def test_media_docs_describe_qbittorrent_split_and_metube_cleanup():
    overview = (REPO_ROOT / "apps/README.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "apps/compose/media/README.md").read_text(
        encoding="utf-8"
    )

    assert "Gluetun-routed qBittorrent" in overview
    assert "public.qbt.home.hchu.me" in readme
    assert "qbt.home.hchu.me" in readme
    assert "qbt-vpn.home.hchu.me" not in readme
    assert "existing torrents" in readme
    assert "direct public IP" in readme
    assert "metube.home.hchu.me" in readme
    assert "/srv/homelab/copyparty/downloads" in readme
    assert "trash" in readme.lower()
