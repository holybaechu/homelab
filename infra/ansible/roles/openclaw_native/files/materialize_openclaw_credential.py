#!/usr/bin/python3 -I
"""Materialize one systemd credential for OpenClaw without printing it."""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys


EXPECTED_UID = 1000
EXPECTED_GID = 1000
EXPECTED_DIRECTORY_MODE = 0o700
EXPECTED_FILE_MODE = 0o400
TOKEN_PATTERN = re.compile(rb"[0-9A-Fa-f]{64}\n\Z")
MAX_READ_BYTES = 66


def require_regular_file(path: str, expected_uid: int, expected_gid: int) -> os.stat_result:
    file_stat = os.lstat(path)
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError("credential target is not a regular file")
    if file_stat.st_uid != expected_uid or file_stat.st_gid != expected_gid:
        raise RuntimeError("credential target ownership is invalid")
    if stat.S_IMODE(file_stat.st_mode) != EXPECTED_FILE_MODE:
        raise RuntimeError("credential target mode is invalid")
    if file_stat.st_nlink != 1 or file_stat.st_size != 65:
        raise RuntimeError("credential target identity is invalid")
    return file_stat


def _main() -> int:
    if len(sys.argv) != 3:
        return 64

    source, destination = sys.argv[1:]
    runtime_directory = os.path.dirname(destination)
    if not runtime_directory or os.path.basename(destination) != "gateway_token":
        return 65

    uid = os.getuid()
    gid = os.getgid()
    if uid != EXPECTED_UID or gid != EXPECTED_GID:
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
    temp_path = os.path.join(runtime_directory, f".gateway_token.{secrets.token_hex(16)}")
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
        payload = os.read(source_fd, MAX_READ_BYTES)
        if os.read(source_fd, 1) or TOKEN_PATTERN.fullmatch(payload) is None:
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
            or temp_stat.st_size != 65
        ):
            return 71
        os.close(temp_fd)
        temp_fd = -1

        os.replace(temp_path, destination)
        require_regular_file(destination, uid, gid)
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
                or destination_stat.st_size != 65
                or destination_stat.st_dev != path_stat.st_dev
                or destination_stat.st_ino != path_stat.st_ino
                or os.read(destination_fd, MAX_READ_BYTES) != payload
                or os.read(destination_fd, 1)
            ):
                return 72
        finally:
            os.close(destination_fd)
        directory_fd = os.open(runtime_directory, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
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
