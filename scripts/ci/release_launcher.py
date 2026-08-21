#!/usr/bin/env python3
"""Stable host launcher for a versioned Compose release engine bundle."""

from __future__ import annotations

import argparse
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
from typing import Any, Sequence


LAUNCHER_VERSION = 1
SCHEMA_VERSION = 1
ENGINE_VERSION = 1
ENGINE_PATH = "engine/compose_release_engine.py"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RELEASE_ID_RE = SHA256_RE
MAX_FILES = 10_000
MAX_BYTES = 512 * 1024 * 1024
DEFAULT_ROOTS = {"apps": Path("/opt/homelab"), "openclaw": Path("/opt/openclaw")}


class LauncherError(RuntimeError):
    """An uploaded bundle cannot be trusted or launched."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise LauncherError(f"cannot read release metadata {path}: {exc}") from exc


def _allowed_member(name: str) -> bool:
    path = PurePosixPath(name)
    parts = path.parts
    if name == "manifest.json" or name == ENGINE_PATH:
        return True
    return len(parts) >= 3 and parts[:2] in {
        ("payload", "stack"),
        ("payload", "config"),
    }


def safe_extract(archive: Path, destination: Path) -> None:
    seen: set[str] = set()
    total_size = 0
    try:
        bundle = tarfile.open(archive, mode="r:")
    except (OSError, tarfile.TarError) as exc:
        raise LauncherError("uploaded release is not a readable tar archive") from exc
    with bundle:
        members = bundle.getmembers()
        if not members or len(members) > MAX_FILES:
            raise LauncherError("uploaded release has an invalid entry count")
        for member in members:
            name = member.name.rstrip("/")
            raw_parts = name.split("/")
            if (
                not name
                or member.name.startswith("/")
                or "\\" in member.name
                or any(part in {"", ".", ".."} for part in raw_parts)
                or name in seen
                or not _allowed_member(name)
                or not (member.isdir() or member.isfile())
                or member.mode & 0o7000
            ):
                raise LauncherError(f"unsafe or unexpected bundle member: {member.name!r}")
            seen.add(name)
            if member.isfile():
                if member.size < 0:
                    raise LauncherError("uploaded release contains a negative file size")
                total_size += member.size
                if total_size > MAX_BYTES:
                    raise LauncherError("uploaded release exceeds the size limit")
        if "manifest.json" not in seen or ENGINE_PATH not in seen:
            raise LauncherError("uploaded release lacks manifest or engine")

        for member in members:
            name = member.name.rstrip("/")
            target = destination.joinpath(*name.split("/"))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    target.chmod(member.mode & 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = bundle.extractfile(member)
            if source is None:
                raise LauncherError(f"cannot read bundle member: {member.name!r}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            if os.name != "nt":
                target.chmod(member.mode & 0o755)


def validate_embedded_engine(root: Path, *, expected_target: str) -> dict[str, Any]:
    manifest = load_json(root / "manifest.json")
    if not isinstance(manifest, dict):
        raise LauncherError("bundle manifest must be an object")
    if (
        type(manifest.get("schema")) is not int
        or manifest.get("schema") != SCHEMA_VERSION
        or manifest.get("target") != expected_target
    ):
        raise LauncherError("bundle manifest schema/target differs from launcher request")
    engine = manifest.get("engine")
    if not isinstance(engine, dict) or set(engine) != {"version", "path", "sha256"}:
        raise LauncherError("bundle engine descriptor is invalid")
    if (
        type(engine.get("version")) is not int
        or engine.get("version") != ENGINE_VERSION
        or engine.get("path") != ENGINE_PATH
    ):
        raise LauncherError("bundle engine version/path is unsupported")
    expected_sha256 = engine.get("sha256")
    if not isinstance(expected_sha256, str) or SHA256_RE.fullmatch(expected_sha256) is None:
        raise LauncherError("bundle engine SHA-256 is invalid")
    engine_path = root / ENGINE_PATH
    if engine_path.is_symlink() or not engine_path.is_file():
        raise LauncherError("bundle engine is not a regular file")
    if file_sha256(engine_path) != expected_sha256:
        raise LauncherError("bundle engine SHA-256 differs from manifest")
    return manifest


def _prepare_incoming(install_root: Path) -> Path:
    if install_root.is_symlink():
        raise LauncherError("install root cannot be a symlink")
    install_root.mkdir(parents=True, exist_ok=True)
    incoming = install_root / "compose-incoming"
    if incoming.is_symlink():
        raise LauncherError("incoming root cannot be a symlink")
    incoming.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        incoming.chmod(0o700)
    return Path(tempfile.mkdtemp(prefix="release-", dir=incoming))


def _engine_command(
    engine: Path,
    command: str,
    target: str,
    *,
    install_root: Path,
    bundle_root: Path | None = None,
    secret_bundle: Path | None = None,
    secret_root: Path | None = None,
    docker_gid: int | None = None,
    docker_command: str = "docker",
) -> list[str]:
    argv = [
        sys.executable,
        str(engine),
        command,
        "--target",
        target,
        "--install-root",
        str(install_root),
        "--docker-command",
        docker_command,
    ]
    if bundle_root is not None:
        argv.extend(("--bundle-root", str(bundle_root)))
    if secret_bundle is not None:
        argv.extend(("--secret-bundle", str(secret_bundle)))
    if secret_root is not None:
        argv.extend(("--secret-root", str(secret_root)))
    if docker_gid is not None:
        argv.extend(("--docker-gid", str(docker_gid)))
    return argv


def _run_engine(argv: Sequence[str]) -> int:
    completed = subprocess.run(list(argv), check=False)
    if completed.returncode:
        raise LauncherError(f"release engine exited with status {completed.returncode}")
    return 0


def deploy_archive(
    *,
    target: str,
    archive: Path,
    expected_sha256: str,
    secret_bundle: Path,
    install_root: Path,
    secret_root: Path | None = None,
    docker_gid: int | None = None,
    docker_command: str = "docker",
) -> int:
    if target not in DEFAULT_ROOTS:
        raise LauncherError("target must be apps or openclaw")
    _require_archive(archive, expected_sha256)
    _require_secret_bundle(secret_bundle)
    staging = _prepare_incoming(install_root)
    try:
        safe_extract(archive, staging)
        manifest = validate_embedded_engine(staging, expected_target=target)
        return _run_engine(
            _engine_command(
                staging / manifest["engine"]["path"],
                "deploy",
                target,
                install_root=install_root,
                bundle_root=staging,
                secret_bundle=secret_bundle,
                secret_root=secret_root,
                docker_gid=docker_gid,
                docker_command=docker_command,
            )
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _require_archive(archive: Path, expected_sha256: str) -> None:
    if not isinstance(expected_sha256, str) or SHA256_RE.fullmatch(expected_sha256) is None:
        raise LauncherError("bundle SHA-256 must be 64 lowercase hex")
    if archive.is_symlink() or not archive.is_file():
        raise LauncherError("uploaded bundle must be a regular file")
    actual = file_sha256(archive)
    if actual != expected_sha256:
        raise LauncherError("uploaded bundle SHA-256 mismatch")


def _require_secret_bundle(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LauncherError("component secret bundle is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise LauncherError("component secret bundle must be a regular non-symlink file")
    if metadata.st_size > 1024 * 1024:
        raise LauncherError("component secret bundle exceeds the size limit")


def _installed_engine(install_root: Path, *, target: str) -> Path:
    state = load_json(install_root / "compose-control" / "release-state.json")
    if (
        not isinstance(state, dict)
        or type(state.get("schema")) is not int
        or state.get("schema") != SCHEMA_VERSION
        or state.get("target") != target
    ):
        raise LauncherError("installed release state is invalid")
    pending = state.get("pending")
    current = (
        pending.get("candidate")
        if isinstance(pending, dict) and isinstance(pending.get("candidate"), dict)
        else state.get("current")
    )
    if not isinstance(current, dict):
        raise LauncherError("there is no current release engine")
    release_id = current.get("release_id")
    engine = current.get("engine")
    if not isinstance(release_id, str) or RELEASE_ID_RE.fullmatch(release_id) is None:
        raise LauncherError("current release id is invalid")
    if (
        not isinstance(engine, dict)
        or type(engine.get("version")) is not int
        or engine.get("version") != ENGINE_VERSION
        or engine.get("path") != ENGINE_PATH
    ):
        raise LauncherError("current release engine descriptor is invalid")
    expected_sha256 = engine.get("sha256")
    if not isinstance(expected_sha256, str) or SHA256_RE.fullmatch(expected_sha256) is None:
        raise LauncherError("current release engine digest is invalid")
    path = install_root / "compose-releases" / release_id / ENGINE_PATH
    if path.is_symlink() or not path.is_file() or file_sha256(path) != expected_sha256:
        raise LauncherError("current release engine is unavailable or changed")
    return path


def run_installed(
    *,
    command: str,
    target: str,
    install_root: Path,
    secret_bundle: Path | None = None,
    secret_root: Path | None = None,
    docker_gid: int | None = None,
    docker_command: str = "docker",
) -> int:
    if command == "sync-secrets":
        if secret_bundle is None:
            raise LauncherError("sync-secrets requires a component secret bundle")
        _require_secret_bundle(secret_bundle)
    engine = _installed_engine(install_root, target=target)
    return _run_engine(
        _engine_command(
            engine,
            command,
            target,
            install_root=install_root,
            secret_bundle=secret_bundle,
            secret_root=secret_root,
            docker_gid=docker_gid,
            docker_command=docker_command,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=str(LAUNCHER_VERSION))
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("deploy", "sync-secrets", "audit", "rollback"):
        item = subparsers.add_parser(command)
        item.add_argument("--target", choices=tuple(DEFAULT_ROOTS), required=True)
        item.add_argument("--install-root", type=Path)
        item.add_argument("--secret-root", type=Path)
        item.add_argument("--docker-gid", type=int)
        item.add_argument("--docker-command", default="docker")
        if command == "deploy":
            item.add_argument("--archive", type=Path, required=True)
            item.add_argument("--sha256", required=True)
        if command in {"deploy", "sync-secrets"}:
            item.add_argument("--secret-bundle", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    install_root = args.install_root or DEFAULT_ROOTS[args.target]
    try:
        if args.command == "deploy":
            return deploy_archive(
                target=args.target,
                archive=args.archive,
                expected_sha256=args.sha256,
                secret_bundle=args.secret_bundle,
                install_root=install_root,
                secret_root=args.secret_root,
                docker_gid=args.docker_gid,
                docker_command=args.docker_command,
            )
        return run_installed(
            command=args.command,
            target=args.target,
            install_root=install_root,
            secret_bundle=(
                args.secret_bundle if args.command == "sync-secrets" else None
            ),
            secret_root=args.secret_root,
            docker_gid=args.docker_gid,
            docker_command=args.docker_command,
        )
    except (OSError, LauncherError, subprocess.SubprocessError) as exc:
        print(f"release-launcher: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
