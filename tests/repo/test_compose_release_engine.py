from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Mapping, Sequence

import pytest
import yaml

from scripts.ci.compose_release_engine import (
    ComposeReleaseEngine,
    ENGINE_BUNDLE_PATH,
    ENGINE_VERSION,
    FileLock,
    ReleaseError,
    build_bundle,
    canonical_json_bytes,
    file_sha256,
    release_record,
    validate_manifest,
    _tree_content_sha256,
)
from tests.helpers import REPO_ROOT


ENGINE = REPO_ROOT / "scripts" / "ci" / "compose_release_engine.py"
TOPOLOGY = REPO_ROOT / "infra/ansible/inventory/prod/topology.json"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    path.chmod(0o600)


def app_secrets(tag: str) -> dict[str, object]:
    return {
        "component": "apps",
        "version": 2,
        "authentik": {
            "secret_key": "k" * 64,
            "database_password": f"database-{tag}",
            "bootstrap_email": "admin@example.test",
            "bootstrap_password": f"bootstrap-{tag}",
        },
        "headscale": {"oidc_client_secret": f"oidc-{tag}"},
        "cloudflare": {
            "traefik_dns_api_token": f"traefik-{tag}",
            "ddns_api_token": f"ddns-{tag}",
        },
        "adguard": {
            "username": "admin",
            "password_hash": "$2b$12$" + "a" * 53,
        },
        "qbittorrent": {
            "username": "admin",
            "password_hash": "@ByteArray(" + "A" * 22 + "==:" + "B" * 86 + "==)",
        },
        "copyparty_users": [{"name": "owner", "password": f"copy-{tag}"}],
    }


def test_v2_upgrade_requires_a_compatible_rollback_engine_before_secret_install(tmp_path):
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    legacy, original = make_bundle_root(tmp_path, "1", legacy_secrets=True, compatible=False)
    payload = app_secrets("old")
    payload.pop("authentik")
    payload.pop("headscale")
    payload["version"] = 1
    source = tmp_path / "apps.json"
    write_json(source, payload)
    engine.deploy_bundle(legacy, source)
    candidate, _ = make_bundle_root(tmp_path, "2")
    write_json(source, app_secrets("new"))
    with pytest.raises(ReleaseError, match="v1 secret-compatibility release"):
        engine.deploy_bundle(candidate, source)
    assert state(engine)["current"] == original
    assert json.loads((engine.secret_root / "apps.json").read_text()) == payload


def test_failed_v2_activation_recovers_v1_with_current_credentials_and_can_audit(tmp_path):
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    legacy, original = make_bundle_root(tmp_path, "1", legacy_secrets=True)
    payload = app_secrets("old")
    payload.pop("authentik")
    payload.pop("headscale")
    payload["version"] = 1
    source = tmp_path / "apps.json"
    write_json(source, payload)
    engine.deploy_bundle(legacy, source)
    candidate, _ = make_bundle_root(tmp_path, "2")
    current_secrets = app_secrets("new")
    write_json(source, current_secrets)
    runner.fail_next_up = True
    with pytest.raises(ReleaseError):
        engine.deploy_bundle(candidate, source)
    assert state(engine)["current"] == original
    assert json.loads((engine.secret_root / "apps.json").read_text()) == current_secrets
    engine.audit()
    active = engine.runtime_root / state(engine)["active_slot"]
    assert "traefik-new" in (active / "stack/.secrets/traefik.env").read_text()
    assert not list(engine.runtime_root.rglob(".apps-secret-*"))
    assert b"bootstrap-new" not in public_bytes(engine)




