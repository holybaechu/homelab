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
GATEWAY = "ghcr.io/holybaechu/homelab-openclaw-gateway@sha256:" + "a" * 64
CTF = "ghcr.io/holybaechu/homelab-openclaw-ctf@sha256:" + "b" * 64


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    path.chmod(0o600)


def app_secrets(tag: str) -> dict[str, object]:
    return {
        "component": "apps",
        "version": 1,
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


def openclaw_secrets(tag: str) -> dict[str, object]:
    digit = {"old": "1", "new": "2", "latest": "3"}.get(tag, "4")
    return {
        "component": "openclaw",
        "version": 1,
        "gateway_token": digit * 64,
        "discord_bot_token": f"discord-{tag}",
        "exa_api_key": f"exa-{tag}",
    }


def make_bundle_root(
    tmp_path: Path,
    target: str,
    source_digit: str,
    *,
    images: Mapping[str, str] | None = None,
    apps_traefik_ref: str | None = None,
) -> tuple[Path, dict]:
    root = tmp_path / f"bundle-{target}-{source_digit}"
    stack_source = (
        REPO_ROOT / "apps" / "compose" / "homelab"
        if target == "apps"
        else REPO_ROOT / "infra" / "openclaw" / "runtime"
    )
    shutil.copytree(stack_source, root / "payload" / "stack")
    if target == "apps":
        shutil.copy2(TOPOLOGY, root / "payload" / "stack" / "topology.json")
        if apps_traefik_ref is not None:
            compose_path = root / "payload" / "stack" / "compose.yml"
            compose_text = compose_path.read_text(encoding="utf-8")
            current_ref = yaml.safe_load(compose_text)["services"]["traefik"]["image"]
            assert compose_text.count(current_ref) == 1
            compose_path.write_text(
                compose_text.replace(current_ref, apps_traefik_ref, 1),
                encoding="utf-8",
            )
    engine = root / ENGINE_BUNDLE_PATH
    engine.parent.mkdir(parents=True)
    shutil.copy2(ENGINE, engine)
    if target == "openclaw":
        write_json(root / "payload" / "config" / "config" / "openclaw.json", {})
    stack_root = root / "payload" / "stack"
    config_root = root / "payload" / "config"
    manifest = validate_manifest(
        {
            "schema": 1,
            "target": target,
            "source_sha": source_digit * 40,
            "config_commit": ("f" * 40 if target == "openclaw" else None),
            "images": dict(
                images
                if images is not None
                else ({"gateway": GATEWAY, "ctf": CTF} if target == "openclaw" else {})
            ),
            "engine": {
                "version": ENGINE_VERSION,
                "path": ENGINE_BUNDLE_PATH,
                "sha256": file_sha256(engine),
            },
            "payload": {
                "stack_sha256": _tree_content_sha256(stack_root, private=False),
                "config_sha256": (
                    _tree_content_sha256(config_root, private=True)
                    if target == "openclaw"
                    else None
                ),
            },
        },
        expected_target=target,
    )
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

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(str(item) for item in argv)
        visible_env = {
            key: value
            for key, value in (env or {}).items()
            if key.startswith("HOMELAB_") or key.startswith("OPENCLAW_")
        }
        self.calls.append((command, str(cwd), visible_env))

        if len(command) > 1 and Path(command[1]).name == "prepare_release.py":
            return subprocess.run(
                list(command),
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        if Path(command[0]).name == "smoke.sh":
            return self._completed(command)
        if command[0] != "docker":
            raise AssertionError(command)
        if command[1:3] == ("image", "pull"):
            return self._completed(command)
        if command[1:3] == ("image", "inspect"):
            return self._completed(command, json.dumps([command[-1]]) + "\n")
        if command[1] == "ps" and "--all" in command:
            return self._completed(command, "".join(f"{ref}\n" for ref in self.project_images))
        if command[1:3] == ("image", "ls"):
            return self._completed(
                command,
                "".join(json.dumps(item) + "\n" for item in self.image_inventory),
            )
        if command[1:3] == ("image", "rm"):
            if command[-1] in self.blocked_image_ids:
                return self._completed(command, returncode=1)
            self.image_inventory = [
                item for item in self.image_inventory if item.get("ID") != command[-1]
            ]
            return self._completed(command)
        if command[1:3] == ("image", "prune"):
            raise AssertionError("global image pruning is forbidden")
        if command[1:3] == ("network", "ls"):
            return self._completed(
                command,
                "".join(f"{name}\n" for name in self.network_names),
            )
        if command[1:3] == ("network", "inspect"):
            return self._completed(command, json.dumps(self.network_labels) + "\n")
        if command[1] == "inspect":
            if not (cwd / ".release.env").exists():
                metadata = {
                    "State": {
                        "Running": True,
                        "Status": "running",
                        "Restarting": False,
                    },
                    "RestartCount": self.process_restart_count,
                }
                return self._completed(command, json.dumps(metadata) + "\n")
            values = dict(
                line.split("=", 1)
                for line in (cwd / ".release.env").read_text(encoding="utf-8").splitlines()
            )
            metadata = {
                "Config": {
                    "Image": values["OPENCLAW_GATEWAY_REF"],
                    "Env": [
                        f"OPENCLAW_CTF_IMAGE={values['OPENCLAW_CTF_REF']}",
                        f"OPENCLAW_CONFIG_COMMIT={values['OPENCLAW_CONFIG_COMMIT']}",
                        f"OPENCLAW_RELEASE_ID={values['OPENCLAW_RELEASE_ID']}",
                    ],
                },
                "State": {"Running": True, "Health": {"Status": "healthy"}},
            }
            return self._completed(command, json.dumps(metadata) + "\n")

        assert command[1] == "compose"
        project = command[command.index("--project-name") + 1]
        service = "gateway" if project == "openclaw" else "web"
        if "config" in command and self.fail_next_config:
            self.fail_next_config = False
            return self._completed(command, returncode=6)
        if "config" in command and "--format" in command:
            if project == "homelab":
                model = yaml.safe_load((cwd / "compose.yml").read_text(encoding="utf-8"))
                return self._completed(command, json.dumps(model) + "\n")
            values = dict(
                line.split("=", 1)
                for line in (cwd / ".release.env").read_text(encoding="utf-8").splitlines()
            )
            return self._completed(
                command,
                json.dumps(
                    {
                        "services": {
                            "gateway": {
                                "image": values["OPENCLAW_GATEWAY_REF"],
                                "labels": {},
                            }
                        }
                    }
                )
                + "\n",
            )
        if "ps" in command and "--quiet" in command:
            return self._completed(command, "a" * 12 + "\n")
        if "ps" in command and "--status" in command:
            if project == "homelab":
                model = yaml.safe_load((cwd / "compose.yml").read_text(encoding="utf-8"))
                return self._completed(
                    command,
                    "".join(f"{name}\n" for name in model["services"]),
                )
            return self._completed(command, service + "\n")
        if "up" in command and self.fail_next_up:
            self.fail_next_up = False
            return self._completed(command, returncode=7)
        return self._completed(command)


def engine_for(tmp_path: Path, target: str, runner: FakeDockerRunner) -> ComposeReleaseEngine:
    return ComposeReleaseEngine(
        target,
        install_root=tmp_path / f"install-{target}",
        secret_root=tmp_path / f"secrets-{target}",
        docker_gid=991,
        runner=runner,
        minimum_free_bytes=0,
    )


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
    bundle_one, record_one = make_bundle_root(tmp_path, "apps", "1")
    bundle_two, record_two = make_bundle_root(tmp_path, "apps", "2")
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
        call[-7:]
        == (
            "up",
            "-d",
            "--wait",
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
    bundle, _record = make_bundle_root(tmp_path, "apps", "8")
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
    bundle, _record = make_bundle_root(tmp_path, "apps", "9")
    incoming = tmp_path / "apps.json"
    write_json(incoming, app_secrets("old"))

    with pytest.raises(ReleaseError, match="prior state was restored") as caught:
        engine.deploy_bundle(bundle, incoming)

    assert isinstance(caught.value.__cause__, ReleaseError)
    assert "not stably running" in str(caught.value.__cause__)
    assert state(engine)["current"] is None
    assert not any(Path(call[0][0]).name == "smoke.sh" for call in runner.calls)


def test_openclaw_exact_images_and_rollback_use_current_bundle(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "openclaw", runner)
    bundle_one, record_one = make_bundle_root(tmp_path, "openclaw", "3")
    bundle_two, record_two = make_bundle_root(tmp_path, "openclaw", "4")
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("old"))
    engine.deploy_bundle(bundle_one, incoming)

    write_json(incoming, openclaw_secrets("new"))
    engine.deploy_bundle(bundle_two, incoming)
    assert state(engine)["previous"] == record_one
    assert engine.rollback() == record_one
    final = state(engine)
    active = engine.runtime_root / final["active_slot"]
    assert final["current"] == record_one
    assert not (engine.runtime_root / ("a" if final["active_slot"] == "b" else "b")).exists()
    rendered = active / ".secrets"
    assert (rendered / "gateway_token").read_text().strip() == "2" * 64
    assert (rendered / "discord_bot_token").read_text().strip() == "discord-new"
    environment = (active / "stack" / ".release.env").read_text()
    assert f"OPENCLAW_SECRET_ROOT={rendered}" in environment
    assert str(engine.secret_root) not in environment
    if sys.platform != "win32":
        assert stat.S_IMODE((rendered / "gateway_token").stat().st_mode) == 0o600

    commands = [call[0] for call in runner.calls]
    for exact_ref in (GATEWAY, CTF):
        assert ("docker", "image", "pull", exact_ref) in commands
        assert any(call[1:3] == ("image", "inspect") and call[-1] == exact_ref for call in commands)
    assert_not_public(
        engine,
        runner,
        ["1" * 64, "discord-old", "exa-old", "2" * 64, "discord-new", "exa-new"],
    )


def test_failed_activation_keeps_new_bundle_and_restores_prior_release(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "openclaw", runner)
    bundle_one, record_one = make_bundle_root(tmp_path, "openclaw", "5")
    bundle_two, _record_two = make_bundle_root(tmp_path, "openclaw", "6")
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("old"))
    engine.deploy_bundle(bundle_one, incoming)

    write_json(incoming, openclaw_secrets("latest"))
    runner.fail_next_up = True
    with pytest.raises(ReleaseError, match="prior state was restored"):
        engine.deploy_bundle(bundle_two, incoming)
    restored = state(engine)
    assert restored["current"] == record_one
    assert restored["pending"] is None
    assert json.loads((engine.secret_root / "openclaw.json").read_text()) == openclaw_secrets("latest")
    active = engine.runtime_root / restored["active_slot"] / ".secrets"
    assert (active / "gateway_token").read_text().strip() == "3" * 64


def test_failed_first_preflight_never_stops_a_project_that_was_not_started(
    tmp_path: Path,
) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "openclaw", runner)
    bundle, _record = make_bundle_root(tmp_path, "openclaw", "d")
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("old"))
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
        "target": "openclaw",
        "current": None,
        "previous": None,
        "pending": None,
        "active_slot": None,
    }


