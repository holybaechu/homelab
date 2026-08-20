from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.ci.deploy_openclaw_release import (
    DeploymentError,
    ReleaseDeployer,
    _safe_extract,
    deployment_lock,
    identity_can_read,
)
from scripts.ci.openclaw_release import bundle_tree, canonical_json_bytes, create_release_manifest


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.active_release = ""
        self.active_environment: dict[str, str] = {}
        self.gateway_image_override: str | None = None
        self.ctf_image_override: str | None = None
        self.extra_environment: list[str] = []
        self.config_source_override: str | None = None
        self.config_rw = False
        self.socket_rw = False
        self.omit_state_alias = False
        self.state_alias_source = "/var/lib/openclaw"

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        args = tuple(argv)
        self.calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(args, 0, json.dumps([args[-1]]), "")
        if "up" in args:
            env_path = Path(args[args.index("--env-file") + 1])
            values = dict(
                line.split("=", 1)
                for line in env_path.read_text(encoding="utf-8").splitlines()
            )
            self.active_release = values["OPENCLAW_RELEASE_ID"]
            self.active_environment = values
        if "down" in args:
            self.active_release = ""
            self.active_environment = {}
        if args[:2] == ("docker", "compose") and "ps" in args:
            return subprocess.CompletedProcess(
                args, 0, "gateway-container\n" if self.active_release else "", ""
            )
        if args[:2] == ("docker", "inspect"):
            payload = {
                "Config": {
                    "Image": self.gateway_image_override
                    or self.active_environment.get("OPENCLAW_GATEWAY_REF", ""),
                    "Env": [
                        f"OPENCLAW_CTF_IMAGE={self.ctf_image_override or self.active_environment.get('OPENCLAW_CTF_REF', '')}",
                        f"OPENCLAW_CONFIG_COMMIT={self.active_environment.get('OPENCLAW_CONFIG_COMMIT', '')}",
                        f"OPENCLAW_RELEASE_ID={self.active_release}",
                        *self.extra_environment,
                    ],
                },
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": "/var/run/docker.sock",
                        "Destination": "/var/run/docker.sock",
                        "RW": self.socket_rw,
                    },
                    {
                        "Type": "bind",
                        "Source": self.config_source_override
                        or self.active_environment.get("OPENCLAW_CONFIG_ROOT", ""),
                        "Destination": "/etc/openclaw",
                        "RW": self.config_rw,
                    },
                    {
                        "Type": "bind",
                        "Source": "/var/lib/openclaw",
                        "Destination": "/home/node/.openclaw",
                        "RW": True,
                    },
                    *([] if self.omit_state_alias else [{
                        "Type": "bind",
                        "Source": self.state_alias_source,
                        "Destination": "/var/lib/openclaw",
                        "RW": True,
                    }]),
                ],
            }
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(args, 0, "[]", "")


class FakeProbe:
    def __init__(self, docker: FakeDocker) -> None:
        self.docker = docker
        self.calls: list[tuple[str, str | None, str]] = []
        self.fail_authenticated_for: set[str] = set()

    def __call__(self, url: str, token: str | None) -> tuple[int, str]:
        self.calls.append((url, token, self.docker.active_release))
        if token is not None and self.docker.active_release in self.fail_authenticated_for:
            return 503, "injected failure"
        return 200, '{"ok":true}'


def _make_release(tmp_path: Path, letter: str) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime = tmp_path / f"runtime-{letter}"
    runtime.mkdir()
    (runtime / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    config = tmp_path / f"config-{letter}"
    (config / "config").mkdir(parents=True)
    (config / "config" / "openclaw.json").write_text(
        json.dumps({"release": letter}) + "\n", encoding="utf-8"
    )
    runtime_tar = tmp_path / f"runtime-{letter}.tar"
    config_tar = tmp_path / f"config-{letter}.tar"
    runtime_hash = bundle_tree(runtime, runtime_tar)
    config_hash = bundle_tree(config, config_tar)
    release = create_release_manifest(
        deployment_source_sha=letter * 40,
        platform="linux/amd64",
        gateway_ref=f"ghcr.io/example/gateway@sha256:{letter * 64}",
        ctf_ref=f"ghcr.io/example/ctf@sha256:{letter * 64}",
        runtime_sha256=runtime_hash,
        config_commit=letter * 40,
        config_sha256=config_hash,
    )
    manifest = tmp_path / f"release-{letter}.json"
    manifest.write_bytes(canonical_json_bytes(release))
    return manifest, runtime_tar, config_tar, release


def _deployer(tmp_path: Path) -> tuple[ReleaseDeployer, FakeDocker, FakeProbe]:
    secrets = tmp_path / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "gateway_token").write_text("one-secret-token\n", encoding="utf-8")
    docker = FakeDocker()
    probe = FakeProbe(docker)
    trusted_uid = os.getuid() if os.name != "nt" else 0
    trusted_gid = os.getgid() if os.name != "nt" else 0
    deployer = ReleaseDeployer(
        install_root=tmp_path / "install",
        secret_root=secrets,
        readiness_url="http://127.0.0.1/health",
        smoke_url="http://127.0.0.1/api/smoke",
        runner=docker,
        probe=probe,
        readiness_attempts=2,
        readiness_delay=0,
        sleep=lambda _: None,
        gateway_uid=trusted_uid if os.name != "nt" else 1000,
        gateway_gid=trusted_gid if os.name != "nt" else 1000,
        docker_gid=trusted_gid if trusted_gid > 0 else 998,
        validate_host_contract=False,
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )
    return deployer, docker, probe