def make_bundle_root(
    tmp_path: Path, source_digit: str, *, apps_traefik_ref: str | None = None,
    legacy_secrets: bool = False, compatible: bool = True,
) -> tuple[Path, dict]:
    root = tmp_path / f"bundle-apps-{source_digit}"
    stack = root / "payload" / "stack"
    shutil.copytree(REPO_ROOT / "apps/compose/homelab", stack)
    shutil.copy2(TOPOLOGY, stack / "topology.json")
    if legacy_secrets:
        metadata = json.loads((stack / "release.json").read_text())
        metadata["secret_bundle"]["version"] = 1
        if not compatible:
            metadata["secret_bundle"].pop("compatible_versions")
        (stack / "release.json").write_text(json.dumps(metadata))
        (stack / "prepare_release.py").write_text('''import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
for name in ("secret-bundle", "release-root", "topology"):
    p.add_argument("--" + name, required=True)
a = p.parse_args()
bundle = json.loads(Path(a.secret_bundle).read_text())
assert set(bundle) == {"component", "version", "cloudflare", "adguard", "qbittorrent", "copyparty_users"}
assert bundle["version"] == 1
root = Path(a.release_root) / ".secrets"
root.mkdir(mode=0o700, exist_ok=True)
path = root / "traefik.env"
path.write_text("CF_DNS_API_TOKEN=" + bundle["cloudflare"]["traefik_dns_api_token"] + "\\n")
path.chmod(0o600)
''')
    if apps_traefik_ref is not None:
        path = stack / "compose.yml"
        text = path.read_text(encoding="utf-8")
        old = yaml.safe_load(text)["services"]["traefik"]["image"]
        path.write_text(text.replace(old, apps_traefik_ref, 1), encoding="utf-8")
    engine = root / ENGINE_BUNDLE_PATH
    engine.parent.mkdir(parents=True)
    shutil.copy2(ENGINE, engine)
    manifest = validate_manifest({
        "schema": 1, "target": "apps", "source_sha": source_digit * 40,
        "config_commit": None, "images": {},
        "engine": {"version": ENGINE_VERSION, "path": ENGINE_BUNDLE_PATH, "sha256": file_sha256(engine)},
        "payload": {"stack_sha256": _tree_content_sha256(stack, private=False), "config_sha256": None},
    })
    write_json(root / "manifest.json", manifest)
    return root, release_record(manifest)




class FakeDockerRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str, dict[str, str]]] = []
        self.fail_next_config = False
        self.fail_next_up = False
        self.project_images: list[str] = []
        self.image_inventory: list[dict[str, str]] = []
        self.blocked_image_ids: set[str] = set()
        self.network_names: list[str] = []
        self.network_labels: dict[str, str] = {
            "com.docker.compose.project": "homelab",
            "com.docker.compose.network": "proxy",
        }
        self.process_restart_count = 0

    @staticmethod
    def _completed(
        argv: Sequence[str], stdout: str = "", returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), returncode, stdout, "secret-output-is-hidden")

    def run(self, argv: Sequence[str], *, cwd: Path,
            env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = tuple(str(item) for item in argv)
        self.calls.append((command, str(cwd), {
            key: value for key, value in (env or {}).items() if key.startswith("HOMELAB_")
        }))
        if len(command) > 1 and Path(command[1]).name == "prepare_release.py":
            return subprocess.run(list(command), cwd=cwd, check=False,
                                  capture_output=True, text=True, encoding="utf-8")
        if Path(command[0]).name == "smoke.sh":
            return self._completed(command)
        assert command[0] == "docker", command
        if command[1:3] == ("image", "inspect"):
            return self._completed(command, json.dumps([command[-1]]) + "\n")
        if command[1] == "ps" and "--all" in command:
            return self._completed(command, "".join(f"{ref}\n" for ref in self.project_images))
        if command[1:3] == ("image", "ls"):
            return self._completed(command, "".join(json.dumps(row) + "\n" for row in self.image_inventory))
        if command[1:3] == ("image", "rm"):
            if command[-1] in self.blocked_image_ids:
                return self._completed(command, returncode=1)
            self.image_inventory = [row for row in self.image_inventory if row.get("ID") != command[-1]]
            return self._completed(command)
        if command[1:3] == ("image", "prune"):
            raise AssertionError("global image pruning is forbidden")
        if command[1:3] == ("network", "ls"):
            return self._completed(command, "".join(f"{name}\n" for name in self.network_names))
        if command[1:3] == ("network", "inspect"):
            return self._completed(command, json.dumps(self.network_labels) + "\n")
        if command[1] == "inspect":
            return self._completed(command, json.dumps({
                "State": {"Running": True, "Status": "running", "Restarting": False},
                "RestartCount": self.process_restart_count,
            }) + "\n")
        assert command[1] == "compose"
        assert command[command.index("--project-name") + 1] == "homelab"
        if "config" in command and self.fail_next_config:
            self.fail_next_config = False
            return self._completed(command, returncode=6)
        if "config" in command and "--format" in command:
            return self._completed(command, json.dumps(yaml.safe_load((cwd / "compose.yml").read_text())) + "\n")
        if "ps" in command and "--quiet" in command:
            return self._completed(command, "a" * 12 + "\n")
        if "ps" in command and "--status" in command:
            services = yaml.safe_load((cwd / "compose.yml").read_text())["services"]
            return self._completed(command, "".join(f"{name}\n" for name in services))
        if "up" in command and self.fail_next_up:
            self.fail_next_up = False
            return self._completed(command, returncode=7)
        return self._completed(command)


def engine_for(tmp_path: Path, target: str, runner: FakeDockerRunner) -> ComposeReleaseEngine:
    return ComposeReleaseEngine(
        target,
        install_root=tmp_path / f"install-{target}",
        secret_root=tmp_path / f"secrets-{target}",
        runner=runner,
        minimum_free_bytes=0,
    )


@pytest.mark.skipif(os.name != "posix", reason="directory bind-mount inode behavior requires POSIX")
def test_failed_activation_recovery_refreshes_a_replaced_bind_mount(tmp_path):
    class BindMountRunner(FakeDockerRunner):
        mounted_source = None
        descriptor = None

        def run(self, argv, *, cwd, env=None):
            result = super().run(argv, cwd=cwd, env=env)
            if len(argv) > 1 and argv[1] == 'compose' and 'up' in argv:
                source = cwd / 'generated/adguard'
                if self.descriptor is None or self.mounted_source != source or '--force-recreate' in argv:
                    if self.descriptor is not None:
                        os.close(self.descriptor)
                    self.descriptor = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
                    self.mounted_source = source
                try:
                    os.stat('AdGuardHome.yaml', dir_fd=self.descriptor)
                except FileNotFoundError:
                    return self._completed(argv, returncode=1)
            return result

    runner = BindMountRunner()
    engine = engine_for(tmp_path, 'apps', runner)
    original, record = make_bundle_root(tmp_path, '1')
    candidate, _ = make_bundle_root(tmp_path, '2')
    incoming = tmp_path / 'apps.json'
    write_json(incoming, app_secrets('current'))
    try:
        engine.deploy_bundle(original, incoming)
        runner.fail_next_up = True
        with pytest.raises(ReleaseError, match='prior state was restored'):
            engine.deploy_bundle(candidate, incoming)
        assert state(engine)['current'] == record
        assert state(engine)['pending'] is None
        assert stat.S_ISREG(os.stat('AdGuardHome.yaml', dir_fd=runner.descriptor).st_mode)
    finally:
        if runner.descriptor is not None:
            os.close(runner.descriptor)


def state(engine: ComposeReleaseEngine) -> dict:
    return json.loads(engine.state_path.read_text(encoding="utf-8"))


def public_bytes(engine: ComposeReleaseEngine) -> bytes:
    content = bytearray()
    for root in (engine.release_root, engine.state_root):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                content.extend(path.read_bytes())
    return bytes(content)


def assert_not_public(engine: ComposeReleaseEngine, runner: FakeDockerRunner, values: list[str]) -> None:
    public = public_bytes(engine)
    calls = repr(runner.calls).encode()
    for value in values:
        encoded = value.encode()
        digest = hashlib.sha256(encoded).hexdigest().encode()
        assert encoded not in public
        assert digest not in public
        assert encoded not in calls
        assert digest not in calls


def test_apps_common_path_rotates_secrets_and_rolls_back_source_only(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle_one, record_one = make_bundle_root(tmp_path, "1")
    bundle_two, record_two = make_bundle_root(tmp_path, "2")
    incoming = tmp_path / "apps.json"

    write_json(incoming, app_secrets("old"))
    assert engine.deploy_bundle(bundle_one, incoming) == record_one
    assert state(engine)["active_slot"] == "a"
    assert not (engine._release_path(record_one) / "payload" / "stack" / ".secrets").exists()

    write_json(incoming, app_secrets("new"))
    sync_call_start = len(runner.calls)
    assert engine.sync_secrets(incoming) == record_one
    sync_calls = [call[0] for call in runner.calls[sync_call_start:]]
    assert not any(
        call[:2] == ("docker", "compose") and call[-1:] == ("pull",)
        for call in sync_calls
    )
    assert not any(call[:3] == ("docker", "image", "pull") for call in sync_calls)
    assert any(
        call[-2:] == ("--pull", "never")
        for call in sync_calls
        if call[:2] == ("docker", "compose") and "up" in call
    )
    assert state(engine)["active_slot"] == "b"
    assert not (engine.runtime_root / "a").exists()
    assert "ddns-new" in (engine.runtime_root / "b" / "stack" / ".secrets" / "cloudflare-ddns.env").read_text()

    assert engine.deploy_bundle(bundle_two, incoming) == record_two
    assert state(engine)["previous"] == record_one
    write_json(incoming, app_secrets("latest"))
    engine.sync_secrets(incoming)
    assert engine.rollback() == record_one
    final = state(engine)
    assert final["current"] == record_one
    assert final["previous"] == record_two
    assert final["pending"] is None
    active = engine.runtime_root / final["active_slot"]
    assert "ddns-latest" in (active / "stack" / ".secrets" / "cloudflare-ddns.env").read_text()
    assert len([path for path in engine.runtime_root.iterdir() if path.name in {"a", "b"}]) == 1

    compose = [call[0] for call in runner.calls if call[0][:2] == ("docker", "compose")]
    assert any(call[-3:] == ("config", "--format", "json") for call in compose)
    assert any(call[-1:] == ("pull",) for call in compose)
    assert any(
        call[-8:]
        == (
            "up",
            "-d",
            "--wait",
            "--force-recreate",
            "--remove-orphans",
            "--no-build",
            "--pull",
            "never",
        )
        for call in compose
    )
    assert any(Path(call[0][0]).name == "smoke.sh" for call in runner.calls)
    assert_not_public(
        engine,
        runner,
        ["traefik-old", "ddns-old", "copy-old", "traefik-latest", "ddns-latest", "copy-latest"],
    )


def test_first_apps_deploy_rejects_an_unowned_proxy_network_without_stopping_current(
    tmp_path: Path,
) -> None:
    runner = FakeDockerRunner()
    runner.network_names = ["homelab_proxy"]
    runner.network_labels = {}
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "8")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))

    with pytest.raises(ReleaseError, match="prior state was restored") as caught:
        engine.deploy_bundle(bundle, incoming)
    assert isinstance(caught.value.__cause__, ReleaseError)
    assert "not Compose-owned" in str(caught.value.__cause__)

    compose_commands = [
        call[0] for call in runner.calls if call[0][:2] == ("docker", "compose")
    ]
    assert not any("up" in command or "down" in command for command in compose_commands)
    failed_state = state(engine)
    assert failed_state["current"] is None
    assert failed_state["pending"] is None
    assert not any(path.name in {"a", "b"} for path in engine.runtime_root.iterdir())

    runner.network_labels = {
        "com.docker.compose.project": "homelab",
        "com.docker.compose.network": "proxy",
    }
    assert engine.deploy_bundle(bundle, incoming)["source_sha"] == "8" * 40
    assert state(engine)["current"] is not None