def test_failed_first_activation_stops_partial_project_and_clears_pending(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "openclaw", runner)
    bundle, _record = make_bundle_root(tmp_path, "openclaw", "a")
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("old"))
    runner.fail_next_up = True

    with pytest.raises(ReleaseError, match="prior state was restored"):
        engine.deploy_bundle(bundle, incoming)
    assert state(engine) == {
        "schema": 1,
        "target": "openclaw",
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
    engine = engine_for(tmp_path, "openclaw", runner)
    bundle_one, record_one = make_bundle_root(tmp_path, "openclaw", "7")
    bundle_two, record_two = make_bundle_root(tmp_path, "openclaw", "8")
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("new"))
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
    secret_scratch = engine.secret_root / (".openclaw.json.tmp-" + "d" * 32)
    secret_scratch.write_text("must-be-removed")

    assert engine.audit() == record_one
    recovered = state(engine)
    assert recovered["current"] == record_one
    assert recovered["pending"] is None
    assert [path.name for path in engine.runtime_root.iterdir() if path.name in {"a", "b"}] == [recovered["active_slot"]]
    active = engine.runtime_root / recovered["active_slot"] / ".secrets"
    assert (active / "gateway_token").read_text().strip() == "2" * 64
    assert not runtime_scratch.exists()
    assert not validation_scratch.exists()
    assert not state_scratch.exists()
    assert not secret_scratch.exists()


