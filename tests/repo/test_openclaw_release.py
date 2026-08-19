from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from scripts.ci.openclaw_release import (
    ContractError,
    build_contract,
    audit_legacy_recovery,
    bundle_tree,
    canonical_json_bytes,
    create_recovery_manifest,
    create_legacy_recovery_manifest,
    create_release_manifest,
    load_json,
    validate_release_manifest,
    verify_git_checkout,
    LEGACY_GATEWAY_REF,
)


SHA = "a" * 40
DIGEST_A = "sha256:" + "1" * 64
DIGEST_B = "sha256:" + "2" * 64
GATEWAY_REF = f"ghcr.io/example/openclaw-gateway@{DIGEST_A}"
CTF_REF = f"ghcr.io/example/openclaw-ctf@{DIGEST_B}"


def _release(runtime_sha: str = "3" * 64, config_sha: str = "4" * 64):
    return create_release_manifest(
        deployment_source_sha=SHA,
        platform="linux/amd64",
        gateway_ref=GATEWAY_REF,
        ctf_ref=CTF_REF,
        runtime_sha256=runtime_sha,
        config_commit="b" * 40,
        config_sha256=config_sha,
    )


def test_bundle_is_byte_reproducible_and_canonical(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "config.json").write_text('{"ok":true}\n', encoding="utf-8")
    executable = source / "run.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"

    digest = bundle_tree(source, first)
    os.utime(executable, (2_000_000_000, 2_000_000_000))
    assert bundle_tree(source, second) == digest
    assert first.read_bytes() == second.read_bytes()

    with tarfile.open(first, "r:") as archive:
        members = {member.name.rstrip("/"): member for member in archive.getmembers()}
    assert members["nested/config.json"].uid == 0
    assert members["nested/config.json"].mtime == 0
    assert members["nested/config.json"].mode == 0o644
    if os.name != "nt":
        assert members["run.sh"].mode == 0o755


def test_bundle_rejects_symlink_and_output_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("x", encoding="utf-8")
    with pytest.raises(ContractError, match="outside"):
        bundle_tree(source, source / "bundle.tar")
    if hasattr(os, "symlink"):
        try:
            os.symlink(source / "file", source / "link")
        except OSError:
            pytest.skip("test account cannot create symlinks")
        with pytest.raises(ContractError, match="symlink"):
            bundle_tree(source, tmp_path / "bundle.tar")


def test_git_config_bundle_requires_exact_clean_commit(tmp_path: Path) -> None:
    repo = tmp_path / "config"
    repo.mkdir()
    commands = (
        ["git", "init", "-q", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "ci@example.invalid"],
        ["git", "-C", str(repo), "config", "user.name", "CI"],
    )
    for command in commands:
        subprocess.run(command, check=True)
    (repo / "openclaw.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-qm", "config"],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    verify_git_checkout(repo, commit)
    with pytest.raises(ContractError, match="exact promoted commit"):
        verify_git_checkout(repo, "f" * 40)
    (repo / "dirty").write_text("x", encoding="utf-8")
    with pytest.raises(ContractError, match="clean"):
        verify_git_checkout(repo, commit)


def test_manifest_is_canonical_exact_and_self_identifying() -> None:
    manifest = _release()
    assert validate_release_manifest(manifest) == manifest
    body = dict(manifest)
    release_id = body.pop("release_id")
    assert release_id == hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    assert manifest["gateway"]["ref"] == GATEWAY_REF
    assert manifest["ctf"]["ref"] == CTF_REF

    changed = json.loads(json.dumps(manifest))
    changed["gateway"]["digest"] = DIGEST_B
    with pytest.raises(ContractError, match="canonical"):
        validate_release_manifest(changed)
    with pytest.raises(ContractError, match="repository@sha256"):
        create_release_manifest(
            deployment_source_sha=SHA,
            platform="linux/amd64",
            gateway_ref="ghcr.io/example/gateway:latest",
            ctf_ref=CTF_REF,
            runtime_sha256="3" * 64,
            config_commit="b" * 40,
            config_sha256="4" * 64,
        )


