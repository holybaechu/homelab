from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

from tests.helpers import REPO_ROOT


STACK = REPO_ROOT / "apps" / "compose" / "homelab"
COMPOSE = STACK / "compose.yml"
EXPECTED_SERVICES = {
    "traefik",
    "adguard",
    "cloudflare-ddns",
    "qbittorrent",
    "copyparty",
    "metube",
    "t3code",
}


@pytest.fixture(scope="module")
def model() -> dict:
    parsed = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _entries(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _labels(service: dict) -> list[str]:
    labels = service.get("labels", [])
    if isinstance(labels, dict):
        return [f"{key}={value}" for key, value in labels.items()]
    return [str(label) for label in labels]


def test_fixed_project_contains_only_the_seven_workloads_and_one_shared_network(model):
    assert model["name"] == "homelab"
    assert set(model["services"]) == EXPECTED_SERVICES
    assert model["networks"] == {
        "proxy": {"external": True, "name": "homelab_proxy"}
    }
    assert {
        name
        for name, service in model["services"].items()
        if service.get("network_mode") == "host"
    } == {"adguard", "cloudflare-ddns"}
    assert all(
        label.startswith("traefik.")
        for service in model["services"].values()
        for label in _labels(service)
    )


def test_only_edge_and_direct_peer_ports_are_published(model):
    ports = {
        name: set(map(str, _entries(service.get("ports"))))
        for name, service in model["services"].items()
        if service.get("ports")
    }
    assert ports == {
        "traefik": {"80:80", "443:443"},
        "qbittorrent": {
            "${QBT_DIRECT_PEER_PORT}:${QBT_DIRECT_PEER_PORT}/tcp",
            "${QBT_DIRECT_PEER_PORT}:${QBT_DIRECT_PEER_PORT}/udp",
        },
    }


def test_service_secret_files_are_isolated_and_release_env_is_nonsecret(model):
    expected = {
        "traefik": ["/etc/homelab/secrets/traefik.env"],
        "cloudflare-ddns": ["/etc/homelab/secrets/cloudflare-ddns.env"],
    }
    for name, service in model["services"].items():
        assert _entries(service.get("env_file")) == expected.get(name, [])
        environment = service.get("environment", {})
        assert "CF_DNS_API_TOKEN" not in environment
        assert "CLOUDFLARE_API_TOKEN" not in environment

    assert set(expected["traefik"]).isdisjoint(expected["cloudflare-ddns"])
    example = (STACK / ".env.example").read_text(encoding="utf-8")
    assert "TOKEN" not in example
    assert "SECRET" not in example


def test_persistent_volume_identity_and_existing_data_mounts_are_preserved(model):
    assert model["volumes"] == {
        "traefik_data": {
            "external": True,
            "name": "platform_traefik_data",
        },
        "adguard_work": {
            "external": True,
            "name": "platform_adguard_work",
        },
    }
    expected_mounts = {
        "traefik": {
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            "./traefik.yml:/etc/traefik/traefik.yml:ro",
            "./dynamic:/etc/traefik/dynamic:ro",
            "/etc/ssl/certs/homelab-pve-root-ca.pem:/etc/ssl/certs/homelab-pve-root-ca.pem:ro",
            "traefik_data:/data",
        },
        "adguard": {
            "./adguard:/opt/adguardhome/conf:rw",
            "adguard_work:/opt/adguardhome/work",
        },
        "qbittorrent": {
            "/srv/homelab/docker-apps/qbittorrent:/config:rw",
            "/srv/homelab/downloads:/downloads:rw",
            "/srv/homelab/copyparty/public:/public:rw",
        },
        "copyparty": {
            "./copyparty.conf:/config/copyparty.conf:ro",
            "/srv/homelab/docker-apps/copyparty:/config/state:rw",
            "/srv/homelab/copyparty/public:/srv/public:rw",
            "/srv/homelab/copyparty/shared-readonly:/srv/shared-readonly:ro",
            "/srv/homelab/downloads/complete:/srv/downloads:ro",
        },
        "metube": {"/srv/homelab/copyparty/downloads:/downloads:rw"},
        "t3code": {
            "/srv/homelab/docker-apps/t3code/home:/home/t3code:rw",
            "/srv/homelab/docker-apps/t3code/workspaces:/workspace:rw",
        },
    }
    for service, mounts in expected_mounts.items():
        assert mounts <= set(model["services"][service].get("volumes", []))


def test_existing_health_and_container_hardening_contracts_remain(model):
    services = model["services"]
    assert set(services) == EXPECTED_SERVICES
    assert all(service.get("restart") == "unless-stopped" for service in services.values())

    for name in EXPECTED_SERVICES - {"cloudflare-ddns"}:
        healthcheck = services[name].get("healthcheck")
        assert healthcheck and healthcheck.get("test")
        assert healthcheck.get("retries", 0) > 0

    # Favonia's production image is scratch and exposes no probe utility besides
    # its PID-1 updater; the deployer supplies its mandatory host-side gate.
    assert "healthcheck" not in services["cloudflare-ddns"]

    for name in ("cloudflare-ddns", "t3code"):
        assert "ALL" in services[name].get("cap_drop", [])
        assert "no-new-privileges:true" in services[name].get("security_opt", [])
    assert services["cloudflare-ddns"]["read_only"] is True
    assert services["cloudflare-ddns"]["user"] == "1000:1000"
    assert services["t3code"]["init"] is True
    assert "build" not in services["t3code"]
    assert services["t3code"]["image"] == (
        "${T3CODE_IMAGE_REF:?T3CODE_IMAGE_REF must be an exact OCI digest reference}"
    )


def test_ci_validator_uses_the_one_merged_model_and_an_exact_dummy_digest():
    script = (REPO_ROOT / "scripts" / "ci" / "validate-compose.sh").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"T3CODE_IMAGE_REF='([^']+@sha256:[0-9a-f]{64})'", script
    )
    assert match is not None
    assert match.group(1) == (
        "ghcr.io/holybaechu/homelab-t3code@sha256:" + "0" * 64
    )
    assert 'stack=apps/compose/homelab' in script
    assert '--env-file "$stack/.env.example"' in script
    assert '--project-directory "$temporary"' in script
    assert '-f "$temporary/compose.yml"' in script
    assert "config --no-env-resolution --no-path-resolution" in script
    assert "apps/compose/*" not in script
    assert "cp " not in script
    assert "mktemp -d" in script
    assert "trap cleanup EXIT" in script