def test_invalid_component_bundle_never_replaces_installed_bundle(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "apps", runner)
    bundle, _record = make_bundle_root(tmp_path, "apps", "9")
    incoming = tmp_path / "apps.json"
    original = app_secrets("old")
    write_json(incoming, original)
    engine.deploy_bundle(bundle, incoming)

    write_json(incoming, {"component": "apps", "version": 1, "unexpected": True})
    with pytest.raises(ReleaseError, match="validation"):
        engine.sync_secrets(incoming)
    assert json.loads((engine.secret_root / "apps.json").read_text()) == original


def test_complete_descriptor_and_deterministic_bundle(tmp_path: Path) -> None:
    with pytest.raises(ReleaseError, match="exactly"):
        validate_manifest(
            {
                "schema": 1,
                "target": "openclaw",
                "source_sha": "1" * 40,
                "config_commit": "2" * 40,
                "images": {"gateway": GATEWAY},
                "engine": {
                    "version": 1,
                    "path": ENGINE_BUNDLE_PATH,
                    "sha256": "3" * 64,
                },
                "payload": {
                    "stack_sha256": "4" * 64,
                    "config_sha256": "5" * 64,
                },
            }
        )

    config = tmp_path / "config"
    write_json(config / "config" / "openclaw.json", {})
    outputs = [tmp_path / "one.tar", tmp_path / "two.tar"]
    results = [
        build_bundle(
            target="openclaw",
            source_sha="4" * 40,
            stack_root=REPO_ROOT / "infra" / "openclaw" / "runtime",
            config_root=config,
            config_commit="5" * 40,
            images={"gateway": GATEWAY, "ctf": CTF},
            engine_path=ENGINE,
            output=output,
        )
        for output in outputs
    ]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert results[0]["sha256"] == results[1]["sha256"]


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
    bundle, _record = make_bundle_root(tmp_path, "apps", "c")
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