def test_json_loader_rejects_duplicate_keys_and_nonstandard_values(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":1,"schema":1}\n', encoding="utf-8")
    with pytest.raises(ContractError, match="duplicate"):
        load_json(duplicate)
    duplicate.write_text('{"value":NaN}\n', encoding="utf-8")
    with pytest.raises(ContractError, match="non-standard"):
        load_json(duplicate)


def test_build_contract_is_ci_push_only_and_all_bases_are_exact() -> None:
    contract = build_contract(
        source_sha=SHA,
        gateway_image="ghcr.io/example/openclaw-gateway",
        ctf_image="ghcr.io/example/openclaw-ctf",
        gateway_base_ref="ghcr.io/openclaw/openclaw@sha256:" + "5" * 64,
        python_base_ref="docker.io/library/python@sha256:" + "8" * 64,
        docker_cli_ref="docker.io/library/docker@sha256:" + "9" * 64,
        ctf_base_ref="docker.io/kalilinux/kali-rolling@sha256:" + "6" * 64,
        uv_base_ref="ghcr.io/astral-sh/uv@sha256:" + "7" * 64,
        platform="linux/amd64",
    )
    assert set(contract["artifacts"]) == {"gateway", "ctf"}
    for artifact in contract["artifacts"].values():
        argv = artifact["build_argv"]
        assert argv[:3] == ["docker", "buildx", "build"]
        assert "--push" in argv
        assert "--provenance=true" in argv
        assert "--sbom=true" in argv
        assert f":source-{SHA}" in argv[argv.index("--tag") + 1]
        for index, value in enumerate(argv):
            if value == "--build-arg":
                assert "@sha256:" in argv[index + 1]


def test_offline_recovery_manifest_hashes_every_rebuildable_artifact(tmp_path: Path) -> None:
    paths = {name: tmp_path / name for name in ("runtime.tar", "config.tar", "gateway.oci", "ctf.oci")}
    for name, path in paths.items():
        path.write_bytes(name.encode("ascii"))
    release = _release(
        hashlib.sha256(paths["runtime.tar"].read_bytes()).hexdigest(),
        hashlib.sha256(paths["config.tar"].read_bytes()).hexdigest(),
    )
    recovery = create_recovery_manifest(
        release=release,
        runtime_archive=paths["runtime.tar"],
        config_archive=paths["config.tar"],
        gateway_oci_archive=paths["gateway.oci"],
        ctf_oci_archive=paths["ctf.oci"],
    )
    assert recovery["kind"] == "openclaw-offline-recovery"
    assert recovery["release"] == release
    assert set(recovery["artifacts"]) == {
        "runtime_bundle",
        "config_bundle",
        "gateway_oci",
        "ctf_oci",
    }
    for entry in recovery["artifacts"].values():
        assert len(entry["sha256"]) == 64


def test_retired_docker_export_is_exact_auditable_and_restore_rebuildable(
    tmp_path: Path,
) -> None:
    config = tmp_path / "legacy-config.tar"
    image = tmp_path / "legacy-gateway.oci"
    config.write_bytes(b"protected config bundle")
    image.write_bytes(b"fake docker save payload for rollback drill")
    manifest = create_legacy_recovery_manifest(
        config_commit="d" * 40,
        config_archive=config,
        gateway_oci_archive=image,
    )
    assert manifest["gateway"]["ref"] == LEGACY_GATEWAY_REF
    assert ":latest" not in json.dumps(manifest)
    assert audit_legacy_recovery(
        manifest, config_archive=config, gateway_oci_archive=image
    ) == manifest

    # The fake restore drill reconstructs both inputs strictly from the manifest.
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / manifest["config"]["file"]).write_bytes(config.read_bytes())
    (restored / manifest["gateway"]["oci_file"]).write_bytes(image.read_bytes())
    assert audit_legacy_recovery(
        manifest,
        config_archive=restored / manifest["config"]["file"],
        gateway_oci_archive=restored / manifest["gateway"]["oci_file"],
    ) == manifest
    (restored / manifest["gateway"]["oci_file"]).write_bytes(b"tampered")
    with pytest.raises(ContractError, match="OCI archive differs"):
        audit_legacy_recovery(
            manifest,
            config_archive=restored / manifest["config"]["file"],
            gateway_oci_archive=restored / manifest["gateway"]["oci_file"],
        )