def test_runtime_assets_exclude_ci_image_build_inputs():
    static = yaml.safe_load((STACK / "traefik.yml").read_text(encoding="utf-8"))
    dynamic = yaml.safe_load(
        (STACK / "dynamic" / "routes.yml").read_text(encoding="utf-8")
    )
    assert static["providers"]["docker"]["exposedByDefault"] is False
    assert static["providers"]["docker"]["network"] == "homelab_proxy"
    assert static["providers"]["file"]["directory"] == "/etc/traefik/dynamic"
    assert {"adguard", "openclaw", "pve", "router"} <= set(
        dynamic["http"]["routers"]
    )
    assert not (STACK / "Dockerfile").exists()
    assert (REPO_ROOT / "apps" / "images" / "t3code" / "Dockerfile").is_file()


def test_docker_compose_accepts_the_merged_model_when_available(tmp_path: Path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable")
    environment = os.environ.copy()
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    environment["DOCKER_CONFIG"] = str(docker_config)
    environment["T3CODE_IMAGE_REF"] = (
        "ghcr.io/holybaechu/homelab-t3code@sha256:" + "1" * 64
    )
    probe = subprocess.run(
        [docker, "compose", "version"],
        text=True,
        capture_output=True,
        env=environment,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("Docker Compose plugin is unavailable")

    validation_compose = tmp_path / "compose.yml"
    (tmp_path / "traefik.env").write_text("", encoding="utf-8")
    (tmp_path / "cloudflare-ddns.env").write_text("", encoding="utf-8")
    validation_compose.write_text(
        COMPOSE.read_text(encoding="utf-8")
        .replace(
            "/etc/homelab/secrets/traefik.env",
            (tmp_path / "traefik.env").as_posix(),
        )
        .replace(
            "/etc/homelab/secrets/cloudflare-ddns.env",
            (tmp_path / "cloudflare-ddns.env").as_posix(),
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            docker,
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(STACK / ".env.example"),
            "-f",
            str(validation_compose),
            "config",
            "--format",
            "json",
            "--no-env-resolution",
            "--no-path-resolution",
        ],
        cwd=STACK,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert rendered["name"] == "homelab"
    assert set(rendered["services"]) == EXPECTED_SERVICES