def test_retention_keeps_current_and_previous_and_prunes_only_unreachable_images(
    tmp_path: Path,
) -> None:
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "openclaw", runner)
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("new"))

    releases = [
        {
            "gateway": (
                "ghcr.io/holybaechu/homelab-openclaw-gateway@sha256:"
                + gateway_digit * 64
            ),
            "ctf": (
                "ghcr.io/holybaechu/homelab-openclaw-ctf@sha256:"
                + ctf_digit * 64
            ),
        }
        for gateway_digit, ctf_digit in (("1", "2"), ("3", "4"), ("5", "6"))
    ]
    bundles_and_records = [
        make_bundle_root(
            tmp_path,
            "openclaw",
            str(index),
            images=images,
        )
        for index, images in enumerate(releases, start=1)
    ]
    for bundle, _record in bundles_and_records[:2]:
        engine.deploy_bundle(bundle, incoming)

    def digest_row(ref: str, image_id: str) -> dict[str, str]:
        repository, digest = ref.split("@", 1)
        return {
            "ID": image_id,
            "Repository": repository,
            "Tag": "<none>",
            "Digest": digest,
        }

    orphan = "ghcr.io/example/project-orphan:dead"
    runner.project_images = [orphan]
    runner.image_inventory = [
        digest_row(releases[0]["gateway"], "sha256:old-gateway"),
        digest_row(releases[0]["ctf"], "sha256:old-ctf"),
        digest_row(releases[1]["gateway"], "sha256:previous-gateway"),
        digest_row(releases[2]["gateway"], "sha256:current-gateway"),
        {
            "ID": "sha256:project-orphan",
            "Repository": "ghcr.io/example/project-orphan",
            "Tag": "dead",
            "Digest": "<none>",
        },
        {
            "ID": "sha256:unrelated",
            "Repository": "ghcr.io/example/unrelated",
            "Tag": "latest",
            "Digest": "<none>",
        },
    ]

    third_bundle, third_record = bundles_and_records[2]
    engine.deploy_bundle(third_bundle, incoming)

    final = state(engine)
    second_record = bundles_and_records[1][1]
    first_record = bundles_and_records[0][1]
    assert final["current"] == third_record
    assert final["previous"] == second_record
    assert {
        path.name
        for path in engine.release_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    } == {third_record["release_id"], second_record["release_id"]}
    assert not engine._release_path(first_record).exists()

    removed_ids = {
        command[-1]
        for command, _cwd, _env in runner.calls
        if command[1:3] == ("image", "rm")
    }
    assert removed_ids == {
        "sha256:old-gateway",
        "sha256:old-ctf",
        "sha256:project-orphan",
    }
    assert "sha256:previous-gateway" not in removed_ids
    assert "sha256:current-gateway" not in removed_ids
    assert "sha256:unrelated" not in removed_ids
    assert not any(
        command[1:3] == ("image", "prune")
        for command, _cwd, _env in runner.calls
    )