def _restarted_deployer(
    deployer: ReleaseDeployer, docker: FakeDocker, probe: FakeProbe
) -> ReleaseDeployer:
    return ReleaseDeployer(
        install_root=deployer.install_root,
        secret_root=deployer.secret_root,
        readiness_url=deployer.readiness_url,
        smoke_url=deployer.smoke_url,
        runner=docker,
        probe=probe,
        readiness_attempts=2,
        readiness_delay=0,
        sleep=lambda _: None,
        gateway_uid=deployer.gateway_uid,
        gateway_gid=deployer.gateway_gid,
        docker_gid=deployer.docker_gid,
        validate_host_contract=False,
        trusted_uid=deployer.trusted_uid,
        trusted_gid=deployer.trusted_gid,
    )


def test_deploy_activates_only_after_digest_readiness_and_one_auth_smoke(tmp_path: Path) -> None:
    manifest, runtime, config, release = _make_release(tmp_path, "a")
    deployer, docker, probe = _deployer(tmp_path)
    assert deployer.deploy(manifest, runtime, config) == release

    state = json.loads(deployer.release_state_path.read_text(encoding="utf-8"))
    assert state == {"version": 1, "current": release, "previous": None}
    assert sum(token is not None for _, token, _ in probe.calls) == 1
    assert probe.calls[-1][1] == "one-secret-token"
    assert [call[:3] for call in docker.calls].count(("docker", "image", "pull")) == 2
    up = next(call for call in docker.calls if "up" in call)
    assert "--no-build" in up
    env = (deployer.release_root / release["release_id"] / ".env").read_text(encoding="utf-8")
    assert release["gateway"]["ref"] in env
    assert release["ctf"]["ref"] in env
    assert ":latest" not in env


def test_failed_release_rolls_back_previous_digest_and_config_on_same_lxc(tmp_path: Path) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    initial_auth_calls = sum(token is not None for _, token, _ in probe.calls)
    probe.fail_authenticated_for.add(second_files[3]["release_id"])

    with pytest.raises(DeploymentError, match="activation failed"):
        deployer.deploy(*second_files[:3])

    restored = json.loads(deployer.release_state_path.read_text(encoding="utf-8"))
    assert restored["current"] == first
    assert restored["previous"] is None
    assert docker.active_release == first["release_id"]
    # One smoke for the candidate and one smoke proving the restored release.
    assert sum(token is not None for _, token, _ in probe.calls) - initial_auth_calls == 2
    events = [json.loads(line)["event"] for line in deployer.audit_path.read_text().splitlines()]
    assert "deployment-failed" in events
    assert "automatic-rollback-completed" in events


