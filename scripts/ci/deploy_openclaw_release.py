#!/usr/bin/env python3
"""Activate, audit, and roll back one immutable OpenClaw release on its LXC.

The production host never builds an image or installs JavaScript dependencies.  It
accepts only a canonical release manifest plus two content-addressed tar bundles,
pulls the exact OCI digests, and changes the active pointer only after readiness
and one authenticated smoke request have succeeded.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable, Iterator, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import grp
except ImportError:  # pragma: no cover - Windows test runner
    grp = None  # type: ignore[assignment]

try:  # Package import in tests; sibling import when executed as a script.
    from .openclaw_release import (
        ContractError,
        atomic_write,
        canonical_json_bytes,
        file_sha256,
        load_json,
        validate_release_manifest,
    )
except ImportError:  # pragma: no cover - exercised by CLI smoke tests
    from openclaw_release import (
        ContractError,
        atomic_write,
        canonical_json_bytes,
        file_sha256,
        load_json,
        validate_release_manifest,
    )


class DeploymentError(RuntimeError):
    """The requested immutable release could not be proven healthy."""


TRANSACTION_VERSION = 2
TRANSACTION_PHASES = frozenset(
    {"prepared", "committing-target", "restoring-original"}
)


class CommandRunner(Protocol):
    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]: ...


class Probe(Protocol):
    def __call__(self, url: str, token: str | None) -> tuple[int, str]: ...


def run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def http_probe(url: str, token: str | None) -> tuple[int, str]:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - operator URL
            return response.status, response.read(4096).decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read(4096).decode("utf-8", "replace")
    except (OSError, URLError) as exc:
        return 0, str(exc)


def _check_command(result: subprocess.CompletedProcess[str], *, action: str) -> None:
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise DeploymentError(f"{action} failed: {detail}")


def _safe_extract(
    archive: Path,
    destination: Path,
    *,
    file_mode: int = 0o600,
    directory_mode: int = 0o700,
    owner_uid: int = 0,
    owner_gid: int = 0,
) -> None:
    """Extract only canonical regular files/directories without link traversal."""

    seen: set[str] = set()
    casefolded: set[str] = set()
    if destination.is_symlink():
        raise DeploymentError("bundle destination must not be a symlink")
    try:
        handle = tarfile.open(archive, mode="r:")
    except (OSError, tarfile.TarError) as exc:
        raise DeploymentError(f"cannot open release bundle {archive}: {exc}") from exc
    with handle:
        for member in handle.getmembers():
            pure = PurePosixPath(member.name)
            canonical = pure.as_posix().rstrip("/")
            if (
                not canonical
                or pure.is_absolute()
                or ".." in pure.parts
                or member.name.startswith(("/", "\\"))
                or "\\" in member.name
            ):
                raise DeploymentError(f"bundle contains a non-canonical path: {member.name}")
            folded = canonical.casefold()
            if canonical in seen or folded in casefolded:
                raise DeploymentError(f"bundle contains a duplicate path: {canonical}")
            seen.add(canonical)
            casefolded.add(folded)
            if not (member.isdir() or member.isreg()):
                raise DeploymentError(f"bundle contains a forbidden member: {canonical}")
            target = destination.joinpath(*pure.parts)
            parent = destination
            for part in pure.parts[:-1]:
                parent /= part
                if parent.is_symlink():
                    raise DeploymentError(f"bundle path crosses a symlink: {canonical}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                if target.is_symlink() or not target.is_dir():
                    raise DeploymentError(f"bundle directory is not a directory: {canonical}")
                os.chmod(target, directory_mode)
                if os.name != "nt":
                    os.chown(target, owner_uid, owner_gid)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = handle.extractfile(member)
            if source is None:
                raise DeploymentError(f"bundle member cannot be read: {canonical}")
            descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, file_mode)
                if os.name != "nt":
                    os.chown(temporary, owner_uid, owner_gid)
                os.replace(temporary, target)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _attest_extracted_tree(
    archive: Path,
    destination: Path,
    *,
    label: str,
    file_mode: int,
    directory_mode: int,
    owner_uid: int = 0,
    owner_gid: int = 0,
) -> None:
    """Prove that a materialized tree is exactly the retained canonical tar."""

    expected: dict[str, tuple[str, int, str | None]] = {}
    casefolded: set[str] = set()
    try:
        handle = tarfile.open(archive, mode="r:")
    except (OSError, tarfile.TarError) as exc:
        raise DeploymentError(f"cannot open retained {label} bundle: {exc}") from exc
    try:
        with handle:
            for member in handle.getmembers():
                pure = PurePosixPath(member.name)
                canonical = pure.as_posix().rstrip("/")
                folded = canonical.casefold()
                if (
                    not canonical
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or member.name.startswith(("/", "\\"))
                    or "\\" in member.name
                    or canonical in expected
                    or folded in casefolded
                    or not (member.isdir() or member.isreg())
                ):
                    raise DeploymentError(
                        f"retained {label} bundle contains an unsafe member: {member.name}"
                    )
                casefolded.add(folded)
                if member.isdir():
                    expected[canonical] = ("directory", 0, None)
                    continue
                source = handle.extractfile(member)
                if source is None:
                    raise DeploymentError(
                        f"retained {label} bundle member cannot be read: {canonical}"
                    )
                digest = hashlib.sha256()
                size = 0
                with source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(block)
                        digest.update(block)
                if size != member.size:
                    raise DeploymentError(
                        f"retained {label} bundle member size is inconsistent: {canonical}"
                    )
                expected[canonical] = ("file", size, digest.hexdigest())
    except (OSError, tarfile.TarError) as exc:
        raise DeploymentError(f"cannot attest retained {label} bundle: {exc}") from exc

    if destination.is_symlink() or not destination.is_dir():
        raise DeploymentError(f"materialized {label} root must be a real directory")
    root_metadata = destination.lstat()
    if os.name != "nt" and (
        root_metadata.st_uid != owner_uid
        or root_metadata.st_gid != owner_gid
        or stat.S_IMODE(root_metadata.st_mode) != directory_mode
    ):
        raise DeploymentError(f"materialized {label} root ownership or mode is invalid")

    actual: dict[str, tuple[str, int, str | None]] = {}
    for root, directories, files in os.walk(destination, topdown=True, followlinks=False):
        base = Path(root)
        directories.sort()
        files.sort()
        for name in (*directories, *files):
            path = base / name
            relative = path.relative_to(destination).as_posix()
            metadata = path.lstat()
            if path.is_symlink():
                raise DeploymentError(f"materialized {label} tree contains a symlink")
            if stat.S_ISDIR(metadata.st_mode):
                kind, size, digest = "directory", 0, None
                expected_mode = directory_mode
            elif stat.S_ISREG(metadata.st_mode):
                kind, size, digest = "file", metadata.st_size, file_sha256(path)
                expected_mode = file_mode
            else:
                raise DeploymentError(
                    f"materialized {label} tree contains a non-regular entry"
                )
            if os.name != "nt" and (
                metadata.st_uid != owner_uid
                or metadata.st_gid != owner_gid
                or stat.S_IMODE(metadata.st_mode) != expected_mode
            ):
                raise DeploymentError(
                    f"materialized {label} entry ownership or mode is invalid: {relative}"
                )
            actual[relative] = (kind, size, digest)

    if actual != expected:
        missing = sorted(set(expected).difference(actual))
        unexpected = sorted(set(actual).difference(expected))
        changed = sorted(
            path for path in set(actual).intersection(expected)
            if actual[path] != expected[path]
        )
        detail = ", ".join(
            part
            for part in (
                f"missing={missing}" if missing else "",
                f"unexpected={unexpected}" if unexpected else "",
                f"changed={changed}" if changed else "",
            )
            if part
        )
        raise DeploymentError(
            f"materialized {label} tree differs from its immutable bundle: {detail}"
        )


def _env_value(name: str, value: str) -> str:
    if not value or any(character in value for character in "\r\n\x00"):
        raise DeploymentError(f"{name} is not a valid environment-file value")
    return value


@contextmanager
def deployment_lock(state_root: Path, trusted_uid: int = 0) -> Iterator[None]:
    state_root.mkdir(parents=True, exist_ok=True)
    lock = state_root / ".deployment.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock, flags, 0o600)
    except OSError as exc:
        raise DeploymentError(f"cannot open trusted deployment lock: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or (
            os.name != "nt" and metadata.st_mode & 0o077
        ):
            raise DeploymentError("deployment lock must be a private regular file")
        if os.name != "nt" and metadata.st_uid != trusted_uid:
            raise DeploymentError("deployment lock must remain trusted-owner controlled")
        if os.name == "nt":  # Test portability; production uses POSIX flock below.
            import msvcrt

            if metadata.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise DeploymentError("another OpenClaw deployment holds the state lock") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise DeploymentError(
                        "another OpenClaw deployment holds the state lock"
                    ) from exc
                raise
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def identity_can_read(metadata: os.stat_result, *, uid: int, gids: set[int]) -> bool:
    """Model the kernel's owner/group/other read decision for a regular file."""

    if uid == 0:
        return True
    if metadata.st_uid == uid:
        return bool(metadata.st_mode & stat.S_IRUSR)
    if metadata.st_gid in gids:
        return bool(metadata.st_mode & stat.S_IRGRP)
    return bool(metadata.st_mode & stat.S_IROTH)


