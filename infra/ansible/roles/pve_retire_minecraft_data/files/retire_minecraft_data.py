#!/usr/bin/env python3
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


EXPECTED_TARGET = "/var/lib/homelab/minecraft"
EXPECTED_PARENT = "/var/lib/homelab"
EXPECTED_DEVICE = "/dev/pve/homelab-data"
EXPECTED_FILESYSTEM = "ext4"
MOUNTINFO_PATH = Path("/proc/self/mountinfo")
DELETE_COMMAND = ("/usr/bin/rm", "-rf", "--one-file-system", "--")
_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")


class UnsafeRetirement(RuntimeError):
    pass


class MountRecord(NamedTuple):
    device_id: str
    root: str
    target: str
    source: str
    fs_type: str


class StorageEvidence(NamedTuple):
    expected_device_is_block: bool
    expected_device_realpath: str
    expected_device_id: str
    parent_is_directory: bool
    parent_is_symlink: bool
    parent_realpath: str
    target_exists: bool
    target_is_directory: bool
    target_is_symlink: bool
    target_realpath: str | None
    mounts: tuple[MountRecord, ...]


def _decode_mountinfo_field(value: str) -> str:
    return _MOUNTINFO_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def parse_mountinfo(text: str) -> tuple[MountRecord, ...]:
    records = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        fields = line.split()
        try:
            separator = fields.index("-")
            device_id = fields[2]
            root = fields[3]
            target = fields[4]
            fs_type = fields[separator + 1]
            source = fields[separator + 2]
        except (IndexError, ValueError) as error:
            raise UnsafeRetirement(
                f"Refusing malformed mountinfo line {line_number}"
            ) from error
        records.append(
            MountRecord(
                device_id=device_id,
                root=_decode_mountinfo_field(root),
                target=_decode_mountinfo_field(target),
                source=_decode_mountinfo_field(source),
                fs_type=fs_type,
            )
        )
    return tuple(records)


def _path_state(path: str) -> tuple[bool, bool, bool, str | None]:
    exists = os.path.lexists(path)
    if not exists:
        return False, False, False, None
    path_stat = os.lstat(path)
    is_symlink = stat.S_ISLNK(path_stat.st_mode)
    is_directory = stat.S_ISDIR(path_stat.st_mode)
    return exists, is_directory, is_symlink, os.path.realpath(path)


def gather_storage_evidence(target: str, expected_device: str) -> StorageEvidence:
    try:
        device_stat = os.stat(expected_device)
    except OSError as error:
        raise UnsafeRetirement(
            f"Refusing unavailable expected block device: {expected_device}"
        ) from error

    parent_exists, parent_is_directory, parent_is_symlink, parent_realpath = (
        _path_state(EXPECTED_PARENT)
    )
    if not parent_exists:
        parent_is_directory = False
        parent_realpath = ""

    target_exists, target_is_directory, target_is_symlink, target_realpath = (
        _path_state(target)
    )
    mounts = []
    try:
        mountinfo_text = MOUNTINFO_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise UnsafeRetirement("Refusing unreadable Linux mountinfo") from error
    for record in parse_mountinfo(mountinfo_text):
        source = record.source
        if source.startswith("/dev/"):
            source = os.path.realpath(source)
        mounts.append(record._replace(source=source))

    return StorageEvidence(
        expected_device_is_block=stat.S_ISBLK(device_stat.st_mode),
        expected_device_realpath=os.path.realpath(expected_device),
        expected_device_id=(
            f"{os.major(device_stat.st_rdev)}:{os.minor(device_stat.st_rdev)}"
        ),
        parent_is_directory=parent_is_directory,
        parent_is_symlink=parent_is_symlink,
        parent_realpath=parent_realpath or "",
        target_exists=target_exists,
        target_is_directory=target_is_directory,
        target_is_symlink=target_is_symlink,
        target_realpath=target_realpath,
        mounts=tuple(mounts),
    )


def build_deletion_command(
    target: str, expected_device: str, evidence: StorageEvidence
) -> tuple[str, ...] | None:
    if target != EXPECTED_TARGET:
        raise UnsafeRetirement(f"Refusing retired Minecraft data path: {target}")
    if expected_device != EXPECTED_DEVICE:
        raise UnsafeRetirement(
            f"Refusing unexpected expected block device: {expected_device}"
        )
    if not evidence.expected_device_is_block:
        raise UnsafeRetirement("Refusing expected device that is not a block device")
    if not evidence.expected_device_realpath.startswith("/dev/"):
        raise UnsafeRetirement("Refusing non-device canonical block path")
    if not evidence.parent_is_directory or evidence.parent_is_symlink:
        raise UnsafeRetirement("Refusing missing, non-directory, or aliased data parent")
    if evidence.parent_realpath != EXPECTED_PARENT:
        raise UnsafeRetirement("Refusing noncanonical data parent")

    parent_mounts = [
        record for record in evidence.mounts if record.target == EXPECTED_PARENT
    ]
    if len(parent_mounts) != 1:
        raise UnsafeRetirement("Refusing data parent without exactly one mount record")
    parent_mount = parent_mounts[0]
    if parent_mount.device_id != evidence.expected_device_id:
        raise UnsafeRetirement("Refusing mismatched homelab-data device identity")
    if parent_mount.source != evidence.expected_device_realpath:
        raise UnsafeRetirement("Refusing mismatched canonical mount source")
    if parent_mount.root != "/":
        raise UnsafeRetirement("Refusing bind-mounted homelab filesystem root")
    if parent_mount.fs_type != EXPECTED_FILESYSTEM:
        raise UnsafeRetirement("Refusing unexpected homelab filesystem type")

    nested_mounts = [
        record.target
        for record in evidence.mounts
        if record.target == target or record.target.startswith(f"{target}/")
    ]
    if nested_mounts:
        raise UnsafeRetirement(
            "Refusing retired Minecraft path with nested mount: "
            + ", ".join(nested_mounts)
        )

    if not evidence.target_exists:
        return None
    if evidence.target_is_symlink or not evidence.target_is_directory:
        raise UnsafeRetirement("Refusing symlinked or non-directory Minecraft target")
    if evidence.target_realpath != target:
        raise UnsafeRetirement("Refusing noncanonical retired Minecraft target")
    return (*DELETE_COMMAND, target)


def retire_minecraft_data(target: str, expected_device: str) -> bool:
    evidence = gather_storage_evidence(target, expected_device)
    command = build_deletion_command(target, expected_device, evidence)
    if command is None:
        return False
    subprocess.run(command, check=True)
    if os.path.lexists(target):
        raise UnsafeRetirement(f"Retired Minecraft data path still exists: {target}")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--delete":
        print(
            f"usage: {Path(sys.argv[0]).name} --delete "
            f"{EXPECTED_TARGET} {EXPECTED_DEVICE}",
            file=sys.stderr,
        )
        return 2
    try:
        changed = retire_minecraft_data(argv[1], argv[2])
    except (OSError, subprocess.CalledProcessError, UnsafeRetirement) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("changed=yes" if changed else "changed=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