def test_audit_checks_exact_digests_without_another_authenticated_smoke(tmp_path: Path) -> None:
    files = _make_release(tmp_path, "a")
    deployer, docker, probe = _deployer(tmp_path)
    release = deployer.deploy(*files[:3])
    probe_count = len(probe.calls)
    call_count = len(docker.calls)
    assert deployer.audit() == release
    assert len(probe.calls) == probe_count
    audit_calls = docker.calls[call_count:]
    assert not any(call[:3] == ("docker", "image", "pull") for call in audit_calls)
    assert any("--file" in call and "--env-file" in call and "ps" in call for call in audit_calls)
    assert any(call[:2] == ("docker", "inspect") for call in audit_calls)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("gateway", "Gateway image differs"),
        ("ctf", "CTF/config identity differs"),
        ("ctf-duplicate", "CTF/config identity differs"),
        ("socket", "Docker socket must be one read-only"),
        ("state-alias-missing", "Gateway state must expose one writable host bind"),
        ("state-alias-source", "Gateway state must expose one writable host bind"),
        ("config-source", "config must be the exact release's read-only bind"),
        ("config-rw", "config must be the exact release's read-only bind"),
    ],
)
def test_audit_rejects_running_identity_or_socket_drift(
    tmp_path: Path, mutation: str, message: str,
) -> None:
    files = _make_release(tmp_path, "a")
    deployer, docker, _probe = _deployer(tmp_path)
    deployer.deploy(*files[:3])
    if mutation == "gateway":
        docker.gateway_image_override = "ghcr.io/example/gateway@sha256:" + "f" * 64
    elif mutation == "ctf":
        docker.ctf_image_override = "ghcr.io/example/ctf@sha256:" + "f" * 64
    elif mutation == "ctf-duplicate":
        docker.extra_environment.append(
            "OPENCLAW_CTF_IMAGE=ghcr.io/example/ctf@sha256:" + "f" * 64
        )
    elif mutation == "socket":
        docker.socket_rw = True
    elif mutation == "state-alias-missing":
        docker.omit_state_alias = True
    elif mutation == "state-alias-source":
        docker.state_alias_source = "/home/node/.openclaw"
    elif mutation == "config-source":
        docker.config_source_override = "/opt/openclaw/releases/wrong/config"
    else:
        docker.config_rw = True

    with pytest.raises(DeploymentError, match=message):
        deployer.audit()


def test_manual_rollback_swaps_current_and_previous(tmp_path: Path) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, _probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = deployer.deploy(*second_files[:3])
    assert deployer.rollback() == first
    state = json.loads(deployer.release_state_path.read_text(encoding="utf-8"))
    assert state == {"version": 1, "current": first, "previous": second}
    assert docker.active_release == first["release_id"]


def test_manual_rollback_rejects_a_tampered_previous_bundle(tmp_path: Path) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, _docker, _probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    deployer.deploy(*second_files[:3])
    retained = deployer.release_root / first["release_id"] / "artifacts" / "config.tar"
    retained.write_bytes(b"tampered")

    with pytest.raises(DeploymentError, match="config bundle hash"):
        deployer.rollback()


@pytest.mark.parametrize(
    ("relative", "replacement", "mode", "message"),
    [
        (
            "runtime/compose.yml",
            "services:\n  attacker: {}\n",
            0o644,
            "materialized runtime tree differs",
        ),
        (
            "config/config/openclaw.json",
            '{"release":"tampered"}\n',
            0o440,
            "materialized config tree differs",
        ),
        (
            ".env",
            "OPENCLAW_RELEASE_ID=tampered\n",
            0o600,
            "materialized generated environment differs",
        ),
    ],
)
def test_audit_rejects_mode_preserving_extracted_or_generated_tampering(
    tmp_path: Path,
    relative: str,
    replacement: str,
    mode: int,
    message: str,
) -> None:
    files = _make_release(tmp_path, "a")
    deployer, _docker, _probe = _deployer(tmp_path)
    release = deployer.deploy(*files[:3])
    path = deployer.release_root / release["release_id"] / relative
    path.chmod(mode | stat.S_IWUSR)
    path.write_text(replacement, encoding="utf-8")
    path.chmod(mode)

    with pytest.raises(DeploymentError, match=message):
        deployer.audit()


def test_bundle_extraction_rejects_links_and_traversal(tmp_path: Path) -> None:
    import io
    import tarfile

    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as handle:
        member = tarfile.TarInfo("../escape")
        member.size = 1
        handle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(DeploymentError, match="non-canonical"):
        _safe_extract(archive, tmp_path / "out")


def test_gateway_uid_access_model_requires_controlled_group_read_not_world_read(
) -> None:
    metadata = SimpleNamespace(st_uid=0, st_gid=1000, st_mode=stat.S_IFREG | 0o440)
    assert identity_can_read(metadata, uid=1000, gids={1000})
    assert not identity_can_read(metadata, uid=1000, gids={1001})


