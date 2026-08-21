from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import sys
import tarfile

import pytest
import yaml

from scripts.ci.compose_release_engine import build_bundle, file_sha256
from scripts.ci import release_launcher as launcher
from tests.helpers import REPO_ROOT


ENGINE = REPO_ROOT / "scripts" / "ci" / "compose_release_engine.py"
TOPOLOGY = REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "topology.json"
HOST_ROLE = REPO_ROOT / "infra" / "ansible" / "roles" / "release_launcher" / "tasks" / "main.yml"


def app_archive(tmp_path: Path) -> Path:
    output = tmp_path / "apps.tar"
    build_bundle(
        target="apps",
        source_sha="1" * 40,
        stack_root=REPO_ROOT / "apps" / "compose" / "homelab",
        engine_path=ENGINE,
        output=output,
        topology_path=TOPOLOGY,
    )
    return output


def test_host_role_is_only_a_stable_launcher_installer() -> None:
    tasks = yaml.safe_load(HOST_ROLE.read_text(encoding="utf-8"))
    modules = {
        key
        for task in tasks
        for key in task
        if key.startswith("ansible.builtin.")
    }
    assert modules <= {"ansible.builtin.copy", "ansible.builtin.file"}
    assert all(
        "when" not in task
        and "register" not in task
        and "block" not in task
        and "rescue" not in task
        for task in tasks
    )

    executable_installs = [
        task["ansible.builtin.copy"]
        for task in tasks
        if task.get("ansible.builtin.copy", {}).get("mode") == "0755"
        and task["ansible.builtin.copy"].get("dest", "").startswith("/usr/local/")
    ]
    assert len(executable_installs) == 1
    source = executable_installs[0]["src"]
    assert Path(source).name == Path(launcher.__file__).name


def test_launcher_verifies_archive_and_passes_secret_to_shipped_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = app_archive(tmp_path)
    secret_bundle = tmp_path / "apps.json"
    secret_bundle.write_text('{"component":"apps","version":1}\n', encoding="utf-8")
    captured: list[str] = []

    def capture(argv: list[str]) -> int:
        captured.extend(argv)
        assert Path(argv[1]).is_file()
        assert Path(argv[argv.index("--bundle-root") + 1]).is_dir()
        return 0

    monkeypatch.setattr(launcher, "_run_engine", capture)
    result = launcher.deploy_archive(
        target="apps",
        archive=archive,
        expected_sha256=file_sha256(archive),
        secret_bundle=secret_bundle,
        install_root=tmp_path / "install",
    )
    assert result == 0
    assert captured[2:5] == ["deploy", "--target", "apps"]
    assert captured[captured.index("--secret-bundle") + 1] == str(secret_bundle)
    assert captured[captured.index("--bundle-root") + 1] != str(archive)


def test_launcher_rejects_checksum_mismatch_before_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = app_archive(tmp_path)
    secret_bundle = tmp_path / "apps.json"
    secret_bundle.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        launcher,
        "_run_engine",
        lambda _argv: pytest.fail("unverified archive reached engine"),
    )
    with pytest.raises(launcher.LauncherError, match="mismatch"):
        launcher.deploy_archive(
            target="apps",
            archive=archive,
            expected_sha256="0" * 64,
            secret_bundle=secret_bundle,
            install_root=tmp_path / "install",
        )


def test_safe_extract_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as bundle:
        member = tarfile.TarInfo("payload/stack/../../../escape")
        member.size = 1
        bundle.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(launcher.LauncherError, match="unsafe"):
        launcher.safe_extract(archive, tmp_path / "extract")
    assert not (tmp_path / "escape").exists()


def test_embedded_engine_must_match_its_exact_manifest_checksum(tmp_path: Path) -> None:
    archive = app_archive(tmp_path)
    extracted = tmp_path / "extracted"
    launcher.safe_extract(archive, extracted)
    engine = extracted / launcher.ENGINE_PATH
    engine.write_bytes(engine.read_bytes() + b"\n# changed after bundle creation\n")
    with pytest.raises(launcher.LauncherError, match="SHA-256 differs"):
        launcher.validate_embedded_engine(extracted, expected_target="apps")


def test_installed_commands_use_pending_versioned_engine_for_recovery(tmp_path: Path) -> None:
    install = tmp_path / "install"
    release_id = "a" * 64
    engine = install / "compose-releases" / release_id / launcher.ENGINE_PATH
    engine.parent.mkdir(parents=True)
    shutil.copy2(ENGINE, engine)
    descriptor = {
        "version": launcher.ENGINE_VERSION,
        "path": launcher.ENGINE_PATH,
        "sha256": file_sha256(engine),
    }
    state = {
        "schema": 1,
        "target": "openclaw",
        "current": None,
        "pending": {"candidate": {"release_id": release_id, "engine": descriptor}},
    }
    state_path = install / "compose-control" / "release-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert launcher._installed_engine(install, target="openclaw") == engine


def test_sync_secret_cli_is_fixed_to_component_targets() -> None:
    args = launcher.build_parser().parse_args(
        [
            "sync-secrets",
            "--target",
            "openclaw",
            "--secret-bundle",
            "/tmp/openclaw.json",
        ]
    )
    assert args.command == "sync-secrets"
    assert args.target == "openclaw"
    assert args.secret_bundle == Path("/tmp/openclaw.json")


def test_sync_secret_launcher_runs_installed_engine_without_release_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "openclaw.json"
    incoming.write_text('{"component":"openclaw","version":1}', encoding="utf-8")
    captured: list[str] = []
    monkeypatch.setattr(launcher, "_installed_engine", lambda *_args, **_kwargs: ENGINE)

    def capture(argv: list[str]) -> int:
        captured.extend(argv)
        return 0

    monkeypatch.setattr(launcher, "_run_engine", capture)
    assert (
        launcher.run_installed(
            command="sync-secrets",
            target="openclaw",
            install_root=tmp_path / "install",
            secret_bundle=incoming,
        )
        == 0
    )
    assert captured[2:5] == ["sync-secrets", "--target", "openclaw"]
    assert captured[captured.index("--secret-bundle") + 1] == str(incoming)
    assert "--bundle-root" not in captured