def test_process_health_label_rejects_an_early_restart_before_commit(
    tmp_path: Path,
) -> None:
    runner = FakeDockerRunner()
    runner.process_restart_count = 1
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "9")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))

    with pytest.raises(ReleaseError, match="prior state was restored") as caught:
        engine.deploy_bundle(bundle, incoming)

    assert isinstance(caught.value.__cause__, ReleaseError)
    assert "not stably running" in str(caught.value.__cause__)
    assert state(engine)["current"] is None
    assert not any(Path(call[0][0]).name == "smoke.sh" for call in runner.calls)




def test_failed_activation_keeps_new_bundle_and_restores_prior_release(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle_one, record_one = make_bundle_root(tmp_path, "5")
    bundle_two, _record_two = make_bundle_root(tmp_path, "6")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))
    engine.deploy_bundle(bundle_one, incoming)

    write_json(incoming, app_secrets("latest"))
    runner.fail_next_up = True
    with pytest.raises(ReleaseError, match="prior state was restored"):
        engine.deploy_bundle(bundle_two, incoming)
    restored = state(engine)
    assert restored["current"] == record_one
    assert restored["pending"] is None
    assert json.loads((engine.secret_root / "apps.json").read_text()) == app_secrets("latest")
    active = engine.runtime_root / restored["active_slot"] / "stack" / ".secrets"
    assert "traefik-latest" in (active / "traefik.env").read_text()