def test_deploy_fails_closed_on_bundle_digest_or_missing_smoke_secret(tmp_path: Path) -> None:
    files = _make_release(tmp_path, "a")
    deployer, _docker, _probe = _deployer(tmp_path)
    files[1].write_bytes(b"tampered")
    with pytest.raises(DeploymentError, match="runtime bundle SHA-256"):
        deployer.deploy(*files[:3])

    files = _make_release(tmp_path, "b")
    deployer, _docker, _probe = _deployer(tmp_path / "other")
    (deployer.secret_root / "gateway_token").unlink()
    with pytest.raises(DeploymentError, match="activation failed"):
        deployer.deploy(*files[:3])


def test_failed_first_activation_cleans_candidate_without_legacy_gate(
    tmp_path: Path,
) -> None:
    files = _make_release(tmp_path, "a")
    deployer, docker, probe = _deployer(tmp_path)
    probe.fail_authenticated_for.add(files[3]["release_id"])
    with pytest.raises(DeploymentError, match="activation failed"):
        deployer.deploy(*files[:3])
    assert not deployer.release_state_path.exists()
    down = [call for call in docker.calls if "down" in call]
    assert len(down) == 1
    assert "--remove-orphans" in down[0]


def test_reused_release_revalidates_original_bundle_hashes(tmp_path: Path) -> None:
    files = _make_release(tmp_path, "a")
    deployer, _docker, _probe = _deployer(tmp_path)
    release = deployer.deploy(*files[:3])
    retained = deployer.release_root / release["release_id"] / "artifacts" / "runtime.tar"
    retained.write_bytes(b"tampered")
    with pytest.raises(DeploymentError, match="runtime bundle hash"):
        deployer.deploy(*files[:3])


def test_same_release_retry_is_a_nonmutating_audit_without_pull_up_or_smoke(
    tmp_path: Path,
) -> None:
    files = _make_release(tmp_path, "a")
    deployer, docker, probe = _deployer(tmp_path)
    release = deployer.deploy(*files[:3])
    state_before = deployer.release_state_path.read_bytes()
    call_count = len(docker.calls)
    probe_count = len(probe.calls)

    assert deployer.deploy(*files[:3]) == release

    retry_calls = docker.calls[call_count:]
    assert deployer.release_state_path.read_bytes() == state_before
    assert len(probe.calls) == probe_count
    assert not any("up" in call or "down" in call for call in retry_calls)
    assert not any(call[:3] == ("docker", "image", "pull") for call in retry_calls)
    assert any("ps" in call for call in retry_calls)


def test_state_commit_failure_rolls_runtime_and_atomic_state_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, _probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    original_state = deployer.release_state_path.read_bytes()
    real_write = deployer._write_state
    failed = False

    def fail_candidate_once(payload: dict[str, Any]) -> None:
        nonlocal failed
        if payload["current"]["release_id"] == second_files[3]["release_id"] and not failed:
            failed = True
            raise OSError("injected state commit failure")
        real_write(payload)

    monkeypatch.setattr(deployer, "_write_state", fail_candidate_once)
    with pytest.raises(DeploymentError, match="activation failed"):
        deployer.deploy(*second_files[:3])

    assert failed
    assert deployer.release_state_path.read_bytes() == original_state
    assert docker.active_release == first["release_id"]


def test_completion_audit_failure_rolls_runtime_and_state_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, _probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    original_state = deployer.release_state_path.read_bytes()
    real_record = deployer._record
    failed = False

    def fail_completion_once(event: str, release: dict[str, Any], **details: Any) -> None:
        nonlocal failed
        if event == "deployment-completed" and not failed:
            failed = True
            raise OSError("injected audit commit failure")
        real_record(event, release, **details)

    monkeypatch.setattr(deployer, "_record", fail_completion_once)
    with pytest.raises(DeploymentError, match="activation failed"):
        deployer.deploy(*second_files[:3])

    assert failed
    assert deployer.release_state_path.read_bytes() == original_state
    assert docker.active_release == first["release_id"]


