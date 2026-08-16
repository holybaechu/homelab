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
    assert '"/run/openclaw-gateway/gateway_token"' in source
    assert '"/run/openclaw-credential-probe/gateway_token"' in source
    assert '"/run/openclaw-gateway/discord_bot_token"' in source
    assert '"/run/openclaw-credential-probe/discord_bot_token"' in source
    assert '"/run/openclaw-gateway/ctf_docker_client_key"' in source
    assert "MAX_GATEWAY_TOKEN_READ_BYTES = 66" in source
    assert "MAX_DISCORD_TOKEN_BYTES = 4096" in source
    assert "def is_valid_discord_token(" in source
    assert "def credential_spec(" in source
    assert "def parse_arguments(" in source
    assert 'argv[1] != "--owner"' in source
    assert "owner.count(\":\") != 1" in source
    assert "uid < 1 or gid < 1" in source
    assert "os.O_EXCL" in source
    assert "os.O_NOFOLLOW" in source
    assert "os.fchmod(temp_fd, EXPECTED_FILE_MODE)" in source
    assert "os.replace(temp_path, destination)" in source
    assert "os.fsync(temp_fd)" in source
    assert "os.fsync(directory_fd)" in source
    assert "st_nlink != 1" in source
    assert "st_size != expected_size" in source
    assert source.count("destination_stat.st_uid != uid") == 1
    assert source.count("destination_stat.st_gid != gid") == 1
    assert "print(" not in source
    assert "sys.stdout" not in source
    assert "sys.stderr" not in source


def test_materializer_accepts_the_explicit_ctf_service_owner_without_changing_core_default():
    helper = load_helper()

    assert helper.parse_arguments([str(HELPER), "source", "gateway_token"]) == (
        1000,
        1000,
        "source",
        "gateway_token",
    )
    assert helper.parse_arguments(
        [str(HELPER), "--owner", "1001:1001", "source", "gateway_token"]
    ) == (1001, 1001, "source", "gateway_token")
    for invalid in (
        [str(HELPER), "--owner", "1001", "source", "gateway_token"],
        [str(HELPER), "--owner", "0:1001", "source", "gateway_token"],
        [str(HELPER), "--owner", "1001:0", "source", "gateway_token"],
        [str(HELPER), "--owner", "1001:1001:1", "source", "gateway_token"],
    ):
        assert helper.parse_arguments(invalid) is None


def test_materializer_allows_only_the_fixed_credential_destinations():
    helper = load_helper()

    for destination in (
        "/run/openclaw-gateway/gateway_token",
        "/run/openclaw-credential-probe/gateway_token",
        "/run/openclaw-gateway/discord_bot_token",
        "/run/openclaw-credential-probe/discord_bot_token",
        "/run/openclaw-gateway/ctf_docker_client_key",
    ):
        assert helper.credential_spec(destination) is not None
    for destination in (
        "/run/openclaw-gateway/other_token",
        "/tmp/gateway_token",
        "/run/openclaw-gateway/../openclaw-gateway/gateway_token",
        "/run/openclaw-credential-probe/other_token",
    ):
        assert helper.credential_spec(destination) is None


@pytest.mark.parametrize(
    "payload",
    [
        b"MTIz.NDU2-abc_DEF",
        b"MTIz.NDU2-abc_DEF\n",
        b"a" * 4096,
    ],
)
def test_materializer_accepts_bounded_printable_discord_tokens(payload):
    helper = load_helper()

    assert helper.is_valid_discord_token(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"token\r\n",
        b"token\nother",
        b"token\x00",
        b"a" * 4097,
    ],
)
def test_materializer_rejects_invalid_discord_tokens(payload):
    helper = load_helper()

    assert helper.is_valid_discord_token(payload) is False


def test_materializer_accepts_only_canonical_openssh_private_key_envelopes():
    helper = load_helper()
    import base64

    body = base64.b64encode(b"openssh-key-v1\x00fixture")
    valid = (
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + body
        + b"\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    assert helper.is_valid_openssh_private_key(valid) is True
    for invalid in (
        valid.rstrip(b"\n"),
        valid.replace(body, base64.b64encode(b"invalid-key-v1\x00fixture"), 1),
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n!!!!\n-----END OPENSSH PRIVATE KEY-----\n",
        b"x" * 16385,
    ):
        assert helper.is_valid_openssh_private_key(invalid) is False


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
    monkeypatch.setattr(helper, "GATEWAY_TOKEN_DESTINATIONS", frozenset({str(destination)}))

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
def test_materializer_honors_an_explicit_service_owner(
    tmp_path, monkeypatch, capsys
):
    helper = load_helper()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    source = tmp_path / "credential"
    payload = b"a1" * 32 + b"\n"
    source.write_bytes(payload)
    source.chmod(0o400)
    destination = runtime / "gateway_token"
    monkeypatch.setattr(helper, "GATEWAY_TOKEN_DESTINATIONS", frozenset({str(destination)}))

    monkeypatch.setattr(
        helper.sys,
        "argv",
        [
            str(HELPER),
            "--owner",
            f"{os.getuid()}:{os.getgid()}",
            str(source),
            str(destination),
        ],
    )

    assert helper.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    target_stat = destination.lstat()
    assert stat.S_ISREG(target_stat.st_mode)
    assert stat.S_IMODE(target_stat.st_mode) == 0o400
    assert target_stat.st_uid == os.getuid()
    assert target_stat.st_gid == os.getgid()


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership required")
def test_materializer_atomically_materializes_the_discord_token_without_output(
    tmp_path, monkeypatch, capsys
):
    helper = load_helper()
    monkeypatch.setattr(helper, "EXPECTED_UID", os.getuid())
    monkeypatch.setattr(helper, "EXPECTED_GID", os.getgid())
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    source = tmp_path / "credential"
    payload = b"MTIz.NDU2-abc_DEF\n"
    source.write_bytes(payload)
    source.chmod(0o400)
    destination = runtime / "discord_bot_token"
    monkeypatch.setattr(helper, "DISCORD_TOKEN_DESTINATIONS", frozenset({str(destination)}))

    monkeypatch.setattr(
        helper.sys,
        "argv",
        [str(HELPER), str(source), str(destination)],
    )

    assert helper.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    target_stat = destination.lstat()
    assert stat.S_ISREG(target_stat.st_mode)
    assert stat.S_IMODE(target_stat.st_mode) == 0o400
    assert target_stat.st_uid == os.getuid()
    assert target_stat.st_gid == os.getgid()
    assert target_stat.st_nlink == 1
    assert target_stat.st_size == len(payload)
    assert destination.read_bytes() == payload
    assert not list(runtime.glob(".discord_bot_token.*"))


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
    monkeypatch.setattr(helper, "GATEWAY_TOKEN_DESTINATIONS", frozenset({str(destination)}))
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
