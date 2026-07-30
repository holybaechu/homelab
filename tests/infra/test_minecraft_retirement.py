import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import REPO_ROOT


GUARD_PATH = REPO_ROOT / (
    "infra/ansible/roles/pve_retire_minecraft_data/"
    "files/retire_minecraft_data.py"
)


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def load_guard():
    spec = importlib.util.spec_from_file_location("retire_minecraft_data", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    return load_guard()


def safe_evidence(guard, *, target_exists: bool = True):
    return guard.StorageEvidence(
        expected_device_is_block=True,
        expected_device_realpath="/dev/dm-7",
        expected_device_id="253:7",
        parent_is_directory=True,
        parent_is_symlink=False,
        parent_realpath="/var/lib/homelab",
        target_exists=target_exists,
        target_is_directory=target_exists,
        target_is_symlink=False,
        target_realpath="/var/lib/homelab/minecraft" if target_exists else None,
        mounts=(
            guard.MountRecord(
                device_id="253:7",
                root="/",
                target="/var/lib/homelab",
                source="/dev/dm-7",
                fs_type="ext4",
            ),
        ),
    )


def test_mountinfo_parser_preserves_bind_roots_and_descendant_targets(guard):
    records = guard.parse_mountinfo(
        "41 30 253:7 / /var/lib/homelab rw,relatime - ext4 /dev/dm-7 rw\n"
        "42 41 253:7 /world /var/lib/homelab/minecraft/world rw - "
        "ext4 /dev/dm-7 rw\n"
    )

    assert records == (
        guard.MountRecord(
            device_id="253:7",
            root="/",
            target="/var/lib/homelab",
            source="/dev/dm-7",
            fs_type="ext4",
        ),
        guard.MountRecord(
            device_id="253:7",
            root="/world",
            target="/var/lib/homelab/minecraft/world",
            source="/dev/dm-7",
            fs_type="ext4",
        ),
    )


def test_minecraft_is_retired_with_runtime_and_data_tombstones():
    variables = read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")
    all_variables = read("infra/ansible/inventory/prod/group_vars/all.yml")
    active = variables.split("\ndocker_compose_projects:", 1)[1]

    assert "name: game" in variables.split("\ndocker_compose_projects:", 1)[0]
    assert "name: game" not in active
    assert "retired_docker_data_paths:" not in variables
    assert "retired_minecraft_data_path: /var/lib/homelab/minecraft" in all_variables
    assert 'homelab_data_device: "/dev/pve/{{ homelab_data_lv_name }}"' in all_variables
    assert not (REPO_ROOT / "apps/compose/game").exists()
    assert not (REPO_ROOT / "docs/runbooks/minecraft-server.md").exists()


def test_retired_data_guard_builds_only_the_exact_one_filesystem_delete(guard):
    command = guard.build_deletion_command(
        "/var/lib/homelab/minecraft",
        "/dev/pve/homelab-data",
        safe_evidence(guard),
    )

    assert command == (
        "/usr/bin/rm",
        "-rf",
        "--one-file-system",
        "--",
        "/var/lib/homelab/minecraft",
    )


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "/var",
        "/var/lib/homelab",
        "/var/lib/homelab/",
        "/var/lib/homelab/./minecraft",
        "/var/lib/homelab//minecraft",
        "/var/lib/homelab/minecraft/",
        "/var/lib/homelab/mine\ncraft",
        "/var/lib/homelab/../homelab/minecraft",
        "/var/lib/homelab/minecraft/../other",
        "/var/lib/homelab/mine*craft",
        "/var/lib/homelab/mine?craft",
        "/var/lib/homelab/mine[craft",
        "/srv/homelab/minecraft",
        "/var/lib/other/minecraft",
    ],
)
def test_retired_data_guard_rejects_noncanonical_or_unscoped_paths(guard, path):
    with pytest.raises(guard.UnsafeRetirement):
        guard.build_deletion_command(
            path,
            "/dev/pve/homelab-data",
            safe_evidence(guard),
        )


def test_retired_data_guard_rejects_the_wrong_block_device(guard):
    evidence = safe_evidence(guard)
    evidence = evidence._replace(expected_device_id="253:8")

    with pytest.raises(guard.UnsafeRetirement, match="device identity"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
            evidence,
        )


def test_retired_data_guard_rejects_a_mismatched_canonical_mount_source(guard):
    evidence = safe_evidence(guard)
    mount = evidence.mounts[0]._replace(source="/dev/dm-8")
    evidence = evidence._replace(mounts=(mount,))

    with pytest.raises(guard.UnsafeRetirement, match="canonical mount source"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
            evidence,
        )