@pytest.mark.parametrize("phase", ["prepared", "committing-target"])
def test_next_invocation_recovers_crash_between_compose_switch_and_state_commit(
    tmp_path: Path, phase: str,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, _probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = second_files[3]
    original_state = deployer._load_state()
    next_state = {"version": 1, "current": second, "previous": first}

    # Model a killed process: the fsynced intent and live Compose switch exist,
    # while the one atomic state pointer still names the previous release.
    deployer._materialize(second, second_files[1], second_files[2])
    deployer._verify_images(second)
    transaction = deployer._write_transaction(
        operation="deploy",
        target=second,
        original_state=original_state,
        original_state_existed=True,
        next_state=next_state,
    )
    deployer._compose(second, "up")
    if phase == "committing-target":
        deployer._set_transaction_phase(transaction, phase)
    assert docker.active_release == second["release_id"]
    assert deployer._load_state() == original_state

    # The next locked command restores the recorded release before its normal
    # same-release audit; no operator repair or stale-lock deletion is needed.
    assert deployer.deploy(*first_files[:3]) == first
    assert docker.active_release == first["release_id"]
    assert deployer._load_state() == original_state
    assert not deployer.transaction_path.exists()
    events = [json.loads(line)["event"] for line in deployer.audit_path.read_text().splitlines()]
    assert "pending-transaction-rollback-reconciled" in events


def test_next_audit_finishes_crash_after_atomic_state_commit(
    tmp_path: Path,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, _probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = second_files[3]
    original_state = deployer._load_state()
    next_state = {"version": 1, "current": second, "previous": first}

    deployer._materialize(second, second_files[1], second_files[2])
    deployer._verify_images(second)
    transaction = deployer._write_transaction(
        operation="deploy",
        target=second,
        original_state=original_state,
        original_state_existed=True,
        next_state=next_state,
    )
    deployer._compose(second, "up")
    deployer._verify_running_release(second)
    deployer._prove_healthy(second)
    deployer._set_transaction_phase(transaction, "committing-target")
    deployer._write_state(next_state)

    assert deployer.audit() == second
    assert docker.active_release == second["release_id"]
    assert deployer._load_state() == next_state
    assert not deployer.transaction_path.exists()
    events = [json.loads(line)["event"] for line in deployer.audit_path.read_text().splitlines()]
    assert "pending-transaction-commit-reconciled" in events


def test_restart_rolls_back_a_committed_state_when_target_attestation_fails(
    tmp_path: Path,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = second_files[3]
    original_state = deployer._load_state()
    next_state = {"version": 1, "current": second, "previous": first}
    deployer._materialize(second, second_files[1], second_files[2])
    transaction = deployer._write_transaction(
        operation="deploy",
        target=second,
        original_state=original_state,
        original_state_existed=True,
        next_state=next_state,
    )
    deployer._compose(second, "up")
    deployer._set_transaction_phase(transaction, "committing-target")
    deployer._write_state(next_state)
    config = (
        deployer.release_root
        / second["release_id"]
        / "config/config/openclaw.json"
    )
    config.chmod(0o640)
    config.write_text('{"tampered":true}\n', encoding="utf-8")
    config.chmod(0o440)

    restarted = _restarted_deployer(deployer, docker, probe)
    assert restarted.audit() == first
    assert restarted._load_state() == original_state
    assert docker.active_release == first["release_id"]
    assert not restarted.transaction_path.exists()


@pytest.mark.parametrize(
    "boundary",
    [
        "before-original-runtime",
        "after-original-runtime",
        "after-original-state",
    ],
)
def test_restart_finishes_restoring_original_at_every_inverse_boundary(
    tmp_path: Path, boundary: str,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = second_files[3]
    original_state = deployer._load_state()
    next_state = {"version": 1, "current": second, "previous": first}

    deployer._materialize(second, second_files[1], second_files[2])
    deployer._verify_images(second)
    transaction = deployer._write_transaction(
        operation="deploy",
        target=second,
        original_state=original_state,
        original_state_existed=True,
        next_state=next_state,
    )
    deployer._compose(second, "up")
    transaction = deployer._set_transaction_phase(
        transaction, "committing-target"
    )
    deployer._write_state(next_state)
    deployer._set_transaction_phase(transaction, "restoring-original")

    if boundary != "before-original-runtime":
        deployer._activate_existing(first, pull=False)
    if boundary == "after-original-state":
        deployer._restore_state(original_state, existed=True)

    pull_count = sum(call[:3] == ("docker", "image", "pull") for call in docker.calls)
    restarted = _restarted_deployer(deployer, docker, probe)
    assert restarted.audit() == first
    assert docker.active_release == first["release_id"]
    assert restarted._load_state() == original_state
    assert not restarted.transaction_path.exists()
    assert sum(call[:3] == ("docker", "image", "pull") for call in docker.calls) == pull_count


def test_injected_termination_during_inverse_state_restore_recovers_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = second_files[3]
    real_record = deployer._record
    real_restore = deployer._restore_state

    def fail_completion(event: str, release: dict[str, Any], **details: Any) -> None:
        if event == "deployment-completed":
            raise OSError("injected post-state termination")
        real_record(event, release, **details)

    def terminate_before_state(*_args: Any, **_kwargs: Any) -> None:
        raise SystemExit("injected process termination before inverse state write")

    monkeypatch.setattr(deployer, "_record", fail_completion)
    monkeypatch.setattr(deployer, "_restore_state", terminate_before_state)
    with pytest.raises(DeploymentError, match="rollback failed"):
        deployer.deploy(*second_files[:3])

    assert docker.active_release == first["release_id"]
    assert deployer._load_state()["current"] == second
    assert deployer._load_transaction()["phase"] == "restoring-original"

    monkeypatch.setattr(deployer, "_record", real_record)
    monkeypatch.setattr(deployer, "_restore_state", real_restore)
    restarted = _restarted_deployer(deployer, docker, probe)
    assert restarted.audit() == first
    assert restarted._load_state()["current"] == first
    assert not restarted.transaction_path.exists()


def test_restart_cleans_interrupted_first_activation_without_recorded_state(
    tmp_path: Path,
) -> None:
    files = _make_release(tmp_path, "a")
    release = files[3]
    deployer, docker, _probe = _deployer(tmp_path)
    deployer._prepare_trusted_roots()
    deployer._materialize(release, files[1], files[2])
    original_state = {"version": 1, "current": None, "previous": None}
    next_state = {"version": 1, "current": release, "previous": None}
    deployer._write_transaction(
        operation="deploy",
        target=release,
        original_state=original_state,
        original_state_existed=False,
        next_state=next_state,
    )
    deployer._compose(release, "up")

    with pytest.raises(DeploymentError, match="no active OpenClaw release"):
        deployer.audit()
    assert docker.active_release == ""
    assert not deployer.release_state_path.exists()
    assert not deployer.transaction_path.exists()


def test_version_one_journal_conservatively_restores_original_when_state_is_target(
    tmp_path: Path,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = second_files[3]
    original_state = deployer._load_state()
    next_state = {"version": 1, "current": second, "previous": first}
    deployer._materialize(second, second_files[1], second_files[2])
    transaction = deployer._write_transaction(
        operation="deploy",
        target=second,
        original_state=original_state,
        original_state_existed=True,
        next_state=next_state,
    )
    legacy_transaction = dict(transaction)
    legacy_transaction["version"] = 1
    legacy_transaction.pop("phase")
    deployer.transaction_path.write_bytes(canonical_json_bytes(legacy_transaction))
    deployer.transaction_path.chmod(0o600)
    deployer._compose(second, "up")
    deployer._write_state(next_state)
    deployer._activate_existing(first, pull=False)

    restarted = _restarted_deployer(deployer, docker, probe)
    assert restarted.audit() == first
    assert restarted._load_state() == original_state
    assert docker.active_release == first["release_id"]
    assert not restarted.transaction_path.exists()


def test_manual_rollback_inverse_boundary_restores_pre_rollback_release(
    tmp_path: Path,
) -> None:
    first_files = _make_release(tmp_path, "a")
    second_files = _make_release(tmp_path, "b")
    deployer, docker, probe = _deployer(tmp_path)
    first = deployer.deploy(*first_files[:3])
    second = deployer.deploy(*second_files[:3])
    original_state = deployer._load_state()
    next_state = {"version": 1, "current": first, "previous": second}
    transaction = deployer._write_transaction(
        operation="rollback",
        target=first,
        original_state=original_state,
        original_state_existed=True,
        next_state=next_state,
    )
    deployer._compose(first, "up")
    transaction = deployer._set_transaction_phase(
        transaction, "committing-target"
    )
    deployer._write_state(next_state)
    deployer._set_transaction_phase(transaction, "restoring-original")
    deployer._activate_existing(second, pull=False)

    restarted = _restarted_deployer(deployer, docker, probe)
    assert restarted.audit() == second
    assert restarted._load_state() == original_state
    assert docker.active_release == second["release_id"]
    assert not restarted.transaction_path.exists()


def test_persistent_kernel_lock_is_reusable_but_rejects_concurrent_writer(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    trusted_uid = os.getuid() if os.name != "nt" else 0
    with deployment_lock(state, trusted_uid):
        with pytest.raises(DeploymentError, match="another OpenClaw deployment"):
            with deployment_lock(state, trusted_uid):
                pass
    assert (state / ".deployment.lock").is_file()
    with deployment_lock(state, trusted_uid):
        pass
