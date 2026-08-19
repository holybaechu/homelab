from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from scripts.ci.deploy_compose_release import (
    ComposeReleaseDeployer,
    DeploymentError,
    ImageApproval,
    ProjectSnapshot,
    Release,
    T3_IMAGE,
)
from scripts.ci.immutable_image_release import (
    ArtifactReleaseStore,
    approved_record,
    deployment_payload,
)
from scripts.recovery.compose_stack_cutover import (
    ComposeStackMigrator,
    EMPTY_HOST_APPROVAL_NAME,
    _load_migration_record,
)


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_64 = "c" * 64
PLATFORM_SHA = "1" * 40
MEDIA_SHA = "2" * 40
CODE_SHA = "3" * 40
STACK_SHA = "4" * 40
STACK_NEXT_SHA = "5" * 40
STACK_THIRD_SHA = "6" * 40
T3_DIGEST_A = "sha256:" + "a" * 64
T3_DIGEST_B = "sha256:" + "b" * 64
requires_symlink = pytest.mark.skipif(
    os.name == "nt", reason="Windows test user cannot create symbolic links"
)


class FakeDockerRunner:
    def __init__(self, services: tuple[str, ...] = ("alpha", "beta")) -> None:
        self.services = services
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str] | None]] = []
        self.fail_up: set[str] = set()
        self.fail_smoke: set[str] = set()
        self.unhealthy: set[str] = set()
        self.fail_pull: set[str] = set()
        self.fail_down: set[str] = set()
        self.scaled: set[str] = set()
        self.no_health: set[str] = set()
        self.restart_counts: dict[tuple[str, str], int] = {}
        self._containers: dict[str, tuple[str, str]] = {}
        self.active: dict[str, str] = {}
        self.events: list[tuple[str, str, str]] = []

    @staticmethod
    def _sha(cwd: Path) -> str:
        # RELEASE_ROOT/<sha>/.staged/<project>
        return cwd.parents[1].name

    def run(
        self,
        argv,
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = tuple(str(value) for value in argv)
        self.calls.append((args, Path(cwd), None if env is None else dict(env)))
        sha = self._sha(Path(cwd))
        project = (
            args[args.index("--project-name") + 1]
            if "--project-name" in args
            else env.get("HOMELAB_PROJECT", "") if env else ""
        )

        if len(args) >= 2 and args[:2] == ("docker", "inspect"):
            container = args[-1]
            container_sha, service = self._containers[container]
            if args[args.index("--format") + 1] == "{{.RestartCount}}":
                count = self.restart_counts.get((container_sha, service), 0)
                return subprocess.CompletedProcess(args, 0, f"{count}\n", "")
            if container_sha in self.unhealthy:
                health = "unhealthy"
            elif container_sha in self.no_health:
                health = "none"
            else:
                health = "healthy"
            return subprocess.CompletedProcess(args, 0, f"running {health}\n", "")

        if args and args[0].endswith(os.path.join(".homelab", "smoke")):
            self.events.append(("smoke", project, sha))
            return subprocess.CompletedProcess(
                args, 9 if sha in self.fail_smoke else 0, "", ""
            )

        if "config" in args and "--quiet" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "config" in args and "--services" in args:
            return subprocess.CompletedProcess(
                args, 0, "".join(f"{service}\n" for service in self.services), ""
            )
        if "config" in args and "--images" in args:
            artifact_env = Path(cwd) / ".homelab" / "artifacts.env"
            if artifact_env.is_file():
                image_ref = artifact_env.read_text(encoding="utf-8").strip().split(
                    "=", 1
                )[1]
                images = (image_ref, *(
                    f"example/{service}:1" for service in self.services[1:]
                ))
            else:
                images = tuple(f"example/{service}:1" for service in self.services)
            return subprocess.CompletedProcess(
                args, 0, "".join(f"{image}\n" for image in images), ""
            )
        if "pull" in args:
            return subprocess.CompletedProcess(
                args, 7 if sha in self.fail_pull else 0, "", ""
            )
        if "up" in args:
            self.active[project] = sha
            self.events.append(("up", project, sha))
            return subprocess.CompletedProcess(
                args, 8 if sha in self.fail_up else 0, "", ""
            )
        if "down" in args:
            self.events.append(("down", project, sha))
            if sha not in self.fail_down:
                self.active.pop(project, None)
            return subprocess.CompletedProcess(
                args, 12 if sha in self.fail_down else 0, "", ""
            )
        if "ps" in args and "--services" in args:
            running = self.services if self.active.get(project) == sha else ()
            return subprocess.CompletedProcess(
                args, 0, "".join(f"{service}\n" for service in running), ""
            )
        if "ps" in args and "-q" in args:
            service = args[-1]
            container = hashlib.sha256(f"{sha}:{service}".encode()).hexdigest()
            self._containers[container] = (sha, service)
            containers = [container]
            if sha in self.scaled:
                extra = hashlib.sha256(f"{sha}:{service}:scaled".encode()).hexdigest()
                self._containers[extra] = (sha, service)
                containers.append(extra)
            return subprocess.CompletedProcess(
                args, 0, "".join(f"{item}\n" for item in containers), ""
            )
        raise AssertionError(f"unexpected command: {args}")


class RecordingLock(AbstractContextManager["RecordingLock"]):
    def __init__(self, records: list[tuple[str, Path, int]], path: Path, uid: int) -> None:
        self.records = records
        self.path = path
        self.uid = uid

    def __enter__(self) -> "RecordingLock":
        self.records.append(("enter", self.path, self.uid))
        return self

    def __exit__(self, *_args: object) -> None:
        self.records.append(("exit", self.path, self.uid))


def _write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    os.chmod(path, mode)


def _make_release(release_root: Path, sha: str, project: str = "homelab") -> Path:
    project_root = release_root / sha / "apps" / "compose" / project
    project_root.mkdir(parents=True)
    _write(
        project_root / "compose.yml",
        f"name: {project}\nservices:\n  alpha:\n    image: example/alpha:1\n",
        0o644,
    )
    _write(project_root / "README.md", f"release {sha}\n", 0o644)
    return project_root


def _make_runtime(
    runtime_root: Path,
    project: str = "homelab",
    *,
    secret: str = "homelab-secret",
    smoke: bool = True,
) -> Path:
    project_root = runtime_root / project
    (project_root / "files").mkdir(parents=True)
    os.chmod(project_root, 0o700)
    os.chmod(project_root / "files", 0o700)
    _write(project_root / ".env", f"TOKEN={secret}\n", 0o600)
    _write(project_root / "files" / "generated.conf", f"token={secret}\n", 0o600)
    if smoke:
        _write(project_root / "smoke", "#!/bin/sh\nexit 0\n", 0o700)
    return project_root


@pytest.fixture
def deployment(tmp_path: Path):
    release_root = tmp_path / "releases"
    current_root = tmp_path / "current"
    state_root = tmp_path / "state"
    runtime_root = tmp_path / "runtime"
    for root in (release_root, current_root, state_root, runtime_root):
        root.mkdir()
        os.chmod(root, 0o700)
    _make_release(release_root, SHA_A)
    _make_release(release_root, SHA_B)
    _make_release(release_root, SHA_64)
    _make_runtime(runtime_root)

    runner = FakeDockerRunner()
    lock_records: list[tuple[str, Path, int]] = []
    trusted_uid = release_root.stat().st_uid

    def lock_factory(path: Path, uid: int) -> RecordingLock:
        return RecordingLock(lock_records, path, uid)

    deployer = ComposeReleaseDeployer(
        release_root=release_root,
        current_root=current_root,
        state_root=state_root,
        runtime_config_root=runtime_root,
        trusted_uid=trusted_uid,
        runner=runner,
        lock_factory=lock_factory,
    )
    ArtifactReleaseStore(
        state_root / "artifacts", artifact="t3code"
    ).approve(
        source_sha=SHA_A,
        image=T3_IMAGE,
        platform="linux/amd64",
        digest=T3_DIGEST_A,
    )
    return deployer, runner, lock_records, release_root, current_root, state_root, runtime_root


def _compose_actions(runner: FakeDockerRunner) -> list[tuple[str, ...]]:
    actions = []
    for args, _cwd, _env in runner.calls:
        if args[:2] != ("docker", "compose"):
            continue
        for action in ("config", "pull", "up", "down", "ps"):
            if action in args:
                actions.append(args[args.index(action) :])
                break
    return actions


@requires_symlink
def test_happy_path_stages_only_project_runtime_and_records_good_release(deployment):
    deployer, runner, locks, _release_root, current_root, state_root, runtime_root = deployment
    _make_runtime(runtime_root, "unrelated", secret="must-not-leak", smoke=False)

    release = deployer.deploy(SHA_A)

    staged = Path(release.path)
    assert staged.is_dir()
    assert (staged / ".env").read_text(encoding="utf-8") == "TOKEN=homelab-secret\n"
    assert (staged / "generated.conf").read_text(encoding="utf-8") == (
        "token=homelab-secret\n"
    )
    assert (staged / ".homelab" / "smoke").is_file()
    assert "must-not-leak" not in "".join(
        path.read_text(encoding="utf-8")
        for path in staged.rglob("*")
        if path.is_file()
    )
    assert (runtime_root / "homelab" / ".env").read_text(encoding="utf-8") == (
        "TOKEN=homelab-secret\n"
    )

    pointer = current_root / "homelab"
    assert pointer.is_symlink()
    assert Path(os.readlink(pointer)) == staged
    state = json.loads((state_root / "homelab.json").read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert state["good"] == SHA_A
    assert state["previous"] is None
    assert not (state_root / "homelab.pending-transaction.json").exists()
    assert locks == [
        ("enter", state_root / "locks" / "homelab.lock", deployer.trusted_uid),
        ("exit", state_root / "locks" / "homelab.lock", deployer.trusted_uid),
    ]

    actions = _compose_actions(runner)
    assert actions[:4] == [
        ("config", "--quiet"),
        ("config", "--services"),
        ("pull", "--ignore-buildable"),
        ("up", "-d", "--wait", "--remove-orphans", "--no-build"),
    ]
    smoke_call = next(call for call in runner.calls if call[0][0].endswith("smoke"))
    assert smoke_call[2]["HOMELAB_PROJECT"] == "homelab"
    assert smoke_call[2]["HOMELAB_RELEASE_SHA"] == SHA_A


@requires_symlink
def test_64_character_sha_is_accepted(deployment):
    deployer, _runner, _locks, *_rest = deployment

    assert deployer.deploy(SHA_64).sha == SHA_64


@pytest.mark.parametrize(
    ("sha", "message"),
    [
        ("a" * 39, "40 or 64"),
        ("z" * 40, "40 or 64"),
        ("A" * 40, "lowercase"),
    ],
)
def test_rejects_non_exact_identity_before_touching_state(
    deployment, sha: str, message: str
):
    deployer, runner, locks, *_rest = deployment

    with pytest.raises(DeploymentError, match=message):
        deployer.deploy(sha)

    assert runner.calls == []
    assert locks == []


@requires_symlink
def test_deployment_failure_restores_pointer_and_reapplies_previous_release(deployment):
    deployer, runner, _locks, _release_root, current_root, state_root, _runtime = deployment
    first = deployer.deploy(SHA_A)
    runner.calls.clear()
    runner.fail_up.add(SHA_B)

    with pytest.raises(DeploymentError, match="previous release was restored"):
        deployer.deploy(SHA_B)

    assert Path(os.readlink(current_root / "homelab")) == first.path
    state = json.loads((state_root / "homelab.json").read_text(encoding="utf-8"))
    assert state["good"] == SHA_A
    up_shas = [
        cwd.parents[1].name
        for args, cwd, _env in runner.calls
        if args[:2] == ("docker", "compose") and "up" in args
    ]
    assert up_shas == [SHA_B, SHA_A]
    assert runner.active == {"homelab": SHA_A}
    assert not any("down" in args for args, _cwd, _env in runner.calls)


@requires_symlink
def test_smoke_failure_restores_and_smoke_checks_previous_release(deployment):
    deployer, runner, _locks, _release_root, current_root, _state_root, _runtime = deployment
    first = deployer.deploy(SHA_A)
    runner.calls.clear()
    runner.fail_smoke.add(SHA_B)

    with pytest.raises(DeploymentError, match="previous release was restored"):
        deployer.deploy(SHA_B)

    assert Path(os.readlink(current_root / "homelab")) == first.path
    smoke_shas = [
        cwd.parents[1].name
        for args, cwd, _env in runner.calls
        if args[0].endswith("smoke")
    ]
    assert smoke_shas == [SHA_B, SHA_A]


@requires_symlink
def test_unhealthy_service_triggers_rollback(deployment):
    deployer, runner, _locks, _release_root, current_root, _state_root, _runtime = deployment
    first = deployer.deploy(SHA_A)
    runner.calls.clear()
    runner.unhealthy.add(SHA_B)

    with pytest.raises(DeploymentError, match="previous release was restored"):
        deployer.deploy(SHA_B)

    assert Path(os.readlink(current_root / "homelab")) == first.path


def test_missing_health_gate_fails_closed_for_routed_service(homelab_deployment):
    deployment = homelab_deployment
    deployment.runner.services = ("qbittorrent",)
    deployment.runner.no_health.add(SHA_A)

    with pytest.raises(DeploymentError, match="mandatory health gate"):
        deployment.deployer.deploy(
            SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
        )

    assert deployment.runner.active == {}


def test_scratch_daemon_uses_mandatory_zero_restart_process_gate(
    homelab_deployment,
):
    deployment = homelab_deployment
    deployment.runner.services = ("cloudflare-ddns",)
    deployment.runner.no_health.update({SHA_A, SHA_B})

    deployment.deployer.deploy(
        SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
    )
    deployment.runner.restart_counts[(SHA_B, "cloudflare-ddns")] = 1

    with pytest.raises(DeploymentError, match="process stability gate"):
        deployment.deployer.deploy(SHA_B)

    assert deployment.deployer.states["homelab"] == (SHA_A, None)
    assert deployment.runner.active == {"homelab": SHA_A}


@requires_symlink
def test_preflight_pull_failure_does_not_change_or_reapply_current_release(deployment):
    deployer, runner, _locks, _release_root, current_root, _state_root, _runtime = deployment
    first = deployer.deploy(SHA_A)
    runner.calls.clear()
    runner.fail_pull.add(SHA_B)

    with pytest.raises(DeploymentError, match="failed with exit status 7"):
        deployer.deploy(SHA_B)

    assert Path(os.readlink(current_root / "homelab")) == first.path
    assert not any("up" in args for args, _cwd, _env in runner.calls)


@requires_symlink
def test_rejects_pointer_escape_before_deploying(deployment):
    deployer, runner, _locks, _release_root, current_root, _state_root, _runtime = deployment
    outside = current_root.parent / "outside"
    outside.mkdir()
    os.symlink(outside, current_root / "homelab", target_is_directory=True)

    with pytest.raises(DeploymentError, match="outside the release root"):
        deployer.deploy(SHA_A)

    assert runner.calls == []


def test_runtime_overlay_cannot_replace_release_or_reserved_files(deployment):
    deployer, runner, _locks, _release_root, _current_root, _state_root, runtime = deployment
    _write(runtime / "homelab" / "files" / "compose.yml", "malicious\n", 0o600)

    with pytest.raises(DeploymentError, match="reserved path"):
        deployer.deploy(SHA_A)

    assert runner.calls == []


def test_missing_mandatory_smoke_contract_fails_before_compose(deployment):
    deployer, runner, _locks, _release_root, _current_root, state_root, runtime = deployment
    (runtime / "homelab" / "smoke").unlink()

    with pytest.raises(DeploymentError, match="required smoke executable"):
        deployer.deploy(SHA_A)

    assert runner.calls == []
    assert not (state_root / "homelab.pending-transaction.json").exists()


def test_rejects_legacy_regular_file_current_pointer(deployment):
    deployer, runner, _locks, _release_root, current_root, _state_root, _runtime = deployment
    _write(current_root / "homelab", SHA_A + "\n", 0o600)

    with pytest.raises(DeploymentError, match="not a symlink"):
        deployer.deploy(SHA_A)

    assert runner.calls == []


def test_rejects_symlinked_runtime_input_when_supported(deployment):
    deployer, runner, _locks, _release_root, _current_root, _state_root, runtime = deployment
    target = runtime / "homelab" / "real.env"
    _write(target, "TOKEN=value\n", 0o600)
    (runtime / "homelab" / ".env").unlink()
    try:
        (runtime / "homelab" / ".env").symlink_to(target)
    except OSError:
        pytest.skip("the test platform does not permit symlink creation")

    with pytest.raises(DeploymentError, match="symlink"):
        deployer.deploy(SHA_A)

    assert runner.calls == []


@pytest.mark.parametrize("failure", ["up", "smoke", "state-write"])
def test_initial_failure_stops_project_before_removing_pointer(
    deployment, monkeypatch: pytest.MonkeyPatch, failure: str
):
    deployer, runner, _locks, _release_root, current_root, state_root, _runtime = deployment
    if failure == "up":
        runner.fail_up.add(SHA_A)
    elif failure == "smoke":
        runner.fail_smoke.add(SHA_A)
    else:
        def fail_state_write(*_args, **_kwargs):
            raise OSError("injected state write failure")

        monkeypatch.setattr(deployer, "_write_state", fail_state_write)

    pointer_live = {"value": False}
    if os.name == "nt":
        def record_pointer_write(_project: str, _sha: str) -> None:
            pointer_live["value"] = True

        monkeypatch.setattr(deployer, "_write_pointer", record_pointer_write)
        original_remove = None
    else:
        original_remove = deployer._remove_pointer

    def record_pointer_removal(project: str) -> None:
        assert project not in runner.active
        runner.events.append(("pointer-remove", project, SHA_A))
        pointer_live["value"] = False
        if original_remove is not None:
            original_remove(project)

    monkeypatch.setattr(deployer, "_remove_pointer", record_pointer_removal)

    with pytest.raises(DeploymentError, match="failed release was stopped"):
        deployer.deploy(SHA_A)

    assert runner.active == {}
    assert not pointer_live["value"]
    if os.name != "nt":
        assert not (current_root / "homelab").exists()
        assert not (current_root / "homelab").is_symlink()
    assert not (state_root / "homelab.json").exists()
    event_names = [event[0] for event in runner.events]
    assert event_names.index("down") < event_names.index("pointer-remove")
    down_calls = [
        args[args.index("down") :]
        for args, _cwd, _env in runner.calls
        if args[:2] == ("docker", "compose") and "down" in args
    ]
    assert down_calls == [("down", "--remove-orphans")]


def test_initial_failure_reports_rollback_failure_when_compose_down_fails(
    deployment, monkeypatch: pytest.MonkeyPatch
):
    deployer, runner, _locks, _release_root, current_root, _state_root, _runtime = deployment
    runner.fail_up.add(SHA_A)
    runner.fail_down.add(SHA_A)
    pointer_live = {"value": False}
    if os.name == "nt":
        def record_pointer_write(_project: str, _sha: str) -> None:
            pointer_live["value"] = True

        monkeypatch.setattr(deployer, "_write_pointer", record_pointer_write)

    with pytest.raises(DeploymentError, match=r"rollback also failed .*exit status 12"):
        deployer.deploy(SHA_A)

    assert runner.active == {"homelab": SHA_A}
    assert pointer_live["value"] if os.name == "nt" else (current_root / "homelab").is_symlink()
    assert [event[0] for event in runner.events] == ["up", "down"]


def test_staged_private_inputs_are_not_world_accessible_on_posix(deployment):
    if os.name != "posix":
        pytest.skip("POSIX mode assertion")
    deployer, _runner, _locks, *_rest = deployment

    release = deployer.deploy(SHA_A)

    for path in (Path(release.path) / ".env", Path(release.path) / "generated.conf"):
        assert stat.S_IMODE(path.stat().st_mode) & 0o007 == 0


@requires_symlink
def test_homelab_uses_one_absolute_current_symlink(deployment):
    deployer, _runner, _locks, _release_root, current_root, _state_root, _runtime = deployment

    release = deployer.deploy(SHA_A)

    target = Path(os.readlink(current_root / "homelab"))
    assert target.is_absolute() and target == release.path
    assert list(current_root.iterdir()) == [current_root / "homelab"]


class ArtifactTestDeployer(ComposeReleaseDeployer):
    """Use real staging/artifact state without Windows symlink requirements."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pointers: dict[str, str] = {}
        self.states: dict[str, tuple[str, str | None]] = {}

    def _load_current(self, project: str) -> Release | None:
        pointer = self.pointers.get(project)
        state = self.states.get(project)
        if pointer is None and state is None:
            return None
        if pointer is None or state is None or pointer != state[0]:
            raise DeploymentError("test pointer and state disagree")
        return self._release(pointer, project)

    def _write_pointer(self, project: str, sha: str) -> None:
        self._release(sha, project)
        self.pointers[project] = sha

    def _remove_pointer(self, project: str) -> None:
        self.pointers.pop(project, None)

    def _pointer_release_for_reconciliation(self, project: str) -> Release | None:
        sha = self.pointers.get(project)
        return None if sha is None else self._release(sha, project)

    def _write_state(self, project: str, good: str, previous: str | None) -> None:
        self._release(good, project)
        self.states[project] = (good, previous)

    def _read_state(self, project: str) -> tuple[str, str | None] | None:
        return self.states.get(project)

    def _remove_state(self, project: str) -> None:
        self.states.pop(project, None)


def _make_homelab_release(release_root: Path, sha: str) -> Path:
    root = release_root / sha / "apps" / "compose" / "homelab"
    root.mkdir(parents=True)
    _write(
        root / "compose.yml",
        """name: homelab
services:
  t3code:
    image: "${T3CODE_IMAGE_REF:?exact digest required}"
""",
        0o644,
    )
    _write(root / "README.md", f"release {sha}\n", 0o644)
    return root


@pytest.fixture
def homelab_deployment(tmp_path: Path):
    release_root = tmp_path / "releases"
    current_root = tmp_path / "current"
    state_root = tmp_path / "state"
    runtime_root = tmp_path / "runtime"
    for root in (release_root, current_root, state_root, runtime_root):
        root.mkdir()
        os.chmod(root, 0o700)
    for sha in (SHA_A, SHA_B, STACK_THIRD_SHA):
        _make_homelab_release(release_root, sha)
    _make_runtime(runtime_root, "homelab", secret="runtime-only", smoke=True)
    runner = FakeDockerRunner(("t3code",))
    locks: list[tuple[str, Path, int]] = []

    def lock_factory(path: Path, uid: int) -> RecordingLock:
        return RecordingLock(locks, path, uid)

    deployer = ArtifactTestDeployer(
        release_root=release_root,
        current_root=current_root,
        state_root=state_root,
        runtime_config_root=runtime_root,
        trusted_uid=release_root.stat().st_uid,
        runner=runner,
        lock_factory=lock_factory,
    )
    return SimpleNamespace(
        deployer=deployer,
        runner=runner,
        state_root=state_root,
        release_root=release_root,
    )


def _approval(sha: str, digest: str) -> ImageApproval:
    return ImageApproval(source_sha=sha, ref=f"{T3_IMAGE}@{digest}")


def _release_artifacts(release: Release) -> dict:
    return json.loads(
        (release.path / ".homelab" / "artifacts.json").read_text(encoding="utf-8")
    )


def test_first_homelab_release_fails_closed_then_promotes_health_checked_digest(
    homelab_deployment,
):
    deployment = homelab_deployment
    with pytest.raises(DeploymentError, match="first homelab release requires"):
        deployment.deployer.deploy(SHA_A)
    assert deployment.runner.calls == []

    release = deployment.deployer.deploy(
        SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
    )

    store = ArtifactReleaseStore(
        deployment.state_root / "artifacts", artifact="t3code"
    )
    approved = store.load_approved(expected_image=T3_IMAGE)
    assert approved["source_sha"] == SHA_A
    assert approved["ref"] == f"{T3_IMAGE}@{T3_DIGEST_A}"
    assert _release_artifacts(release)["artifacts"]["t3code"]["reused"] is False
    assert (release.path / ".homelab" / "artifacts.env").read_text(
        encoding="utf-8"
    ) == f"T3CODE_IMAGE_REF={T3_IMAGE}@{T3_DIGEST_A}\n"
    assert not (release.path / "Dockerfile").exists()

    compose_calls = [
        args for args, _cwd, _env in deployment.runner.calls if args[:2] == ("docker", "compose")
    ]
    image_gate = next(index for index, args in enumerate(compose_calls) if "--images" in args)
    pull = next(index for index, args in enumerate(compose_calls) if "pull" in args)
    assert image_gate < pull
    assert all("build" not in args and "--build" not in args for args in compose_calls)
    assert any("--no-build" in args for args in compose_calls if "up" in args)
    assert all(
        str(release.path / ".homelab" / "artifacts.env") in args
        for args in compose_calls
    )


def test_unrelated_release_reuses_approved_digest_and_records_release_binding(
    homelab_deployment,
):
    deployment = homelab_deployment
    deployment.deployer.deploy(
        SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
    )

    release = deployment.deployer.deploy(SHA_B)

    artifact = _release_artifacts(release)["artifacts"]["t3code"]
    assert artifact["source_sha"] == SHA_A
    assert artifact["ref"] == f"{T3_IMAGE}@{T3_DIGEST_A}"
    assert artifact["reused"] is True
    store = ArtifactReleaseStore(
        deployment.state_root / "artifacts", artifact="t3code"
    )
    assert store.load_approved(expected_image=T3_IMAGE)["source_sha"] == SHA_A


def test_unrelated_release_derives_digest_from_current_good_release_not_pointer(
    homelab_deployment,
):
    deployment = homelab_deployment
    deployment.deployer.deploy(
        SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
    )
    store = ArtifactReleaseStore(
        deployment.state_root / "artifacts", artifact="t3code"
    )
    store.record(
        source_sha=STACK_THIRD_SHA,
        image=T3_IMAGE,
        platform="linux/amd64",
        digest=T3_DIGEST_B,
    )
    store.promote(STACK_THIRD_SHA, expected_image=T3_IMAGE)

    release = deployment.deployer.deploy(SHA_B)

    artifact = _release_artifacts(release)["artifacts"]["t3code"]
    assert artifact["source_sha"] == SHA_A
    assert artifact["ref"] == f"{T3_IMAGE}@{T3_DIGEST_A}"
    assert artifact["reused"] is True
    assert store.load_approved(expected_image=T3_IMAGE)["ref"] == (
        f"{T3_IMAGE}@{T3_DIGEST_A}"
    )


def test_failed_changed_image_is_not_promoted_and_rollback_uses_prior_digest(
    homelab_deployment,
):
    deployment = homelab_deployment
    deployment.deployer.deploy(
        SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
    )
    previous = deployment.deployer.deploy(SHA_B)
    deployment.runner.calls.clear()
    deployment.runner.fail_smoke.add(STACK_THIRD_SHA)

    with pytest.raises(DeploymentError, match="previous release was restored"):
        deployment.deployer.deploy(
            STACK_THIRD_SHA,
            t3_approval=_approval(STACK_THIRD_SHA, T3_DIGEST_B),
        )

    store = ArtifactReleaseStore(
        deployment.state_root / "artifacts", artifact="t3code"
    )
    assert store.load_approved(expected_image=T3_IMAGE)["ref"] == (
        f"{T3_IMAGE}@{T3_DIGEST_A}"
    )
    assert store.load_source(
        STACK_THIRD_SHA, expected_image=T3_IMAGE
    )["ref"] == f"{T3_IMAGE}@{T3_DIGEST_B}"
    assert deployment.deployer.pointers["homelab"] == SHA_B
    assert deployment.deployer.states["homelab"] == (SHA_B, SHA_A)
    rollback_ups = [
        (cwd.parents[1].name, args)
        for args, cwd, _env in deployment.runner.calls
        if args[:2] == ("docker", "compose") and "up" in args
    ]
    assert [sha for sha, _args in rollback_ups] == [STACK_THIRD_SHA, SHA_B]
    assert _release_artifacts(previous)["artifacts"]["t3code"]["ref"] == (
        f"{T3_IMAGE}@{T3_DIGEST_A}"
    )

    deployment.runner.fail_smoke.clear()
    deployment.deployer.deploy(
        STACK_THIRD_SHA,
        t3_approval=_approval(STACK_THIRD_SHA, T3_DIGEST_B),
    )
    assert store.load_approved(expected_image=T3_IMAGE)["ref"] == (
        f"{T3_IMAGE}@{T3_DIGEST_B}"
    )


def test_state_commit_failure_cannot_approve_the_failed_candidate_digest(
    homelab_deployment, monkeypatch: pytest.MonkeyPatch
):
    deployment = homelab_deployment
    deployment.deployer.deploy(
        SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A)
    )
    deployment.deployer.deploy(SHA_B)
    original_write_state = deployment.deployer._write_state

    def fail_candidate_state(project: str, good: str, previous: str | None) -> None:
        if good == STACK_THIRD_SHA:
            raise OSError("injected state commit failure")
        original_write_state(project, good, previous)

    monkeypatch.setattr(deployment.deployer, "_write_state", fail_candidate_state)

    with pytest.raises(DeploymentError, match="previous release was restored"):
        deployment.deployer.deploy(
            STACK_THIRD_SHA,
            t3_approval=_approval(STACK_THIRD_SHA, T3_DIGEST_B),
        )

    store = ArtifactReleaseStore(
        deployment.state_root / "artifacts", artifact="t3code"
    )
    assert store.load_approved(expected_image=T3_IMAGE)["ref"] == (
        f"{T3_IMAGE}@{T3_DIGEST_A}"
    )
    assert store.load_source(
        STACK_THIRD_SHA, expected_image=T3_IMAGE
    )["ref"] == f"{T3_IMAGE}@{T3_DIGEST_B}"
    assert deployment.deployer.pointers["homelab"] == SHA_B
    assert deployment.deployer.states["homelab"] == (SHA_B, SHA_A)
    assert deployment.runner.active == {"homelab": SHA_B}


def _restarted_artifact_deployer(deployment) -> ArtifactTestDeployer:
    original = deployment.deployer
    restarted = ArtifactTestDeployer(
        release_root=original.release_root,
        current_root=original.current_root,
        state_root=original.state_root,
        runtime_config_root=original.runtime_config_root,
        trusted_uid=original.trusted_uid,
        runner=deployment.runner,
        lock_factory=original.lock_factory,
    )
    restarted.pointers = original.pointers
    restarted.states = original.states
    return restarted


@pytest.mark.parametrize(
    ("boundary", "committed"),
    [
        ("journal", False),
        ("stage", False),
        ("pointer", False),
        ("runtime", False),
        ("state", True),
        ("promotion", True),
        ("clear", True),
    ],
)
def test_restart_reconciles_fsynced_transaction_at_every_mutation_boundary(
    homelab_deployment,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    committed: bool,
):
    deployment = homelab_deployment
    deployer = deployment.deployer
    deployer.deploy(SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A))

    if boundary == "journal":
        original = deployer._begin_transaction

        def crash_after_journal(*args, **kwargs):
            result = original(*args, **kwargs)
            raise SystemExit("simulated crash after journal fsync")

        monkeypatch.setattr(deployer, "_begin_transaction", crash_after_journal)
    elif boundary == "stage":
        original = deployer._stage

        def crash_after_stage(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == SHA_B:
                raise SystemExit("simulated crash after runtime materialization")
            return result

        monkeypatch.setattr(deployer, "_stage", crash_after_stage)
    elif boundary == "pointer":
        original = deployer._write_pointer

        def crash_after_pointer(project: str, sha: str) -> None:
            original(project, sha)
            if sha == SHA_B:
                raise SystemExit("simulated crash after pointer replacement")

        monkeypatch.setattr(deployer, "_write_pointer", crash_after_pointer)
    elif boundary == "runtime":
        original = deployer._activate

        def crash_after_runtime(release, services, *, smoke):
            original(release, services, smoke=smoke)
            if release.sha == SHA_B:
                raise SystemExit("simulated crash after healthy runtime switch")

        monkeypatch.setattr(deployer, "_activate", crash_after_runtime)
    elif boundary == "state":
        original = deployer._write_state

        def crash_after_state(project: str, good: str, previous: str | None) -> None:
            original(project, good, previous)
            if good == SHA_B:
                raise SystemExit("simulated crash after state commit")

        monkeypatch.setattr(deployer, "_write_state", crash_after_state)
    elif boundary == "promotion":
        original = deployer._promote_release_artifact

        def crash_after_promotion(release) -> None:
            original(release)
            if release.sha == SHA_B:
                raise SystemExit("simulated crash after artifact promotion")

        monkeypatch.setattr(
            deployer, "_promote_release_artifact", crash_after_promotion
        )
    else:
        original = deployer._clear_transaction

        def crash_before_clear(project: str) -> None:
            raise SystemExit("simulated crash before journal removal")

        monkeypatch.setattr(deployer, "_clear_transaction", crash_before_clear)

    with pytest.raises(SystemExit, match="simulated crash"):
        deployer.deploy(SHA_B, t3_approval=_approval(SHA_B, T3_DIGEST_B))

    transaction = deployer._read_transaction("homelab")
    assert transaction is not None
    assert transaction["candidate"] == SHA_B
    restarted = _restarted_artifact_deployer(deployment)

    assert restarted.reconcile() == ("committed" if committed else "rolled-back")
    assert restarted._read_transaction("homelab") is None
    expected_sha = SHA_B if committed else SHA_A
    assert restarted.pointers["homelab"] == expected_sha
    assert restarted.states["homelab"] == (
        (SHA_B, SHA_A) if committed else (SHA_A, None)
    )
    assert deployment.runner.active == {"homelab": expected_sha}
    store = ArtifactReleaseStore(
        deployment.state_root / "artifacts", artifact="t3code"
    )
    assert store.load_approved(expected_image=T3_IMAGE)["ref"] == (
        f"{T3_IMAGE}@{T3_DIGEST_B if committed else T3_DIGEST_A}"
    )


def test_next_deploy_command_reconciles_before_same_release_audit(
    homelab_deployment, monkeypatch: pytest.MonkeyPatch
):
    deployment = homelab_deployment
    deployer = deployment.deployer
    deployer.deploy(SHA_A, t3_approval=_approval(SHA_A, T3_DIGEST_A))
    original = deployer._begin_transaction

    def crash_after_journal(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("simulated killed deployment")

    monkeypatch.setattr(deployer, "_begin_transaction", crash_after_journal)
    with pytest.raises(SystemExit, match="killed deployment"):
        deployer.deploy(SHA_B, t3_approval=_approval(SHA_B, T3_DIGEST_B))

    restarted = _restarted_artifact_deployer(deployment)
    deployment.runner.calls.clear()
    assert restarted.deploy(SHA_A).sha == SHA_A

    assert restarted.states["homelab"] == (SHA_A, None)
    assert restarted.pointers["homelab"] == SHA_A
    assert restarted._read_transaction("homelab") is None
    assert not any(
        "up" in args or "down" in args
        for args, _cwd, _env in deployment.runner.calls
        if args[:2] == ("docker", "compose")
    )


def test_explicit_empty_host_reconstruction_initializes_without_legacy_state(
    homelab_deployment,
):
    deployment = homelab_deployment
    approval_file = deployment.state_root / EMPTY_HOST_APPROVAL_NAME
    _write(
        approval_file,
        json.dumps(
            {
                "version": 1,
                "operation": "initialize-empty-host",
                "sha": SHA_A,
            },
            sort_keys=True,
        )
        + "\n",
        0o600,
    )

    result = ComposeStackMigrator(deployment.deployer).initialize_empty_host(
        SHA_A,
        approval_file=approval_file,
        t3_approval=_approval(SHA_A, T3_DIGEST_A),
    )

    assert result == "initialized"
    record = _load_migration_record(
        deployment.state_root, deployment.deployer.trusted_uid
    )
    assert record is not None
    assert record.origin == "empty-host-reconstruction"
    assert record.active == "homelab"
    assert record.legacy is None
    assert deployment.runner.active == {"homelab": SHA_A}
    with pytest.raises(DeploymentError, match="no legacy rollback checkpoint"):
        ComposeStackMigrator(deployment.deployer).rollback_to_legacy()

    assert deployment.deployer.deploy(SHA_B).sha == SHA_B


def test_empty_host_reconstruction_requires_exact_canonical_approval(
    homelab_deployment,
):
    deployment = homelab_deployment
    approval_file = deployment.state_root / EMPTY_HOST_APPROVAL_NAME
    _write(
        approval_file,
        json.dumps(
            {
                "version": 1,
                "operation": "initialize-empty-host",
                "sha": SHA_B,
            }
        )
        + "\n",
        0o600,
    )

    with pytest.raises(DeploymentError, match="exactly bind this release SHA"):
        ComposeStackMigrator(deployment.deployer).initialize_empty_host(
            SHA_A,
            approval_file=approval_file,
            t3_approval=_approval(SHA_A, T3_DIGEST_A),
        )

    assert deployment.runner.calls == []
    assert not (deployment.state_root / "stack-migration.json").exists()


class MemoryMigrationDeployer(ComposeReleaseDeployer):
    """Keep pointer/state mutations in memory while exercising real orchestration."""

    def __init__(self, *args, releases: dict[tuple[str, str], Release], **kwargs):
        super().__init__(*args, **kwargs)
        self.releases = releases
        self.pointers: dict[str, str] = {}
        self.states: dict[str, tuple[str, str | None]] = {}
        self.staged: list[tuple[str, str]] = []

    def _prepare_roots(self) -> None:
        for root in (
            self.release_root,
            self.current_root,
            self.state_root,
            self.runtime_config_root,
            self.state_root / "locks",
        ):
            root.mkdir(parents=True, exist_ok=True)
            os.chmod(root, 0o700)

    def _validate_release(self, release: Release) -> None:
        expected = self.releases.get((release.sha, release.project))
        if expected != release or not release.path.is_dir():
            raise DeploymentError("test release identity is invalid")
        if release.project == "homelab":
            self._load_release_artifacts(release)

    def _stage(self, sha: str, project: str, *, artifacts=None) -> Release:
        release = self.releases.get((sha, project))
        if release is None:
            raise DeploymentError("test uploaded release is unavailable")
        self._validate_release(release)
        if project == "homelab" and self._load_release_artifacts(release) != artifacts:
            raise DeploymentError("test T3 artifact metadata mismatch")
        self.staged.append((sha, project))
        return release

    def _release(self, sha: str, project: str) -> Release:
        release = self.releases.get((sha, project))
        if release is None:
            raise DeploymentError("test staged release is unavailable")
        self._validate_release(release)
        return release

    def _load_current(self, project: str) -> Release | None:
        pointer = self.pointers.get(project)
        state = self.states.get(project)
        if pointer is None and state is None:
            return None
        if pointer is None or state is None or state[0] != pointer:
            raise DeploymentError("current pointer and recorded good release disagree")
        return self._release(pointer, project)

    def _snapshot(self, project: str, *, require_state: bool) -> ProjectSnapshot:
        current = self._load_current(project)
        state = self.states.get(project)
        if current is None or (require_state and state is None):
            raise DeploymentError(
                f"project {project!r} has no trustworthy current release and state"
            )
        return ProjectSnapshot(current, state[1] if state else None)

    def _write_pointer(self, project: str, sha: str) -> None:
        self._release(sha, project)
        self.pointers[project] = sha

    def _remove_pointer(self, project: str) -> None:
        self.pointers.pop(project, None)

    def _pointer_release_for_reconciliation(self, project: str) -> Release | None:
        sha = self.pointers.get(project)
        return None if sha is None else self._release(sha, project)

    def _read_state(self, project: str) -> tuple[str, str | None] | None:
        return self.states.get(project)

    def _write_state(self, project: str, good: str, previous: str | None) -> None:
        self._release(good, project)
        self.states[project] = (good, previous)

    def _remove_state(self, project: str) -> None:
        self.states.pop(project, None)

    def _require_project_artifacts_absent(self, project: str) -> None:
        if project in self.pointers or project in self.states:
            raise DeploymentError(
                f"partial or ambiguous prior {project} activation is present"
            )


def _staged_release(
    root: Path,
    sha: str,
    project: str,
    *,
    t3_source_sha: str = STACK_SHA,
) -> Release:
    path = root / sha / ".staged" / project
    path.mkdir(parents=True)
    _write(path / "compose.yml", f"name: {project}\nservices:\n  app:\n    image: x\n", 0o600)
    _write(path / ".env", "TOKEN=test\n", 0o600)
    _write(path / ".homelab" / "smoke", "#!/bin/sh\nexit 0\n", 0o700)
    if project == "homelab":
        record = approved_record(
            artifact="t3code",
            source_sha=t3_source_sha,
            image=T3_IMAGE,
            platform="linux/amd64",
            digest=T3_DIGEST_A,
        )
        payload = deployment_payload(
            deployment_source_sha=sha,
            artifact="t3code",
            record=record,
        )
        _write(
            path / ".homelab" / "artifacts.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        _write(
            path / ".homelab" / "artifacts.env",
            f"T3CODE_IMAGE_REF={T3_IMAGE}@{T3_DIGEST_A}\n",
            0o600,
        )
    return Release(sha, project, path)


@pytest.fixture
def stack_migration(tmp_path: Path):
    release_root = tmp_path / "releases"
    current_root = tmp_path / "current"
    state_root = tmp_path / "state"
    runtime_root = tmp_path / "runtime"
    releases: dict[tuple[str, str], Release] = {}
    identities = {
        "platform": PLATFORM_SHA,
        "media": MEDIA_SHA,
        "code": CODE_SHA,
        "homelab": STACK_SHA,
    }
    for project, sha in identities.items():
        releases[(sha, project)] = _staged_release(release_root, sha, project)
    releases[(STACK_NEXT_SHA, "homelab")] = _staged_release(
        release_root, STACK_NEXT_SHA, "homelab"
    )

    runner = FakeDockerRunner(("app",))
    runner.active.update(
        {project: identities[project] for project in ("platform", "media", "code")}
    )
    locks: list[tuple[str, Path, int]] = []

    def lock_factory(path: Path, uid: int) -> RecordingLock:
        return RecordingLock(locks, path, uid)

    deployer = MemoryMigrationDeployer(
        release_root=release_root,
        current_root=current_root,
        state_root=state_root,
        runtime_config_root=runtime_root,
        trusted_uid=release_root.stat().st_uid,
        runner=runner,
        lock_factory=lock_factory,
        releases=releases,
    )
    artifact_state = state_root / "artifacts"
    artifact_state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_root, 0o700)
    ArtifactReleaseStore(artifact_state, artifact="t3code").approve(
        source_sha=STACK_SHA,
        image=T3_IMAGE,
        platform="linux/amd64",
        digest=T3_DIGEST_A,
    )
    for project in ("platform", "media", "code"):
        deployer.pointers[project] = identities[project]
        deployer.states[project] = (identities[project], None)
    return SimpleNamespace(
        deployer=deployer,
        migrator=ComposeStackMigrator(deployer),
        runner=runner,
        locks=locks,
        state_root=state_root,
    )


def _mutating_compose_calls(runner: FakeDockerRunner):
    return [
        args
        for args, _cwd, _env in runner.calls
        if args[:2] == ("docker", "compose")
        and any(action in args for action in ("pull", "up", "down"))
    ]


def test_stack_migration_preflights_locks_orders_cutover_and_records_state(
    stack_migration, monkeypatch: pytest.MonkeyPatch,
):
    migration = stack_migration
    reconciled: list[str] = []
    original_reconcile = migration.deployer._reconcile_pending_transaction

    def record_reconcile(project: str) -> str:
        reconciled.append(project)
        return original_reconcile(project)

    monkeypatch.setattr(
        migration.deployer, "_reconcile_pending_transaction", record_reconcile
    )

    assert migration.migrator.migrate(STACK_SHA) == "activated"
    assert reconciled == ["homelab"]

    assert migration.deployer.staged == [(STACK_SHA, "homelab")]
    events = migration.runner.events
    assert events == [
        ("smoke", "platform", PLATFORM_SHA),
        ("smoke", "media", MEDIA_SHA),
        ("smoke", "code", CODE_SHA),
        ("down", "code", CODE_SHA),
        ("down", "media", MEDIA_SHA),
        ("down", "platform", PLATFORM_SHA),
        ("up", "homelab", STACK_SHA),
        ("smoke", "homelab", STACK_SHA),
    ]
    assert migration.runner.active == {"homelab": STACK_SHA}
    pull_index = next(
        index
        for index, (args, _cwd, _env) in enumerate(migration.runner.calls)
        if "pull" in args
    )
    first_legacy_health = next(
        index
        for index, (args, cwd, _env) in enumerate(migration.runner.calls)
        if "ps" in args and cwd.parents[1].name == PLATFORM_SHA
    )
    assert pull_index < first_legacy_health
    marker = json.loads(
        (migration.state_root / "stack-migration.json").read_text(encoding="utf-8")
    )
    assert marker == {
        "active": "homelab",
        "homelab": STACK_SHA,
        "legacy": {
            "platform": {"good": PLATFORM_SHA, "previous": None},
            "media": {"good": MEDIA_SHA, "previous": None},
            "code": {"good": CODE_SHA, "previous": None},
        },
        "version": 1,
    }
    entered = [path.name for action, path, _uid in migration.locks if action == "enter"]
    assert entered == [
        "stack-migration.lock",
        "platform.lock",
        "media.lock",
        "code.lock",
        "homelab.lock",
    ]
    for args in _mutating_compose_calls(migration.runner):
        assert "build" not in args and "--build" not in args
        if "up" in args:
            assert "--no-build" in args
        if "down" in args:
            assert "--volumes" not in args and "-v" not in args


def test_stack_migration_failure_after_stop_restores_legacy_in_ingress_first_order(
    stack_migration,
):
    migration = stack_migration
    migration.runner.fail_smoke.add(STACK_SHA)

    with pytest.raises(DeploymentError, match="legacy was restored"):
        migration.migrator.migrate(STACK_SHA)

    assert migration.runner.events[-9:] == [
        ("up", "homelab", STACK_SHA),
        ("smoke", "homelab", STACK_SHA),
        ("down", "homelab", STACK_SHA),
        ("up", "platform", PLATFORM_SHA),
        ("smoke", "platform", PLATFORM_SHA),
        ("up", "media", MEDIA_SHA),
        ("smoke", "media", MEDIA_SHA),
        ("up", "code", CODE_SHA),
        ("smoke", "code", CODE_SHA),
    ]
    assert migration.runner.active == {
        "platform": PLATFORM_SHA,
        "media": MEDIA_SHA,
        "code": CODE_SHA,
    }
    assert "homelab" not in migration.deployer.pointers
    assert "homelab" not in migration.deployer.states
    assert not (migration.state_root / "stack-migration.json").exists()


def test_stack_migration_precondition_failure_never_stops_legacy(stack_migration):
    migration = stack_migration
    migration.runner.fail_pull.add(STACK_SHA)

    with pytest.raises(DeploymentError, match="exit status 7"):
        migration.migrator.migrate(STACK_SHA)

    assert not any(event[0] == "down" for event in migration.runner.events)
    assert migration.runner.active == {
        "platform": PLATFORM_SHA,
        "media": MEDIA_SHA,
        "code": CODE_SHA,
    }


def test_stack_migration_rejects_scaled_legacy_service_before_stopping(stack_migration):
    migration = stack_migration
    migration.runner.scaled.add(PLATFORM_SHA)

    with pytest.raises(DeploymentError, match="no unique valid container"):
        migration.migrator.migrate(STACK_SHA)

    assert not any(event[0] == "down" for event in migration.runner.events)


def test_stack_migration_same_sha_rerun_is_observational_and_idempotent(
    stack_migration,
):
    migration = stack_migration
    assert migration.migrator.migrate(STACK_SHA) == "activated"
    marker_before = (migration.state_root / "stack-migration.json").read_bytes()
    migration.runner.calls.clear()
    migration.runner.events.clear()
    migration.locks.clear()

    assert migration.migrator.migrate(STACK_SHA) == "already-active"

    assert marker_before == (migration.state_root / "stack-migration.json").read_bytes()
    assert _mutating_compose_calls(migration.runner) == []
    assert migration.deployer.staged == [(STACK_SHA, "homelab")]


def test_explicit_rollback_drill_restores_legacy_and_can_be_rerun(stack_migration):
    migration = stack_migration
    migration.migrator.migrate(STACK_SHA)
    migration.runner.calls.clear()
    migration.runner.events.clear()

    assert migration.migrator.rollback_to_legacy() == "legacy-restored"

    assert migration.runner.events == [
        ("smoke", "homelab", STACK_SHA),
        ("down", "homelab", STACK_SHA),
        ("up", "platform", PLATFORM_SHA),
        ("smoke", "platform", PLATFORM_SHA),
        ("up", "media", MEDIA_SHA),
        ("smoke", "media", MEDIA_SHA),
        ("up", "code", CODE_SHA),
        ("smoke", "code", CODE_SHA),
    ]
    assert migration.runner.active == {
        "platform": PLATFORM_SHA,
        "media": MEDIA_SHA,
        "code": CODE_SHA,
    }
    record = _load_migration_record(
        migration.state_root, migration.deployer.trusted_uid
    )
    assert record is not None and record.active == "legacy"
    migration.runner.calls.clear()
    migration.runner.events.clear()
    assert migration.migrator.rollback_to_legacy() == "already-legacy"
    assert _mutating_compose_calls(migration.runner) == []


def test_failed_rollback_drill_restores_homelab_and_keeps_active_record(
    stack_migration,
):
    migration = stack_migration
    migration.migrator.migrate(STACK_SHA)
    migration.runner.calls.clear()
    migration.runner.events.clear()
    migration.runner.fail_smoke.add(PLATFORM_SHA)

    with pytest.raises(DeploymentError, match="homelab was restored"):
        migration.migrator.rollback_to_legacy()

    assert migration.runner.active == {"homelab": STACK_SHA}
    record = _load_migration_record(
        migration.state_root, migration.deployer.trusted_uid
    )
    assert record is not None and record.active == "homelab"
    assert migration.runner.events[-2:] == [
        ("up", "homelab", STACK_SHA),
        ("smoke", "homelab", STACK_SHA),
    ]


def test_later_homelab_release_uses_ordinary_deploy_rollback(stack_migration):
    migration = stack_migration
    migration.migrator.migrate(STACK_SHA)
    migration.runner.calls.clear()
    migration.runner.events.clear()
    migration.runner.fail_smoke.add(STACK_NEXT_SHA)

    with pytest.raises(DeploymentError, match="previous release was restored"):
        migration.deployer.deploy(STACK_NEXT_SHA)

    assert migration.deployer.pointers["homelab"] == STACK_SHA
    assert migration.deployer.states["homelab"] == (STACK_SHA, None)
    assert migration.runner.active == {"homelab": STACK_SHA}
    assert migration.runner.events[-2:] == [
        ("up", "homelab", STACK_SHA),
        ("smoke", "homelab", STACK_SHA),
    ]


def test_stack_migration_rejects_ambiguous_or_partial_prior_state(stack_migration):
    migration = stack_migration
    migration.deployer.pointers["homelab"] = STACK_SHA

    with pytest.raises(DeploymentError, match="partial or ambiguous"):
        migration.migrator.migrate(STACK_SHA)
    assert migration.runner.calls == []

    migration.deployer.pointers.pop("homelab")
    _write(migration.state_root / "stack-migration.json.pending", "partial\n")
    with pytest.raises(DeploymentError, match="partial stack migration"):
        migration.migrator.migrate(STACK_SHA)
    assert migration.runner.calls == []


def test_stack_migration_rejects_non_exact_sha_before_locks_or_commands(
    stack_migration,
):
    migration = stack_migration

    with pytest.raises(DeploymentError, match="lowercase"):
        migration.migrator.migrate("A" * 40)

    assert migration.runner.calls == []
    assert migration.locks == []
