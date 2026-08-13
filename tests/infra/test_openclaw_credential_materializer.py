import importlib.util
import os
import stat

import pytest

from tests.helpers import REPO_ROOT


HELPER = (
    REPO_ROOT
    / "infra/ansible/roles/openclaw_native/files/materialize_openclaw_credential.py"
)


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "materialize_openclaw_credential", HELPER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_materializer_has_a_fixed_silent_fail_closed_contract():
    source = HELPER.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/python3 -I\n")
    assert "EXPECTED_UID = 1000" in source
    assert "EXPECTED_GID = 1000" in source
    assert "EXPECTED_DIRECTORY_MODE = 0o700" in source
    assert "EXPECTED_FILE_MODE = 0o400" in source
    assert 're.compile(rb"[0-9A-Fa-f]{64}\\n\\Z")' in source
    assert "MAX_READ_BYTES = 66" in source
    assert "os.O_EXCL" in source
    assert "os.O_NOFOLLOW" in source
    assert "os.fchmod(temp_fd, EXPECTED_FILE_MODE)" in source
    assert "os.replace(temp_path, destination)" in source
    assert "os.fsync(temp_fd)" in source
    assert "os.fsync(directory_fd)" in source
    assert "st_nlink != 1" in source
    assert "st_size != 65" in source
    assert source.count("destination_stat.st_uid != uid") == 1
    assert source.count("destination_stat.st_gid != gid") == 1
    assert "print(" not in source
    assert "sys.stdout" not in source
    assert "sys.stderr" not in source


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership required")
def test_materializer_atomically_replaces_a_planted_symlink_without_touching_target(
    tmp_path, monkeypatch, capsys
):
    helper = load_helper()
    monkeypatch.setattr(helper, "EXPECTED_UID", os.getuid())
    monkeypatch.setattr(helper, "EXPECTED_GID", os.getgid())

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    source = tmp_path / "credential"
    payload = b"a1" * 32 + b"\n"
    source.write_bytes(payload)
    source.chmod(0o400)
    decoy = tmp_path / "decoy"
    decoy.write_bytes(b"unchanged")
    destination = runtime / "gateway_token"
    destination.symlink_to(decoy)

    monkeypatch.setattr(
        helper.sys,
        "argv",
        [str(HELPER), str(source), str(destination)],
    )
    assert helper.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not destination.is_symlink()
    assert destination.read_bytes() == payload
    assert decoy.read_bytes() == b"unchanged"
    target_stat = destination.lstat()
    assert stat.S_ISREG(target_stat.st_mode)
    assert stat.S_IMODE(target_stat.st_mode) == 0o400
    assert target_stat.st_uid == os.getuid()
    assert target_stat.st_gid == os.getgid()
    assert target_stat.st_nlink == 1
    assert target_stat.st_size == 65
    assert not list(runtime.glob(".gateway_token.*"))


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership required")
@pytest.mark.parametrize(
    "payload",
    [
        b"a1" * 32,
        b"a1" * 32 + b"\r\n",
        b"g1" * 32 + b"\n",
        b"a1" * 32 + b"\nextra",
    ],
)
def test_materializer_rejects_every_noncanonical_token_without_output(
    tmp_path, monkeypatch, capsys, payload
):
    helper = load_helper()
    monkeypatch.setattr(helper, "EXPECTED_UID", os.getuid())
    monkeypatch.setattr(helper, "EXPECTED_GID", os.getgid())
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    source = tmp_path / "credential"
    source.write_bytes(payload)
    source.chmod(0o400)
    destination = runtime / "gateway_token"
    monkeypatch.setattr(
        helper.sys,
        "argv",
        [str(HELPER), str(source), str(destination)],
    )

    assert helper.main() != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not destination.exists()
    assert not list(runtime.glob(".gateway_token.*"))
