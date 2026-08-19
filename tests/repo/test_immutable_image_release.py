from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.ci import immutable_image_release as release


SHA_A = "a" * 40
SHA_B = "b" * 40
DIGEST_A = "sha256:" + "1" * 64
DIGEST_B = "sha256:" + "2" * 64
IMAGE = "ghcr.io/example/homelab-t3code"


def _contract() -> dict:
    return release.build_contract(
        artifact="t3code",
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        context="apps/images/t3code",
        dockerfile="apps/images/t3code/Dockerfile",
        metadata_file=".immutable-images/t3code-build.json",
    )


def _store(tmp_path: Path) -> release.ArtifactReleaseStore:
    return release.ArtifactReleaseStore(tmp_path / "state", artifact="t3code")


def test_build_contract_is_deterministic_and_source_addressed():
    first = _contract()
    second = _contract()

    assert first == second
    assert first["kind"] == "immutable-oci-build-contract"
    assert first["source_sha"] == SHA_A
    assert first["staging_ref"] == f"{IMAGE}:source-{SHA_A}"
    assert first["build_argv"][-1] == "apps/images/t3code"
    assert "--push" in first["build_argv"]
    assert "--provenance=true" in first["build_argv"]
    assert "latest" not in json.dumps(first)


def test_build_contract_uses_same_build_metadata_instead_of_resolving_tag():
    contract = _contract()

    metadata = ".immutable-images/t3code-build.json"
    metadata_index = contract["build_argv"].index("--metadata-file")
    assert contract["build_argv"][metadata_index + 1] == metadata
    assert contract["digest_source"] == {
        "metadata_file": metadata,
        "json_field": "containerimage.digest",
    }
    assert "imagetools" not in json.dumps(contract)
    assert contract["approval_requirement"].endswith("sha256:<64-hex>")


@pytest.mark.parametrize(
    "source_sha",
    ["", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "a" * 40 + "\n"],
)
def test_source_sha_requires_exact_lowercase_commit(source_sha: str):
    with pytest.raises(release.ContractError, match="lowercase 40-character"):
        release.validate_source_sha(source_sha)


@pytest.mark.parametrize(
    "image",
    [
        "ubuntu",
        "example/image",
        "https://ghcr.io/example/image",
        "ghcr.io/Example/image",
        "ghcr.io/example/image:latest",
        f"{IMAGE}@{DIGEST_A}",
        "ghcr.io//image",
        "ghcr.io/example/../image",
        "ghcr.io/example/image name",
        "ghcr..io/example/image",
        "-registry.example/example/image",
        "registry.example:0/example/image",
        "registry.example:05000/example/image",
        "registry.example:65536/example/image",
    ],
)
def test_image_input_forbids_ambiguous_or_mutable_references(image: str):
    with pytest.raises(release.ContractError, match="image|port"):
        release.validate_image(image)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/example/t3code",
        "registry.example:5000/team/t3code",
        "localhost/example/t3code",
        "localhost:5000/example/t3code",
    ],
)
def test_image_input_accepts_canonical_registry_repositories(image: str):
    assert release.validate_image(image) == image


@pytest.mark.parametrize(
    "digest",
    [
        "",
        "1" * 64,
        "sha256:" + "1" * 63,
        "sha256:" + "1" * 65,
        "sha256:" + "A" * 64,
        "sha512:" + "1" * 64,
        DIGEST_A + "\n",
    ],
)
def test_digest_requires_exact_canonical_sha256(digest: str):
    with pytest.raises(release.ContractError, match="exact lowercase"):
        release.validate_digest(digest)


def test_immutable_ref_requires_digest_and_expected_repository():
    immutable = f"{IMAGE}@{DIGEST_A}"

    assert release.validate_immutable_ref(immutable, expected_image=IMAGE) == immutable
    with pytest.raises(release.ContractError, match="repository@sha256"):
        release.validate_immutable_ref(f"{IMAGE}:0.0.28", expected_image=IMAGE)
    with pytest.raises(release.ContractError, match="does not match"):
        release.validate_immutable_ref(
            f"ghcr.io/other/t3code@{DIGEST_A}", expected_image=IMAGE
        )


@pytest.mark.parametrize(
    ("context", "dockerfile"),
    [
        ("/absolute", "/absolute/Dockerfile"),
        ("apps//homelab", "apps/homelab/Dockerfile"),
        ("apps/../homelab", "apps/homelab/Dockerfile"),
        ("apps/homelab", "apps/other/Dockerfile"),
        ("apps/homelab", "../Dockerfile"),
        ("apps\\homelab", "apps\\homelab\\Dockerfile"),
    ],
)
def test_build_paths_must_be_canonical_and_dockerfile_stays_in_context(
    context: str, dockerfile: str
):
    with pytest.raises(release.ContractError, match="context|Dockerfile"):
        release.build_contract(
            artifact="t3code",
            source_sha=SHA_A,
            image=IMAGE,
            platform="linux/amd64",
            context=context,
            dockerfile=dockerfile,
            metadata_file=".immutable-images/t3code-build.json",
        )