class ReleaseDeployer:
    def __init__(
        self,
        *,
        install_root: Path,
        secret_root: Path,
        readiness_url: str,
        smoke_url: str,
        runner: CommandRunner = run_command,
        probe: Probe = http_probe,
        readiness_attempts: int = 30,
        readiness_delay: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        gateway_uid: int = 1000,
        gateway_gid: int = 1000,
        docker_gid: int | None = None,
        validate_host_contract: bool = True,
        trusted_uid: int = 0,
        trusted_gid: int = 0,
    ) -> None:
        if trusted_uid < 0 or trusted_gid < 0:
            raise DeploymentError("trusted deployment identity must be nonnegative")
        self.install_root = Path(install_root)
        self.release_root = self.install_root / "releases"
        self.state_root = self.install_root / "state"
        self.secret_root = Path(secret_root)
        self.readiness_url = readiness_url
        self.smoke_url = smoke_url
        self.runner = runner
        self.probe = probe
        self.readiness_attempts = readiness_attempts
        self.readiness_delay = readiness_delay
        self.sleep = sleep
        self.gateway_uid = gateway_uid
        self.gateway_gid = gateway_gid
        self.docker_gid = docker_gid
        self.validate_host_contract = validate_host_contract
        self.trusted_uid = trusted_uid
        self.trusted_gid = trusted_gid

    @property
    def release_state_path(self) -> Path:
        return self.state_root / "release-state.json"

    @property
    def audit_path(self) -> Path:
        return self.state_root / "audit.jsonl"

    @property
    def transaction_path(self) -> Path:
        return self.state_root / "pending-transaction.json"

    @staticmethod
    def _validated_state(payload: Any, *, label: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"version", "current", "previous"}:
            raise DeploymentError(f"{label} must contain only version/current/previous")
        if payload["version"] != 1:
            raise DeploymentError(f"unsupported {label} version")
        validated: dict[str, Any] = {"version": 1}
        for name in ("current", "previous"):
            value = payload[name]
            if value is None:
                validated[name] = None
                continue
            try:
                validated[name] = validate_release_manifest(value)
            except ContractError as exc:
                raise DeploymentError(f"invalid {name} {label}: {exc}") from exc
        if (
            validated["current"] is not None
            and validated["previous"] is not None
            and validated["current"]["release_id"] == validated["previous"]["release_id"]
        ):
            raise DeploymentError(f"current and previous {label} must differ")
        if validated["current"] is None and validated["previous"] is not None:
            raise DeploymentError(f"previous {label} requires a current release")
        return validated

    def _load_state(self) -> dict[str, Any]:
        path = self.release_state_path
        if not path.exists():
            return {"version": 1, "current": None, "previous": None}
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise DeploymentError("release state must be a real regular file")
        if os.name != "nt" and (
            metadata.st_uid != self.trusted_uid
            or metadata.st_gid != self.trusted_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError("release state ownership or mode is invalid")
        try:
            payload = load_json(path)
        except ContractError as exc:
            raise DeploymentError(f"invalid release state {path}: {exc}") from exc
        return self._validated_state(payload, label="release state")

    def _write_state(self, state_payload: dict[str, Any]) -> None:
        # Round-trip validation before the one atomic pointer replacement.
        candidate = self._validated_state(state_payload, label="release state")
        atomic_write(
            self.release_state_path,
            canonical_json_bytes(candidate),
            mode=0o600,
        )

    def _validated_transaction(self, payload: Any) -> dict[str, Any]:
        common = {
            "version",
            "operation",
            "target",
            "original_state",
            "original_state_existed",
            "next_state",
        }
        if not isinstance(payload, dict):
            raise DeploymentError("pending transaction schema is invalid")
        # Version 1 had no recovery phase.  Treat it conservatively as prepared:
        # a host upgraded with an old journal restores the original release rather
        # than guessing whether a reverse operation had begun.
        if payload.get("version") == 1 and set(payload) == common:
            phase = "prepared"
        elif payload.get("version") == TRANSACTION_VERSION and set(payload) == common | {
            "phase"
        }:
            phase = payload["phase"]
        else:
            raise DeploymentError("pending transaction schema is invalid")
        if phase not in TRANSACTION_PHASES:
            raise DeploymentError("pending transaction phase is invalid")
        if payload["operation"] not in {"deploy", "rollback"}:
            raise DeploymentError("pending transaction operation is invalid")
        if not isinstance(payload["original_state_existed"], bool):
            raise DeploymentError("pending transaction state-existence flag is invalid")
        try:
            target = validate_release_manifest(payload["target"])
        except ContractError as exc:
            raise DeploymentError(f"pending transaction target is invalid: {exc}") from exc
        original = self._validated_state(payload["original_state"], label="original state")
        next_state = self._validated_state(payload["next_state"], label="next state")
        if next_state["current"] != target:
            raise DeploymentError("pending transaction target differs from next state")
        if original["current"] is not None and original["current"] == target:
            raise DeploymentError(
                "pending transaction target is already the original current release"
            )
        if not payload["original_state_existed"] and original != {
            "version": 1,
            "current": None,
            "previous": None,
        }:
            raise DeploymentError(
                "pending transaction claims a missing nonempty original state"
            )
        if payload["operation"] == "deploy":
            if next_state["previous"] != original["current"]:
                raise DeploymentError(
                    "deploy transaction does not preserve the original current release"
                )
        elif (
            original["current"] is None
            or original["previous"] is None
            or target != original["previous"]
            or next_state["previous"] != original["current"]
        ):
            raise DeploymentError("rollback transaction release relationship is invalid")
        return {
            "version": TRANSACTION_VERSION,
            "phase": phase,
            "operation": payload["operation"],
            "target": target,
            "original_state": original,
            "original_state_existed": payload["original_state_existed"],
            "next_state": next_state,
        }

    def _load_transaction(self) -> dict[str, Any] | None:
        path = self.transaction_path
        if not path.exists():
            return None
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise DeploymentError("pending transaction must be a real regular file")
        if os.name != "nt" and (
            metadata.st_uid != self.trusted_uid
            or metadata.st_gid != self.trusted_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError("pending transaction ownership or mode is invalid")
        try:
            payload = load_json(path)
        except ContractError as exc:
            raise DeploymentError(f"invalid pending transaction {path}: {exc}") from exc
        return self._validated_transaction(payload)

    def _write_transaction_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate = self._validated_transaction(payload)
        atomic_write(
            self.transaction_path,
            canonical_json_bytes(candidate),
            mode=0o600,
        )
        if self._load_transaction() != candidate:
            raise DeploymentError("pending transaction did not round-trip exactly")
        return candidate

    def _write_transaction(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        original_state: dict[str, Any],
        original_state_existed: bool,
        next_state: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "version": TRANSACTION_VERSION,
            "phase": "prepared",
            "operation": operation,
            "target": validate_release_manifest(target),
            "original_state": self._validated_state(original_state, label="original state"),
            "original_state_existed": original_state_existed,
            "next_state": self._validated_state(next_state, label="next state"),
        }
        if payload["next_state"]["current"] != payload["target"]:
            raise DeploymentError("pending transaction target differs from next state")
        # The journal's atomic write and parent fsync must finish before Compose
        # is allowed to switch the live Gateway.
        return self._write_transaction_payload(payload)

    def _set_transaction_phase(
        self, transaction: dict[str, Any], phase: str
    ) -> dict[str, Any]:
        current = self._validated_transaction(transaction)
        if phase not in TRANSACTION_PHASES:
            raise DeploymentError("pending transaction phase is invalid")
        allowed = {
            "prepared": {"prepared", "committing-target", "restoring-original"},
            "committing-target": {"committing-target", "restoring-original"},
            "restoring-original": {"restoring-original"},
        }
        if phase not in allowed[current["phase"]]:
            raise DeploymentError(
                f"invalid transaction phase transition: {current['phase']} -> {phase}"
            )
        updated = dict(current)
        updated["phase"] = phase
        return self._write_transaction_payload(updated)

    def _clear_transaction(self) -> None:
        path = self.transaction_path
        if not path.exists():
            return
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise DeploymentError("cannot clear an unsafe pending transaction")
        if os.name != "nt" and (
            metadata.st_uid != self.trusted_uid
            or metadata.st_gid != self.trusted_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError("pending transaction ownership or mode is invalid")
        path.unlink()
        _fsync_directory(self.state_root)

    def _finish_original_restore(self, transaction: dict[str, Any]) -> None:
        transaction = self._validated_transaction(transaction)
        if transaction["phase"] != "restoring-original":
            raise DeploymentError("original restore requires the restoring-original phase")
        target = transaction["target"]
        original_state = transaction["original_state"]
        original = original_state["current"]
        if original is None:
            self._verify_materialized(target)
            self._compose(target, "down")
            self._verify_stopped(target)
        else:
            # A previously running exact digest is already local.  Recovery must
            # not become unavailable merely because its registry is unreachable.
            self._activate_existing(original, pull=False)
        self._restore_state(
            original_state,
            existed=transaction["original_state_existed"],
        )
        if self._load_state() != original_state:
            raise DeploymentError("restored release state did not round-trip exactly")

    def _reconcile_pending_transaction(self) -> None:
        transaction = self._load_transaction()
        if transaction is None:
            return
        target = transaction["target"]
        next_state = transaction["next_state"]
        original_state = transaction["original_state"]
        try:
            recorded_state = self._load_state()
        except DeploymentError:
            recorded_state = None

        commit_error: BaseException | None = None
        if transaction["phase"] == "committing-target" and recorded_state == next_state:
            # A crash after the state commit is a completed activation only if
            # exact materialized artifacts, local digests, and the live
            # container identity still agree with that commit.
            try:
                self._verify_materialized(target)
                self._verify_images(target, pull=False)
                self._verify_running_release(target)
            except BaseException as exc:
                commit_error = exc
            else:
                self._record("pending-transaction-commit-reconciled", target)
                self._clear_transaction()
                return

        if transaction["phase"] != "restoring-original":
            transaction = self._set_transaction_phase(
                transaction, "restoring-original"
            )
        self._finish_original_restore(transaction)
        original = original_state["current"]
        self._record(
            "pending-transaction-rollback-reconciled",
            original or target,
            failed_release_id=target["release_id"],
            reason=(type(commit_error).__name__ if commit_error is not None else "incomplete"),
        )
        self._clear_transaction()

    def _restore_state(self, original: dict[str, Any], *, existed: bool) -> None:
        if existed:
            self._write_state(original)
            return
        path = self.release_state_path
        if not path.exists():
            return
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise DeploymentError("cannot remove unsafe release state during rollback")
        path.unlink()
        _fsync_directory(path.parent)

    def _prepare_trusted_roots(self) -> None:
        for path, mode in (
            (self.install_root, 0o755),
            (self.release_root, 0o755),
            (self.state_root, 0o700),
        ):
            if path.is_symlink():
                raise DeploymentError(f"trusted runtime root must not be a symlink: {path}")
            path.mkdir(parents=True, exist_ok=True, mode=mode)
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise DeploymentError(f"trusted runtime root must be a directory: {path}")
            if os.name != "nt" and (
                metadata.st_uid != self.trusted_uid
                or metadata.st_gid != self.trusted_gid
                or stat.S_IMODE(metadata.st_mode) != mode
            ):
                raise DeploymentError(f"trusted runtime root ownership or mode is invalid: {path}")

    def _expected_env_data(self, release: dict[str, Any]) -> bytes:
        release = validate_release_manifest(release)
        target = self.release_root / release["release_id"]
        environment = {
            "OPENCLAW_CONFIG_COMMIT": release["config"]["commit"],
            "OPENCLAW_CONFIG_ROOT": str(target / "config"),
            "OPENCLAW_CTF_REF": release["ctf"]["ref"],
            "OPENCLAW_GATEWAY_REF": release["gateway"]["ref"],
            "OPENCLAW_DOCKER_GID": str(self._docker_group_gid()),
            "OPENCLAW_RELEASE_ID": release["release_id"],
            "OPENCLAW_SECRET_ROOT": str(self.secret_root),
        }
        return "".join(
            f"{key}={_env_value(key, value)}\n"
            for key, value in sorted(environment.items())
        ).encode("utf-8")

    def _verify_materialized(self, release: dict[str, Any]) -> Path:
        try:
            release = validate_release_manifest(release)
        except ContractError as exc:
            raise DeploymentError(f"release manifest is invalid: {exc}") from exc
        target = self.release_root / release["release_id"]
        if target.is_symlink() or not target.is_dir():
            raise DeploymentError("materialized release must be a real directory")
        top_level = {path.name for path in target.iterdir()}
        if top_level != {"release.json", ".env", "artifacts", "runtime", "config"}:
            raise DeploymentError("materialized release has unexpected top-level entries")
        artifacts = target / "artifacts"
        if artifacts.is_symlink() or not artifacts.is_dir() or {
            path.name for path in artifacts.iterdir()
        } != {"runtime.tar", "config.tar"}:
            raise DeploymentError("materialized release artifact directory is invalid")
        if os.name != "nt":
            for path, mode, gid in (
                (target, 0o755, self.trusted_gid),
                (artifacts, 0o700, self.trusted_gid),
            ):
                metadata = path.lstat()
                if (
                    metadata.st_uid != self.trusted_uid
                    or metadata.st_gid != gid
                    or stat.S_IMODE(metadata.st_mode) != mode
                ):
                    raise DeploymentError(
                        "materialized release directory ownership or mode is invalid"
                    )
        required = {
            "release.json": (0o600, self.trusted_gid),
            ".env": (0o600, self.trusted_gid),
            "artifacts/runtime.tar": (0o600, self.trusted_gid),
            "artifacts/config.tar": (0o600, self.trusted_gid),
            "runtime/compose.yml": (0o644, self.trusted_gid),
            "config/config/openclaw.json": (0o440, self.gateway_gid),
        }
        for relative, (expected_mode, expected_gid) in required.items():
            path = target / relative
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise DeploymentError(f"release artifact is not a regular file: {relative}")
            if os.name != "nt" and (
                metadata.st_uid != self.trusted_uid
                or metadata.st_gid != expected_gid
                or stat.S_IMODE(metadata.st_mode) != expected_mode
            ):
                raise DeploymentError(f"release artifact ownership or mode is invalid: {relative}")
        if os.name != "nt":
            config_stat = (target / "config" / "config" / "openclaw.json").stat()
            if config_stat.st_gid != self.gateway_gid or not identity_can_read(
                config_stat, uid=self.gateway_uid, gids={self.gateway_gid}
            ):
                raise DeploymentError("Gateway UID cannot read exact config bundle")
        try:
            recorded = validate_release_manifest(load_json(target / "release.json"))
        except ContractError as exc:
            raise DeploymentError(f"materialized release manifest is invalid: {exc}") from exc
        if recorded != release:
            raise DeploymentError("materialized release manifest differs from active pointer")
        if (target / "release.json").read_bytes() != canonical_json_bytes(release):
            raise DeploymentError("materialized release manifest is not canonical")
        if file_sha256(target / "artifacts" / "runtime.tar") != release["runtime"]["sha256"]:
            raise DeploymentError("materialized runtime bundle hash differs from release")
        if file_sha256(target / "artifacts" / "config.tar") != release["config"]["sha256"]:
            raise DeploymentError("materialized config bundle hash differs from release")
        _attest_extracted_tree(
            target / "artifacts" / "runtime.tar",
            target / "runtime",
            label="runtime",
            file_mode=0o644,
            directory_mode=0o755,
            owner_uid=self.trusted_uid,
            owner_gid=self.trusted_gid,
        )
        _attest_extracted_tree(
            target / "artifacts" / "config.tar",
            target / "config",
            label="config",
            file_mode=0o440,
            directory_mode=0o750,
            owner_uid=self.trusted_uid,
            owner_gid=self.gateway_gid,
        )
        if (target / ".env").read_bytes() != self._expected_env_data(release):
            raise DeploymentError(
                "materialized generated environment differs from the exact release"
            )
        return target

    def _record(self, event: str, release: dict[str, Any], **details: Any) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "event": event,
            "release_id": release["release_id"],
            "source_sha": release["deployment_source_sha"],
            "time_unix": int(time.time()),
            **details,
        }
        data = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.audit_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise DeploymentError("deployment audit must be a regular file")
            if os.name != "nt" and (
                metadata.st_uid != self.trusted_uid
                or metadata.st_gid != self.trusted_gid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise DeploymentError("deployment audit ownership or mode is invalid")
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise DeploymentError("deployment audit write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _materialize(
        self,
        release: dict[str, Any],
        runtime_archive: Path,
        config_archive: Path,
    ) -> Path:
        if file_sha256(runtime_archive) != release["runtime"]["sha256"]:
            raise DeploymentError("runtime bundle SHA-256 differs from release manifest")
        if file_sha256(config_archive) != release["config"]["sha256"]:
            raise DeploymentError("config bundle SHA-256 differs from release manifest")
        target = self.release_root / release["release_id"]
        if target.exists():
            self._verify_materialized(release)
            return target
        staging = self.release_root / f".{release['release_id']}.staging-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        (staging / "runtime").mkdir(parents=True, mode=0o755)
        (staging / "config").mkdir(parents=True, mode=0o750)
        os.chmod(staging, 0o755)
        os.chmod(staging / "runtime", 0o755)
        os.chmod(staging / "config", 0o750)
        if os.name != "nt":
            os.chown(staging, self.trusted_uid, self.trusted_gid)
            os.chown(staging / "runtime", self.trusted_uid, self.trusted_gid)
            os.chown(staging / "config", self.trusted_uid, self.gateway_gid)
        try:
            _safe_extract(
                runtime_archive,
                staging / "runtime",
                file_mode=0o644,
                directory_mode=0o755,
                owner_uid=self.trusted_uid,
                owner_gid=self.trusted_gid,
            )
            _safe_extract(
                config_archive,
                staging / "config",
                file_mode=0o440,
                directory_mode=0o750,
                owner_uid=self.trusted_uid,
                owner_gid=self.gateway_gid,
            )
            compose = staging / "runtime" / "compose.yml"
            config = staging / "config" / "config" / "openclaw.json"
            if not compose.is_file():
                raise DeploymentError("runtime bundle must contain compose.yml")
            if not config.is_file():
                raise DeploymentError("config bundle must contain config/openclaw.json")
            atomic_write(staging / "release.json", canonical_json_bytes(release), mode=0o600)
            (staging / "artifacts").mkdir(mode=0o700)
            atomic_write(
                staging / "artifacts" / "runtime.tar",
                Path(runtime_archive).read_bytes(),
                mode=0o600,
            )
            atomic_write(
                staging / "artifacts" / "config.tar",
                Path(config_archive).read_bytes(),
                mode=0o600,
            )
            atomic_write(
                staging / ".env",
                self._expected_env_data(release),
                mode=0o600,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self._verify_materialized(release)

    def _docker_group_gid(self) -> int:
        if self.docker_gid is not None:
            if self.docker_gid <= 0:
                raise DeploymentError("Docker group GID must be positive")
            return self.docker_gid
        try:
            if grp is None:
                raise KeyError("docker")
            value = grp.getgrnam("docker").gr_gid
        except KeyError as exc:
            raise DeploymentError("host Docker group does not exist") from exc
        if value <= 0:
            raise DeploymentError("host Docker group GID must be positive")
        return value

    def _validate_host_boundary(self) -> None:
        if not self.validate_host_contract:
            return
        docker_gid = self._docker_group_gid()
        socket_path = Path("/var/run/docker.sock")
        socket_stat = socket_path.lstat()
        if socket_path.is_symlink() or not stat.S_ISSOCK(socket_stat.st_mode):
            raise DeploymentError("Docker control path must be a real Unix socket")
        if socket_stat.st_gid != docker_gid or not socket_stat.st_mode & stat.S_IRGRP \
                or not socket_stat.st_mode & stat.S_IWGRP:
            raise DeploymentError("Docker socket is not accessible through its exact host group")

        root_stat = self.secret_root.lstat()
        if self.secret_root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
            raise DeploymentError("Gateway secret root must be a real directory")
        for name in ("gateway_token", "discord_bot_token", "exa_api_key"):
            path = self.secret_root / name
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise DeploymentError(f"Gateway credential is not a regular file: {name}")
            if os.name != "nt" and (
                metadata.st_uid != self.gateway_uid
                or metadata.st_gid != self.gateway_gid
            ):
                raise DeploymentError(f"Gateway credential ownership is invalid: {name}")
            if stat.S_IMODE(metadata.st_mode) != 0o400:
                raise DeploymentError(f"Gateway credential mode is invalid: {name}")
            if not identity_can_read(metadata, uid=self.gateway_uid, gids={self.gateway_gid}):
                raise DeploymentError(f"Gateway UID cannot read credential: {name}")

    def _verify_images(self, release: dict[str, Any], *, pull: bool = True) -> None:
        for name in ("gateway", "ctf"):
            exact_ref = release[name]["ref"]
            if pull:
                result = self.runner(["docker", "image", "pull", exact_ref])
                _check_command(result, action=f"pull exact {name} image")
            result = self.runner(
                ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", exact_ref]
            )
            _check_command(result, action=f"inspect exact {name} image")
            try:
                repo_digests = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise DeploymentError(f"Docker returned invalid RepoDigests for {name}") from exc
            if not isinstance(repo_digests, list) or exact_ref not in repo_digests:
                raise DeploymentError(f"Docker did not prove exact {name} image digest")

    def _compose_argv(self, release: dict[str, Any]) -> list[str]:
        directory = self.release_root / release["release_id"]
        return [
            "docker",
            "compose",
            "--project-name",
            "openclaw",
            "--project-directory",
            str(directory / "runtime"),
            "--file",
            str(directory / "runtime" / "compose.yml"),
            "--env-file",
            str(directory / ".env"),
        ]

    def _compose(self, release: dict[str, Any], action: str) -> None:
        argv = self._compose_argv(release)
        if action == "up":
            argv.extend(("up", "--detach", "--remove-orphans", "--wait", "--no-build"))
        elif action == "down":
            argv.extend(("down", "--remove-orphans"))
        else:
            argv.append(action)
        _check_command(self.runner(argv), action=f"Compose {action}")

    def _verify_running_release(self, release: dict[str, Any]) -> None:
        ps = self.runner(self._compose_argv(release) + ["ps", "--quiet", "gateway"])
        _check_command(ps, action="locate active Gateway container")
        containers = tuple(line.strip() for line in ps.stdout.splitlines() if line.strip())
        if len(containers) != 1:
            raise DeploymentError("exactly one active Gateway container is required")
        inspect = self.runner(
            ["docker", "inspect", "--format", "{{json .}}", containers[0]]
        )
        _check_command(inspect, action="inspect active Gateway container")
        try:
            payload = json.loads(inspect.stdout)
        except json.JSONDecodeError as exc:
            raise DeploymentError("Docker returned invalid active Gateway metadata") from exc
        if not isinstance(payload, dict):
            raise DeploymentError("active Gateway metadata must be an object")
        config = payload.get("Config")
        state = payload.get("State")
        mounts = payload.get("Mounts")
        if not isinstance(config, dict) or not isinstance(state, dict) or not isinstance(mounts, list):
            raise DeploymentError("active Gateway metadata is incomplete")
        if config.get("Image") != release["gateway"]["ref"]:
            raise DeploymentError("active Gateway image differs from the recorded exact ref")
        environment = config.get("Env")
        expected_environment = {
            "OPENCLAW_CTF_IMAGE": release["ctf"]["ref"],
            "OPENCLAW_CONFIG_COMMIT": release["config"]["commit"],
            "OPENCLAW_RELEASE_ID": release["release_id"],
        }
        if not isinstance(environment, list) or any(
            not isinstance(value, str) for value in environment
        ):
            raise DeploymentError("active Gateway CTF/config identity differs from the release")
        for name, expected in expected_environment.items():
            matches = [
                value for value in environment
                if value.startswith(f"{name}=")
            ]
            if matches != [f"{name}={expected}"]:
                # Duplicate keys are also rejected: Docker permits them, while
                # their effective value depends on ordering and is not exact.
                raise DeploymentError(
                    "active Gateway CTF/config identity differs from the release"
                )
        health = state.get("Health")
        if state.get("Running") is not True or not isinstance(health, dict) \
                or health.get("Status") != "healthy":
            raise DeploymentError("active Gateway container is not healthy")
        socket_mounts = [
            mount for mount in mounts
            if isinstance(mount, dict)
            and mount.get("Destination") == "/var/run/docker.sock"
        ]
        if len(socket_mounts) != 1 or socket_mounts[0].get("Type") != "bind" \
                or socket_mounts[0].get("Source") != "/var/run/docker.sock" \
                or socket_mounts[0].get("RW") is not False:
            raise DeploymentError("Gateway Docker socket must be one read-only host bind")
        for destination in ("/home/node/.openclaw", "/var/lib/openclaw"):
            state_mounts = [
                mount for mount in mounts
                if isinstance(mount, dict)
                and mount.get("Destination") == destination
            ]
            if (
                len(state_mounts) != 1
                or state_mounts[0].get("Type") != "bind"
                or state_mounts[0].get("Source") != "/var/lib/openclaw"
                or state_mounts[0].get("RW") is not True
            ):
                raise DeploymentError(
                    "Gateway state must expose one writable host bind at both runtime paths"
                )
        expected_config_root = str(
            self.release_root / release["release_id"] / "config"
        )
        config_mounts = [
            mount for mount in mounts
            if isinstance(mount, dict)
            and mount.get("Destination") == "/etc/openclaw"
        ]
        if (
            len(config_mounts) != 1
            or config_mounts[0].get("Type") != "bind"
            or config_mounts[0].get("Source") != expected_config_root
            or config_mounts[0].get("RW") is not False
        ):
            raise DeploymentError(
                "Gateway config must be the exact release's read-only bind"
            )

    def _verify_stopped(self, release: dict[str, Any]) -> None:
        result = self.runner(self._compose_argv(release) + ["ps", "--quiet", "gateway"])
        _check_command(result, action="verify stopped Gateway container")
        if any(line.strip() for line in result.stdout.splitlines()):
            raise DeploymentError("failed release Gateway container is still active")

    def _prove_healthy(self, release: dict[str, Any]) -> None:
        # Readiness is deliberately unauthenticated and retryable.
        last: tuple[int, str] = (0, "not attempted")
        for attempt in range(self.readiness_attempts):
            last = self.probe(self.readiness_url, None)
            if 200 <= last[0] < 300:
                break
            if attempt + 1 < self.readiness_attempts:
                self.sleep(self.readiness_delay)
        else:
            raise DeploymentError(f"readiness failed with HTTP {last[0]}: {last[1][:200]}")

        token_path = self.secret_root / "gateway_token"
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise DeploymentError("cannot read Gateway smoke credential") from exc
        if not token or "\n" in token or "\r" in token:
            raise DeploymentError("Gateway smoke credential is invalid")

        # Exactly one authenticated request: never retry an authorization result.
        status, body = self.probe(self.smoke_url, token)
        if not 200 <= status < 300:
            raise DeploymentError(f"authenticated smoke failed with HTTP {status}: {body[:200]}")
    def _activate_existing(
        self, release: dict[str, Any], *, pull: bool = False
    ) -> None:
        self._verify_materialized(release)
        self._verify_images(release, pull=pull)
        self._compose(release, "up")
        self._verify_running_release(release)
        self._prove_healthy(release)

    def deploy(
        self,
        manifest: Path,
        runtime_archive: Path,
        config_archive: Path,
    ) -> dict[str, Any]:
        try:
            release = validate_release_manifest(load_json(manifest))
        except ContractError as exc:
            raise DeploymentError(str(exc)) from exc
        self._prepare_trusted_roots()
        with deployment_lock(self.state_root, self.trusted_uid):
            self._validate_host_boundary()
            self._reconcile_pending_transaction()
            state_existed = self.release_state_path.exists()
            original_state = self._load_state()
            current = original_state["current"]
            previous = original_state["previous"]
            if current is not None and current["release_id"] == release["release_id"]:
                if current != release:
                    raise DeploymentError("same release id has different immutable metadata")
                # An exact retry is an audit, not another activation or smoke.
                self._materialize(release, runtime_archive, config_archive)
                self._verify_images(release, pull=False)
                self._verify_running_release(release)
                self._record("same-release-audit-passed", release)
                return release

            self._record("deployment-started", release)
            transaction: dict[str, Any] | None = None
            try:
                self._materialize(release, runtime_archive, config_archive)
                self._verify_images(release)
                next_state = {
                    "version": 1,
                    "current": release,
                    "previous": current if current is not None else previous,
                }
                transaction = self._write_transaction(
                    operation="deploy",
                    target=release,
                    original_state=original_state,
                    original_state_existed=state_existed,
                    next_state=next_state,
                )
                self._compose(release, "up")
                self._verify_running_release(release)
                self._prove_healthy(release)
                self._record("health-proven", release, authenticated_smoke_count=1)
                transaction = self._set_transaction_phase(
                    transaction, "committing-target"
                )
                self._write_state(next_state)
                self._record("deployment-completed", release)
                self._clear_transaction()
                return release
            except BaseException as original:
                try:
                    self._record("deployment-failed", release, error=type(original).__name__)
                except BaseException:
                    # Audit storage failure must never prevent runtime rollback.
                    pass
                rollback_error: BaseException | None = None
                if transaction is not None:
                    try:
                        transaction = self._set_transaction_phase(
                            transaction, "restoring-original"
                        )
                        self._finish_original_restore(transaction)
                        self._record(
                            "automatic-rollback-completed" if current is not None
                            else "failed-first-activation-cleaned",
                            current or release,
                            failed_release_id=release["release_id"],
                        )
                        self._clear_transaction()
                    except BaseException as exc:
                        rollback_error = exc
                if rollback_error is not None:
                    try:
                        self._record(
                            "automatic-rollback-failed",
                            current or release,
                            error=type(rollback_error).__name__,
                        )
                    except BaseException:
                        pass
                    raise DeploymentError(
                        f"release activation failed and rollback failed: {rollback_error}"
                    ) from original
                raise DeploymentError(f"release activation failed: {original}") from original

    def rollback(self) -> dict[str, Any]:
        self._prepare_trusted_roots()
        with deployment_lock(self.state_root, self.trusted_uid):
            self._validate_host_boundary()
            self._reconcile_pending_transaction()
            state_existed = self.release_state_path.exists()
            original_state = self._load_state()
            current = original_state["current"]
            previous = original_state["previous"]
            if current is None or previous is None:
                raise DeploymentError("both current and previous releases are required for rollback")
            self._record("manual-rollback-started", previous, replaced_release_id=current["release_id"])
            transaction: dict[str, Any] | None = None
            try:
                self._verify_materialized(previous)
                self._verify_images(previous)
                next_state = {"version": 1, "current": previous, "previous": current}
                transaction = self._write_transaction(
                    operation="rollback",
                    target=previous,
                    original_state=original_state,
                    original_state_existed=state_existed,
                    next_state=next_state,
                )
                self._compose(previous, "up")
                self._verify_running_release(previous)
                self._prove_healthy(previous)
                self._record("health-proven", previous, authenticated_smoke_count=1)
                transaction = self._set_transaction_phase(
                    transaction, "committing-target"
                )
                self._write_state(next_state)
                self._record("manual-rollback-completed", previous)
                self._clear_transaction()
                return previous
            except BaseException as original:
                restoration_error: BaseException | None = None
                if transaction is not None:
                    try:
                        transaction = self._set_transaction_phase(
                            transaction, "restoring-original"
                        )
                        self._finish_original_restore(transaction)
                        self._record(
                            "manual-rollback-original-restored",
                            current,
                            failed_release_id=previous["release_id"],
                        )
                        self._clear_transaction()
                    except BaseException as exc:
                        restoration_error = exc
                if restoration_error is not None:
                    raise DeploymentError(
                        f"manual rollback failed and original release recovery failed: {restoration_error}"
                    ) from original
                raise DeploymentError(f"manual rollback failed: {original}") from original

    def audit(self) -> dict[str, Any]:
        self._prepare_trusted_roots()
        with deployment_lock(self.state_root, self.trusted_uid):
            self._validate_host_boundary()
            self._reconcile_pending_transaction()
            current = self._load_state()["current"]
            if current is None:
                raise DeploymentError("there is no active OpenClaw release")
            self._verify_materialized(current)
            self._verify_images(current, pull=False)
            self._verify_running_release(current)
            self._record("audit-passed", current)
            return current


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, default=Path("/opt/openclaw"))
    parser.add_argument("--secret-root", type=Path, default=Path("/etc/openclaw/secrets"))
    parser.add_argument("--readiness-url", default="http://127.0.0.1:18789/readyz")
    parser.add_argument(
        "--smoke-url",
        default="http://127.0.0.1:18789/control-ui-config.json",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser("deploy")
    deploy.add_argument("--manifest", required=True, type=Path)
    deploy.add_argument("--runtime-archive", required=True, type=Path)
    deploy.add_argument("--config-archive", required=True, type=Path)
    commands.add_parser("rollback")
    commands.add_parser("audit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    deployer = ReleaseDeployer(
        install_root=args.install_root,
        secret_root=args.secret_root,
        readiness_url=args.readiness_url,
        smoke_url=args.smoke_url,
    )
    try:
        if args.command == "deploy":
            release = deployer.deploy(
                args.manifest,
                args.runtime_archive,
                args.config_archive,
            )
        elif args.command == "rollback":
            release = deployer.rollback()
        elif args.command == "audit":
            release = deployer.audit()
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except (ContractError, DeploymentError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(release, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