def test_session_blocked_image_cleanup_is_persisted_and_retried_later(
    tmp_path: Path,
) -> None:
    releases = [
        {
            "gateway": (
                "ghcr.io/holybaechu/homelab-openclaw-gateway@sha256:"
                + gateway_digit * 64
            ),
            "ctf": (
                "ghcr.io/holybaechu/homelab-openclaw-ctf@sha256:"
                + ctf_digit * 64
            ),
        }
        for gateway_digit, ctf_digit in (("1", "2"), ("3", "4"), ("5", "6"))
    ]
    bundles = [
        make_bundle_root(
            tmp_path,
            "openclaw",
            str(index),
            images=images,
        )
        for index, images in enumerate(releases, start=1)
    ]
    runner = FakeDockerRunner()
    engine = engine_for(tmp_path, "openclaw", runner)
    incoming = tmp_path / "openclaw.json"
    write_json(incoming, openclaw_secrets("new"))
    for bundle, _record in bundles[:2]:
        engine.deploy_bundle(bundle, incoming)

    stale_ctf_id = "sha256:running-ctf-session"
    stale_repository, stale_digest = releases[0]["ctf"].split("@", 1)
    runner.image_inventory = [
        {
            "ID": stale_ctf_id,
            "Repository": stale_repository,
            "Tag": "<none>",
            "Digest": stale_digest,
        }
    ]
    runner.blocked_image_ids.add(stale_ctf_id)

    engine.deploy_bundle(bundles[2][0], incoming)

    first_record = bundles[0][1]
    assert not engine._release_path(first_record).exists()
    assert runner.image_inventory[0]["ID"] == stale_ctf_id
    deferred = json.loads(engine.deferred_image_path.read_text(encoding="utf-8"))
    assert deferred == {
        "schema": 1,
        "target": "openclaw",
        "refs": [releases[0]["ctf"]],
    }
    assert sum(
        command[1:3] == ("image", "rm") and command[-1] == stale_ctf_id
        for command, _cwd, _env in runner.calls
    ) == 1

    runner.blocked_image_ids.clear()
    restarted = engine_for(tmp_path, "openclaw", runner)
    restarted.audit()

    assert runner.image_inventory == []
    assert not restarted.deferred_image_path.exists()
    assert sum(
        command[1:3] == ("image", "rm") and command[-1] == stale_ctf_id
        for command, _cwd, _env in runner.calls
    ) == 2
    assert not any(
        command[1:3] == ("image", "prune")
        for command, _cwd, _env in runner.calls
    )


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
        make_bundle_root(
            tmp_path,
            "apps",
            str(index),
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
        docker_gid=991,
        runner=runner,
        minimum_free_bytes=4 * 1024**3,
    )
    bundle, _record = make_bundle_root(tmp_path, "apps", "a")
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
