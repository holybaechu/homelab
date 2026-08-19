#!/usr/bin/env python3
"""Create and verify immutable OpenClaw build, release, and recovery contracts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, NoReturn

try:
    from .immutable_image_release import (
        ContractError,
        build_contract as build_image_contract,
        load_json,
        validate_digest as validate_oci_digest,
        validate_image as validate_oci_image,
        validate_immutable_ref,
        validate_source_sha as validate_image_source_sha,
    )
except ImportError:  # pragma: no cover - direct CLI execution
    from immutable_image_release import (
        ContractError,
        build_contract as build_image_contract,
        load_json,
        validate_digest as validate_oci_digest,
        validate_image as validate_oci_image,
        validate_immutable_ref,
        validate_source_sha as validate_image_source_sha,
    )


SCHEMA_VERSION = 1
SHA256 = re.compile(r"[0-9a-f]{64}")
PLATFORMS = frozenset(("linux/amd64", "linux/arm64"))
LEGACY_GATEWAY_REF = (
    "ghcr.io/openclaw/openclaw@sha256:"
    "8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac"
)


def _invalid(name: str, requirement: str) -> NoReturn:
    raise ContractError(f"{name} must be {requirement}")


def validate_source_sha(value: Any, *, name: str = "source commit") -> str:
    return validate_image_source_sha(value, name=name)


def validate_sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        _invalid(name, "an exact lowercase 64-character SHA-256")
    return value


def validate_digest(value: Any, *, name: str = "OCI digest") -> str:
    return validate_oci_digest(value, name=name)


def validate_image(value: Any, *, name: str = "image repository") -> str:
    try:
        return validate_oci_image(value)
    except ContractError as exc:
        raise ContractError(f"{name}: {exc}") from exc


def validate_ref(value: Any, *, name: str = "image reference") -> str:
    try:
        return validate_immutable_ref(value)
    except ContractError as exc:
        raise ContractError(f"{name}: {exc}") from exc


def validate_platform(value: Any) -> str:
    if not isinstance(value, str) or value not in PLATFORMS:
        _invalid("platform", "linux/amd64 or linux/arm64")
    return value


def canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name != "nt":
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            directory = os.open(path.parent, directory_flags)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_paths(root: Path) -> tuple[Path, ...]:
    root = root.resolve(strict=True)
    paths: list[Path] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names[:] = sorted(name for name in names if name != ".git")
        files.sort()
        for name in names:
            candidate = base / name
            if candidate.is_symlink():
                raise ContractError(f"bundle tree contains a symlink: {candidate}")
            paths.append(candidate)
        for name in files:
            candidate = base / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ContractError(f"bundle tree contains a non-regular file: {candidate}")
            paths.append(candidate)
    return tuple(paths)


def bundle_tree(source: Path, output: Path) -> str:
    """Write a byte-for-byte reproducible, traversal-free uncompressed tar."""

    source = Path(source).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    if not source.is_dir():
        raise ContractError("bundle source must be a directory")
    if output == source or source in output.parents:
        raise ContractError("bundle output must be outside its source tree")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path in _tree_paths(source):
            relative = path.relative_to(source).as_posix()
            pure = PurePosixPath(relative)
            if not relative or pure.is_absolute() or ".." in pure.parts:
                raise ContractError(f"bundle path is not canonical: {relative}")
            stat = path.stat()
            info = tarfile.TarInfo(relative + ("/" if path.is_dir() else ""))
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            if path.is_dir():
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.size = 0
                tar.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.mode = 0o755 if stat.st_mode & 0o111 else 0o644
                info.size = stat.st_size
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
    content = archive.getvalue()
    atomic_write(output, content)
    return hashlib.sha256(content).hexdigest()


def verify_git_checkout(source: Path, expected_commit: str) -> None:
    expected_commit = validate_source_sha(expected_commit, name="config commit")
    source = Path(source).resolve(strict=True)
    commands = (
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "--untracked-files=all"),
    )
    results: list[str] = []
    for args in commands:
        completed = subprocess.run(
            ["git", "-C", str(source), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode:
            raise ContractError(completed.stderr.strip() or "config git check failed")
        results.append(completed.stdout.strip())
    if results[0] != expected_commit:
        raise ContractError("config checkout does not match the exact promoted commit")
    if results[1]:
        raise ContractError("config checkout must be clean before bundling")


def _image_record(ref: str, *, name: str) -> dict[str, str]:
    ref = validate_ref(ref, name=name)
    image, digest = ref.split("@", 1)
    return {"digest": digest, "image": image, "ref": ref}


def create_release_manifest(
    *,
    deployment_source_sha: str,
    platform: str,
    gateway_ref: str,
    ctf_ref: str,
    runtime_sha256: str,
    config_commit: str,
    config_sha256: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "config": {
            "commit": validate_source_sha(config_commit, name="config commit"),
            "sha256": validate_sha256(config_sha256, name="config bundle SHA-256"),
        },
        "ctf": _image_record(ctf_ref, name="CTF image reference"),
        "deployment_source_sha": validate_source_sha(deployment_source_sha),
        "gateway": _image_record(gateway_ref, name="Gateway image reference"),
        "kind": "openclaw-immutable-release",
        "platform": validate_platform(platform),
        "runtime": {
            "sha256": validate_sha256(runtime_sha256, name="runtime bundle SHA-256")
        },
        "schema": SCHEMA_VERSION,
    }
    release_id = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return {**body, "release_id": release_id}


RELEASE_FIELDS = {
    "config",
    "ctf",
    "deployment_source_sha",
    "gateway",
    "kind",
    "platform",
    "release_id",
    "runtime",
    "schema",
}


def validate_release_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != RELEASE_FIELDS:
        raise ContractError("release manifest has an invalid field set")
    if payload.get("schema") != SCHEMA_VERSION:
        raise ContractError("release manifest has an unsupported schema")
    if payload.get("kind") != "openclaw-immutable-release":
        raise ContractError("release manifest has an invalid kind")
    expected = create_release_manifest(
        deployment_source_sha=payload.get("deployment_source_sha"),
        platform=payload.get("platform"),
        gateway_ref=(payload.get("gateway") or {}).get("ref")
        if isinstance(payload.get("gateway"), dict)
        else None,
        ctf_ref=(payload.get("ctf") or {}).get("ref")
        if isinstance(payload.get("ctf"), dict)
        else None,
        runtime_sha256=(payload.get("runtime") or {}).get("sha256")
        if isinstance(payload.get("runtime"), dict)
        else None,
        config_commit=(payload.get("config") or {}).get("commit")
        if isinstance(payload.get("config"), dict)
        else None,
        config_sha256=(payload.get("config") or {}).get("sha256")
        if isinstance(payload.get("config"), dict)
        else None,
    )
    if payload != expected:
        raise ContractError("release manifest is not canonical or is internally inconsistent")
    return expected


def build_contract(
    *,
    source_sha: str,
    gateway_image: str,
    ctf_image: str,
    gateway_base_ref: str,
    python_base_ref: str,
    docker_cli_ref: str,
    ctf_base_ref: str,
    uv_base_ref: str,
    platform: str,
) -> dict[str, Any]:
    source_sha = validate_source_sha(source_sha)
    platform = validate_platform(platform)
    gateway_image = validate_image(gateway_image, name="Gateway image repository")
    ctf_image = validate_image(ctf_image, name="CTF image repository")
    gateway_base_ref = validate_ref(gateway_base_ref, name="Gateway base reference")
    python_base_ref = validate_ref(python_base_ref, name="Python build reference")
    docker_cli_ref = validate_ref(docker_cli_ref, name="Docker CLI build reference")
    ctf_base_ref = validate_ref(ctf_base_ref, name="CTF base reference")
    uv_base_ref = validate_ref(uv_base_ref, name="uv base reference")

    def image_contract(
        *, artifact: str, image: str, context: str, metadata: str, args: tuple[str, ...]
    ) -> dict[str, Any]:
        contract = build_image_contract(
            artifact=artifact,
            source_sha=source_sha,
            image=image,
            context=context,
            dockerfile=f"{context}/Dockerfile",
            metadata_file=metadata,
            platform=platform,
        )
        argv = list(contract["build_argv"])
        insertion = argv.index("--tag")
        build_args: list[str] = []
        for argument in args:
            build_args.extend(("--build-arg", argument))
        argv[insertion:insertion] = ["--sbom=true", *build_args]
        return {**contract, "build_argv": argv}

    return {
        "artifacts": {
            "ctf": {
                **image_contract(
                    artifact="openclaw_ctf",
                    image=ctf_image,
                    context="infra/openclaw/ctf",
                    metadata=".immutable-images/openclaw-ctf.json",
                    args=(f"CTF_BASE_REF={ctf_base_ref}", f"UV_BASE_REF={uv_base_ref}"),
                ),
            },
            "gateway": {
                **image_contract(
                    artifact="openclaw_gateway",
                    image=gateway_image,
                    context="infra/openclaw/gateway",
                    metadata=".immutable-images/openclaw-gateway.json",
                    args=(
                        f"OPENCLAW_BASE_REF={gateway_base_ref}",
                        f"PYTHON_BASE_REF={python_base_ref}",
                        f"DOCKER_CLI_REF={docker_cli_ref}",
                    ),
                ),
            },
        },
        "kind": "openclaw-immutable-build-contract",
        "platform": platform,
        "schema": SCHEMA_VERSION,
        "source_sha": source_sha,
    }


def create_recovery_manifest(
    *,
    release: dict[str, Any],
    runtime_archive: Path,
    config_archive: Path,
    gateway_oci_archive: Path,
    ctf_oci_archive: Path,
) -> dict[str, Any]:
    release = validate_release_manifest(release)
    paths = {
        "config_bundle": Path(config_archive),
        "ctf_oci": Path(ctf_oci_archive),
        "gateway_oci": Path(gateway_oci_archive),
        "runtime_bundle": Path(runtime_archive),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ContractError(f"offline recovery artifact is missing: {name}")
    if file_sha256(paths["runtime_bundle"]) != release["runtime"]["sha256"]:
        raise ContractError("offline runtime bundle differs from the release")
    if file_sha256(paths["config_bundle"]) != release["config"]["sha256"]:
        raise ContractError("offline config bundle differs from the release")
    artifacts = {
        name: {"file": path.name, "sha256": file_sha256(path)}
        for name, path in sorted(paths.items())
    }
    return {
        "artifacts": artifacts,
        "kind": "openclaw-offline-recovery",
        "release": release,
        "restore_order": [
            "docker load --input gateway OCI archive",
            "docker load --input CTF OCI archive",
            "verify both exact RepoDigests",
            "deploy runtime and config bundles through openclaw-release-deployer",
        ],
        "schema": SCHEMA_VERSION,
    }


def create_legacy_recovery_manifest(
    *, config_commit: str, config_archive: Path, gateway_oci_archive: Path
) -> dict[str, Any]:
    """Record the retired retained-Docker state before its one-time removal."""

    config_commit = validate_source_sha(config_commit, name="legacy config commit")
    for name, path in (
        ("legacy config bundle", Path(config_archive)),
        ("legacy Gateway OCI archive", Path(gateway_oci_archive)),
    ):
        if not path.is_file():
            raise ContractError(f"{name} is missing")
    body: dict[str, Any] = {
        "config": {
            "commit": config_commit,
            "file": Path(config_archive).name,
            "sha256": file_sha256(config_archive),
        },
        "gateway": {
            "oci_file": Path(gateway_oci_archive).name,
            "oci_sha256": file_sha256(gateway_oci_archive),
            "ref": LEGACY_GATEWAY_REF,
        },
        "kind": "openclaw-retired-docker-recovery",
        "schema": SCHEMA_VERSION,
    }
    return {**body, "manifest_id": hashlib.sha256(canonical_json_bytes(body)).hexdigest()}


LEGACY_RECOVERY_FIELDS = {"config", "gateway", "kind", "manifest_id", "schema"}


def validate_legacy_recovery_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != LEGACY_RECOVERY_FIELDS:
        raise ContractError("legacy recovery manifest has an invalid field set")
    if payload.get("schema") != SCHEMA_VERSION or payload.get("kind") != \
            "openclaw-retired-docker-recovery":
        raise ContractError("legacy recovery manifest has an invalid contract identity")
    config = payload.get("config")
    gateway = payload.get("gateway")
    if not isinstance(config, dict) or set(config) != {"commit", "file", "sha256"}:
        raise ContractError("legacy recovery config record is invalid")
    if not isinstance(gateway, dict) or set(gateway) != {"oci_file", "oci_sha256", "ref"}:
        raise ContractError("legacy recovery Gateway record is invalid")
    if Path(config.get("file", "")).name != config.get("file") or not config.get("file"):
        raise ContractError("legacy recovery config filename is not canonical")
    if Path(gateway.get("oci_file", "")).name != gateway.get("oci_file") or not gateway.get("oci_file"):
        raise ContractError("legacy recovery OCI filename is not canonical")
    body = {
        "config": {
            "commit": validate_source_sha(config.get("commit"), name="legacy config commit"),
            "file": config["file"],
            "sha256": validate_sha256(config.get("sha256"), name="legacy config SHA-256"),
        },
        "gateway": {
            "oci_file": gateway["oci_file"],
            "oci_sha256": validate_sha256(
                gateway.get("oci_sha256"), name="legacy Gateway OCI SHA-256"
            ),
            "ref": validate_ref(gateway.get("ref"), name="legacy Gateway reference"),
        },
        "kind": "openclaw-retired-docker-recovery",
        "schema": SCHEMA_VERSION,
    }
    if body["gateway"]["ref"] != LEGACY_GATEWAY_REF:
        raise ContractError("legacy recovery must identify the exact retired Gateway digest")
    expected = {**body, "manifest_id": hashlib.sha256(canonical_json_bytes(body)).hexdigest()}
    if payload != expected:
        raise ContractError("legacy recovery manifest is not canonical")
    return expected


def audit_legacy_recovery(
    payload: Any, *, config_archive: Path, gateway_oci_archive: Path
) -> dict[str, Any]:
    manifest = validate_legacy_recovery_manifest(payload)
    if file_sha256(config_archive) != manifest["config"]["sha256"]:
        raise ContractError("legacy config bundle differs from its recovery manifest")
    if file_sha256(gateway_oci_archive) != manifest["gateway"]["oci_sha256"]:
        raise ContractError("legacy Gateway OCI archive differs from its recovery manifest")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    contract = commands.add_parser("contract")
    contract.add_argument("--source-sha", required=True)
    contract.add_argument("--gateway-image", required=True)
    contract.add_argument("--ctf-image", required=True)
    contract.add_argument("--gateway-base-ref", required=True)
    contract.add_argument("--python-base-ref", required=True)
    contract.add_argument("--docker-cli-ref", required=True)
    contract.add_argument("--ctf-base-ref", required=True)
    contract.add_argument("--uv-base-ref", required=True)
    contract.add_argument("--platform", default="linux/amd64")
    contract.add_argument("--output", required=True)

    bundle = commands.add_parser("bundle")
    bundle.add_argument("--source", required=True, type=Path)
    bundle.add_argument("--output", required=True, type=Path)
    bundle.add_argument("--git-commit")
    bundle.add_argument("--expected-sha256")
    bundle.add_argument("--result", type=Path)

    manifest = commands.add_parser("manifest")
    manifest.add_argument("--deployment-source-sha", required=True)
    manifest.add_argument("--platform", default="linux/amd64")
    manifest.add_argument("--gateway-ref", required=True)
    manifest.add_argument("--ctf-ref", required=True)
    manifest.add_argument("--runtime-sha256", required=True)
    manifest.add_argument("--config-commit", required=True)
    manifest.add_argument("--config-sha256", required=True)
    manifest.add_argument("--output", required=True, type=Path)

    verify = commands.add_parser("verify")
    verify.add_argument("manifest", type=Path)

    recovery = commands.add_parser("recovery")
    recovery.add_argument("--manifest", required=True, type=Path)
    recovery.add_argument("--runtime-archive", required=True, type=Path)
    recovery.add_argument("--config-archive", required=True, type=Path)
    recovery.add_argument("--gateway-oci-archive", required=True, type=Path)
    recovery.add_argument("--ctf-oci-archive", required=True, type=Path)
    recovery.add_argument("--output", required=True, type=Path)

    legacy = commands.add_parser("export-legacy-recovery")
    legacy.add_argument("--config-commit", required=True)
    legacy.add_argument("--config-archive", required=True, type=Path)
    legacy.add_argument("--gateway-oci-archive", required=True, type=Path)
    legacy.add_argument("--output", required=True, type=Path)

    legacy_verify = commands.add_parser("verify-legacy-recovery")
    legacy_verify.add_argument("--manifest", required=True, type=Path)
    legacy_verify.add_argument("--config-archive", required=True, type=Path)
    legacy_verify.add_argument("--gateway-oci-archive", required=True, type=Path)

    legacy_manifest_verify = commands.add_parser("verify-legacy-manifest")
    legacy_manifest_verify.add_argument("manifest", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            payload = build_contract(
                source_sha=args.source_sha,
                gateway_image=args.gateway_image,
                ctf_image=args.ctf_image,
                gateway_base_ref=args.gateway_base_ref,
                python_base_ref=args.python_base_ref,
                docker_cli_ref=args.docker_cli_ref,
                ctf_base_ref=args.ctf_base_ref,
                uv_base_ref=args.uv_base_ref,
                platform=args.platform,
            )
            atomic_write(Path(args.output), canonical_json_bytes(payload))
        elif args.command == "bundle":
            if args.git_commit:
                verify_git_checkout(args.source, args.git_commit)
            digest = bundle_tree(args.source, args.output)
            if args.expected_sha256 and digest != validate_sha256(
                args.expected_sha256, name="expected bundle SHA-256"
            ):
                raise ContractError("bundle SHA-256 does not match the promoted value")
            payload = {"path": str(args.output), "sha256": digest}
            if args.result:
                atomic_write(args.result, canonical_json_bytes(payload))
            print(json.dumps(payload, sort_keys=True))
            return 0
        elif args.command == "manifest":
            payload = create_release_manifest(
                deployment_source_sha=args.deployment_source_sha,
                platform=args.platform,
                gateway_ref=args.gateway_ref,
                ctf_ref=args.ctf_ref,
                runtime_sha256=args.runtime_sha256,
                config_commit=args.config_commit,
                config_sha256=args.config_sha256,
            )
            atomic_write(args.output, canonical_json_bytes(payload))
        elif args.command == "verify":
            payload = validate_release_manifest(load_json(args.manifest))
        elif args.command == "recovery":
            payload = create_recovery_manifest(
                release=load_json(args.manifest),
                runtime_archive=args.runtime_archive,
                config_archive=args.config_archive,
                gateway_oci_archive=args.gateway_oci_archive,
                ctf_oci_archive=args.ctf_oci_archive,
            )
            atomic_write(args.output, canonical_json_bytes(payload))
        elif args.command == "export-legacy-recovery":
            payload = create_legacy_recovery_manifest(
                config_commit=args.config_commit,
                config_archive=args.config_archive,
                gateway_oci_archive=args.gateway_oci_archive,
            )
            atomic_write(args.output, canonical_json_bytes(payload), mode=0o600)
        elif args.command == "verify-legacy-recovery":
            payload = audit_legacy_recovery(
                load_json(args.manifest),
                config_archive=args.config_archive,
                gateway_oci_archive=args.gateway_oci_archive,
            )
        elif args.command == "verify-legacy-manifest":
            payload = validate_legacy_recovery_manifest(load_json(args.manifest))
        else:  # pragma: no cover
            raise AssertionError(args.command)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
