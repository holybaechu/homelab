from pathlib import PurePosixPath

import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/docker_apps_host"
COMPOSE = REPO_ROOT / "apps/compose/homelab/compose.yml"


def test_role_is_limited_to_host_primitives():
    source = (ROLE / "tasks/main.yml").read_text(encoding="utf-8")
    tasks = yaml.safe_load(source)

    assert {key for task in tasks for key in task if key.startswith("ansible.builtin.")} <= {
        "ansible.builtin.command",
        "ansible.builtin.file",
    }
    assert "docker compose" not in source
    assert "docker network" not in source
    assert "ansible.builtin.template" not in source
    mount_guards = [
        task["ansible.builtin.command"]
        for task in tasks
        if "ansible.builtin.command" in task
    ]
    assert mount_guards == ["mountpoint -q /srv/homelab"]

def test_host_directories_cover_every_durable_bind_mount():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
    directory_task = next(task for task in tasks if task["name"] == "Create durable application data directories")
    created = {PurePosixPath(item["path"]) for item in directory_task["loop"]}

    model = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    mounted = {
        PurePosixPath(volume.split(":", 1)[0])
        for service in model["services"].values()
        for volume in service.get("volumes", [])
        if volume.startswith("/srv/homelab/")
    }
    for source in mounted:
        assert any(source == path or source in path.parents or path in source.parents for path in created), source


def test_host_role_creates_only_the_component_secret_root():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
    secret_directory = next(
        task for task in tasks if task["name"] == "Create private application component secret directory"
    )
    assert secret_directory["ansible.builtin.file"] == {
        "path": "/etc/homelab/secrets",
        "state": "directory",
        "owner": "root",
        "group": "root",
        "mode": "0700",
        "follow": False,
    }
