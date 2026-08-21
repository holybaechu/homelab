import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from tests.helpers import REPO_ROOT


PACKAGE = REPO_ROOT / "apps" / "compose" / "homelab"
TOPOLOGY = REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "topology.json"


def valid_bundle() -> dict:
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
                base64.b64encode(bytes(range(16))).decode(),
                base64.b64encode(bytes(range(64))).decode(),
            ),
        },
        "copyparty_users": [
            {"name": "operator", "password": "share-secret"},
            {"name": "reader", "password": "read:secret"},
        ],
    }


def stage_and_bundle(tmp_path: Path, payload: dict | None = None) -> tuple[Path, Path]:
    stage = tmp_path / "homelab"
    shutil.copytree(PACKAGE, stage)
    shutil.copy2(TOPOLOGY, stage / "topology.json")
    bundle = tmp_path / "apps.json"
    bundle.write_text(json.dumps(payload or valid_bundle()), encoding="utf-8")
    if os.name == "posix":
        bundle.chmod(0o600)
    return stage, bundle


def run_prepare(stage: Path, bundle: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
        encoding="utf-8",
        check=False,
    )


def test_preparer_materializes_every_private_input_atomically(tmp_path):
    payload = valid_bundle()
    stage, bundle = stage_and_bundle(tmp_path, payload)

    result = run_prepare(stage, bundle)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "homelab release preparation completed\n"

    outputs = {
        stage / ".secrets/traefik.env",
        stage / ".secrets/cloudflare-ddns.env",
        stage / "generated/adguard/AdGuardHome.yaml",
        stage / "generated/traefik/routes.yml",
        stage / "generated/copyparty.conf",
        stage / "generated/qbittorrent/qBittorrent.conf",
    }
    assert all(path.is_file() and not path.is_symlink() for path in outputs)
    if os.name == "posix":
        assert {path.stat().st_mode & 0o777 for path in outputs} == {0o600}

    assert (stage / ".secrets/traefik.env").read_text() == "CF_DNS_API_TOKEN=traefik-token\n"
    assert (stage / ".secrets/cloudflare-ddns.env").read_text() == "CLOUDFLARE_API_TOKEN=ddns-token\n"

    adguard = yaml.safe_load((stage / "generated/adguard/AdGuardHome.yaml").read_text())
    assert adguard["users"] == [
        {"name": "admin", "password": payload["adguard"]["password_hash"]}
    ]
    assert adguard["filtering"]["safe_search"]["enabled"] is False

    topology = json.loads((stage / "topology.json").read_text(encoding="utf-8"))
    hosts = topology["all"]["children"]
    pve = hosts["pve_hosts"]["hosts"]["pve"]
    debian = hosts["debian"]["hosts"]
    assert adguard["filtering"]["rewrites"] == [
        {
            "domain": "*.home.hchu.me",
            "answer": debian["docker_apps"]["ansible_host"],
            "enabled": True,
        }
    ]
    routes = yaml.safe_load(
        (stage / "generated/traefik/routes.yml").read_text(encoding="utf-8")
    )
    services = routes["http"]["services"]
    assert services["openclaw"]["loadBalancer"]["servers"] == [
        {"url": f"http://{debian['openclaw']['ansible_host']}:18789"}
    ]
    assert services["pve"]["loadBalancer"]["servers"] == [
        {"url": f"https://{pve['ansible_host']}:8006"}
    ]
    assert services["router"]["loadBalancer"]["servers"] == [
        {"url": f"http://{debian['docker_apps']['gateway']}"}
    ]

    qbit = (stage / "generated/qbittorrent/qBittorrent.conf").read_text()
    assert payload["qbittorrent"]["password_hash"] in qbit
    assert "password=" not in json.dumps(payload["qbittorrent"])
    copyparty = (stage / "generated/copyparty.conf").read_text()
    assert "  operator: share-secret" in copyparty
    assert "  reader: read:secret" in copyparty
    assert copyparty.count("A: operator") == 3
    assert "r: operator" in copyparty
    assert "@@" not in adguard.__repr__() + routes.__repr__() + qbit + copyparty


def test_managed_route_addresses_exist_only_in_the_bundle_topology_snapshot() -> None:
    topology = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    children = topology["all"]["children"]
    debian = children["debian"]["hosts"]
    managed_addresses = {
        children["pve_hosts"]["hosts"]["pve"]["ansible_host"],
        debian["docker_apps"]["ansible_host"],
        debian["docker_apps"]["gateway"],
        debian["openclaw"]["ansible_host"],
    }
    package_inputs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PACKAGE / "config").rglob("*")
        if path.is_file()
    )
    assert managed_addresses.isdisjoint(package_inputs.split())


def test_preparer_rejects_a_topology_snapshot_that_cannot_route_every_service(tmp_path):
    stage, bundle = stage_and_bundle(tmp_path)
    topology = json.loads((stage / "topology.json").read_text(encoding="utf-8"))
    del topology["all"]["children"]["debian"]["hosts"]["openclaw"]
    (stage / "topology.json").write_text(json.dumps(topology), encoding="utf-8")

    result = run_prepare(stage, bundle)

    assert result.returncode == 1
    assert "topology lacks a required application route host" in result.stderr
    assert not (stage / "generated").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(component="openclaw"),
        lambda value: value.update(version=True),
        lambda value: value.update(unexpected="value"),
        lambda value: value["adguard"].update(password_hash="$2y$99$" + "." * 53),
        lambda value: value["qbittorrent"].update(password="plaintext"),
        lambda value: value["qbittorrent"].update(password_hash="not-a-hash"),
        lambda value: value["copyparty_users"].append(copy.deepcopy(value["copyparty_users"][0])),
    ],
)
def test_preparer_rejects_wrong_component_schema_or_secret_shape(tmp_path, mutation):
    payload = valid_bundle()
    mutation(payload)
    stage, bundle = stage_and_bundle(tmp_path, payload)

    result = run_prepare(stage, bundle)
    assert result.returncode == 1
    assert result.stderr.startswith("homelab release preparation failed:")
    assert not (stage / ".secrets").exists()
    assert not (stage / "generated").exists()


def test_preparer_rejects_duplicate_json_keys(tmp_path):
    stage, bundle = stage_and_bundle(tmp_path)
    bundle.write_text(
        json.dumps(valid_bundle()).replace('"component": "apps"', '"component": "apps", "component": "apps"'),
        encoding="utf-8",
    )
    if os.name == "posix":
        bundle.chmod(0o600)

    result = run_prepare(stage, bundle)
    assert result.returncode == 1
    assert "not valid UTF-8 JSON" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission boundary")
def test_preparer_rejects_broad_bundle_permissions_and_symlinks(tmp_path):
    stage, bundle = stage_and_bundle(tmp_path)
    bundle.chmod(0o644)
    assert run_prepare(stage, bundle).returncode == 1

    bundle.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(bundle)
    assert run_prepare(stage, link).returncode == 1