def test_failed_first_preflight_never_stops_a_project_that_was_not_started(
    tmp_path: Path,
) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "d")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))
    runner.fail_next_config = True

    with pytest.raises(ReleaseError, match="prior state was restored"):
        engine.deploy_bundle(bundle, incoming)

    compose_actions = [
        command
        for command, _cwd, _env in runner.calls
        if command[:2] == ("docker", "compose")
    ]
    assert not any("up" in command for command in compose_actions)
    assert not any("down" in command for command in compose_actions)
    assert state(engine) == {
        "schema": 1,
        "target": "apps",
        "current": None,
        "previous": None,
        "pending": None,
        "active_slot": None,
    }


def test_failed_first_activation_stops_partial_project_and_clears_pending(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "a")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))
    runner.fail_next_up = True

    with pytest.raises(ReleaseError, match="prior state was restored"):
        engine.deploy_bundle(bundle, incoming)
    assert state(engine) == {
        "schema": 1,
        "target": "apps",
        "current": None,
        "previous": None,
        "pending": None,
        "active_slot": None,
    }
    assert not any(path.name in {"a", "b"} for path in engine.runtime_root.iterdir())
    assert any("down" in call[0] for call in runner.calls)


@pytest.mark.parametrize(
    "kill_point",
    ("pending", "rendered", "activated", "old-slot-removed"),
)
def test_audit_recovers_every_interrupted_transition_point(
    tmp_path: Path, kill_point: str
) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle_one, record_one = make_bundle_root(tmp_path, "7")
    bundle_two, record_two = make_bundle_root(tmp_path, "8")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("new"))
    engine.deploy_bundle(bundle_one, incoming)

    candidate = engine._materialize(
        bundle_two, json.loads((bundle_two / "manifest.json").read_text())
    )
    original = engine._load_state()
    candidate_slot = engine._inactive_slot(original["active_slot"])
    engine._pending_state(original, candidate, candidate_slot)
    if kill_point in {"rendered", "activated", "old-slot-removed"}:
        engine._render_slot(candidate, candidate_slot)
    if kill_point in {"activated", "old-slot-removed"}:
        engine._activate(
            candidate,
            candidate_slot,
            pull=False,
            mark_pending_activation=True,
        )
    if kill_point == "old-slot-removed":
        engine._remove_slot(original["active_slot"])
    assert state(engine)["pending"]["candidate"] == record_two
    assert state(engine)["pending"]["activation_started"] is (
        kill_point in {"activated", "old-slot-removed"}
    )

    runtime_scratch = engine.runtime_root / ".a.tmp-deadbeef"
    runtime_scratch.mkdir()
    (runtime_scratch / "old-secret").write_text("must-be-removed")
    validation_scratch = engine.state_root / ".secret-check-interrupted"
    validation_scratch.mkdir()
    (validation_scratch / "old-secret").write_text("must-be-removed")
    state_scratch = engine.state_root / (".release-state.json.tmp-" + "c" * 32)
    state_scratch.write_text("incomplete")
    secret_scratch = engine.secret_root / (".apps.json.tmp-" + "d" * 32)
    secret_scratch.write_text("must-be-removed")

    assert engine.audit() == record_one
    recovered = state(engine)
    assert recovered["current"] == record_one
    assert recovered["pending"] is None
    assert [path.name for path in engine.runtime_root.iterdir() if path.name in {"a", "b"}] == [recovered["active_slot"]]
    active = engine.runtime_root / recovered["active_slot"] / "stack" / ".secrets"
    assert "traefik-new" in (active / "traefik.env").read_text()
    assert not runtime_scratch.exists()
    assert not validation_scratch.exists()
    assert not state_scratch.exists()
    assert not secret_scratch.exists()