def test_buildx_metadata_yields_the_exact_pushed_digest(tmp_path: Path):
    metadata = tmp_path / "build.json"
    metadata.write_text(
        json.dumps(
            {
                "buildx.build.ref": "builder/example/example",
                "containerimage.digest": DIGEST_A,
            }
        ),
        encoding="utf-8",
    )

    assert release.digest_from_build_metadata(metadata) == DIGEST_A


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"containerimage.digest": f"{IMAGE}:latest"},
        {"containerimage.digest": "sha256:" + "A" * 64},
        {"containerimage.digest": None},
    ],
)
def test_buildx_metadata_fails_closed_without_canonical_digest(
    tmp_path: Path, payload: object
):
    metadata = tmp_path / "build.json"
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(release.ContractError, match="metadata|digest"):
        release.digest_from_build_metadata(metadata)


def test_approval_atomically_records_source_and_current_pointer(tmp_path: Path):
    store = _store(tmp_path)
    record = store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )

    source_record = json.loads(store.record_path(SHA_A).read_text(encoding="utf-8"))
    approved = json.loads(store.approved_path.read_text(encoding="utf-8"))
    assert source_record == approved == record
    assert record["ref"] == f"{IMAGE}@{DIGEST_A}"
    assert ":source-" not in json.dumps(record)
    if os.name != "nt":
        assert store.record_path(SHA_A).stat().st_mode & 0o777 == 0o444
        assert store.approved_path.stat().st_mode & 0o777 == 0o644


def test_identical_approval_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    values = {
        "source_sha": SHA_A,
        "image": IMAGE,
        "platform": "linux/amd64",
        "digest": DIGEST_A,
    }

    assert store.approve(**values) == store.approve(**values)
    assert len(list(store.by_source.glob("*.json"))) == 1


def test_candidate_record_is_not_approved_until_explicit_promotion(tmp_path: Path):
    store = _store(tmp_path)
    candidate = store.record(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )

    assert json.loads(store.record_path(SHA_A).read_text(encoding="utf-8")) == candidate
    assert not store.approved_path.exists()
    with pytest.raises(release.ContractError, match="no approved"):
        store.materialize(deployment_source_sha=SHA_A, expected_image=IMAGE)

    candidate_payload = store.materialize_source(
        deployment_source_sha=SHA_A,
        artifact_source_sha=SHA_A,
        expected_image=IMAGE,
    )
    assert candidate_payload["artifacts"]["t3code"]["ref"] == f"{IMAGE}@{DIGEST_A}"
    assert candidate_payload["artifacts"]["t3code"]["reused"] is False

    assert store.promote(SHA_A, expected_image=IMAGE) == candidate
    assert store.load_approved(expected_image=IMAGE) == candidate


def test_source_sha_record_cannot_be_rebound_to_another_digest(tmp_path: Path):
    store = _store(tmp_path)
    original = store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )

    with pytest.raises(release.ContractError, match="different contents"):
        store.approve(
            source_sha=SHA_A,
            image=IMAGE,
            platform="linux/amd64",
            digest=DIGEST_B,
        )

    assert store.load_approved(expected_image=IMAGE) == original


def test_new_approval_retains_prior_source_record_and_moves_pointer(tmp_path: Path):
    store = _store(tmp_path)
    old = store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    new = store.approve(
        source_sha=SHA_B,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_B,
    )

    assert json.loads(store.record_path(SHA_A).read_text(encoding="utf-8")) == old
    assert json.loads(store.record_path(SHA_B).read_text(encoding="utf-8")) == new
    assert store.load_approved(expected_image=IMAGE) == new


def test_an_old_record_can_be_explicitly_reapproved_for_rollback(tmp_path: Path):
    store = _store(tmp_path)
    old_values = {
        "source_sha": SHA_A,
        "image": IMAGE,
        "platform": "linux/amd64",
        "digest": DIGEST_A,
    }
    old = store.approve(**old_values)
    store.approve(
        source_sha=SHA_B,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_B,
    )

    store.approve(**old_values)

    assert store.load_approved(expected_image=IMAGE) == old


def test_unrelated_deployment_reuses_last_approved_exact_digest(tmp_path: Path):
    store = _store(tmp_path)
    store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )

    release_payload = store.materialize(
        deployment_source_sha=SHA_B,
        expected_image=IMAGE,
    )

    artifact = release_payload["artifacts"]["t3code"]
    assert release_payload["deployment_source_sha"] == SHA_B
    assert artifact == {
        "source_sha": SHA_A,
        "image": IMAGE,
        "platform": "linux/amd64",
        "digest": DIGEST_A,
        "ref": f"{IMAGE}@{DIGEST_A}",
        "reused": True,
    }
    assert "staging_ref" not in json.dumps(release_payload)
    assert (
        release.validate_deployment_payload(
            release_payload,
            expected_artifact="t3code",
            expected_deployment_source_sha=SHA_B,
            expected_image=IMAGE,
        )
        == release_payload
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("ref", f"{IMAGE}:latest", "immutable reference"),
        ("digest", DIGEST_B, "immutable reference"),
        ("reused", False, "reuse marker"),
        ("source_sha", SHA_B, "reuse marker"),
    ],
)
def test_deployment_metadata_tampering_fails_closed(
    tmp_path: Path, field: str, value: object, message: str
):
    store = _store(tmp_path)
    store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    payload = store.materialize(
        deployment_source_sha=SHA_B,
        expected_image=IMAGE,
    )
    payload["artifacts"]["t3code"][field] = value

    with pytest.raises(release.ContractError, match=message):
        release.validate_deployment_payload(
            payload,
            expected_artifact="t3code",
            expected_deployment_source_sha=SHA_B,
            expected_image=IMAGE,
        )


