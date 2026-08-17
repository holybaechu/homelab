#!/usr/bin/python3 -I
"""Materialize fixed systemd credentials for OpenClaw without printing them."""

from __future__ import annotations

import base64
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable


EXPECTED_UID = 1000
EXPECTED_GID = 1000
EXPECTED_DIRECTORY_MODE = 0o700
EXPECTED_FILE_MODE = 0o400
GATEWAY_TOKEN_DESTINATIONS = frozenset(
    {
        "/run/openclaw-gateway/gateway_token",
        "/run/openclaw-credential-probe/gateway_token",
    }
)
DISCORD_TOKEN_DESTINATIONS = frozenset(
    {
        "/run/openclaw-gateway/discord_bot_token",
        "/run/openclaw-credential-probe/discord_bot_token",
    }
)
EXA_API_KEY_DESTINATIONS = frozenset(
    {
        "/run/openclaw-gateway/exa_api_key",
        "/run/openclaw-credential-probe/exa_api_key",
    }
)
CTF_DOCKER_KEY_DESTINATIONS = frozenset(
    {"/run/openclaw-gateway/ctf_docker_client_key"}
)
GATEWAY_TOKEN_PATTERN = re.compile(rb"[0-9A-Fa-f]{64}\n\Z")
MAX_GATEWAY_TOKEN_READ_BYTES = 66
MAX_DISCORD_TOKEN_BYTES = 4096
MAX_DISCORD_TOKEN_READ_BYTES = MAX_DISCORD_TOKEN_BYTES + 1
MAX_EXA_API_KEY_BYTES = 4096
MAX_EXA_API_KEY_READ_BYTES = MAX_EXA_API_KEY_BYTES + 1
MAX_CTF_DOCKER_KEY_BYTES = 16384
MAX_CTF_DOCKER_KEY_READ_BYTES = MAX_CTF_DOCKER_KEY_BYTES + 1


def parse_arguments(argv: list[str]) -> tuple[int, int, str, str] | None:
    """Accept the fixed core owner or an explicit root-owned service owner."""
    if len(argv) == 3:
        source, destination = argv[1:]
        return EXPECTED_UID, EXPECTED_GID, source, destination

    if len(argv) != 5 or argv[1] != "--owner":
        return None

    owner, source, destination = argv[2:]
    if owner.count(":") != 1:
        return None
    raw_uid, raw_gid = owner.split(":", 1)
    if not raw_uid.isdecimal() or not raw_gid.isdecimal():
        return None
    uid, gid = int(raw_uid), int(raw_gid)
    if uid < 1 or gid < 1 or uid > 2**31 - 1 or gid > 2**31 - 1:
        return None
    return uid, gid, source, destination


def is_valid_discord_token(payload: bytes) -> bool:
    """Permit one bounded printable token with an optional terminal newline."""
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    return (
        0 < len(payload) <= MAX_DISCORD_TOKEN_BYTES
        and all(0x21 <= byte <= 0x7E for byte in payload)
    )


def is_valid_exa_api_key(payload: bytes) -> bool:
    """Permit one bounded printable Exa API key with an optional newline."""
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    return (
        0 < len(payload) <= MAX_EXA_API_KEY_BYTES
        and all(0x21 <= byte <= 0x7E for byte in payload)
    )