def test_invalid_component_bundle_never_replaces_installed_bundle(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "9")
    incoming = tmp_path / "apps.json"
    original = app_secrets("old")
    write_json(incoming, original)
    engine.deploy_bundle(bundle, incoming)

    write_json(incoming, {"component": "apps", "version": 1, "unexpected": True})
    with pytest.raises(ReleaseError, match="validation"):
        engine.sync_secrets(incoming)
    assert json.loads((engine.secret_root / "apps.json").read_text()) == original


def test_complete_descriptor_and_deterministic_bundle(tmp_path: Path) -> None:
    outputs = [tmp_path / "one.tar", tmp_path / "two.tar"]
    results = [build_bundle(
        target="apps", source_sha="4" * 40,
        stack_root=REPO_ROOT / "apps/compose/homelab", topology_path=TOPOLOGY,
        engine_path=ENGINE, output=output,
    ) for output in outputs]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert results[0]["sha256"] == results[1]["sha256"]




@pytest.mark.parametrize("missing", ("compose.yml", "smoke.sh", "prepare_release.py", "release.json"))
def test_incomplete_package_cannot_produce_a_release_bundle(tmp_path: Path, missing: str) -> None:
    stack = tmp_path / "stack"
    shutil.copytree(REPO_ROOT / "apps/compose/homelab", stack)
    (stack / missing).unlink()
    output = tmp_path / "incomplete.tar"
    with pytest.raises(ReleaseError, match=missing):
        build_bundle(target="apps", source_sha="a" * 40, stack_root=stack,
                     engine_path=ENGINE, output=output, topology_path=TOPOLOGY)
    assert not output.exists()




def test_bundle_rejects_rendered_secret_state_in_source_package(tmp_path: Path) -> None:
    stack = tmp_path / "stack"
    shutil.copytree(REPO_ROOT / "apps" / "compose" / "homelab", stack)
    rendered = stack / ".secrets"
    rendered.mkdir()
    (rendered / "leaked.env").write_text("TOKEN=must-not-ship\n")
    with pytest.raises(ReleaseError, match="rendered runtime state"):
        build_bundle(
            target="apps",
            source_sha="b" * 40,
            stack_root=stack,
            engine_path=ENGINE,
            output=tmp_path / "forbidden.tar",
            topology_path=TOPOLOGY,
        )
    assert not (tmp_path / "forbidden.tar").exists()


def test_bundle_rejects_a_tampered_embedded_topology_before_activation(
    tmp_path: Path,
) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "c")
    topology_path = bundle / "payload" / "stack" / "topology.json"
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    topology["all"]["children"]["debian"]["hosts"]["docker_apps"][
        "ansible_host"
    ] = "192.0.2.99"
    write_json(topology_path, topology)
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))

    with pytest.raises(ReleaseError, match="stack content differs"):
        engine.deploy_bundle(bundle, incoming)

    assert not any("pull" in command or "up" in command for command, _, _ in runner.calls)




def test_blocked_image_cleanup_is_persisted_and_retried_later(tmp_path: Path) -> None:
    refs = [f"traefik:v3@sha256:{digit * 64}" for digit in ("1", "2", "3")]
    bundles = [make_bundle_root(tmp_path, str(i), apps_traefik_ref=ref)
               for i, ref in enumerate(refs, start=1)]
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("new"))
    for bundle, _record in bundles[:2]:
        engine.deploy_bundle(bundle, incoming)
    stale = "sha256:external-container-image"
    runner.image_inventory = [{"ID": stale, "Repository": "traefik",
                               "Tag": "<none>", "Digest": "sha256:" + "1" * 64}]
    runner.blocked_image_ids.add(stale)
    engine.deploy_bundle(bundles[2][0], incoming)
    assert not engine._release_path(bundles[0][1]).exists()
    assert runner.image_inventory[0]["ID"] == stale
    assert json.loads(engine.deferred_image_path.read_text()) == {
        "schema": 1, "target": "apps", "refs": [refs[0]],
    }
    assert sum(cmd[1:3] == ("image", "rm") for cmd, _, _ in runner.calls) == 1
    runner.blocked_image_ids.clear()
    restarted = engine_for(tmp_path, "apps", runner)
    restarted.audit()
    assert runner.image_inventory == []
    assert not restarted.deferred_image_path.exists()
    assert sum(cmd[1:3] == ("image", "rm") for cmd, _, _ in runner.calls) == 2




