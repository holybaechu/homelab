import re

import yaml
from jinja2 import Template

from tests.helpers import REPO_ROOT


def _service_block(compose: str, service: str, next_service: str) -> str:
    return compose.split(f"  {service}:", 1)[1].split(f"  {next_service}:", 1)[0]


def test_qbittorrent_is_direct_and_uses_the_canonical_private_route():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )
    qbittorrent = _service_block(compose, "qbittorrent", "copyparty")

    assert "network_mode:" not in qbittorrent
    assert "depends_on:" not in qbittorrent
    assert "- proxy" in qbittorrent
    assert 'TORRENTING_PORT: "${QBT_DIRECT_PEER_PORT}"' in qbittorrent
    assert '"${QBT_DIRECT_PEER_PORT}:${QBT_DIRECT_PEER_PORT}/tcp"' in qbittorrent
    assert '"${QBT_DIRECT_PEER_PORT}:${QBT_DIRECT_PEER_PORT}/udp"' in qbittorrent
    assert "traefik.http.routers.qbt.rule=Host(`qbt.home.hchu.me`)" in qbittorrent
    assert "public.qbt.home.hchu.me" not in compose
    assert "gluetun:" not in compose
    assert "qbittorrent-vpn:" not in compose


def test_qbittorrent_does_not_receive_a_secret_environment_file():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )
    qbittorrent = _service_block(compose, "qbittorrent", "copyparty")

    assert "env_file:" not in qbittorrent
    for variable in ("PUID", "PGID", "TZ"):
        assert f'{variable}: "${{{variable}}}"' in qbittorrent
    assert "WIREGUARD_PRIVATE_KEY" not in qbittorrent


def test_shared_data_and_isolated_state_use_the_intended_mounts():
    compose = (REPO_ROOT / "apps/compose/media/compose.yml").read_text(
        encoding="utf-8"
    )

    assert compose.count("/srv/homelab/downloads:/downloads:rw") == 1
    assert compose.count("/srv/homelab/copyparty/public:/public:rw") == 1
    assert "/srv/homelab/docker-apps/qbittorrent:/config:rw" in compose
    assert "/srv/homelab/downloads/complete:/srv/downloads:ro" in compose
    assert "/srv/homelab/docker-apps/copyparty:/config/state:rw" in compose
    assert "qbittorrent-vpn" not in compose
    assert "gluetun" not in compose




def test_copyparty_history_uses_the_backed_up_state_mount():
    config = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/templates/copyparty.conf.j2"
    ).read_text(encoding="utf-8")
    assert "hist: /config/state" in config


def test_qbittorrent_uses_vuetorrent_and_managed_network_settings():
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
    qbittorrent = _service_block(compose, "qbittorrent", "copyparty")

    match = re.search(
        r"^\s+DOCKER_MODS: ghcr\.io/vuetorrent/vuetorrent-lsio-mod:(\d+\.\d+\.\d+)$",
        qbittorrent,
        re.MULTILINE,
    )
    assert match
    assert ":latest" not in qbittorrent

    assert "WebUI\\AlternativeUIEnabled=true" in config
    assert "WebUI\\RootFolder=/vuetorrent" in config
    assert "WebUI\\RootFolder=/vuetorrent/public" not in config
    assert "qbittorrent_instance.connection_interface" in config
    assert "Connection\\PortRangeMin={{ qbittorrent_instance.peer_port }}" in config
    assert "connection_interface: \"\"" in variables
    assert "connection_interface: tun0" not in variables
    assert "qbittorrent_direct_peer_port: 35435" in variables
    assert len(parsed_variables["qbittorrent_instances"]) == 1
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
        qbittorrent_instance={"connection_interface": "", "peer_port": 35435},
    )
    assert "Connection\\Interface=tun0" not in direct_config
    assert "Connection\\PortRangeMin=35435" in direct_config


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


def test_media_docs_describe_single_qbittorrent_and_metube_cleanup():
    overview = (REPO_ROOT / "apps/README.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "apps/compose/media/README.md").read_text(
        encoding="utf-8"
    )

    assert "one direct qBittorrent" in overview
    assert "qbt.home.hchu.me" in readme
    assert "public.qbt.home.hchu.me" not in readme
    assert "single active qBittorrent" in readme
    assert "35435" in readme
    assert "preserved but unmanaged" in readme
    assert "metube.home.hchu.me" in readme
    assert "/srv/homelab/copyparty/downloads" in readme
    assert "trash" in readme.lower()