def is_valid_openssh_private_key(payload: bytes) -> bool:
    """Accept one bounded canonical OpenSSH private-key envelope."""
    if not payload.endswith(b"\n") or len(payload) > MAX_CTF_DOCKER_KEY_BYTES:
        return False
    lines = payload.splitlines()
    if (
        len(lines) < 3
        or lines[0] != b"-----BEGIN OPENSSH PRIVATE KEY-----"
        or lines[-1] != b"-----END OPENSSH PRIVATE KEY-----"
        or any(not 1 <= len(line) <= 70 for line in lines[1:-1])
    ):
        return False
    try:
        decoded = base64.b64decode(b"".join(lines[1:-1]), validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    return decoded.startswith(b"openssh-key-v1\x00")


def credential_spec(destination: str) -> tuple[int, Callable[[bytes], bool]] | None:
    """Return the fixed read bound and validator for an allowed destination."""
    if destination in GATEWAY_TOKEN_DESTINATIONS:
        return MAX_GATEWAY_TOKEN_READ_BYTES, lambda payload: (
            GATEWAY_TOKEN_PATTERN.fullmatch(payload) is not None
        )
    if destination in DISCORD_TOKEN_DESTINATIONS:
        return MAX_DISCORD_TOKEN_READ_BYTES, is_valid_discord_token
    if destination in EXA_API_KEY_DESTINATIONS:
        return MAX_EXA_API_KEY_READ_BYTES, is_valid_exa_api_key
    if destination in CTF_DOCKER_KEY_DESTINATIONS:
        return MAX_CTF_DOCKER_KEY_READ_BYTES, is_valid_openssh_private_key
    return None


def require_regular_file(
    path: str, expected_uid: int, expected_gid: int, expected_size: int
) -> os.stat_result:
    file_stat = os.lstat(path)
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError("credential target is not a regular file")
    if file_stat.st_uid != expected_uid or file_stat.st_gid != expected_gid:
        raise RuntimeError("credential target ownership is invalid")
    if stat.S_IMODE(file_stat.st_mode) != EXPECTED_FILE_MODE:
        raise RuntimeError("credential target mode is invalid")
    if file_stat.st_nlink != 1 or file_stat.st_size != expected_size:
        raise RuntimeError("credential target identity is invalid")
    return file_stat


def _main() -> int:
    arguments = parse_arguments(sys.argv)
    if arguments is None:
        return 64

    expected_uid, expected_gid, source, destination = arguments
    spec = credential_spec(destination)
    runtime_directory = os.path.dirname(destination)
    if spec is None or not runtime_directory:
        return 65
    max_read_bytes, payload_is_valid = spec

    uid = os.getuid()
    gid = os.getgid()
    if uid != expected_uid or gid != expected_gid:
        return 66

    directory_stat = os.lstat(runtime_directory)
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != uid
        or directory_stat.st_gid != gid
        or stat.S_IMODE(directory_stat.st_mode) != EXPECTED_DIRECTORY_MODE
    ):
        return 67

    source_stat = os.lstat(source)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
        return 68

    source_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    source_fd = os.open(source, source_flags)
    temp_path = os.path.join(
        runtime_directory, f".{os.path.basename(destination)}.{secrets.token_hex(16)}"
    )
    temp_fd = -1
    try:
        opened_source_stat = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_source_stat.st_mode)
            or opened_source_stat.st_dev != source_stat.st_dev
            or opened_source_stat.st_ino != source_stat.st_ino
            or opened_source_stat.st_nlink != 1
        ):
            return 69
        payload = os.read(source_fd, max_read_bytes)
        if os.read(source_fd, 1) or not payload_is_valid(payload):
            return 70

        temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            temp_flags |= os.O_NOFOLLOW
        temp_fd = os.open(temp_path, temp_flags, EXPECTED_FILE_MODE)
        os.fchmod(temp_fd, EXPECTED_FILE_MODE)
        written = 0
        while written < len(payload):
            count = os.write(temp_fd, payload[written:])
            if count <= 0:
                return 71
            written += count
        os.fsync(temp_fd)
        temp_stat = os.fstat(temp_fd)
        if (
            not stat.S_ISREG(temp_stat.st_mode)
            or temp_stat.st_uid != uid
            or temp_stat.st_gid != gid
            or stat.S_IMODE(temp_stat.st_mode) != EXPECTED_FILE_MODE
            or temp_stat.st_nlink != 1
            or temp_stat.st_size != len(payload)
        ):
            return 71
        os.close(temp_fd)
        temp_fd = -1

        os.replace(temp_path, destination)
        require_regular_file(destination, uid, gid, len(payload))
        destination_fd = os.open(destination, source_flags)
        try:
            destination_stat = os.fstat(destination_fd)
            path_stat = os.lstat(destination)
            if (
                not stat.S_ISREG(destination_stat.st_mode)
                or destination_stat.st_uid != uid
                or destination_stat.st_gid != gid
                or stat.S_IMODE(destination_stat.st_mode) != EXPECTED_FILE_MODE
                or destination_stat.st_nlink != 1
                or destination_stat.st_size != len(payload)
                or destination_stat.st_dev != path_stat.st_dev
                or destination_stat.st_ino != path_stat.st_ino
                or os.read(destination_fd, max_read_bytes) != payload
                or os.read(destination_fd, 1)
            ):
                return 72
        finally:
            os.close(destination_fd)
        directory_fd = os.open(
            runtime_directory, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return 0
    except (OSError, RuntimeError):
        return 73
    finally:
        os.close(source_fd)
        if temp_fd >= 0:
            os.close(temp_fd)
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def main() -> int:
    try:
        return _main()
    except Exception:
        return 73


if __name__ == "__main__":
    raise SystemExit(main())
