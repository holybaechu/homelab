#!/usr/bin/env python3
"""One fixed-purpose, versioned release engine for the homelab Compose project.

The stable host launcher verifies and extracts an uploaded bundle, then runs the
copy of this file carried by that bundle.  The engine deliberately supports only
the ``apps`` release shape; it is not a plugin framework.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager, contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Iterator, Mapping, Protocol, Sequence
import uuid

SCHEMA_VERSION = 1
ENGINE_VERSION = 1
ENGINE_BUNDLE_PATH = "engine/compose_release_engine.py"
SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}")
RELEASE_ID_RE = re.compile(r"[0-9a-f]{64}")
SHA256_RE = RELEASE_ID_RE
MAX_BUNDLE_FILES = 10_000
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
GIB = 1024 * 1024 * 1024
MINIMUM_FREE_BYTES = 4 * GIB
COMPOSE_IMAGE_LINE_RE = re.compile(
    r"^\s+image:\s*(?:['\"])?(?P<ref>[a-zA-Z0-9][a-zA-Z0-9._/:@-]*)"
    r"(?:['\"])?\s*(?:#.*)?$"
)


class ReleaseError(RuntimeError):
    """The release bundle or host transition is invalid."""


class CommandRunner(Protocol):
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
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


TARGETS = ("apps",)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_source_sha(value: Any, *, name: str = "source_sha") -> str:
    if not isinstance(value, str) or SOURCE_SHA_RE.fullmatch(value) is None:
        raise ReleaseError(f"{name} must be exact lowercase 40-hex")
    return value


def _require_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ReleaseError(f"{name} must be exact lowercase SHA-256")
    return value


MANIFEST_FIELDS = {
    "schema",
    "target",
    "source_sha",
    "config_commit",
    "images",
    "engine",
    "payload",
}
ENGINE_FIELDS = {"version", "path", "sha256"}
PAYLOAD_FIELDS = {"stack_sha256", "config_sha256"}


def validate_manifest(payload: Any, *, expected_target: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS:
        raise ReleaseError("bundle manifest has an invalid field set")
    if type(payload.get("schema")) is not int or payload.get("schema") != SCHEMA_VERSION:
        raise ReleaseError("bundle manifest has an unsupported schema")
    target = payload.get("target")
    if target not in TARGETS or (expected_target is not None and target != expected_target):
        raise ReleaseError("bundle target is invalid")
    source_sha = _require_source_sha(payload.get("source_sha"))

    engine = payload.get("engine")
    if not isinstance(engine, dict) or set(engine) != ENGINE_FIELDS:
        raise ReleaseError("bundle engine descriptor is invalid")
    if (
        type(engine.get("version")) is not int
        or engine.get("version") != ENGINE_VERSION
        or engine.get("path") != ENGINE_BUNDLE_PATH
    ):
        raise ReleaseError("bundle engine version/path is unsupported")
    engine_sha256 = _require_sha256(engine.get("sha256"), name="engine.sha256")

    payload_descriptor = payload.get("payload")
    if (
        not isinstance(payload_descriptor, dict)
        or set(payload_descriptor) != PAYLOAD_FIELDS
    ):
        raise ReleaseError("bundle payload descriptor is invalid")
    stack_sha256 = _require_sha256(
        payload_descriptor.get("stack_sha256"), name="payload.stack_sha256"
    )
    config_sha256 = payload_descriptor.get("config_sha256")

    images = payload.get("images")
    if not isinstance(images, dict) or images:
        raise ReleaseError("apps images must contain exactly: none")
    if payload.get("config_commit") is not None:
        raise ReleaseError("apps config_commit must be null")
    if config_sha256 is not None:
        raise ReleaseError("apps payload.config_sha256 must be null")

    return {
        "schema": SCHEMA_VERSION,
        "target": target,
        "source_sha": source_sha,
        "config_commit": None,
        "images": {},
        "engine": {
            "version": ENGINE_VERSION,
            "path": ENGINE_BUNDLE_PATH,
            "sha256": engine_sha256,
        },
        "payload": {
            "stack_sha256": stack_sha256,
            "config_sha256": config_sha256,
        },
    }


def release_record(manifest: Mapping[str, Any]) -> dict[str, Any]:
    canonical = validate_manifest(dict(manifest))
    release_id = hashlib.sha256(canonical_json_bytes(canonical)).hexdigest()
    return {**canonical, "release_id": release_id}


def validate_release_record(payload: Any, *, expected_target: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS | {"release_id"}:
        raise ReleaseError("release record has an invalid field set")
    expected = release_record({field: payload.get(field) for field in MANIFEST_FIELDS})
    if payload != expected or payload.get("target") != expected_target:
        raise ReleaseError("release record is not canonical")
    return expected


def _load_unique_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseError(f"cannot load canonical JSON {path}: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    content = canonical_json_bytes(payload)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class FileLock(AbstractContextManager["FileLock"]):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        if os.name == "nt":  # pragma: no cover - production hosts are Linux
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        if os.name == "nt":  # pragma: no cover - production hosts are Linux
            import msvcrt

            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def _tree_entries(root: Path) -> list[Path]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ReleaseError(f"bundle source must be a real directory: {root}")
    entries: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise ReleaseError(f"bundle tree contains unsupported entry: {relative}")
        entries.append(path)
        if len(entries) > MAX_BUNDLE_FILES:
            raise ReleaseError("bundle contains too many files")
        if stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
            if total > MAX_BUNDLE_BYTES:
                raise ReleaseError("bundle content exceeds the size limit")
    return entries


def _tree_content_sha256(
    root: Path,
    *,
    private: bool,
    additions: Mapping[str, bytes] | None = None,
) -> str:
    """Hash the normalized tree exactly as it is represented in the bundle."""

    root = Path(root)
    additions = dict(additions or {})
    items: list[tuple[str, str, int, str]] = []
    for path in _tree_entries(root):
        relative = path.relative_to(root).as_posix()
        if relative in additions:
            raise ReleaseError(f"bundle addition duplicates package path: {relative}")
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            mode = 0o700 if private else 0o755
            items.append((relative, "directory", mode, ""))
        else:
            executable = bool(metadata.st_mode & stat.S_IXUSR)
            mode = (
                0o700
                if private and executable
                else 0o600
                if private
                else 0o755
                if executable
                else 0o644
            )
            items.append((relative, "file", mode, file_sha256(path)))
    for name, content in additions.items():
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or not isinstance(content, bytes)
        ):
            raise ReleaseError("bundle tree addition is invalid")
        items.append(
            (
                path.as_posix(),
                "file",
                0o600 if private else 0o644,
                hashlib.sha256(content).hexdigest(),
            )
        )
    items.sort(key=lambda item: item[0])
    return hashlib.sha256(canonical_json_bytes(items)).hexdigest()


def _add_bytes(bundle: tarfile.TarFile, name: str, content: bytes, mode: int) -> None:
    import io

    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    member.mtime = 0
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    bundle.addfile(member, io.BytesIO(content))


def _add_tree(bundle: tarfile.TarFile, root: Path, destination: str, *, private: bool) -> None:
    for path in _tree_entries(root):
        relative = path.relative_to(root).as_posix()
        name = f"{destination}/{relative}"
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            member = tarfile.TarInfo(name + "/")
            member.type = tarfile.DIRTYPE
            member.mode = 0o700 if private else 0o755
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            bundle.addfile(member)
        else:
            executable = bool(metadata.st_mode & stat.S_IXUSR)
            mode = 0o700 if private and executable else 0o600 if private else 0o755 if executable else 0o644
            _add_bytes(bundle, name, path.read_bytes(), mode)


def build_bundle(
    *,
    target: str,
    source_sha: str,
    stack_root: Path,
    output: Path,
    engine_path: Path,
    topology_path: Path | None = None,
) -> dict[str, Any]:
    if target not in TARGETS:
        raise ReleaseError("target must be apps")
    source_sha = _require_source_sha(source_sha)
    engine_path = Path(engine_path)
    if engine_path.is_symlink() or not engine_path.is_file():
        raise ReleaseError("engine source must be a regular file")
    engine_bytes = engine_path.read_bytes()
    stack_root = Path(stack_root)
    _validate_package_root(stack_root)
    if topology_path is None:
        raise ReleaseError("apps bundle requires the exact repository topology")
    topology_path = Path(topology_path)
    if topology_path.is_symlink() or not topology_path.is_file():
        raise ReleaseError("apps topology must be a regular file")
    if topology_path.stat().st_size > 1024 * 1024:
        raise ReleaseError("apps topology exceeds the size limit")
    _load_unique_json(topology_path)
    topology_bytes = topology_path.read_bytes()
    stack_additions = {"topology.json": topology_bytes}
    manifest = validate_manifest(
        {
            "schema": SCHEMA_VERSION,
            "target": target,
            "source_sha": source_sha,
            "config_commit": None,
            "images": {},
            "engine": {
                "version": ENGINE_VERSION,
                "path": ENGINE_BUNDLE_PATH,
                "sha256": hashlib.sha256(engine_bytes).hexdigest(),
            },
            "payload": {
                "stack_sha256": _tree_content_sha256(
                    stack_root, private=False, additions=stack_additions
                ),
                "config_sha256": None,
            },
        },
        expected_target=target,
    )

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    try:
        with tarfile.open(temporary, mode="w", format=tarfile.PAX_FORMAT) as bundle:
            _add_bytes(bundle, "manifest.json", canonical_json_bytes(manifest), 0o600)
            _add_bytes(bundle, ENGINE_BUNDLE_PATH, engine_bytes, 0o755)
            _add_tree(bundle, stack_root, "payload/stack", private=False)
            _add_bytes(bundle, "payload/stack/topology.json", topology_bytes, 0o644)
        os.replace(temporary, output)
        _fsync_directory(output.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    result = {
        "schema": SCHEMA_VERSION,
        "target": target,
        "source_sha": source_sha,
        "sha256": file_sha256(output),
        "path": str(output),
    }
    return result


def _validate_package_root(root: Path, *, rendered: bool = False, bundled: bool = False) -> None:
    for name in ("compose.yml", "release.json", "smoke.sh", "prepare_release.py"):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ReleaseError(f"apps package is missing regular {name}")
    # This marker is also consumed by older installed release engines.
    metadata = _load_unique_json(root / "release.json")
    if (not isinstance(metadata, dict) or type(metadata.get("version")) is not int
        or metadata.get("version") != 1 or metadata.get("project") != "homelab"):
        raise ReleaseError("apps package release.json is invalid")
    if os.name != "nt" and not (root / "smoke.sh").stat().st_mode & stat.S_IXUSR:
        raise ReleaseError("apps package smoke.sh must be owner-executable")
    if not rendered:
        for name in (".secrets", ".release.env", "generated"):
            path = root / name
            if path.exists() or path.is_symlink():
                raise ReleaseError(f"apps source package contains rendered runtime state: {name}")
    topology = root / "topology.json"
    if bundled:
        if topology.is_symlink() or not topology.is_file():
            raise ReleaseError("apps bundle is missing its exact topology snapshot")
        _load_unique_json(topology)
    elif topology.exists() or topology.is_symlink():
        raise ReleaseError("apps source package must not duplicate the topology")


def _verify_payload_descriptor(root: Path, manifest: Mapping[str, Any]) -> None:
    canonical = validate_manifest(dict(manifest))
    stack = root / "payload" / "stack"
    _validate_package_root(stack, bundled=True)
    if _tree_content_sha256(stack, private=False) != canonical["payload"]["stack_sha256"]:
        raise ReleaseError("bundle stack content differs from its manifest checksum")


SLOTS = {"a", "b"}
STATE_FIELDS = {
    "schema",
    "target",
    "current",
    "previous",
    "pending",
    "active_slot",
}
PENDING_FIELDS = {
    "candidate",
    "original_current",
    "original_previous",
    "candidate_slot",
    "original_slot",
    "activation_started",
}


def empty_state(target: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "target": target,
        "current": None,
        "previous": None,
        "pending": None,
        "active_slot": None,
    }


def validate_state(payload: Any, *, target: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != STATE_FIELDS:
        raise ReleaseError("release state has an invalid field set")
    if (
        type(payload.get("schema")) is not int
        or payload.get("schema") != SCHEMA_VERSION
        or payload.get("target") != target
    ):
        raise ReleaseError("release state schema/target is invalid")

    def record(value: Any) -> dict[str, Any] | None:
        return None if value is None else validate_release_record(value, expected_target=target)

    current = record(payload.get("current"))
    previous = record(payload.get("previous"))
    active_slot = payload.get("active_slot")
    if active_slot is not None and active_slot not in SLOTS:
        raise ReleaseError("active release slot is invalid")
    if (current is None) != (active_slot is None):
        raise ReleaseError("current release and active slot must be set together")
    if current is None and previous is not None:
        raise ReleaseError("previous release cannot exist without a current release")

    pending = payload.get("pending")
    if pending is not None:
        if not isinstance(pending, dict) or set(pending) != PENDING_FIELDS:
            raise ReleaseError("pending release state is invalid")
        pending = {
            "candidate": validate_release_record(pending.get("candidate"), expected_target=target),
            "original_current": record(pending.get("original_current")),
            "original_previous": record(pending.get("original_previous")),
            "candidate_slot": pending.get("candidate_slot"),
            "original_slot": pending.get("original_slot"),
            "activation_started": pending.get("activation_started"),
        }
        if pending["candidate_slot"] not in SLOTS:
            raise ReleaseError("pending candidate slot is invalid")
        if pending["original_slot"] is not None and pending["original_slot"] not in SLOTS:
            raise ReleaseError("pending original slot is invalid")
        if (pending["original_current"] is None) != (pending["original_slot"] is None):
            raise ReleaseError("pending original release and slot must be set together")
        if pending["original_slot"] == pending["candidate_slot"]:
            raise ReleaseError("pending release must use the inactive slot")
        if type(pending["activation_started"]) is not bool:
            raise ReleaseError("pending activation marker must be boolean")
        if (
            pending["original_current"] != current
            or pending["original_previous"] != previous
            or pending["original_slot"] != active_slot
        ):
            raise ReleaseError("pending release does not match the original state")
    return {
        "schema": SCHEMA_VERSION,
        "target": target,
        "current": current,
        "previous": previous,
        "pending": pending,
        "active_slot": active_slot,
    }


class ComposeReleaseEngine:
    def __init__(
        self,
        target: str,
        *,
        install_root: Path | None = None,
        secret_root: Path | None = None,
        docker_command: str = "docker",
        runner: CommandRunner | None = None,
        lock_factory: Any = FileLock,
        minimum_free_bytes: int | None = None,
    ):
        if target not in TARGETS:
            raise ReleaseError("target must be apps")
        self.target = target
        self.install_root = Path(install_root or Path("/opt/homelab"))
        self.secret_root = (
            Path(secret_root)
            if secret_root is not None
            else Path("/etc/homelab/secrets")
        )
        self.docker_command = docker_command
        self.runner = runner or SubprocessRunner()
        self.lock_factory = lock_factory
        self.minimum_free_bytes = (
            MINIMUM_FREE_BYTES
            if minimum_free_bytes is None
            else minimum_free_bytes
        )
        if not isinstance(self.minimum_free_bytes, int) or self.minimum_free_bytes < 0:
            raise ReleaseError("minimum free bytes must be a nonnegative integer")
        self.release_root = self.install_root / "compose-releases"
        self.runtime_root = self.install_root / "compose-runtime"
        self.state_root = self.install_root / "compose-control"
        self.state_path = self.state_root / "release-state.json"
        self.deferred_image_path = self.state_root / "deferred-image-refs.json"
        self.lock_path = self.state_root / "release.lock"

    def _prepare_roots(self) -> None:
        for path, mode in (
            (self.install_root, 0o755),
            (self.release_root, 0o755),
            (self.runtime_root, 0o700),
            (self.state_root, 0o700),
        ):
            if path.is_symlink():
                raise ReleaseError(f"release root cannot be a symlink: {path}")
            path.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(path, mode)
    def _cleanup_scratch(self) -> None:
        for path in self.runtime_root.iterdir():
            if re.fullmatch(r"\.[ab]\.(?:tmp|old)-[0-9a-f]{8}", path.name):
                if path.is_symlink() or not path.is_dir():
                    raise ReleaseError("runtime scratch entry is unsafe")
                shutil.rmtree(path)
        for path in self.release_root.iterdir():
            if re.fullmatch(r"\.new-[0-9a-f]{8}", path.name):
                if path.is_symlink() or not path.is_dir():
                    raise ReleaseError("release scratch entry is unsafe")
                shutil.rmtree(path)
        for path in self.state_root.iterdir():
            if path.name.startswith(".secret-check-"):
                if path.is_symlink() or not path.is_dir():
                    raise ReleaseError("secret validation scratch entry is unsafe")
                shutil.rmtree(path)
            elif re.fullmatch(
                r"\.(?:release-state|deferred-image-refs)\.json\.tmp-[0-9a-f]{32}",
                path.name,
            ):
                if path.is_symlink() or not path.is_file():
                    raise ReleaseError("release state scratch entry is unsafe")
                path.unlink()
        if self.secret_root is not None and (
            self.secret_root.exists() or self.secret_root.is_symlink()
        ):
            if self.secret_root.is_symlink() or not self.secret_root.is_dir():
                raise ReleaseError("component secret directory is unsafe")
            pattern = re.compile(
                rf"\.{re.escape(self.target)}\.json\.tmp-[0-9a-f]{{32}}"
            )
            for path in self.secret_root.iterdir():
                if pattern.fullmatch(path.name):
                    if path.is_symlink() or not path.is_file():
                        raise ReleaseError("component secret scratch entry is unsafe")
                    path.unlink()

    def _load_deferred_image_refs(self) -> set[str]:
        path = self.deferred_image_path
        if not path.exists() and not path.is_symlink():
            return set()
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("deferred image state must be a regular file")
        payload = _load_unique_json(path)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema", "target", "refs"}
            or type(payload.get("schema")) is not int
            or payload.get("schema") != SCHEMA_VERSION
            or payload.get("target") != self.target
        ):
            raise ReleaseError("deferred image state has an invalid schema")
        refs = payload.get("refs")
        if (
            not isinstance(refs, list)
            or any(
                not isinstance(ref, str)
                or not ref
                or len(ref) > 4096
                or any(character in ref for character in ("\0", "\r", "\n"))
                for ref in refs
            )
            or refs != sorted(set(refs))
        ):
            raise ReleaseError("deferred image state has invalid references")
        return set(refs)

    def _write_deferred_image_refs(self, refs: set[str]) -> None:
        canonical = sorted(refs)
        if not canonical:
            if self.deferred_image_path.is_symlink():
                raise ReleaseError("deferred image state cannot be a symlink")
            try:
                self.deferred_image_path.unlink()
            except FileNotFoundError:
                return
            _fsync_directory(self.state_root)
            return
        atomic_write_json(
            self.deferred_image_path,
            {
                "schema": SCHEMA_VERSION,
                "target": self.target,
                "refs": canonical,
            },
            mode=0o600,
        )

    def _merge_deferred_image_refs(self, refs: set[str]) -> set[str]:
        merged = self._load_deferred_image_refs()
        merged.update(refs)
        self._write_deferred_image_refs(merged)
        return merged

    @staticmethod
    def _state_records(state: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        """Return every release record that a valid state can still reference."""

        records: list[Mapping[str, Any]] = []
        for name in ("current", "previous"):
            record = state.get(name)
            if record is not None:
                records.append(record)
        pending = state.get("pending")
        if pending is not None:
            for name in ("candidate", "original_current", "original_previous"):
                record = pending.get(name)
                if record is not None:
                    records.append(record)
        return tuple(records)

    def _record_image_refs(self, record: Mapping[str, Any]) -> set[str]:
        """Read only image identities declared by one verified immutable release."""

        canonical = validate_release_record(
            dict(record), expected_target=self.target
        )
        refs: set[str] = set()
        compose = self._release_path(canonical) / "payload" / "stack" / "compose.yml"
        try:
            lines = compose.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise ReleaseError("cannot read the retained Compose image inventory") from exc
        for line in lines:
            if re.match(r"^\s+image\s*:", line) is None:
                continue
            match = COMPOSE_IMAGE_LINE_RE.fullmatch(line)
            if match is None or "$" in match.group("ref"):
                raise ReleaseError("apps Compose images must be literal references")
            refs.add(match.group("ref"))
        return refs

    def _prune_release_sources(self, state: Mapping[str, Any]) -> set[str]:
        """Bound immutable source retention to state-reachable release records.

        The directory is dedicated to SHA-256-addressed releases.  Unknown names
        fail closed rather than being guessed at or deleted.
        """

        protected = {
            validate_release_record(dict(record), expected_target=self.target)[
                "release_id"
            ]
            for record in self._state_records(state)
        }
        removed_refs: set[str] = set()
        for path in self.release_root.iterdir():
            if path.name.startswith(".new-"):
                continue
            if RELEASE_ID_RE.fullmatch(path.name) is None:
                raise ReleaseError(
                    f"unexpected immutable release entry blocks retention: {path.name}"
                )
            if path.is_symlink() or not path.is_dir():
                raise ReleaseError("immutable release entry is unsafe")
            if path.name in protected:
                continue
            record_refs: set[str] | None = None
            try:
                manifest = validate_manifest(
                    _load_unique_json(path / "manifest.json"),
                    expected_target=self.target,
                )
                record = release_record(manifest)
                if record["release_id"] != path.name:
                    raise ReleaseError("immutable release directory identity differs")
                record_refs = self._record_image_refs(record)
            except ReleaseError:
                # A non-reachable corrupt release is not a rollback source.  Its
                # exact SHA directory is still safe to remove, but never follow a
                # symlink or remove an unexpected path.
                pass
            if record_refs is not None:
                # Persist cleanup knowledge before deleting its only immutable
                # source.  A live session may make Docker refuse image removal;
                # later operations must still be able to retry that exact ref.
                self._merge_deferred_image_refs(record_refs)
                removed_refs.update(record_refs)
            shutil.rmtree(path)
            _fsync_directory(self.release_root)
        return removed_refs

    def _project_container_image_refs(self) -> set[str]:
        result = self.runner.run(
            [
                self.docker_command,
                "ps",
                "--all",
                "--filter",
                "label=com.docker.compose.project=homelab",
                "--format",
                "{{.Image}}",
            ],
            cwd=self.install_root,
        )
        if result.returncode:
            return set()
        return {
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and line.strip() != "<none>"
        }

    @staticmethod
    def _expanded_image_refs(refs: set[str]) -> set[str]:
        expanded: set[str] = set()
        for ref in refs:
            expanded.add(ref)
            if "@" not in ref:
                continue
            name, digest = ref.rsplit("@", 1)
            if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                continue
            expanded.add(name)
            last_component = name.rsplit("/", 1)[-1]
            repository = name.rsplit(":", 1)[0] if ":" in last_component else name
            expanded.add(f"{repository}@{digest}")
        return expanded

    def _prune_managed_images(
        self, candidates: set[str], state: Mapping[str, Any]
    ) -> set[str]:
        """Best-effort deletion of no-longer-retained project images only.

        Image IDs that also have a current/previous reference remain protected.
        Docker itself refuses removal while an external/session container still
        uses an image, so unrelated live containers are not interrupted.
        """

        raw_candidates = set(candidates)
        candidate_expansions = {
            ref: self._expanded_image_refs({ref}) for ref in raw_candidates
        }
        candidates = set().union(*candidate_expansions.values()) if candidate_expansions else set()
        if not candidates:
            return set()
        protected: set[str] = set()
        try:
            for record in self._state_records(state):
                protected.update(self._record_image_refs(record))
        except (OSError, ReleaseError):
            return raw_candidates
        protected = self._expanded_image_refs(protected)
        inventory = self.runner.run(
            [
                self.docker_command,
                "image",
                "ls",
                "--digests",
                "--format",
                "{{json .}}",
            ],
            cwd=self.install_root,
        )
        if inventory.returncode:
            return raw_candidates
        by_id: dict[str, set[str]] = {}
        try:
            for line in inventory.stdout.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    return raw_candidates
                image_id = item.get("ID")
                repository = item.get("Repository")
                tag = item.get("Tag")
                digest = item.get("Digest")
                if not all(isinstance(value, str) for value in (image_id, repository, tag, digest)):
                    return raw_candidates
                refs = by_id.setdefault(image_id, {image_id})
                if repository != "<none>" and tag != "<none>":
                    refs.add(f"{repository}:{tag}")
                if repository != "<none>" and digest != "<none>":
                    refs.add(f"{repository}@{digest}")
        except (json.JSONDecodeError, TypeError):
            return raw_candidates
        unresolved: set[str] = set()
        for image_id, refs in by_id.items():
            matching = {
                raw
                for raw, expanded in candidate_expansions.items()
                if not refs.isdisjoint(expanded)
            }
            if not matching or not refs.isdisjoint(protected):
                continue
            result = self.runner.run(
                [self.docker_command, "image", "rm", image_id],
                cwd=self.install_root,
            )
            if result.returncode:
                unresolved.update(matching)
        return unresolved

    def _retry_managed_image_cleanup(
        self, candidates: set[str], state: Mapping[str, Any]
    ) -> set[str]:
        retry = self._merge_deferred_image_refs(set(candidates))
        unresolved = self._prune_managed_images(retry, state)
        self._write_deferred_image_refs(unresolved)
        return unresolved

    def _require_pull_capacity(self) -> None:
        try:
            free = shutil.disk_usage(self.install_root).free
        except OSError as exc:
            raise ReleaseError("cannot measure release filesystem capacity") from exc
        if free < self.minimum_free_bytes:
            required_gib = self.minimum_free_bytes / GIB
            free_gib = free / GIB
            raise ReleaseError(
                f"release pull requires {required_gib:.0f} GiB free; "
                f"only {free_gib:.1f} GiB is available"
            )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return empty_state(self.target)
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise ReleaseError("release state path is not a regular file")
        return validate_state(_load_unique_json(self.state_path), target=self.target)

    def _write_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        canonical = validate_state(dict(state), target=self.target)
        atomic_write_json(self.state_path, canonical)
        return canonical

    def _release_path(self, record: Mapping[str, Any]) -> Path:
        release_id = record.get("release_id")
        if not isinstance(release_id, str) or RELEASE_ID_RE.fullmatch(release_id) is None:
            raise ReleaseError("release id is invalid")
        return self.release_root / release_id

    def _slot_path(self, slot: str) -> Path:
        if slot not in SLOTS:
            raise ReleaseError("runtime slot is invalid")
        return self.runtime_root / slot

    def _verify_materialized(self, record: Mapping[str, Any]) -> Path:
        record = validate_release_record(dict(record), expected_target=self.target)
        root = self._release_path(record)
        if root.is_symlink() or not root.is_dir():
            raise ReleaseError("materialized release directory is unavailable")
        manifest = validate_manifest(_load_unique_json(root / "manifest.json"), expected_target=self.target)
        if release_record(manifest) != record:
            raise ReleaseError("materialized release manifest differs from state")
        engine = root / ENGINE_BUNDLE_PATH
        if engine.is_symlink() or not engine.is_file() or file_sha256(engine) != record["engine"]["sha256"]:
            raise ReleaseError("materialized release engine differs from manifest")
        _tree_entries(root / "payload" / "stack")
        _verify_payload_descriptor(root, manifest)
        return root

    def _materialize(self, bundle_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
        record = release_record(manifest)
        target = self._release_path(record)
        if target.exists():
            self._verify_materialized(record)
            return record
        temporary = self.release_root / f".new-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copytree(bundle_root, temporary, symlinks=False)
            os.replace(temporary, target)
            _fsync_directory(self.release_root)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self._verify_materialized(record)
        return record

    @staticmethod
    def _require_secret_source(path: Path, *, installed: bool) -> Path:
        path = Path(path)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ReleaseError("component secret bundle is unavailable") from exc
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ReleaseError("component secret bundle must be a regular non-symlink file")
        if metadata.st_size > 1024 * 1024:
            raise ReleaseError("component secret bundle exceeds the size limit")
        if installed and os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ReleaseError("component secret bundle permissions are broader than 0600")
        return path

    def _component_secret_bundle(self) -> Path:
        if self.secret_root is None:
            raise ReleaseError(f"{self.target} secret root is unavailable")
        return self._require_secret_source(
            self.secret_root / f"{self.target}.json", installed=True
        )

    def _prepare_apps(self, slot_root: Path) -> None:
        secret_bundle = self._component_secret_bundle()
        stack = slot_root / "stack"
        with self._apps_secret_for_package(secret_bundle, stack) as compatible_bundle:
            self._checked(
                [
                    sys.executable,
                    str(stack / "prepare_release.py"),
                    "--secret-bundle",
                    str(compatible_bundle),
                    "--release-root",
                    str(stack),
                    "--topology",
                    str(stack / "topology.json"),
                ],
                cwd=stack,
                action="apps release preparation",
            )

    @contextmanager
    def _apps_secret_for_package(self, source: Path, stack: Path) -> Iterator[Path]:
        """Keep v2 credentials installed when recovering a v1 application package."""
        payload = _load_unique_json(source)
        if not isinstance(payload, dict):
            raise ReleaseError("apps component secret bundle must be an object")
        metadata = _load_unique_json(stack / "release.json")
        wanted = metadata.get("secret_bundle", {}).get("version")
        if wanted == 1 and type(payload.get("version")) is int and payload["version"] == 2:
            if 2 not in metadata.get("secret_bundle", {}).get("compatible_versions", []):
                raise ReleaseError("rollback target predates the apps v2 secret-compatibility release")
            legacy_keys = {"component", "version", "cloudflare", "adguard", "qbittorrent", "copyparty_users"}
            if set(payload) != legacy_keys | {"authentik", "headscale"}:
                raise ReleaseError("apps v2 component secret fields are invalid")
            projected = {key: payload[key] for key in legacy_keys}
            projected["version"] = 1
            with tempfile.TemporaryDirectory(prefix=".apps-secret-", dir=stack.parent) as directory:
                path = Path(directory) / "apps.json"
                atomic_write_json(path, projected)
                yield path
        else:
            yield source


    def _validate_apps_secret_bundle(
        self, source: Path, package_root: Path
    ) -> None:
        source = self._require_secret_source(source, installed=False)
        _load_unique_json(source)
        temporary = Path(
            tempfile.mkdtemp(prefix=".secret-check-", dir=self.state_root)
        )
        try:
            stack = temporary / "stack"
            shutil.copytree(package_root, stack)
            with self._apps_secret_for_package(source, stack) as compatible_bundle:
                self._checked(
                    [
                        sys.executable,
                        str(stack / "prepare_release.py"),
                        "--secret-bundle",
                        str(compatible_bundle),
                        "--release-root",
                        str(stack),
                        "--topology",
                        str(stack / "topology.json"),
                    ],
                    cwd=stack,
                    action="apps component secret validation",
                )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def _install_secret_bundle(self, source: Path, package_root: Path) -> Path:
        source = self._require_secret_source(source, installed=False)
        payload = _load_unique_json(source)
        if not isinstance(payload, dict):
            raise ReleaseError("apps component secret bundle must be an object")
        if payload.get("version") == 2:
            # A v1 engine cannot read the expanded installed bundle after rollback.
            # Require the compatibility engine in the immediate rollback target.
            record = self._load_state().get("current")
            if record is not None:
                metadata = _load_unique_json(self._release_path(record) / "payload" / "stack" / "release.json")
                contract = metadata.get("secret_bundle", {})
                if 2 not in contract.get("compatible_versions", [contract.get("version")]):
                    raise ReleaseError("deploy the apps v1 secret-compatibility release before installing a v2 bundle")
        self._validate_apps_secret_bundle(source, package_root)
        if self.secret_root is None:
            raise ReleaseError(f"{self.target} secret root is unavailable")
        if self.secret_root.is_symlink():
            raise ReleaseError("component secret directory cannot be a symlink")
        self.secret_root.mkdir(parents=True, exist_ok=True)
        if not self.secret_root.is_dir():
            raise ReleaseError("component secret directory is unavailable")
        if os.name != "nt":
            os.chmod(self.secret_root, 0o700)
        destination = self.secret_root / f"{self.target}.json"
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_file():
                raise ReleaseError("installed component secret bundle is unsafe")
        data = source.read_bytes()
        temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return destination


    def _replace_slot(self, temporary: Path, slot: str) -> Path:
        target = self._slot_path(slot)
        backup = self.runtime_root / f".{slot}.old-{uuid.uuid4().hex[:8]}"
        moved = False
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise ReleaseError("runtime slot is not a regular directory")
            os.replace(target, backup)
            moved = True
        try:
            os.replace(temporary, target)
            _fsync_directory(self.runtime_root)
        except BaseException:
            if moved and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if moved:
            shutil.rmtree(backup)
        return target

    def _render_slot(self, record: Mapping[str, Any], slot: str) -> Path:
        immutable = self._verify_materialized(record)
        target = self._slot_path(slot)
        temporary = self.runtime_root / f".{slot}.tmp-{uuid.uuid4().hex[:8]}"
        try:
            temporary.mkdir(mode=0o700)
            shutil.copytree(immutable / "payload" / "stack", temporary / "stack")
            atomic_write_json(
                temporary / "release.json",
                {"schema": SCHEMA_VERSION, "release_id": record["release_id"]},
                mode=0o644,
            )
            self._prepare_apps(temporary)
            self._replace_slot(temporary, slot)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self._verify_slot(record, slot)

    def _verify_slot(self, record: Mapping[str, Any], slot: str) -> Path:
        root = self._slot_path(slot)
        if root.is_symlink() or not root.is_dir():
            raise ReleaseError("runtime slot is unavailable")
        marker = _load_unique_json(root / "release.json")
        if marker != {"schema": SCHEMA_VERSION, "release_id": record["release_id"]}:
            raise ReleaseError("runtime slot release identity is invalid")
        _validate_package_root(
            root / "stack", rendered=True, bundled=True
        )
        return root

    def _slot_matches(self, record: Mapping[str, Any], slot: str) -> bool:
        try:
            self._verify_slot(record, slot)
            return True
        except (OSError, ReleaseError):
            return False

    def _remove_slot(self, slot: str | None) -> None:
        if slot is None:
            return
        path = self._slot_path(slot)
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or not path.is_dir():
            raise ReleaseError("runtime slot cannot be removed safely")
        shutil.rmtree(path)
        _fsync_directory(self.runtime_root)

    def _compose(
        self, record: Mapping[str, Any], slot: str, *arguments: str
    ) -> list[str]:
        root = self._slot_path(slot)
        stack = root / "stack"
        argv = [
            self.docker_command,
            "compose",
            "--project-name",
            "homelab",
            "--project-directory",
            str(stack),
        ]
        argv.extend(("--file", str(stack / "compose.yml")))
        argv.extend(arguments)
        return argv

    def _checked(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        action: str,
    ) -> str:
        result = self.runner.run(argv, cwd=cwd, env=env)
        if result.returncode:
            raise ReleaseError(
                f"{action} failed with exit status {result.returncode}"
            )
        return result.stdout

    def _verify_exact_image(self, name: str, exact_ref: str, stack: Path) -> None:
        repo_digests = self._checked(
            [
                self.docker_command,
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                exact_ref,
            ],
            cwd=stack,
            action=f"inspect exact {name} image",
        )
        try:
            digests = json.loads(repo_digests)
        except json.JSONDecodeError as exc:
            raise ReleaseError(f"Docker returned invalid RepoDigests for {name}") from exc
        expected_digest = exact_ref.rsplit("@", 1)[-1]
        if (
            not isinstance(digests, list)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) is None
            or not any(
                isinstance(item, str) and item.endswith(f"@{expected_digest}")
                for item in digests
            )
        ):
            raise ReleaseError(f"Docker did not prove exact {name} image digest")

    def _preflight(
        self, record: Mapping[str, Any], slot: str, *, pull: bool
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        root = self._verify_slot(record, slot)
        stack = root / "stack"
        model_output = self._checked(
            self._compose(record, slot, "config", "--format", "json"),
            cwd=stack,
            action="Compose model",
        )
        try:
            model = json.loads(model_output)
        except json.JSONDecodeError as exc:
            raise ReleaseError("Compose returned an invalid JSON model") from exc
        service_models = model.get("services") if isinstance(model, dict) else None
        if not isinstance(service_models, dict):
            raise ReleaseError("Compose model has no service mapping")
        services = tuple(service_models)
        if not services or len(services) != len(set(services)):
            raise ReleaseError("Compose returned an empty or duplicate service set")
        process_health_services: list[str] = []
        rendered_images: list[str] = []
        for service in services:
            if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", service) is None:
                raise ReleaseError(f"Compose returned invalid service name: {service!r}")
            service_model = service_models[service]
            if not isinstance(service_model, dict) or not isinstance(
                service_model.get("image"), str
            ):
                raise ReleaseError(f"Compose service has no exact image: {service!r}")
            rendered_images.append(service_model["image"])
            labels = service_model.get("labels", {})
            if isinstance(labels, list):
                labels = {
                    item.split("=", 1)[0]: item.split("=", 1)[1]
                    for item in labels
                    if isinstance(item, str) and "=" in item
                }
            if not isinstance(labels, dict):
                raise ReleaseError(f"Compose service labels are invalid: {service!r}")
            if labels.get("homelab.health") == "process":
                process_health_services.append(service)
        if pull:
            self._require_pull_capacity()
        expected_images = self._record_image_refs(record)
        if (
            not rendered_images
            or any(
                re.fullmatch(r".+@sha256:[0-9a-f]{64}", image) is None
                for image in rendered_images
            )
            or {
                image.rsplit("@", 1)[-1]
                for image in rendered_images
            }
            != {image.rsplit("@", 1)[-1] for image in expected_images}
        ):
            raise ReleaseError(
                "apps Compose model must select every exact package image digest"
            )
        if pull:
            self._checked(
                self._compose(record, slot, "pull"),
                cwd=stack,
                action="Compose pull",
            )
            verification_refs = {
                f"service-{index}": exact_ref
                for index, exact_ref in enumerate(sorted(self._record_image_refs(record)), start=1)
            }
            for name, exact_ref in verification_refs.items():
                self._verify_exact_image(name, exact_ref, stack)
        return services, tuple(process_health_services)

    def _activate(
        self,
        record: Mapping[str, Any],
        slot: str,
        *,
        pull: bool,
        mark_pending_activation: bool = False,
    ) -> None:
        self._require_compose_owned_apps_network(self._slot_path(slot) / "stack")
        services, process_health_services = self._preflight(record, slot, pull=pull)
        root = self._slot_path(slot)
        stack = root / "stack"
        if mark_pending_activation:
            self._mark_pending_activation_started(record, slot)
        self._checked(
            self._compose(
                record,
                slot,
                "up",
                "-d",
                "--wait",
                "--remove-orphans",
                "--no-build",
                "--pull",
                "never",
            ),
            cwd=stack,
            action="Compose activation",
        )
        running_output = self._checked(
            self._compose(
                record, slot, "ps", "--services", "--status", "running"
            ),
            cwd=stack,
            action="Compose running services",
        )
        running = {line.strip() for line in running_output.splitlines() if line.strip()}
        if running != set(services):
            raise ReleaseError("running services differ from the Compose service set")
        self._verify_process_health_services(
            record,
            slot,
            stack,
            process_health_services,
        )
        self._run_smoke(record, slot, stack)

    def _verify_process_health_services(
        self,
        record: Mapping[str, Any],
        slot: str,
        stack: Path,
        services: Sequence[str],
    ) -> None:
        for service in services:
            container_output = self._checked(
                self._compose(record, slot, "ps", "--quiet", service),
                cwd=stack,
                action=f"locate process-health service {service}",
            )
            containers = [
                line.strip() for line in container_output.splitlines() if line.strip()
            ]
            if (
                len(containers) != 1
                or re.fullmatch(r"[0-9a-f]{12,64}", containers[0]) is None
            ):
                raise ReleaseError(
                    f"process-health service {service} has no unique container"
                )
            metadata_output = self._checked(
                [
                    self.docker_command,
                    "inspect",
                    "--format",
                    "{{json .}}",
                    containers[0],
                ],
                cwd=stack,
                action=f"inspect process-health service {service}",
            )
            try:
                metadata = json.loads(metadata_output)
            except json.JSONDecodeError as exc:
                raise ReleaseError(
                    f"Docker returned invalid process state for {service}"
                ) from exc
            state = metadata.get("State") if isinstance(metadata, dict) else None
            restart_count = (
                metadata.get("RestartCount") if isinstance(metadata, dict) else None
            )
            if (
                not isinstance(state, dict)
                or state.get("Running") is not True
                or state.get("Status") != "running"
                or state.get("Restarting") is not False
                or type(restart_count) is not int
                or restart_count != 0
            ):
                raise ReleaseError(
                    f"process-health service {service} is not stably running"
                )

    def _require_compose_owned_apps_network(self, stack: Path) -> None:
        network_name = "homelab_proxy"
        listed = self.runner.run(
            [
                self.docker_command,
                "network",
                "ls",
                "--format",
                "{{.Name}}",
            ],
            cwd=stack,
        )
        if listed.returncode != 0:
            raise ReleaseError("cannot inspect the apps proxy network")
        names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if network_name not in names:
            return
        labels_output = self._checked(
            [
                self.docker_command,
                "network",
                "inspect",
                network_name,
                "--format",
                "{{json .Labels}}",
            ],
            cwd=stack,
            action="inspect apps proxy network ownership",
        )
        try:
            labels = json.loads(labels_output)
        except json.JSONDecodeError as exc:
            raise ReleaseError("apps proxy network labels are invalid") from exc
        if not isinstance(labels, dict) or (
            labels.get("com.docker.compose.project") != "homelab"
            or labels.get("com.docker.compose.network") != "proxy"
        ):
            raise ReleaseError(
                "existing homelab_proxy network is not Compose-owned; "
                "complete the documented network ownership transition before deployment"
            )


    def _run_smoke(
        self, record: Mapping[str, Any], slot: str, stack: Path
    ) -> None:
        smoke = stack / "smoke.sh"
        if smoke.is_symlink() or not smoke.is_file():
            raise ReleaseError("release smoke command is unavailable")
        environment = os.environ.copy()
        environment.update(
            {
                "HOMELAB_TARGET": self.target,
                "HOMELAB_RELEASE_ID": record["release_id"],
                "HOMELAB_SOURCE_SHA": record["source_sha"],
                "HOMELAB_RELEASE_ROOT": str(self._slot_path(slot)),
            }
        )
        self._checked([str(smoke)], cwd=stack, env=environment, action=f"{self.target} smoke")

    def _stop(self, record: Mapping[str, Any], slot: str) -> None:
        root = self._verify_slot(record, slot)
        stack = root / "stack"
        self._checked(
            self._compose(record, slot, "down", "--remove-orphans"),
            cwd=stack,
            action="Compose stop",
        )

    @staticmethod
    def _inactive_slot(active_slot: str | None) -> str:
        return "b" if active_slot == "a" else "a"

    def _pending_state(
        self,
        state: Mapping[str, Any],
        candidate: Mapping[str, Any],
        candidate_slot: str,
    ) -> dict[str, Any]:
        pending = {
            "candidate": dict(candidate),
            "original_current": state["current"],
            "original_previous": state["previous"],
            "candidate_slot": candidate_slot,
            "original_slot": state["active_slot"],
            "activation_started": False,
        }
        return self._write_state({**state, "pending": pending})

    def _mark_pending_activation_started(
        self, candidate: Mapping[str, Any], candidate_slot: str
    ) -> dict[str, Any]:
        state = self._load_state()
        pending = state.get("pending")
        if (
            pending is None
            or pending["candidate"] != dict(candidate)
            or pending["candidate_slot"] != candidate_slot
        ):
            raise ReleaseError("pending activation marker does not match the candidate")
        if pending["activation_started"]:
            return state
        marked = {**pending, "activation_started": True}
        return self._write_state({**state, "pending": marked})

    def _restore_pending(self, state: Mapping[str, Any]) -> dict[str, Any]:
        pending = state.get("pending")
        if pending is None:
            return dict(state)
        original_current = pending["original_current"]
        candidate_slot = pending["candidate_slot"]
        if not pending["activation_started"]:
            self._remove_slot(candidate_slot)
            restored = {
                "schema": SCHEMA_VERSION,
                "target": self.target,
                "current": original_current,
                "previous": pending["original_previous"],
                "pending": None,
                "active_slot": pending["original_slot"],
            }
            return self._write_state(restored)
        if original_current is None:
            if self._slot_matches(pending["candidate"], candidate_slot):
                self._stop(pending["candidate"], candidate_slot)
            self._remove_slot(candidate_slot)
            return self._write_state(empty_state(self.target))

        self._render_slot(original_current, candidate_slot)
        self._activate(original_current, candidate_slot, pull=False)
        self._remove_slot(pending["original_slot"])
        restored = {
            "schema": SCHEMA_VERSION,
            "target": self.target,
            "current": original_current,
            "previous": pending["original_previous"],
            "pending": None,
            "active_slot": candidate_slot,
        }
        return self._write_state(restored)

    def _transition(
        self,
        state: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        pull: bool,
        success_previous: Mapping[str, Any] | None,
        operation: str,
        cleanup_refs: set[str] | None = None,
    ) -> dict[str, Any]:
        cleanup_candidates = set(cleanup_refs or ())
        cleanup_candidates.update(self._project_container_image_refs())
        for record in self._state_records(state):
            try:
                cleanup_candidates.update(self._record_image_refs(record))
            except (OSError, ReleaseError):
                # Retention cannot make the release transaction less reliable.
                # A strict source audit runs before every materialization.
                pass
        candidate_slot = self._inactive_slot(state["active_slot"])
        self._pending_state(state, candidate, candidate_slot)
        try:
            self._render_slot(candidate, candidate_slot)
            self._activate(
                candidate,
                candidate_slot,
                pull=pull,
                mark_pending_activation=True,
            )
            self._remove_slot(state["active_slot"])
        except BaseException as original:
            try:
                self._restore_pending(self._load_state())
            except BaseException as recovery:
                raise ReleaseError(
                    f"{operation} failed and interrupted-state recovery failed"
                ) from original
            raise ReleaseError(f"{operation} failed and the prior state was restored") from original

        committed = {
            "schema": SCHEMA_VERSION,
            "target": self.target,
            "current": dict(candidate),
            "previous": None if success_previous is None else dict(success_previous),
            "pending": None,
            "active_slot": candidate_slot,
        }
        committed = self._write_state(committed)
        try:
            cleanup_candidates.update(self._prune_release_sources(committed))
            self._retry_managed_image_cleanup(cleanup_candidates, committed)
        except (OSError, ReleaseError):
            # Activation and its durable state are already committed.  A later
            # operation retries strict source retention; never misreport the
            # successfully activated release as failed because cleanup did.
            pass
        return dict(candidate)

    def deploy_bundle(
        self, bundle_root: Path, secret_bundle: Path
    ) -> dict[str, Any]:
        bundle_root = Path(bundle_root)
        if bundle_root.is_symlink() or not bundle_root.is_dir():
            raise ReleaseError("bundle root must be a regular directory")
        manifest = validate_manifest(
            _load_unique_json(bundle_root / "manifest.json"),
            expected_target=self.target,
        )
        embedded_engine = bundle_root / manifest["engine"]["path"]
        if (
            embedded_engine.is_symlink()
            or not embedded_engine.is_file()
            or file_sha256(embedded_engine) != manifest["engine"]["sha256"]
        ):
            raise ReleaseError("embedded release engine differs from manifest")
        _tree_entries(bundle_root / "payload" / "stack")
        _verify_payload_descriptor(bundle_root, manifest)

        self._prepare_roots()
        with self.lock_factory(self.lock_path):
            self._cleanup_scratch()
            self._install_secret_bundle(
                secret_bundle, bundle_root / "payload" / "stack"
            )
            state = self._restore_pending(self._load_state())
            cleanup_refs = self._prune_release_sources(state)
            self._retry_managed_image_cleanup(cleanup_refs, state)
            candidate = self._materialize(bundle_root, manifest)
            same_release = (
                state["current"] is not None
                and state["current"]["release_id"] == candidate["release_id"]
            )
            previous = state["previous"] if same_release else state["current"]
            return self._transition(
                state,
                candidate,
                pull=True,
                success_previous=previous,
                operation="release activation",
                cleanup_refs=cleanup_refs,
            )

    def sync_secrets(self, secret_bundle: Path) -> dict[str, Any]:
        self._prepare_roots()
        with self.lock_factory(self.lock_path):
            self._cleanup_scratch()
            state = self._load_state()
            pending = state.get("pending")
            package_record = (
                pending["candidate"] if pending is not None else state["current"]
            )
            if package_record is None:
                raise ReleaseError("secret sync requires an installed release")
            package_root = self._verify_materialized(package_record) / "payload" / "stack"
            # Explicit transaction semantics: a valid new component bundle becomes
            # host current before activation and remains current if activation fails.
            self._install_secret_bundle(secret_bundle, package_root)
            state = self._restore_pending(state)
            cleanup_refs = self._prune_release_sources(state)
            current = state["current"]
            if current is None:
                raise ReleaseError("secret sync requires an active release")
            return self._transition(
                state,
                current,
                pull=False,
                success_previous=state["previous"],
                operation="secret sync",
                cleanup_refs=cleanup_refs,
            )

    def audit(self) -> dict[str, Any]:
        self._prepare_roots()
        with self.lock_factory(self.lock_path):
            self._cleanup_scratch()
            state = self._restore_pending(self._load_state())
            cleanup_refs = self._prune_release_sources(state)
            current = state["current"]
            if current is None:
                raise ReleaseError("there is no active release")
            return self._transition(
                state,
                current,
                pull=False,
                success_previous=state["previous"],
                operation="release audit",
                cleanup_refs=cleanup_refs,
            )

    def rollback(self) -> dict[str, Any]:
        self._prepare_roots()
        with self.lock_factory(self.lock_path):
            self._cleanup_scratch()
            state = self._restore_pending(self._load_state())
            cleanup_refs = self._prune_release_sources(state)
            current = state["current"]
            previous = state["previous"]
            if current is None or previous is None:
                raise ReleaseError("rollback requires current and previous releases")
            return self._transition(
                state,
                previous,
                pull=False,
                success_previous=current,
                operation="release rollback",
                cleanup_refs=cleanup_refs,
            )


def _add_engine_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--secret-root", type=Path)
    parser.add_argument("--docker-command", default="docker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bundle = subparsers.add_parser("bundle", help="build one deterministic release bundle")
    bundle.add_argument("--target", choices=tuple(TARGETS), required=True)
    bundle.add_argument("--source-sha", required=True)
    bundle.add_argument("--stack", type=Path, required=True)
    bundle.add_argument("--topology", type=Path)
    bundle.add_argument("--engine", type=Path, default=Path(__file__).resolve())
    bundle.add_argument("--output", type=Path, required=True)

    for name in ("deploy", "sync-secrets", "audit", "rollback"):
        command = subparsers.add_parser(name)
        command.add_argument("--target", choices=tuple(TARGETS), required=True)
        if name == "deploy":
            command.add_argument("--bundle-root", type=Path, required=True)
        if name in {"deploy", "sync-secrets"}:
            command.add_argument("--secret-bundle", type=Path, required=True)
        _add_engine_roots(command)
    return parser


def _engine_from_args(args: argparse.Namespace) -> ComposeReleaseEngine:
    return ComposeReleaseEngine(
        args.target,
        install_root=args.install_root,
        secret_root=args.secret_root,
        docker_command=args.docker_command,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bundle":
            result = build_bundle(
                target=args.target,
                source_sha=args.source_sha,
                stack_root=args.stack,
                topology_path=args.topology,
                engine_path=args.engine,
                output=args.output,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        engine = _engine_from_args(args)
        result = (
            engine.deploy_bundle(args.bundle_root, args.secret_bundle)
            if args.command == "deploy"
            else engine.sync_secrets(args.secret_bundle)
            if args.command == "sync-secrets"
            else engine.audit()
            if args.command == "audit"
            else engine.rollback()
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ReleaseError, subprocess.SubprocessError) as exc:
        # ReleaseError messages name controlled stages; captured command output
        # remains private. Preserve the reason even after successful rollback.
        messages = []
        cause = exc
        while isinstance(cause, ReleaseError):
            if str(cause) not in messages:
                messages.append(str(cause))
            cause = cause.__cause__
        detail = ": ".join(messages) if messages else str(exc)
        print(f"compose-release-engine: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