def test_deployment_for_artifact_source_is_not_reported_as_reuse(tmp_path: Path):
    store = _store(tmp_path)
    store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )

    payload = store.materialize(
        deployment_source_sha=SHA_A,
        expected_image=IMAGE,
    )

    assert payload["artifacts"]["t3code"]["reused"] is False


def test_materialize_writes_complete_release_metadata_atomically(tmp_path: Path):
    store = _store(tmp_path)
    store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    output = tmp_path / "release" / "artifacts.json"

    expected = store.materialize(
        deployment_source_sha=SHA_B,
        expected_image=IMAGE,
        output=output,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == expected
    assert not list(output.parent.glob(".artifacts.json.*"))


def test_atomic_output_failure_preserves_previous_release_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _store(tmp_path)
    store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    output = tmp_path / "release.json"
    output.write_text('{"previous":true}\n', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected atomic replace failure")

    monkeypatch.setattr(release.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected atomic"):
        store.materialize(
            deployment_source_sha=SHA_B,
            expected_image=IMAGE,
            output=output,
        )

    assert output.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert not list(tmp_path.glob(".release.json.*"))


def test_missing_or_wrong_repository_approval_fails_closed(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(release.ContractError, match="no approved"):
        store.materialize(deployment_source_sha=SHA_A, expected_image=IMAGE)

    store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    with pytest.raises(release.ContractError, match="another image repository"):
        store.materialize(
            deployment_source_sha=SHA_A,
            expected_image="ghcr.io/other/t3code",
        )


def test_tampered_mutable_approved_pointer_fails_closed(tmp_path: Path):
    store = _store(tmp_path)
    record = store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    record["ref"] = f"{IMAGE}:latest"
    store.approved_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(release.ContractError, match="canonical"):
        store.materialize(deployment_source_sha=SHA_B, expected_image=IMAGE)


def test_pointer_must_match_immutable_source_record(tmp_path: Path):
    store = _store(tmp_path)
    record = store.approve(
        source_sha=SHA_A,
        image=IMAGE,
        platform="linux/amd64",
        digest=DIGEST_A,
    )
    record["digest"] = DIGEST_B
    record["ref"] = f"{IMAGE}@{DIGEST_B}"
    store.approved_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(release.ContractError, match="differs"):
        store.load_approved(expected_image=IMAGE)


def test_duplicate_json_keys_are_rejected(tmp_path: Path):
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"schema":1,"schema":1}\n', encoding="utf-8")

    with pytest.raises(release.ContractError, match="duplicate JSON key"):
        release.load_json(metadata)


def test_cli_round_trip_emits_only_exact_digest_deployment_ref(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
):
    state = tmp_path / "state"
    output = tmp_path / "release.json"
    assert (
        release.main(
            [
                "approve",
                "--state-dir",
                str(state),
                "--source-sha",
                SHA_A,
                "--image",
                IMAGE,
                "--digest",
                DIGEST_A,
            ]
        )
        == 0
    )
    capfd.readouterr()

    assert (
        release.main(
            [
                "materialize",
                "--state-dir",
                str(state),
                "--deployment-source-sha",
                SHA_B,
                "--image",
                IMAGE,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    emitted = json.loads(capfd.readouterr().out)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert emitted == written
    assert written["artifacts"]["t3code"]["ref"] == f"{IMAGE}@{DIGEST_A}"


def test_cli_approval_consumes_buildx_metadata_without_tag_lookup(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
):
    metadata = tmp_path / "buildx.json"
    metadata.write_text(
        json.dumps({"containerimage.digest": DIGEST_A}), encoding="utf-8"
    )

    status = release.main(
        [
            "approve",
            "--state-dir",
            str(tmp_path / "state"),
            "--source-sha",
            SHA_A,
            "--image",
            IMAGE,
            "--build-metadata",
            str(metadata),
        ]
    )

    assert status == 0
    assert json.loads(capfd.readouterr().out)["ref"] == f"{IMAGE}@{DIGEST_A}"


def test_cli_verify_ref_rejects_mutable_tag(capfd: pytest.CaptureFixture[str]):
    status = release.main(
        ["verify-ref", "--image", IMAGE, "--ref", f"{IMAGE}:latest"]
    )

    captured = capfd.readouterr()
    assert status == 2
    assert "repository@sha256" in captured.err
    assert captured.out == ""
