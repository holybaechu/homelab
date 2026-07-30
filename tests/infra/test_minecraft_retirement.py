import os
import shutil
import subprocess

import pytest

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_minecraft_is_retired_with_runtime_and_data_tombstones():
    variables = read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")
    all_variables = read("infra/ansible/inventory/prod/group_vars/all.yml")
    active = variables.split("\ndocker_compose_projects:", 1)[1]

    assert "name: game" in variables.split("\ndocker_compose_projects:", 1)[0]
    assert "name: game" not in active
    assert "retired_docker_data_paths:" not in variables
    assert "retired_minecraft_data_path: /var/lib/homelab/minecraft" in all_variables
    assert not (REPO_ROOT / "apps/compose/game").exists()
    assert not (REPO_ROOT / "docs/runbooks/minecraft-server.md").exists()


def run_retired_data_guard(mode: str, path: str) -> subprocess.CompletedProcess[str]:
    script = REPO_ROOT / (
        "infra/ansible/roles/pve_retire_minecraft_data/"
        "files/retire-minecraft-data.sh"
    )
    if os.name == "nt":
        shell = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
        )
        if not os.path.exists(shell):
            pytest.skip("Git sh is unavailable")
        drive, remainder = os.path.splitdrive(str(script))
        script_arg = f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"
    else:
        shell = shutil.which("sh")
        if shell is None:
            pytest.skip("POSIX shell is unavailable")
        script_arg = str(script)

    return subprocess.run(
        [shell, script_arg, mode, path],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def test_retired_data_cleanup_accepts_only_the_exact_host_path():
    result = run_retired_data_guard("--check", "/var/lib/homelab/minecraft")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/var/lib/homelab/minecraft"


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
def test_retired_data_cleanup_rejects_noncanonical_or_unscoped_paths(path):
    result = run_retired_data_guard("--check", path)

    assert result.returncode != 0


def test_retired_data_cleanup_rejects_out_of_scope_delete_without_removing_it(tmp_path):
    out_of_scope = tmp_path / "minecraft"
    out_of_scope.mkdir()
    sentinel = out_of_scope / "world.dat"
    sentinel.write_text("keep", encoding="utf-8")

    result = run_retired_data_guard("--delete", str(out_of_scope))

    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_retired_data_cleanup_runs_on_proxmox_after_compose_retirement():
    site = read("infra/ansible/playbooks/site.yml")
    prepare = read("infra/ansible/playbooks/prepare-low-id-cutover.yml")
    tasks = read("infra/ansible/roles/pve_retire_minecraft_data/tasks/main.yml")

    assert site.index("docker_compose_project") < site.index("pve_retire_minecraft_data")
    assert "pve_retire_minecraft_data" not in prepare
    assert "ansible.builtin.command:" in tasks
    assert "--delete" in tasks
    assert "{{ retired_minecraft_data_path }}" in tasks
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
