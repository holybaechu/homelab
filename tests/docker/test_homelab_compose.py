import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.helpers import REPO_ROOT


PACKAGE = REPO_ROOT / "apps" / "compose" / "homelab"
TOPOLOGY = REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "topology.json"


def _labels(service: dict) -> dict[str, str]:
    labels = service.get("labels", {})
    if isinstance(labels, list):
        return dict(item.split("=", 1) for item in labels if "=" in item)
    return labels


def _valid_bundle() -> dict:
    return {
        "component": "apps",
        "version": 1,
        "cloudflare": {
            "traefik_dns_api_token": "traefik-token",
            "ddns_api_token": "ddns-token",
        },
        "adguard": {
            "username": "admin",
            "password_hash": "$2y$10$" + "." * 53,
        },
        "qbittorrent": {
            "username": "operator",
            "password_hash": "@ByteArray(%s:%s)"
            % (
                base64.b64encode(bytes(16)).decode(),
                base64.b64encode(bytes(64)).decode(),
            ),
        },
        "copyparty_users": [{"name": "operator", "password": "share-secret"}],
    }


@pytest.fixture
def model() -> dict:
    return yaml.safe_load((PACKAGE / "compose.yml").read_text(encoding="utf-8"))


def test_compose_is_one_closed_runtime_boundary(model):
    services = model["services"]
    assert services
    assert "${" not in (PACKAGE / "compose.yml").read_text(encoding="utf-8")
    assert all(
        "build" not in service
        and re.fullmatch(r".+@sha256:[0-9a-f]{64}", service.get("image", ""))
        for service in services.values()
    )

    networks = model["networks"]
    assert len(networks) == 1
    network_key, network = next(iter(networks.items()))
    assert network.get("name")
    assert not network.get("external", False)

    volumes = model["volumes"]
    volume_names = [volume.get("name") for volume in volumes.values()]
    assert volumes and all(volume_names)
    assert len(volume_names) == len(set(volume_names))
    assert all(not volume.get("external", False) for volume in volumes.values())

    for name, service in services.items():
        assert service["restart"] == "unless-stopped", name
        declared_process_health = _labels(service).get("homelab.health") == "process"
        assert "healthcheck" in service or declared_process_health, name
        if "network_mode" not in service:
            assert service.get("networks") == [network_key], name


def test_secret_inputs_and_smoke_endpoints_are_package_local(model):
    services = model["services"]
    env_files = [
        Path(path)
        for service in services.values()
        for path in service.get("env_file", [])
    ]
    assert env_files
    assert all(not path.is_absolute() and path.parts[0] == ".secrets" for path in env_files)

    routed = {
        name
        for name, service in services.items()
        if _labels(service).get("traefik.enable") == "true"
    }
    smoked = {
        name
        for name, service in services.items()
        if _labels(service).get("homelab.smoke.url")
    }
    assert routed <= smoked
    assert "adguard" in smoked


def test_adguard_can_persist_runtime_normalization_in_the_rebuilt_slot(model):
    mounts = model["services"]["adguard"]["volumes"]
    assert "./generated/adguard:/opt/adguardhome/conf:rw" in mounts
    assert not any("AdGuardHome.yaml" in mount for mount in mounts)


def test_real_compose_render_accepts_one_prepared_package(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is unavailable")

    stage = tmp_path / "homelab"
    shutil.copytree(PACKAGE, stage)
    shutil.copy2(TOPOLOGY, stage / "topology.json")
    bundle = tmp_path / "apps.json"
    bundle.write_text(json.dumps(_valid_bundle()), encoding="utf-8")
    if sys.platform != "win32":
        bundle.chmod(0o600)

    prepared = subprocess.run(
        [
            sys.executable,
            str(stage / "prepare_release.py"),
            "--secret-bundle",
            str(bundle),
            "--release-root",
            str(stage),
            "--topology",
            str(stage / "topology.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr

    rendered = subprocess.run(
        [docker, "compose", "--project-name", "homelab", "-f", "compose.yml", "config", "--quiet"],
        cwd=stage,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
