"""Protect credential projection and the prerequisite rollback engine."""

import json
import os
from pathlib import Path

import pytest

from scripts.ci.compose_release_engine import ComposeReleaseEngine, ReleaseError


def identity_bundle():
    return {
        "component": "apps", "version": 2,
        "cloudflare": {"traefik_dns_api_token": "current-token", "ddns_api_token": "ddns-token"},
        "adguard": {}, "qbittorrent": {}, "copyparty_users": [],
        "authentik": {"secret_key": "private-identity-key"},
        "headscale": {"oidc_client_secret": "private-oidc-key"},
    }


def test_legacy_preparation_uses_a_private_projection_without_changing_the_bundle(tmp_path):
    stack = tmp_path / "runtime" / "stack"
    stack.mkdir(parents=True)
    (stack / "release.json").write_text(json.dumps({
        "secret_bundle": {"version": 1, "compatible_versions": [1, 2]},
    }))
    source = tmp_path / "apps.json"
    source.write_text(json.dumps(identity_bundle()))
    original = source.read_bytes()
    engine = ComposeReleaseEngine("apps", install_root=tmp_path / "install")
    with engine._apps_secret_for_package(source, stack) as projected:
        document = json.loads(projected.read_text())
        assert document["version"] == 1
        assert "authentik" not in document and "headscale" not in document
        assert document["cloudflare"]["traefik_dns_api_token"] == "current-token"
        if os.name == "posix":
            assert projected.stat().st_mode & 0o777 == 0o600
    assert not projected.exists()
    assert source.read_bytes() == original


def test_unprepared_current_release_rejects_v2_before_replacing_credentials(tmp_path, monkeypatch):
    package = tmp_path / "previous" / "payload" / "stack"
    package.mkdir(parents=True)
    (package / "release.json").write_text(json.dumps({"secret_bundle": {"version": 1}}))
    secret_root = tmp_path / "secrets"
    secret_root.mkdir()
    installed = secret_root / "apps.json"
    installed.write_text('{"version":1}')
    source = tmp_path / "incoming.json"
    source.write_text(json.dumps(identity_bundle()))
    engine = ComposeReleaseEngine("apps", install_root=tmp_path / "install", secret_root=secret_root)
    monkeypatch.setattr(engine, "_load_state", lambda: {"current": {"release_id": "a" * 64}})
    monkeypatch.setattr(engine, "_release_path", lambda record: tmp_path / "previous")
    with pytest.raises(ReleaseError, match="v1 secret-compatibility release"):
        engine._install_secret_bundle(source, package)
    assert installed.read_text() == '{"version":1}'


def test_pre_compatibility_package_cannot_be_selected_for_v2_recovery(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "release.json").write_text(json.dumps({"secret_bundle": {"version": 1}}))
    source = tmp_path / "apps.json"
    source.write_text(json.dumps(identity_bundle()))
    engine = ComposeReleaseEngine("apps", install_root=tmp_path / "install")
    with pytest.raises(ReleaseError, match="rollback target predates"):
        with engine._apps_secret_for_package(source, stack):
            pytest.fail("an incompatible recovery package was selected")