def test_retired_data_guard_rejects_a_different_expected_device_path(guard):
    with pytest.raises(guard.UnsafeRetirement, match="expected block device"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/other-data",
            safe_evidence(guard),
        )


def test_retired_data_guard_rejects_a_same_device_bind_alias(guard):
    evidence = safe_evidence(guard)
    bind_mount = evidence.mounts[0]._replace(root="/other-homelab-root")
    evidence = evidence._replace(mounts=(bind_mount,))

    with pytest.raises(guard.UnsafeRetirement, match="filesystem root"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
            evidence,
        )


def test_retired_data_guard_rejects_a_stacked_exact_path_bind(guard):
    evidence = safe_evidence(guard)
    evidence = evidence._replace(mounts=(evidence.mounts[0], evidence.mounts[0]))

    with pytest.raises(guard.UnsafeRetirement, match="exactly one mount"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
            evidence,
        )


def test_retired_data_guard_rejects_same_filesystem_bind_descendants(guard):
    evidence = safe_evidence(guard)
    descendant = guard.MountRecord(
        device_id="253:7",
        root="/other-world",
        target="/var/lib/homelab/minecraft/world",
        source="/dev/dm-7",
        fs_type="ext4",
    )
    evidence = evidence._replace(mounts=(*evidence.mounts, descendant))

    with pytest.raises(guard.UnsafeRetirement, match="nested mount"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
            evidence,
        )


def test_retired_data_guard_validates_storage_before_absent_rerun_returns(guard):
    evidence = safe_evidence(guard, target_exists=False)
    evidence = evidence._replace(expected_device_id="253:8")

    with pytest.raises(guard.UnsafeRetirement, match="device identity"):
        guard.build_deletion_command(
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
            evidence,
        )


def test_retired_data_guard_absent_rerun_is_idempotent_after_full_validation(guard):
    command = guard.build_deletion_command(
        "/var/lib/homelab/minecraft",
        "/dev/pve/homelab-data",
        safe_evidence(guard, target_exists=False),
    )

    assert command is None


def test_retired_data_guard_has_no_validation_only_bypass():
    result = subprocess.run(
        [
            sys.executable,
            str(GUARD_PATH),
            "--check",
            "/var/lib/homelab/minecraft",
            "/dev/pve/homelab-data",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_retired_data_cleanup_runs_on_proxmox_after_compose_retirement():
    site = read("infra/ansible/playbooks/site.yml")
    prepare = read("infra/ansible/playbooks/prepare-low-id-cutover.yml")
    tasks = read("infra/ansible/roles/pve_retire_minecraft_data/tasks/main.yml")

    assert site.index("docker_compose_project") < site.index("pve_retire_minecraft_data")
    assert "pve_retire_minecraft_data" not in prepare
    assert "any_errors_fatal: true" in site
    assert "delegate_to: docker_apps" in tasks
    assert tasks.index("Assert no game Compose containers remain") < tasks.index(
        "Permanently remove the exact retired Minecraft host data path"
    )
    assert "retire_minecraft_data.py" in tasks
    assert "{{ retired_minecraft_data_path }}" in tasks
    assert "{{ homelab_data_device }}" in tasks
    assert "'changed=yes' in retired_minecraft_data_cleanup.stdout" in tasks


def test_proxmox_minecraft_tombstone_is_exact_and_idempotent():
    role = REPO_ROOT / "infra/ansible/roles/pve_retire_minecraft/tasks/main.yml"
    assert role.exists()
    tasks = role.read_text(encoding="utf-8")

    assert 'vmid="115"' in tasks
    assert 'expected_hostname="minecraft"' in tasks
    assert 'if [ "$actual_hostname" != "$expected_hostname" ]' in tasks
    assert 'pct destroy "$vmid" --purge 1 --destroy-unreferenced-disks 1' in tasks
    assert 'archive_root="/var/lib/vz/dump"' in tasks
    assert 'vzdump-lxc-115-*.tar.zst' in tasks
    assert 'rm -f -- "$archive"' in tasks
    assert "echo changed=no" in tasks


def test_proxmox_minecraft_tombstone_has_no_predictable_scratch_file():
    tasks = read("infra/ansible/roles/pve_retire_minecraft/tasks/main.yml")

    assert "/tmp/homelab-minecraft-archive-cleanup" not in tasks
    assert 'cleanup_state="$(mktemp)"' in tasks
    assert "trap 'rm -f -- \"$cleanup_state\"' EXIT" in tasks
