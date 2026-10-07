import re
from pathlib import Path

import pytest
import yaml

from tests.helpers import REPO_ROOT


PACKAGE = REPO_ROOT / "apps" / "compose" / "homelab"


def _labels(service: dict) -> dict[str, str]:
    labels = service.get("labels", {})
    if isinstance(labels, list):
        return dict(item.split("=", 1) for item in labels if "=" in item)
    return labels


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
    assert networks == {
        "proxy": {"name": "homelab_proxy"},
        "copyparty_proxy": {"name": "homelab_copyparty_proxy", "internal": True},
    }

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
            expected = ["proxy", "copyparty_proxy"] if name == "traefik" else ["copyparty_proxy"] if name == "copyparty" else ["proxy"]
            assert service.get("networks") == expected, name


def test_secret_inputs_and_smoke_endpoints_are_package_local(model):
    services = model["services"]
    env_files = [
        Path(path["path"] if isinstance(path, dict) else path)
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


def test_public_identity_endpoints_have_managed_dns(model):
    settings = model["services"]["cloudflare-ddns"]["environment"]
    domains = {domain.strip() for domain in settings["DOMAINS"].split(",")}
    assert {"auth.home.hchu.me", "headscale.home.hchu.me", "metube.home.hchu.me"} <= domains
    assert settings["PROXIED"] == "false"
