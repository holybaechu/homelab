#!/usr/bin/env python3
"""Build, approve, and materialize immutable OCI image release metadata.

The build side may use a source-addressed staging tag because registries need a
name to publish.  The deployment side never receives that tag: approvals and
materialized release metadata contain only ``repository@sha256:<digest>``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any, NoReturn


SCHEMA_VERSION = 1
SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
ARTIFACT_RE = re.compile(r"[a-z][a-z0-9_-]{0,62}")
REPOSITORY_PART_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
REGISTRY_HOST_RE = re.compile(
    r"(?:localhost|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?"
)
REGISTRY_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
PLATFORM_RE = re.compile(r"linux/(?:amd64|arm64)(?:/v[1-9][0-9]*)?")


class ContractError(ValueError):
    """An immutable-image contract or state file is invalid."""


def _invalid(name: str, requirement: str) -> NoReturn:
    raise ContractError(f"{name} must be {requirement}")


def validate_source_sha(value: str, *, name: str = "source SHA") -> str:
    if not isinstance(value, str) or SOURCE_SHA_RE.fullmatch(value) is None:
        _invalid(name, "a lowercase 40-character Git SHA")
    return value


def validate_digest(value: str, *, name: str = "digest") -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _invalid(name, "an exact lowercase sha256:<64-hex> digest")
    return value


def validate_artifact(value: str) -> str:
    if not isinstance(value, str) or ARTIFACT_RE.fullmatch(value) is None:
        _invalid("artifact", "a lowercase path-safe name")
    return value


def validate_platform(value: str) -> str:
    if not isinstance(value, str) or PLATFORM_RE.fullmatch(value) is None:
        _invalid("platform", "a supported canonical Linux OCI platform")
    return value


def validate_image(value: str) -> str:
    """Validate a registry/repository name with no scheme, tag, or digest."""

    if not isinstance(value, str) or not value or value != value.lower():
        _invalid("image", "a lowercase OCI registry/repository without tag or digest")
    if any(character.isspace() for character in value) or \
            "://" in value or "@" in value or "\\" in value:
        _invalid("image", "a lowercase OCI registry/repository without tag or digest")

    parts = value.split("/")
    if len(parts) < 2 or any(not part for part in parts):
        _invalid("image", "a registry-qualified OCI repository")
    registry, *repository = parts
    if REGISTRY_HOST_RE.fullmatch(registry) is None or not (
        registry == "localhost" or "." in registry or ":" in registry
    ):
        _invalid("image", "a registry-qualified OCI repository")
    registry_host, separator, port_text = registry.rpartition(":")
    if not separator:
        registry_host = registry
    if registry_host != "localhost" and (
        "." not in registry_host
        or any(
            REGISTRY_LABEL_RE.fullmatch(label) is None
            for label in registry_host.split(".")
        )
    ):
        _invalid("image", "a registry-qualified OCI repository")
    if separator:
        if (len(port_text) > 1 and port_text.startswith("0")) or not (
            1 <= int(port_text) <= 65535
        ):
            _invalid("image registry port", "between 1 and 65535")
    if any(REPOSITORY_PART_RE.fullmatch(part) is None for part in repository):
        _invalid("image", "a lowercase OCI registry/repository without tag or digest")
    return value


def validate_immutable_ref(value: str, *, expected_image: str | None = None) -> str:
    if not isinstance(value, str) or value.count("@") != 1:
        _invalid("image reference", "repository@sha256:<64-hex>")
    image, digest = value.split("@", 1)
    validate_image(image)
    validate_digest(digest, name="image reference digest")
    if expected_image is not None and image != validate_image(expected_image):
        raise ContractError(
            f"image reference repository {image!r} does not match {expected_image!r}"
        )
    return value


def validate_repo_path(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _invalid(name, "a canonical repository-relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0].startswith("-")
        or any(character.isspace() for character in value)
    ):
        _invalid(name, "a canonical repository-relative POSIX path")
    return value


def _staging_ref(image: str, source_sha: str) -> str:
    return f"{image}:source-{source_sha}"


def build_contract(
    *,
    artifact: str,
    source_sha: str,
    image: str,
    context: str,
    dockerfile: str,
    metadata_file: str,
    platform: str,
) -> dict[str, Any]:
    """Return the deterministic CI build/publish command contract."""

    artifact = validate_artifact(artifact)
    source_sha = validate_source_sha(source_sha)
    image = validate_image(image)
    context = validate_repo_path(context, name="build context")
    dockerfile = validate_repo_path(dockerfile, name="Dockerfile path")
    metadata_file = validate_repo_path(metadata_file, name="build metadata path")
    platform = validate_platform(platform)

    context_parts = PurePosixPath(context).parts
    dockerfile_parts = PurePosixPath(dockerfile).parts
    if dockerfile_parts[: len(context_parts)] != context_parts:
        raise ContractError("Dockerfile path must be inside the build context")

    staging_ref = _staging_ref(image, source_sha)
    return {
        "schema": SCHEMA_VERSION,
        "kind": "immutable-oci-build-contract",
        "artifact": artifact,
        "source_sha": source_sha,
        "image": image,
        "platform": platform,
        "staging_ref": staging_ref,
        "build_argv": [
            "docker",
            "buildx",
            "build",
            "--file",
            dockerfile,
            "--platform",
            platform,
            "--label",
            f"org.opencontainers.image.revision={source_sha}",
            "--provenance=true",
            "--push",
            "--metadata-file",
            metadata_file,
            "--tag",
            staging_ref,
            context,
        ],
        # Buildx writes this digest as part of the same push.  Reading it avoids
        # resolving the mutable staging tag in a second, raceable registry call.
        "digest_source": {
            "metadata_file": metadata_file,
            "json_field": "containerimage.digest",
        },
        "approval_requirement": "registry result must be lowercase sha256:<64-hex>",
    }


RECORD_FIELDS = {
    "schema",
    "kind",
    "artifact",
    "source_sha",
    "image",
    "platform",
    "digest",
    "ref",
}
DEPLOYMENT_FIELDS = {
    "schema",
    "kind",
    "deployment_source_sha",
    "artifacts",
}
DEPLOYMENT_ARTIFACT_FIELDS = {
    "source_sha",
    "image",
    "platform",
    "digest",
    "ref",
    "reused",
}


def approved_record(
    *, artifact: str, source_sha: str, image: str, platform: str, digest: str
) -> dict[str, Any]:
    artifact = validate_artifact(artifact)
    source_sha = validate_source_sha(source_sha)
    image = validate_image(image)
    platform = validate_platform(platform)
    digest = validate_digest(digest)
    immutable_ref = validate_immutable_ref(f"{image}@{digest}", expected_image=image)
    return {
        "schema": SCHEMA_VERSION,
        "kind": "approved-immutable-oci-image",
        "artifact": artifact,
        "source_sha": source_sha,
        "image": image,
        "platform": platform,
        "digest": digest,
        "ref": immutable_ref,
    }


def validate_record(
    payload: Any,
    *,
    expected_artifact: str,
    expected_source_sha: str | None = None,
    expected_image: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != RECORD_FIELDS:
        raise ContractError("approved image record has an invalid field set")
    if payload.get("schema") != SCHEMA_VERSION:
        raise ContractError("approved image record has an unsupported schema")
    if payload.get("kind") != "approved-immutable-oci-image":
        raise ContractError("approved image record has an invalid kind")

    canonical = approved_record(
        artifact=payload.get("artifact"),
        source_sha=payload.get("source_sha"),
        image=payload.get("image"),
        platform=payload.get("platform"),
        digest=payload.get("digest"),
    )
    if payload != canonical:
        raise ContractError("approved image record is not canonical")
    if canonical["artifact"] != validate_artifact(expected_artifact):
        raise ContractError("approved image record belongs to another artifact")
    if expected_source_sha is not None and canonical["source_sha"] != validate_source_sha(
        expected_source_sha, name="expected source SHA"
    ):
        raise ContractError("approved image record belongs to another source SHA")
    if expected_image is not None and canonical["image"] != validate_image(expected_image):
        raise ContractError("approved image record belongs to another image repository")
    validate_immutable_ref(canonical["ref"], expected_image=canonical["image"])
    return canonical


def deployment_payload(
    *, deployment_source_sha: str, artifact: str, record: dict[str, Any]
) -> dict[str, Any]:
    deployment_source_sha = validate_source_sha(
        deployment_source_sha, name="deployment source SHA"
    )
    artifact = validate_artifact(artifact)
    canonical = validate_record(record, expected_artifact=artifact)
    return {
        "schema": SCHEMA_VERSION,
        "kind": "deployment-oci-artifacts",
        "deployment_source_sha": deployment_source_sha,
        "artifacts": {
            artifact: {
                "source_sha": canonical["source_sha"],
                "image": canonical["image"],
                "platform": canonical["platform"],
                "digest": canonical["digest"],
                "ref": canonical["ref"],
                "reused": deployment_source_sha != canonical["source_sha"],
            }
        },
    }


def validate_deployment_payload(
    payload: Any,
    *,
    expected_artifact: str,
    expected_deployment_source_sha: str,
    expected_image: str,
) -> dict[str, Any]:
    artifact = validate_artifact(expected_artifact)
    deployment_source_sha = validate_source_sha(
        expected_deployment_source_sha, name="expected deployment source SHA"
    )
    image = validate_image(expected_image)
    if not isinstance(payload, dict) or set(payload) != DEPLOYMENT_FIELDS:
        raise ContractError("deployment artifact metadata has an invalid field set")
    if (
        payload.get("schema") != SCHEMA_VERSION
        or payload.get("kind") != "deployment-oci-artifacts"
        or payload.get("deployment_source_sha") != deployment_source_sha
    ):
        raise ContractError("deployment artifact metadata has an invalid identity")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {artifact}:
        raise ContractError("deployment artifact metadata has an invalid artifact set")
    selected = artifacts[artifact]
    if not isinstance(selected, dict) or set(selected) != DEPLOYMENT_ARTIFACT_FIELDS:
        raise ContractError("deployment artifact entry has an invalid field set")
    record = approved_record(
        artifact=artifact,
        source_sha=selected.get("source_sha"),
        image=selected.get("image"),
        platform=selected.get("platform"),
        digest=selected.get("digest"),
    )
    if record["image"] != image or selected.get("ref") != record["ref"]:
        raise ContractError("deployment artifact entry has an invalid immutable reference")
    expected_reused = deployment_source_sha != record["source_sha"]
    if type(selected.get("reused")) is not bool or selected["reused"] != expected_reused:
        raise ContractError("deployment artifact entry has an invalid reuse marker")
    canonical = deployment_payload(
        deployment_source_sha=deployment_source_sha,
        artifact=artifact,
        record=record,
    )
    if payload != canonical:
        raise ContractError("deployment artifact metadata is not canonical")
    return canonical


def _reject_json_constant(value: str) -> NoReturn:
    raise ContractError(f"non-standard JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON metadata {path}: {exc}") from exc


def digest_from_build_metadata(path: Path) -> str:
    payload = load_json(Path(path))
    if not isinstance(payload, dict):
        raise ContractError("Buildx metadata must be a JSON object")
    digest = payload.get("containerimage.digest")
    if not isinstance(digest, str):
        raise ContractError("Buildx metadata is missing containerimage.digest")
    return validate_digest(digest, name="Buildx container image digest")


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_temporary(parent: Path, name: str, content: bytes, *, mode: int) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    previous_umask = os.umask(0o022)
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=parent)
    finally:
        os.umask(previous_umask)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor_open = False
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        return temporary
    except BaseException:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    """Replace a JSON file only after its complete contents reach disk."""

    temporary = _write_temporary(
        path.parent, path.name, _json_bytes(payload), mode=0o644
    )
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def record_immutable_json(path: Path, payload: Any) -> None:
    """Atomically create an immutable record, or verify an identical retry."""

    # Read-only source records make accidental mutation harder on the Linux CI
    # and deployment filesystems.  Windows chmod maps to a shared file attribute
    # across hard links, so keep 0644 there and rely on create-only semantics.
    mode = 0o644 if os.name == "nt" else 0o444
    temporary = _write_temporary(path.parent, path.name, _json_bytes(payload), mode=mode)
    try:
        try:
            os.link(temporary, path)
            _fsync_directory(path.parent)
        except FileExistsError:
            existing = load_json(path)
            if existing != payload:
                raise ContractError(
                    f"immutable record {path} already exists with different contents"
                )
    finally:
        temporary.unlink(missing_ok=True)


class ArtifactReleaseStore:
    """Persistent approvals for one image artifact."""

    def __init__(self, root: Path, *, artifact: str) -> None:
        self.root = Path(root)
        self.artifact = validate_artifact(artifact)
        self.artifact_root = self.root / self.artifact
        self.by_source = self.artifact_root / "by-source"
        self.approved_path = self.artifact_root / "approved.json"

    def record_path(self, source_sha: str) -> Path:
        return self.by_source / f"{validate_source_sha(source_sha)}.json"

    def approve(
        self,
        *,
        source_sha: str,
        image: str,
        platform: str,
        digest: str,
    ) -> dict[str, Any]:
        record = self.record(
            source_sha=source_sha,
            image=image,
            platform=platform,
            digest=digest,
        )
        self.promote(record["source_sha"], expected_image=record["image"])
        return record

    def record(
        self,
        *,
        source_sha: str,
        image: str,
        platform: str,
        digest: str,
    ) -> dict[str, Any]:
        record = approved_record(
            artifact=self.artifact,
            source_sha=source_sha,
            image=image,
            platform=platform,
            digest=digest,
        )
        record_immutable_json(self.record_path(record["source_sha"]), record)
        return record

    def load_source(
        self, source_sha: str, *, expected_image: str
    ) -> dict[str, Any]:
        path = self.record_path(source_sha)
        if not path.is_file():
            raise ContractError(
                f"no immutable image record exists for {self.artifact} at {source_sha}"
            )
        return validate_record(
            load_json(path),
            expected_artifact=self.artifact,
            expected_source_sha=source_sha,
            expected_image=expected_image,
        )

    def promote(self, source_sha: str, *, expected_image: str) -> dict[str, Any]:
        record = self.load_source(source_sha, expected_image=expected_image)
        atomic_write_json(self.approved_path, record)
        return record

    def load_approved(self, *, expected_image: str) -> dict[str, Any]:
        if not self.approved_path.is_file():
            raise ContractError(f"no approved immutable image exists for {self.artifact}")
        record = validate_record(
            load_json(self.approved_path),
            expected_artifact=self.artifact,
            expected_image=expected_image,
        )
        immutable = self.load_source(
            record["source_sha"], expected_image=expected_image
        )
        if immutable != record:
            raise ContractError("approved pointer differs from its immutable source record")
        return record

    def materialize(
        self,
        *,
        deployment_source_sha: str,
        expected_image: str,
        output: Path | None = None,
    ) -> dict[str, Any]:
        record = self.load_approved(expected_image=expected_image)
        payload = deployment_payload(
            deployment_source_sha=deployment_source_sha,
            artifact=self.artifact,
            record=record,
        )
        if output is not None:
            atomic_write_json(Path(output), payload)
        return payload

    def materialize_source(
        self,
        *,
        deployment_source_sha: str,
        artifact_source_sha: str,
        expected_image: str,
        output: Path | None = None,
    ) -> dict[str, Any]:
        record = self.load_source(
            artifact_source_sha, expected_image=expected_image
        )
        payload = deployment_payload(
            deployment_source_sha=deployment_source_sha,
            artifact=self.artifact,
            record=record,
        )
        if output is not None:
            atomic_write_json(Path(output), payload)
        return payload


def _emit(payload: Any, output: str | None) -> None:
    if output:
        atomic_write_json(Path(output), payload)
    else:
        sys.stdout.buffer.write(_json_bytes(payload))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    contract = subparsers.add_parser("contract", help="emit the CI build contract")
    contract.add_argument("--artifact", default="t3code")
    contract.add_argument("--source-sha", required=True)
    contract.add_argument("--image", required=True)
    contract.add_argument("--platform", default="linux/amd64")
    contract.add_argument("--context", required=True)
    contract.add_argument("--dockerfile", required=True)
    contract.add_argument(
        "--metadata-file", default=".immutable-images/t3code-build.json"
    )
    contract.add_argument("--output")

    approve = subparsers.add_parser("approve", help="record a registry digest")
    approve.add_argument("--state-dir", required=True)
    approve.add_argument("--artifact", default="t3code")
    approve.add_argument("--source-sha", required=True)
    approve.add_argument("--image", required=True)
    approve.add_argument("--platform", default="linux/amd64")
    digest_source = approve.add_mutually_exclusive_group(required=True)
    digest_source.add_argument("--digest")
    digest_source.add_argument("--build-metadata")

    materialize = subparsers.add_parser(
        "materialize", help="write exact-digest metadata for one deployment"
    )
    materialize.add_argument("--state-dir", required=True)
    materialize.add_argument("--artifact", default="t3code")
    materialize.add_argument("--deployment-source-sha", required=True)
    materialize.add_argument("--image", required=True)
    materialize.add_argument("--output", required=True)

    verify = subparsers.add_parser(
        "verify-ref", help="reject any non-digest production image reference"
    )
    verify.add_argument("--image", required=True)
    verify.add_argument("--ref", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            payload = build_contract(
                artifact=args.artifact,
                source_sha=args.source_sha,
                image=args.image,
                platform=args.platform,
                context=args.context,
                dockerfile=args.dockerfile,
                metadata_file=args.metadata_file,
            )
            _emit(payload, args.output)
        elif args.command == "approve":
            store = ArtifactReleaseStore(Path(args.state_dir), artifact=args.artifact)
            digest = (
                args.digest
                if args.digest is not None
                else digest_from_build_metadata(Path(args.build_metadata))
            )
            _emit(
                store.approve(
                    source_sha=args.source_sha,
                    image=args.image,
                    platform=args.platform,
                    digest=digest,
                ),
                None,
            )
        elif args.command == "materialize":
            store = ArtifactReleaseStore(Path(args.state_dir), artifact=args.artifact)
            payload = store.materialize(
                deployment_source_sha=args.deployment_source_sha,
                expected_image=args.image,
                output=Path(args.output),
            )
            _emit(payload, None)
        else:
            immutable_ref = validate_immutable_ref(args.ref, expected_image=args.image)
            _emit({"image": args.image, "ref": immutable_ref, "valid": True}, None)
    except (ContractError, OSError) as exc:
        print(f"immutable image release error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
