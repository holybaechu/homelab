import re

import pytest
import yaml
from jinja2 import Environment, StrictUndefined

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_minecraft_is_retired_with_runtime_and_data_tombstones():
    variables = read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")
    active = variables.split("\ndocker_compose_projects:", 1)[1]

    assert "name: game" in variables.split("\ndocker_compose_projects:", 1)[0]
    assert "name: game" not in active
    assert "retired_docker_data_paths:" in variables
    assert "- /srv/homelab/minecraft" in variables
    assert not (REPO_ROOT / "apps/compose/game").exists()
    assert not (REPO_ROOT / "docs/runbooks/minecraft-server.md").exists()


def retired_data_path_is_safe(path: object) -> bool:
    tasks = yaml.safe_load(
        read("infra/ansible/roles/docker_compose_project/tasks/main.yml")
    )
    validate = next(
        task for task in tasks if task["name"] == "Validate retired Docker data paths"
    )
    conditions = validate["ansible.builtin.assert"]["that"]
    environment = Environment(undefined=StrictUndefined)
    environment.tests["match"] = (
        lambda value, pattern: re.match(pattern, value) is not None
    )
    return all(
        bool(environment.compile_expression(condition)(item=path))
        for condition in conditions
    )


@pytest.mark.parametrize(
    "path",
    [
        "/srv/homelab/minecraft",
        "/srv/homelab/retired.service/data_1",
    ],
)
def test_retired_data_cleanup_accepts_canonical_descendant_paths(path):
    assert retired_data_path_is_safe(path)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "/srv",
        "/srv/homelab",
        "/srv/homelab/",
        "/srv/homelab/.",
        "/srv/homelab//",
        "/srv/homelab/minecraft/",
        "/srv/homelab/minecraft//world",
        "/srv/homelab/minecraft\n",
        "/srv/homelab/..",
        "/srv/homelab/minecraft/../other",
        "/srv/homelab/mine*craft",
        "/srv/other/minecraft",
    ],
)
def test_retired_data_cleanup_rejects_noncanonical_or_unscoped_paths(path):
    assert not retired_data_path_is_safe(path)


def test_retired_data_cleanup_validation_precedes_removal():
    tasks = read("infra/ansible/roles/docker_compose_project/tasks/main.yml")

    validate = tasks.index("Validate retired Docker data paths")
    remove = tasks.index("Remove retired Docker data paths")
    assert validate < remove
    assert 'loop: "{{ retired_docker_data_paths | default([]) }}"' in tasks
    assert "ansible.builtin.file:" in tasks[remove:]
    assert "state: absent" in tasks[remove:]


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