def test_apps_retention_matches_pinned_refs_to_split_docker_inventory_fields(
    tmp_path: Path,
) -> None:
    repository = "traefik"
    tag = "v3.7.10"
    refs = [
        f"{repository}:{tag}@sha256:{digit * 64}"
        for digit in ("1", "2", "3")
    ]
    bundles = [
        make_bundle_root(tmp_path, str(index),
            apps_traefik_ref=ref,
        )
        for index, ref in enumerate(refs, start=1)
    ]
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))
    for bundle, _record in bundles[:2]:
        engine.deploy_bundle(bundle, incoming)

    runner.image_inventory = [
        {
            "ID": "sha256:stale-app",
            "Repository": repository,
            "Tag": "<none>",
            "Digest": "sha256:" + "1" * 64,
        },
        {
            "ID": "sha256:previous-app",
            "Repository": repository,
            "Tag": "<none>",
            "Digest": "sha256:" + "2" * 64,
        },
        {
            "ID": "sha256:current-app",
            "Repository": repository,
            "Tag": tag,
            "Digest": "sha256:" + "3" * 64,
        },
        {
            "ID": "sha256:unrelated-app",
            "Repository": repository,
            "Tag": "<none>",
            "Digest": "sha256:" + "4" * 64,
        },
    ]

    engine.deploy_bundle(bundles[2][0], incoming)

    removed_ids = {
        command[-1]
        for command, _cwd, _env in runner.calls
        if command[1:3] == ("image", "rm")
    }
    assert removed_ids == {"sha256:stale-app"}


def test_low_capacity_aborts_before_any_pull_or_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeDockerRunner()
    engine = ComposeReleaseEngine(
        "apps",
        install_root=tmp_path / "install-apps",
        secret_root=tmp_path / "secrets-apps",
        runner=runner,
        minimum_free_bytes=4 * 1024**3,
    )
    bundle, _record = make_bundle_root(tmp_path, "a")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))
    monkeypatch.setattr(
        "scripts.ci.compose_release_engine.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=2048, used=2048, free=0),
    )

    with pytest.raises(ReleaseError, match="prior state was restored") as caught:
        engine.deploy_bundle(bundle, incoming)
    assert caught.value.__cause__ is not None
    assert "requires 4 GiB free" in str(caught.value.__cause__)

    commands = [call[0] for call in runner.calls]
    assert all("pull" not in command for command in commands)
    assert all("up" not in command for command in commands)
    assert state(engine)["current"] is None
    assert state(engine)["pending"] is None


def test_file_lock_serializes_independent_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "release.lock"
    ready = tmp_path / "waiter-ready"
    acquired = tmp_path / "waiter-acquired"
    program = """
import sys
from pathlib import Path
from scripts.ci.compose_release_engine import FileLock

lock_path, ready, acquired = map(Path, sys.argv[1:])
ready.write_text('ready', encoding='utf-8')
with FileLock(lock_path):
    acquired.write_text('acquired', encoding='utf-8')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    process: subprocess.Popen[str] | None = None
    try:
        with FileLock(lock_path):
            process = subprocess.Popen(
                [sys.executable, "-c", program, str(lock_path), str(ready), str(acquired)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), "the competing lock process did not start"
            time.sleep(0.2)
            assert not acquired.exists()
            assert process.poll() is None

        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
        assert acquired.read_text(encoding="utf-8") == "acquired"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_cli_reports_the_failed_stage_without_exposing_command_output(tmp_path, monkeypatch, capsys):
    from scripts.ci import compose_release_engine as module
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    first, _ = make_bundle_root(tmp_path, "1")
    second, _ = make_bundle_root(tmp_path, "2")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("new"))
    engine.deploy_bundle(first, incoming)
    runner.fail_next_up = True
    monkeypatch.setattr(module, "_engine_from_args", lambda _args: engine)
    assert module.main(["deploy", "--target", "apps", "--bundle-root", str(second),
                        "--secret-bundle", str(incoming)]) == 2
    error = capsys.readouterr().err
    assert "prior state was restored" in error
    assert "Compose activation failed with exit status 7" in error
    assert "secret-output-is-hidden" not in error
    assert "traefik-new" not in error
