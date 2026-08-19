#!/usr/bin/env python3
"""Activate an uploaded Compose release and restore the last good one on failure.

Expected inputs::

    RELEASE_ROOT/<sha>/apps/compose/homelab/compose.yml
    RUNTIME_CONFIG_ROOT/homelab/.env
    RUNTIME_CONFIG_ROOT/homelab/files/**   # optional generated files
    RUNTIME_CONFIG_ROOT/homelab/smoke      # required executable smoke contract

The release and runtime inputs are copied to the immutable
``<sha>/.staged/homelab`` directory. The current pointer is an atomically
replaced absolute symlink. Routine deployment has no project selection or
migration state machine; the legacy cutover is isolated under
``scripts/recovery``. This script does not upload releases, provision Docker,
build images, or manage application data directories.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Callable, Mapping, Protocol, Sequence
import uuid

if __package__:
    from .immutable_image_release import (
        ArtifactReleaseStore,
        ContractError as ImageContractError,
        load_json as load_artifact_json,
        validate_deployment_payload,
        validate_immutable_ref,
        validate_source_sha,
    )
else:  # pragma: no cover - exercised by the installed standalone script
    from immutable_image_release import (
        ArtifactReleaseStore,
        ContractError as ImageContractError,
        load_json as load_artifact_json,
        validate_deployment_payload,
        validate_immutable_ref,
        validate_source_sha,
    )


STACK_PROJECT = "homelab"
SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SERVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
CONTAINER_RE = re.compile(r"[0-9a-f]{12,64}\Z")
RESERVED_OVERLAYS = frozenset({"compose.yml", "compose.yaml", ".env", ".homelab"})
POSIX = os.name == "posix"
T3_ARTIFACT = "t3code"
T3_IMAGE = "ghcr.io/holybaechu/homelab-t3code"
T3_PLATFORM = "linux/amd64"
PROCESS_LIVENESS_SERVICES = frozenset({"cloudflare-ddns"})
ARTIFACT_STATE_DIRECTORY = "artifacts"
ARTIFACT_METADATA_NAME = "artifacts.json"
ARTIFACT_ENV_NAME = "artifacts.env"
TRANSACTION_PHASES = (
    "prepared",
    "staged",
    "pointer-written",
    "runtime-healthy",
    "state-committed",
    "artifact-promoted",
)
TRANSACTION_FIELDS = frozenset(
    {
        "version",
        "operation",
        "project",
        "phase",
        "candidate",
        "original_pointer",
        "original_good",
        "original_previous",
    }
)


class DeploymentError(RuntimeError):
    """A release failed validation, activation, or rollback."""


class CommandRunner(Protocol):
    """Injectable subprocess boundary for tests."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            env=None if env is None else dict(env),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )


class ProjectFileLock(AbstractContextManager["ProjectFileLock"]):
    """Linux ``flock`` on a persistent, trusted per-project file."""

    def __init__(self, path: Path, trusted_uid: int) -> None:
        self.path = path
        self.trusted_uid = trusted_uid
        self._handle = None

    def __enter__(self) -> "ProjectFileLock":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - production is Linux
            raise DeploymentError("flock is unavailable") from exc

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
            self._handle = os.fdopen(fd, "r+", encoding="utf-8")
            metadata = os.fstat(self._handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise DeploymentError(f"lock is not a regular file: {self.path}")
            if metadata.st_uid != self.trusted_uid or metadata.st_mode & 0o022:
                raise DeploymentError(f"lock ownership or mode is untrusted: {self.path}")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(f"{os.getpid()}\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except BaseException:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        if self._handle is None:
            return
        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


@dataclass(frozen=True)
class Release:
    sha: str
    project: str
    path: Path


@dataclass(frozen=True)
class ProjectSnapshot:
    release: Release
    previous: str | None


@dataclass(frozen=True)
class ImageApproval:
    source_sha: str
    ref: str


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _reject_symlink_components(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise DeploymentError(f"path must be absolute and traversal-free: {path}")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DeploymentError(f"cannot inspect path component {current}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise DeploymentError(f"symlink path component is untrusted: {current}")


def _trusted(
    path: Path,
    uid: int,
    *,
    directory: bool,
    private: bool = False,
    label: str = "path",
) -> os.stat_result:
    _reject_symlink_components(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentError(f"required {label} is unavailable at {path}: {exc}") from exc
    wanted = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(metadata.st_mode) or not wanted(metadata.st_mode):
        raise DeploymentError(f"{label} has an untrusted file type: {path}")
    if metadata.st_uid != uid:
        raise DeploymentError(f"{label} is not owned by trusted uid {uid}: {path}")
    if POSIX and metadata.st_mode & 0o022:
        raise DeploymentError(f"{label} is group/world writable: {path}")
    if POSIX and private and metadata.st_mode & 0o007:
        raise DeploymentError(f"{label} is world-accessible: {path}")
    return metadata


def _trusted_tree(path: Path, uid: int, *, private: bool = False) -> None:
    _trusted(path, uid, directory=True, private=private, label="trusted directory")
    for root, directories, files in os.walk(path, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        root_path = Path(root)
        for name in directories:
            _trusted(
                root_path / name,
                uid,
                directory=True,
                private=private,
                label="trusted directory",
            )
        for name in files:
            _trusted(
                root_path / name,
                uid,
                directory=False,
                private=private,
                label="trusted file",
            )


def _fsync_directory(path: Path, *, required: bool = False) -> None:
    if not POSIX:
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError:
        if required:
            raise
        return
    try:
        os.fsync(fd)
    except OSError:
        if required:
            raise
    finally:
        os.close(fd)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent, required=True)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_symlink(path: Path, target: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        os.symlink(str(target), temporary, target_is_directory=True)
        os.replace(temporary, path)
        _fsync_directory(path.parent, required=True)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _overlaps(left: Path, right: Path) -> bool:
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


class ComposeReleaseDeployer:
    def __init__(
        self,
        *,
        release_root: Path,
        current_root: Path,
        state_root: Path,
        runtime_config_root: Path,
        trusted_uid: int,
        runner: CommandRunner | None = None,
        lock_factory: Callable[[Path, int], AbstractContextManager[object]] = ProjectFileLock,
        docker_command: str = "docker",
    ) -> None:
        self.release_root = Path(release_root)
        self.current_root = Path(current_root)
        self.state_root = Path(state_root)
        self.runtime_config_root = Path(runtime_config_root)
        self.trusted_uid = trusted_uid
        self.runner = runner or SubprocessRunner()
        self.lock_factory = lock_factory
        self.docker_command = docker_command

    def _artifact_store(self) -> ArtifactReleaseStore:
        root = self.state_root / ARTIFACT_STATE_DIRECTORY
        if not _present(root):
            root.mkdir(mode=0o700)
        _trusted(
            root,
            self.trusted_uid,
            directory=True,
            private=True,
            label="OCI artifact state root",
        )
        artifact_root = root / T3_ARTIFACT
        if _present(artifact_root):
            _trusted_tree(artifact_root, self.trusted_uid)
        return ArtifactReleaseStore(root, artifact=T3_ARTIFACT)

    def _harden_artifact_store(self, store: ArtifactReleaseStore) -> None:
        for directory in (store.artifact_root, store.by_source):
            if _present(directory):
                _trusted(
                    directory,
                    self.trusted_uid,
                    directory=True,
                    label="OCI artifact state directory",
                )
                os.chmod(directory, 0o700)
        _trusted_tree(store.artifact_root, self.trusted_uid)

    def _select_homelab_artifacts(
        self,
        sha: str,
        approval: ImageApproval | None,
        *,
        current: Release | None = None,
    ) -> tuple[dict[str, object], str | None]:
        store = self._artifact_store()
        try:
            if approval is None:
                if current is None:
                    payload = store.materialize(
                        deployment_source_sha=sha,
                        expected_image=T3_IMAGE,
                    )
                else:
                    current_payload = self._load_release_artifacts(current)
                    selected = dict(current_payload["artifacts"][T3_ARTIFACT])
                    selected["reused"] = sha != selected["source_sha"]
                    payload = {
                        "schema": current_payload["schema"],
                        "kind": current_payload["kind"],
                        "deployment_source_sha": sha,
                        "artifacts": {T3_ARTIFACT: selected},
                    }
                candidate_source = None
            else:
                source_sha = validate_source_sha(
                    approval.source_sha, name="T3 artifact source SHA"
                )
                if source_sha != sha:
                    raise ImageContractError(
                        "T3 artifact source SHA must equal the tested deployment SHA"
                    )
                immutable_ref = validate_immutable_ref(
                    approval.ref, expected_image=T3_IMAGE
                )
                digest = immutable_ref.split("@", 1)[1]
                store.record(
                    source_sha=source_sha,
                    image=T3_IMAGE,
                    platform=T3_PLATFORM,
                    digest=digest,
                )
                self._harden_artifact_store(store)
                payload = store.materialize_source(
                    deployment_source_sha=sha,
                    artifact_source_sha=source_sha,
                    expected_image=T3_IMAGE,
                )
                candidate_source = source_sha
            canonical = validate_deployment_payload(
                payload,
                expected_artifact=T3_ARTIFACT,
                expected_deployment_source_sha=sha,
                expected_image=T3_IMAGE,
            )
        except ImageContractError as exc:
            if approval is None and "no approved immutable image" in str(exc):
                raise DeploymentError(
                    "first homelab release requires an exact T3 digest approval "
                    "bound to the tested deployment SHA"
                ) from exc
            raise DeploymentError(f"T3 artifact contract failed: {exc}") from exc
        self._harden_artifact_store(store)
        return canonical, candidate_source

    def _promote_homelab_artifact(self, source_sha: str) -> None:
        store = self._artifact_store()
        try:
            store.promote(source_sha, expected_image=T3_IMAGE)
        except ImageContractError as exc:
            raise DeploymentError(f"T3 artifact promotion failed: {exc}") from exc
        self._harden_artifact_store(store)

    def _promote_release_artifact(self, release: Release) -> None:
        """Promote only the artifact recorded by the committed good release."""
        canonical = self._load_release_artifacts(release)
        source_sha = canonical["artifacts"][T3_ARTIFACT]["source_sha"]
        if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
            raise DeploymentError("release T3 artifact source identity is invalid")
        self._promote_homelab_artifact(source_sha)

    def _restore_recorded_state(
        self, project: str, state: tuple[str, str | None] | None
    ) -> None:
        if state is None:
            self._remove_state(project)
        else:
            self._write_state(project, state[0], state[1])

    def _transaction_path(self, project: str) -> Path:
        return self.state_root / f"{project}.pending-transaction.json"

    @staticmethod
    def _validate_transaction(payload: object, *, project: str) -> dict[str, object]:
        if not isinstance(payload, dict) or set(payload) != TRANSACTION_FIELDS:
            raise DeploymentError("pending deployment transaction has unexpected fields")
        if (
            payload.get("version") != 1
            or payload.get("operation") != "deploy"
            or payload.get("project") != project
            or payload.get("phase") not in TRANSACTION_PHASES
        ):
            raise DeploymentError("pending deployment transaction identity is invalid")
        candidate = payload.get("candidate")
        original_pointer = payload.get("original_pointer")
        original_good = payload.get("original_good")
        original_previous = payload.get("original_previous")
        if not isinstance(candidate, str) or not SHA_RE.fullmatch(candidate):
            raise DeploymentError("pending deployment candidate is invalid")
        for label, value in (
            ("original pointer", original_pointer),
            ("original good release", original_good),
            ("original previous release", original_previous),
        ):
            if value is not None and (
                not isinstance(value, str) or not SHA_RE.fullmatch(value)
            ):
                raise DeploymentError(f"pending deployment {label} is invalid")
        if original_good is None and original_previous is not None:
            raise DeploymentError(
                "pending deployment previous release requires recorded state"
            )
        if original_good is not None and original_pointer != original_good:
            raise DeploymentError(
                "pending deployment pointer and recorded good release disagree"
            )
        return {
            "version": 1,
            "operation": "deploy",
            "project": project,
            "phase": payload["phase"],
            "candidate": candidate,
            "original_pointer": original_pointer,
            "original_good": original_good,
            "original_previous": original_previous,
        }

    def _read_transaction(self, project: str) -> dict[str, object] | None:
        path = self._transaction_path(project)
        if not _present(path):
            return None
        _trusted(
            path,
            self.trusted_uid,
            directory=False,
            private=True,
            label="pending deployment transaction",
        )
        try:
            payload = load_artifact_json(path)
        except ImageContractError as exc:
            raise DeploymentError(
                f"pending deployment transaction is invalid: {exc}"
            ) from exc
        return self._validate_transaction(payload, project=project)

    def _write_transaction_payload(
        self, project: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        canonical = self._validate_transaction(dict(payload), project=project)
        path = self._transaction_path(project)
        if _present(path):
            _trusted(
                path,
                self.trusted_uid,
                directory=False,
                private=True,
                label="pending deployment transaction",
            )
        _atomic_write(
            path,
            json.dumps(canonical, sort_keys=True, separators=(",", ":")) + "\n",
        )
        if self._read_transaction(project) != canonical:
            raise DeploymentError(
                "pending deployment transaction did not round-trip exactly"
            )
        return canonical

    def _begin_transaction(
        self,
        project: str,
        candidate: str,
        *,
        original_pointer: str | None,
        original_state: tuple[str, str | None] | None,
    ) -> dict[str, object]:
        if self._read_transaction(project) is not None:
            raise DeploymentError("pending deployment transaction was not reconciled")
        return self._write_transaction_payload(
            project,
            {
                "version": 1,
                "operation": "deploy",
                "project": project,
                "phase": "prepared",
                "candidate": candidate,
                "original_pointer": original_pointer,
                "original_good": original_state[0] if original_state else None,
                "original_previous": original_state[1] if original_state else None,
            },
        )

    def _advance_transaction(
        self, transaction: Mapping[str, object], phase: str
    ) -> dict[str, object]:
        project = str(transaction["project"])
        current = self._read_transaction(project)
        canonical = self._validate_transaction(dict(transaction), project=project)
        if current != canonical:
            raise DeploymentError("pending deployment transaction changed unexpectedly")
        try:
            current_index = TRANSACTION_PHASES.index(str(canonical["phase"]))
            next_index = TRANSACTION_PHASES.index(phase)
        except ValueError as exc:
            raise DeploymentError("pending deployment transaction phase is invalid") from exc
        if next_index != current_index + 1:
            raise DeploymentError("pending deployment transaction phase is not sequential")
        updated = dict(canonical)
        updated["phase"] = phase
        return self._write_transaction_payload(project, updated)

    def _clear_transaction(self, project: str) -> None:
        path = self._transaction_path(project)
        if not _present(path):
            return
        self._read_transaction(project)
        path.unlink()
        _fsync_directory(path.parent, required=True)

    def _pointer_release_for_reconciliation(self, project: str) -> Release | None:
        path = self._pointer_path(project)
        return self._read_pointer(project) if _present(path) else None

    def _cleanup_staging_temporary(self, project: str, sha: str) -> None:
        staging = self.release_root / sha / ".staged"
        if not _present(staging):
            return
        _trusted(
            staging,
            self.trusted_uid,
            directory=True,
            private=True,
            label="staging root",
        )
        removed = False
        for path in sorted(staging.glob(f".tmp-{project}-*")):
            if path.is_symlink() or not path.is_dir():
                raise DeploymentError(f"staging temporary has an unsafe type: {path}")
            _trusted_tree(path, self.trusted_uid)
            shutil.rmtree(path)
            removed = True
        if removed:
            _fsync_directory(staging, required=True)

    @staticmethod
    def _transaction_original_state(
        transaction: Mapping[str, object],
    ) -> tuple[str, str | None] | None:
        good = transaction["original_good"]
        if good is None:
            return None
        return str(good), (
            None
            if transaction["original_previous"] is None
            else str(transaction["original_previous"])
        )

    def _reconcile_pending_transaction(self, project: str) -> str:
        transaction = self._read_transaction(project)
        if transaction is None:
            return "none"
        candidate = str(transaction["candidate"])
        original_pointer = transaction["original_pointer"]
        original_pointer = None if original_pointer is None else str(original_pointer)
        original_state = self._transaction_original_state(transaction)
        next_state = (candidate, original_pointer)
        recorded_state = self._read_state(project)
        if recorded_state == next_state:
            outcome = "committed"
        elif recorded_state == original_state:
            outcome = "rolled-back"
        else:
            raise DeploymentError(
                "pending transaction cannot reconcile an unrelated deployment state"
            )

        pointer = self._pointer_release_for_reconciliation(project)
        pointer_sha = pointer.sha if pointer is not None else None
        if pointer_sha not in {None, original_pointer, candidate}:
            raise DeploymentError(
                "pending transaction cannot reconcile an unrelated current pointer"
            )

        if outcome == "committed":
            release = self._release(candidate, project)
            if pointer_sha != candidate:
                self._write_pointer(project, candidate)
            services = self._preflight(release, pull=False)
            self._activate(release, services, smoke=True)
            self._write_state(project, candidate, original_pointer)
            self._promote_release_artifact(release)
        else:
            phase = str(transaction["phase"])
            runtime_may_have_changed = (
                TRANSACTION_PHASES.index(phase)
                >= TRANSACTION_PHASES.index("pointer-written")
                or pointer_sha != original_pointer
            )
            if original_pointer is None:
                staged = self.release_root / candidate / ".staged" / project
                if runtime_may_have_changed and _present(staged):
                    failed = self._release(candidate, project)
                    self._checked(
                        self._compose(failed, "down", "--remove-orphans"),
                        cwd=failed.path,
                    )
                if pointer_sha is not None or runtime_may_have_changed:
                    self._remove_pointer(project)
            else:
                previous = self._release(original_pointer, project)
                if pointer_sha != original_pointer:
                    self._write_pointer(project, original_pointer)
                if runtime_may_have_changed:
                    services = self._preflight(previous, pull=False)
                    self._activate(previous, services, smoke=True)
            self._restore_recorded_state(project, original_state)

        self._cleanup_staging_temporary(project, candidate)
        self._clear_transaction(project)
        return outcome

    def reconcile(self, project: str = STACK_PROJECT) -> str:
        self._prepare_roots()
        with self.lock_factory(
            self.state_root / "locks" / f"{project}.lock", self.trusted_uid
        ):
            return self._reconcile_pending_transaction(project)

    def deploy(
        self,
        sha: str,
        *,
        t3_approval: ImageApproval | None = None,
    ) -> Release:
        if sha != sha.lower() or not SHA_RE.fullmatch(sha):
            raise DeploymentError(
                "release SHA must be exactly 40 or 64 lowercase hexadecimal characters"
            )
        project = STACK_PROJECT

        self._prepare_roots()
        with self.lock_factory(
            self.state_root / "locks" / f"{project}.lock", self.trusted_uid
        ):
            self._reconcile_pending_transaction(project)
            previous = self._load_current(project)
            previous_state = self._read_state(project)
            if previous is not None and previous.sha == sha and t3_approval is None:
                services = self._preflight(previous, pull=False)
                self._verify_running(previous, services, smoke=True)
                self._promote_release_artifact(previous)
                return previous
            artifacts, _candidate_source = self._select_homelab_artifacts(
                sha, t3_approval, current=previous
            )
            transaction: dict[str, object] | None = None
            try:
                transaction = self._begin_transaction(
                    project,
                    sha,
                    original_pointer=previous.sha if previous else None,
                    original_state=previous_state,
                )
                release = self._stage(sha, project, artifacts=artifacts)
                transaction = self._advance_transaction(transaction, "staged")
                services = self._preflight(release, pull=True)
                self._write_pointer(project, release.sha)
                transaction = self._advance_transaction(
                    transaction, "pointer-written"
                )
                self._activate(release, services, smoke=True)
                transaction = self._advance_transaction(
                    transaction, "runtime-healthy"
                )
                self._write_state(project, release.sha, previous.sha if previous else None)
                transaction = self._advance_transaction(
                    transaction, "state-committed"
                )
                self._promote_release_artifact(release)
                transaction = self._advance_transaction(
                    transaction, "artifact-promoted"
                )
                self._clear_transaction(project)
            except Exception as original:
                if self._read_transaction(project) is None:
                    raise
                try:
                    outcome = self._reconcile_pending_transaction(project)
                except Exception as rollback:
                    raise DeploymentError(
                        f"deployment failed ({original}); rollback also failed "
                        f"during pending transaction reconciliation ({rollback})"
                    ) from original
                if outcome == "committed":
                    return self._release(sha, project)
                if previous is None:
                    message = "deployment failed and the failed release was stopped"
                else:
                    message = "deployment failed and the previous release was restored"
                raise DeploymentError(f"{message}: {original}") from original
            return release

    def _prepare_roots(self) -> None:
        roots = (
            self.release_root,
            self.current_root,
            self.state_root,
            self.runtime_config_root,
        )
        for root in roots:
            _reject_symlink_components(root)
        for index, left in enumerate(roots):
            for right in roots[index + 1 :]:
                if _overlaps(left, right) or _overlaps(right, left):
                    raise DeploymentError(f"deployment roots must not overlap: {left}, {right}")

        _trusted(self.release_root, self.trusted_uid, directory=True, label="release root")
        _trusted(
            self.runtime_config_root,
            self.trusted_uid,
            directory=True,
            private=True,
            label="runtime config root",
        )
        for root, label in (
            (self.current_root, "current pointer root"),
            (self.state_root, "deployment state root"),
        ):
            if not _present(root):
                _trusted(root.parent, self.trusted_uid, directory=True, label="deployment parent")
                root.mkdir(mode=0o700)
            _trusted(root, self.trusted_uid, directory=True, private=True, label=label)

        locks = self.state_root / "locks"
        if not _present(locks):
            locks.mkdir(mode=0o700)
        _trusted(locks, self.trusted_uid, directory=True, private=True, label="lock root")

    def _stage(
        self,
        sha: str,
        project: str,
        *,
        artifacts: Mapping[str, object] | None = None,
    ) -> Release:
        uploaded = self.release_root / sha
        source = uploaded / "apps" / "compose" / project
        runtime = self.runtime_config_root / project
        _trusted(uploaded, self.trusted_uid, directory=True, label="uploaded release")
        _trusted_tree(source, self.trusted_uid)
        _trusted_tree(runtime, self.trusted_uid, private=True)
        _trusted(
            source / "compose.yml",
            self.trusted_uid,
            directory=False,
            label="Compose manifest",
        )
        if project == STACK_PROJECT:
            if artifacts is None:
                raise DeploymentError("homelab release has no T3 artifact metadata")
            try:
                artifacts = validate_deployment_payload(
                    artifacts,
                    expected_artifact=T3_ARTIFACT,
                    expected_deployment_source_sha=sha,
                    expected_image=T3_IMAGE,
                )
            except ImageContractError as exc:
                raise DeploymentError(f"T3 release metadata is invalid: {exc}") from exc
        elif artifacts is not None:
            raise DeploymentError("legacy release cannot receive OCI artifact metadata")
        for reserved in (source / ".env", source / ".homelab"):
            if _present(reserved):
                raise DeploymentError(f"uploaded release uses reserved path: {reserved.name}")

        runtime_env = runtime / ".env"
        _trusted(
            runtime_env,
            self.trusted_uid,
            directory=False,
            private=True,
            label="runtime .env",
        )
        runtime_files = runtime / "files"
        if _present(runtime_files):
            _trusted_tree(runtime_files, self.trusted_uid, private=True)
        smoke = runtime / "smoke"
        metadata = _trusted(
            smoke,
            self.trusted_uid,
            directory=False,
            private=True,
            label="required smoke executable",
        )
        if POSIX and not metadata.st_mode & stat.S_IXUSR:
            raise DeploymentError(f"smoke command is not owner-executable: {smoke}")

        staging = uploaded / ".staged"
        if not _present(staging):
            staging.mkdir(mode=0o700)
        _trusted(staging, self.trusted_uid, directory=True, private=True, label="staging root")
        target = staging / project
        release = Release(sha, project, target)
        if _present(target):
            self._validate_release(release)
            if artifacts is not None and self._load_release_artifacts(release) != artifacts:
                raise DeploymentError(
                    "immutable staged release has different T3 artifact metadata"
                )
            return release

        temporary = staging / f".tmp-{project}-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, temporary, symlinks=False, copy_function=shutil.copy2)
            shutil.copy2(runtime_env, temporary / ".env", follow_symlinks=False)
            if _present(runtime_files):
                self._merge_overlay(runtime_files, temporary)
            metadata_root = temporary / ".homelab"
            metadata_root.mkdir(mode=0o700)
            shutil.copy2(smoke, metadata_root / "smoke", follow_symlinks=False)
            if artifacts is not None:
                artifact_path = metadata_root / ARTIFACT_METADATA_NAME
                artifact_path.write_text(
                    json.dumps(artifacts, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                os.chmod(artifact_path, 0o600)
                immutable_ref = artifacts["artifacts"][T3_ARTIFACT]["ref"]
                artifact_env = metadata_root / ARTIFACT_ENV_NAME
                artifact_env.write_text(
                    f"T3CODE_IMAGE_REF={immutable_ref}\n",
                    encoding="utf-8",
                    newline="\n",
                )
                os.chmod(artifact_env, 0o600)
            manifest = metadata_root / "release.json"
            manifest.write_text(
                json.dumps({"project": project, "sha": sha}, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.chmod(manifest, 0o600)
            os.replace(temporary, target)
            _fsync_directory(staging, required=True)
        except BaseException:
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise
        self._validate_release(release)
        return release

    def _merge_overlay(self, source: Path, destination: Path) -> None:
        for root, directories, files in os.walk(source, topdown=True, followlinks=False):
            directories.sort()
            files.sort()
            root_path = Path(root)
            relative_root = root_path.relative_to(source)
            for name in directories:
                relative = relative_root / name
                self._check_overlay(relative)
                target = destination / relative
                if _present(target) and not target.is_dir():
                    raise DeploymentError(f"runtime directory collides with release: {relative}")
                target.mkdir(mode=0o700, exist_ok=True)
            for name in files:
                relative = relative_root / name
                self._check_overlay(relative)
                target = destination / relative
                if _present(target):
                    raise DeploymentError(f"runtime file collides with release: {relative}")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(root_path / name, target, follow_symlinks=False)

    @staticmethod
    def _check_overlay(relative: Path) -> None:
        if not relative.parts or relative.parts[0] in RESERVED_OVERLAYS:
            raise DeploymentError(f"runtime overlay uses reserved path: {relative}")

    def _load_release_artifacts(self, release: Release) -> dict[str, object]:
        if release.project != STACK_PROJECT:
            raise DeploymentError("OCI artifact metadata exists only for homelab")
        metadata_root = release.path / ".homelab"
        artifact_path = metadata_root / ARTIFACT_METADATA_NAME
        artifact_env = metadata_root / ARTIFACT_ENV_NAME
        for path, label in (
            (artifact_path, "release OCI artifact metadata"),
            (artifact_env, "release OCI artifact environment"),
        ):
            _trusted(
                path,
                self.trusted_uid,
                directory=False,
                private=True,
                label=label,
            )
        try:
            payload = load_artifact_json(artifact_path)
            canonical = validate_deployment_payload(
                payload,
                expected_artifact=T3_ARTIFACT,
                expected_deployment_source_sha=release.sha,
                expected_image=T3_IMAGE,
            )
        except ImageContractError as exc:
            raise DeploymentError(f"release T3 artifact metadata is invalid: {exc}") from exc
        immutable_ref = canonical["artifacts"][T3_ARTIFACT]["ref"]
        expected_env = f"T3CODE_IMAGE_REF={immutable_ref}\n"
        try:
            actual_env = artifact_env.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DeploymentError("cannot read release T3 artifact environment") from exc
        if actual_env != expected_env:
            raise DeploymentError(
                "release T3 artifact environment does not match its metadata"
            )
        return canonical

    def _validate_release(self, release: Release) -> None:
        expected = self.release_root / release.sha / ".staged" / release.project
        if release.path != expected or not SHA_RE.fullmatch(release.sha):
            raise DeploymentError("staged release identity is invalid")
        _trusted_tree(release.path, self.trusted_uid)
        _trusted(
            release.path / ".env",
            self.trusted_uid,
            directory=False,
            private=True,
            label="staged .env",
        )
        manifest = release.path / ".homelab" / "release.json"
        _trusted(
            manifest,
            self.trusted_uid,
            directory=False,
            private=True,
            label="release manifest",
        )
        try:
            recorded = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError(f"release manifest is invalid: {manifest}") from exc
        if recorded != {"project": release.project, "sha": release.sha}:
            raise DeploymentError("release manifest does not match its staged path")
        if release.project == STACK_PROJECT:
            smoke = release.path / ".homelab" / "smoke"
            metadata = _trusted(
                smoke,
                self.trusted_uid,
                directory=False,
                private=True,
                label="staged smoke executable",
            )
            if POSIX and not metadata.st_mode & stat.S_IXUSR:
                raise DeploymentError(
                    f"staged smoke command is not owner-executable: {smoke}"
                )
            self._load_release_artifacts(release)
        else:
            for name in (ARTIFACT_METADATA_NAME, ARTIFACT_ENV_NAME):
                if _present(release.path / ".homelab" / name):
                    raise DeploymentError("legacy release contains OCI artifact metadata")

    def _compose(self, release: Release, *arguments: str) -> list[str]:
        command = [
            self.docker_command,
            "compose",
            "--project-name",
            release.project,
            "--project-directory",
            str(release.path),
            "--env-file",
            str(release.path / ".env"),
        ]
        if release.project == STACK_PROJECT:
            command.extend(
                [
                    "--env-file",
                    str(release.path / ".homelab" / ARTIFACT_ENV_NAME),
                ]
            )
        command.extend([
            "-f",
            str(release.path / "compose.yml"),
            *arguments,
        ])
        return command

    def _checked(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> str:
        result = self.runner.run(argv, cwd=cwd, env=env)
        if result.returncode:
            raise DeploymentError(
                f"{' '.join(argv[:2])} failed with exit status {result.returncode}"
            )
        return result.stdout

    def _preflight(self, release: Release, *, pull: bool) -> tuple[str, ...]:
        expected_t3_ref = None
        if release.project == STACK_PROJECT:
            expected_t3_ref = self._load_release_artifacts(release)["artifacts"][
                T3_ARTIFACT
            ]["ref"]
            try:
                validate_immutable_ref(expected_t3_ref, expected_image=T3_IMAGE)
            except ImageContractError as exc:
                raise DeploymentError(f"release T3 image reference is invalid: {exc}") from exc
        self._checked(self._compose(release, "config", "--quiet"), cwd=release.path)
        output = self._checked(
            self._compose(release, "config", "--services"), cwd=release.path
        )
        services = tuple(line.strip() for line in output.splitlines() if line.strip())
        if not services or len(services) != len(set(services)):
            raise DeploymentError("Compose returned an empty or duplicate service list")
        for service in services:
            if not SERVICE_RE.fullmatch(service):
                raise DeploymentError(f"Compose returned an invalid service: {service!r}")
        if expected_t3_ref is not None:
            image_output = self._checked(
                self._compose(release, "config", "--images"), cwd=release.path
            )
            images = [line.strip() for line in image_output.splitlines() if line.strip()]
            if images.count(expected_t3_ref) != 1:
                raise DeploymentError(
                    "resolved Compose model does not contain the recorded T3 digest exactly once"
                )
        if pull:
            self._checked(
                self._compose(release, "pull", "--ignore-buildable"), cwd=release.path
            )
        return services

    def _activate(
        self, release: Release, services: tuple[str, ...], *, smoke: bool
    ) -> None:
        self._checked(
            self._compose(
                release,
                "up",
                "-d",
                "--wait",
                "--remove-orphans",
                "--no-build",
            ),
            cwd=release.path,
        )
        self._verify_running(release, services, smoke=smoke)

    def _verify_running(
        self, release: Release, services: tuple[str, ...], *, smoke: bool
    ) -> None:
        output = self._checked(
            self._compose(release, "ps", "--services", "--status", "running"),
            cwd=release.path,
        )
        running = {line.strip() for line in output.splitlines() if line.strip()}
        if running != set(services):
            raise DeploymentError("running services do not match the Compose service set")

        inspect_format = (
            "{{.State.Status}} "
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
        )
        for service in services:
            output = self._checked(
                self._compose(release, "ps", "-q", service), cwd=release.path
            )
            containers = [line.strip() for line in output.splitlines() if line.strip()]
            if len(containers) != 1 or not CONTAINER_RE.fullmatch(containers[0]):
                raise DeploymentError(f"service {service!r} has no unique valid container")
            state = self._checked(
                [
                    self.docker_command,
                    "inspect",
                    "--format",
                    inspect_format,
                    containers[0],
                ],
                cwd=release.path,
            ).strip()
            if state == "running healthy":
                continue
            if state != "running none" or service not in PROCESS_LIVENESS_SERVICES:
                raise DeploymentError(
                    f"service {service!r} lacks a passing mandatory health gate"
                )
            restart_count = self._checked(
                [
                    self.docker_command,
                    "inspect",
                    "--format",
                    "{{.RestartCount}}",
                    containers[0],
                ],
                cwd=release.path,
            ).strip()
            if restart_count != "0":
                raise DeploymentError(
                    f"service {service!r} failed its process stability gate"
                )

        smoke_path = release.path / ".homelab" / "smoke"
        if smoke and release.project == STACK_PROJECT and not _present(smoke_path):
            raise DeploymentError("homelab release has no mandatory smoke contract")
        if smoke and _present(smoke_path):
            environment = os.environ.copy()
            environment.update(
                {
                    "HOMELAB_PROJECT": release.project,
                    "HOMELAB_PROJECT_DIR": str(release.path),
                    "HOMELAB_RELEASE_SHA": release.sha,
                }
            )
            self._checked([str(smoke_path)], cwd=release.path, env=environment)

    def _verify_stopped(self, release: Release) -> None:
        output = self._checked(
            self._compose(release, "ps", "--services", "--status", "running"),
            cwd=release.path,
        )
        if any(line.strip() for line in output.splitlines()):
            raise DeploymentError(f"project {release.project!r} is not fully stopped")

    def _stop(self, release: Release) -> None:
        self._checked(
            self._compose(release, "down", "--remove-orphans"), cwd=release.path
        )

    def _pointer_path(self, project: str) -> Path:
        return self.current_root / project

    def _state_path(self, project: str) -> Path:
        return self.state_root / f"{project}.json"

    def _read_pointer(self, project: str) -> Release:
        path = self._pointer_path(project)
        _reject_symlink_components(path.parent)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise DeploymentError(f"current pointer is unavailable: {path}") from exc
        if not stat.S_ISLNK(metadata.st_mode):
            raise DeploymentError(f"current pointer is not a symlink: {path}")
        if metadata.st_uid != self.trusted_uid:
            raise DeploymentError(f"current pointer is not owned by trusted uid: {path}")
        try:
            target = Path(os.readlink(path))
        except OSError as exc:
            raise DeploymentError(f"cannot read current pointer: {path}") from exc
        if not target.is_absolute() or ".." in target.parts:
            raise DeploymentError("current pointer target must be absolute and traversal-free")
        try:
            parts = target.relative_to(self.release_root).parts
        except ValueError as exc:
            raise DeploymentError("current pointer target is outside the release root") from exc
        if len(parts) != 3 or parts[1:] != (".staged", project):
            raise DeploymentError("current pointer target does not use the staged project layout")
        sha = parts[0]
        if not SHA_RE.fullmatch(sha):
            raise DeploymentError("current pointer target has an invalid release SHA")
        release = self._release(sha, project)
        if release.path != target:
            raise DeploymentError("current pointer target does not match its release identity")
        return release

    def _read_state(self, project: str) -> tuple[str, str | None] | None:
        state_path = self._state_path(project)
        if not _present(state_path):
            return None
        _trusted(
            state_path,
            self.trusted_uid,
            directory=False,
            private=True,
            label="deployment state",
        )
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError(f"deployment state is invalid: {state_path}") from exc
        if set(state) != {"version", "project", "good", "previous"}:
            raise DeploymentError("deployment state has unexpected fields")
        good, previous = state["good"], state["previous"]
        if (
            state["version"] != 1
            or state["project"] != project
            or not isinstance(good, str)
            or not SHA_RE.fullmatch(good)
            or (previous is not None and (not isinstance(previous, str) or not SHA_RE.fullmatch(previous)))
        ):
            raise DeploymentError("deployment state identity is invalid")
        return good, previous

    def _load_current(self, project: str) -> Release | None:
        pointer_path = self._pointer_path(project)
        pointer = self._read_pointer(project) if _present(pointer_path) else None
        state = self._read_state(project)
        if state is None:
            return pointer
        good, _previous = state
        if pointer is None or pointer.sha != good:
            raise DeploymentError("current pointer and recorded good release disagree")
        return pointer

    def _snapshot(self, project: str, *, require_state: bool) -> ProjectSnapshot:
        release = self._load_current(project)
        state = self._read_state(project)
        if release is None or (require_state and state is None):
            raise DeploymentError(
                f"project {project!r} has no trustworthy current release and state"
            )
        if state is None:
            previous = None
        else:
            good, previous = state
            if good != release.sha:
                raise DeploymentError("current pointer and recorded good release disagree")
        return ProjectSnapshot(release=release, previous=previous)

    def _release(self, sha: str, project: str) -> Release:
        release = Release(sha, project, self.release_root / sha / ".staged" / project)
        self._validate_release(release)
        return release

    def _write_pointer(self, project: str, sha: str) -> None:
        path = self._pointer_path(project)
        if _present(path):
            self._read_pointer(project)
        release = self._release(sha, project)
        _atomic_symlink(path, release.path)

    def _remove_pointer(self, project: str) -> None:
        path = self._pointer_path(project)
        if not _present(path):
            return
        self._read_pointer(project)
        path.unlink()
        _fsync_directory(path.parent, required=True)

    def _remove_state(self, project: str) -> None:
        path = self._state_path(project)
        if not _present(path):
            return
        _trusted(
            path,
            self.trusted_uid,
            directory=False,
            private=True,
            label="deployment state",
        )
        path.unlink()
        _fsync_directory(path.parent, required=True)

    def _require_project_artifacts_absent(self, project: str) -> None:
        if _present(self._pointer_path(project)) or _present(self._state_path(project)):
            raise DeploymentError(
                f"partial or ambiguous prior {project} activation is present"
            )

    def _write_state(self, project: str, good: str, previous: str | None) -> None:
        path = self._state_path(project)
        if _present(path):
            _trusted(
                path,
                self.trusted_uid,
                directory=False,
                private=True,
                label="deployment state",
            )
        content = json.dumps(
            {"version": 1, "project": project, "good": good, "previous": previous},
            sort_keys=True,
            separators=(",", ":"),
        )
        _atomic_write(path, content + "\n")

    def _restore_snapshot(self, snapshot: ProjectSnapshot) -> None:
        project = snapshot.release.project
        self._write_pointer(project, snapshot.release.sha)
        self._write_state(project, snapshot.release.sha, snapshot.previous)
        services = self._preflight(snapshot.release, pull=False)
        self._activate(snapshot.release, services, smoke=True)

    def _rollback(
        self,
        project: str,
        *,
        failed: Release,
        previous: Release | None,
    ) -> None:
        if previous is None:
            self._checked(
                self._compose(failed, "down", "--remove-orphans"), cwd=failed.path
            )
            self._remove_pointer(project)
            return
        self._validate_release(previous)
        self._write_pointer(project, previous.sha)
        services = self._preflight(previous, pull=False)
        self._activate(previous, services, smoke=True)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", help="exact 40- or 64-character hexadecimal Git SHA")
    _add_root_arguments(parser)
    _add_t3_approval_arguments(parser)
    return parser


def _add_root_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-config-root", required=True, type=Path)
    parser.add_argument("--release-root", type=Path, default=Path("/opt/homelab/releases"))
    parser.add_argument("--current-root", type=Path, default=Path("/opt/homelab/current"))
    parser.add_argument(
        "--state-root", type=Path, default=Path("/opt/homelab/deploy-state")
    )


def _add_t3_approval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--t3-source-sha")
    parser.add_argument("--t3-image-ref")


def _deployer_from_args(args: argparse.Namespace) -> ComposeReleaseDeployer:
    return ComposeReleaseDeployer(
        release_root=args.release_root,
        current_root=args.current_root,
        state_root=args.state_root,
        runtime_config_root=args.runtime_config_root,
        trusted_uid=0,
    )


def _t3_approval_from_args(args: argparse.Namespace) -> ImageApproval | None:
    source_sha = getattr(args, "t3_source_sha", None)
    image_ref = getattr(args, "t3_image_ref", None)
    if (source_sha is None) != (image_ref is None):
        raise DeploymentError(
            "--t3-source-sha and --t3-image-ref must be supplied together"
        )
    if source_sha is None:
        return None
    try:
        source_sha = validate_source_sha(source_sha, name="T3 artifact source SHA")
        image_ref = validate_immutable_ref(image_ref, expected_image=T3_IMAGE)
    except ImageContractError as exc:
        raise DeploymentError(f"T3 artifact approval is invalid: {exc}") from exc
    return ImageApproval(source_sha=source_sha, ref=image_ref)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("Compose release deployment must run as root", file=sys.stderr)
        return 1
    deployer = _deployer_from_args(args)
    try:
        t3_approval = _t3_approval_from_args(args)
        release = deployer.deploy(args.sha, t3_approval=t3_approval)
    except (DeploymentError, OSError) as exc:
        print(f"Compose release deployment failed: {exc}", file=sys.stderr)
        return 1
    print(f"Activated {release.project} at {release.sha}: {release.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
