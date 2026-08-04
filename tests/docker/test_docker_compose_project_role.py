import os
import shutil
import subprocess

import pytest
import yaml

from tests.helpers import REPO_ROOT


def test_compose_role_reconciles_projects_in_declared_order():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    variables = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml").read_text(encoding="utf-8")

    assert "docker compose pull --ignore-buildable" in tasks
    assert "docker compose up -d --build --remove-orphans" in tasks
    assert "config_templates" in tasks
    assert 'dest: "{{ item.dest }}/.env"' in tasks
    assert 'mode: "0600"' in tasks
    assert "no_log: true" in tasks
    assert 'owner: "{{ service_uid }}"' in tasks
    assert 'group: "{{ service_gid }}"' in tasks
    assert variables.index("name: platform") < variables.index("name: media")


def test_compose_role_manages_both_qbittorrent_configs_before_startup():
    tasks = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    ).read_text(encoding="utf-8")
    variables = (
        REPO_ROOT
        / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    ).read_text(encoding="utf-8")

    assert "qbittorrent_instances" in variables
    assert "service_name: qbittorrent" in variables
    assert "service_name: qbittorrent-vpn" in variables
    assert 'loop: "{{ qbittorrent_instances }}"' in tasks
    assert "qbittorrent_config_compare.results" in tasks
    assert tasks.index("Render Compose project environment files") < tasks.index(
        "Stop qBittorrent instances before replacing changed configuration"
    )
    assert tasks.index("Require the direct qBittorrent peer port to be available") < tasks.index(
        "Stop qBittorrent instances before replacing changed configuration"
    )
    assert 'docker ps -q --filter "publish={{ qbittorrent_direct_peer_port }}"' in tasks
    assert tasks.index("Build and start Compose projects in dependency order") < tasks.index(
        "Reconcile Proton forwarded port with VPN qBittorrent"
    )
    assert "flush_handlers" in tasks
    assert "current_network_interface" in tasks


def test_compose_role_removes_retired_projects_and_hermes_image():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    variables = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml").read_text(encoding="utf-8")
    active_projects = variables.split("\ndocker_compose_projects:", 1)[1]
    parsed_tasks = yaml.safe_load(tasks)
    parsed_variables = yaml.safe_load(variables)

    assert "docker compose down --volumes --remove-orphans" in tasks
    assert "retired_docker_compose_projects" in tasks
    assert "name: backup" in variables
    assert "name: backup" not in active_projects
    assert "name: hermes" not in active_projects
    assert {"name": "hermes", "dest": "{{ docker_apps_compose_root }}/hermes"} in (
        parsed_variables["retired_docker_compose_projects"]
    )
    assert parsed_variables["retired_docker_images"] == ["homelab/hermes-agent:local"]

    remove_image = next(
        task for task in parsed_tasks if task["name"] == "Remove retired local Docker images"
    )
    assert remove_image["ansible.builtin.command"]["argv"] == [
        "docker",
        "image",
        "rm",
        "{{ item.item }}",
    ]

    no_hermes = next(
        task for task in parsed_tasks if task["name"] == "Assert no Hermes Compose containers remain"
    )
    assert no_hermes["ansible.builtin.command"]["argv"] == [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        "label=com.docker.compose.project=hermes",
    ]
    assert no_hermes["failed_when"] == (
        "retired_hermes_containers.stdout | trim | length > 0"
    )


def _posix_shell_command(script: str) -> list[str]:
    shell = shutil.which("sh")
    if shell and os.name != "nt":
        return [shell, script]

    git_shell = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
    )
    if not os.path.exists(git_shell):
        pytest.skip("POSIX shell is unavailable")
    drive, remainder = os.path.splitdrive(script)
    git_path = f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"
    return [git_shell, git_path]


def test_missing_compose_file_cannot_hide_surviving_game_containers():
    script = str(REPO_ROOT / "tests/docker/test_retired_compose_guard.sh")
    env = os.environ.copy()
    if os.name == "nt":
        git_root = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"), "Git"
        )
        env["PATH"] = os.pathsep.join(
            [
                os.path.join(git_root, "usr", "bin"),
                os.path.join(git_root, "mingw64", "bin"),
                env["PATH"],
            ]
        )
    result = subprocess.run(
        _posix_shell_command(script),
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def test_compose_role_always_runs_the_game_container_postcondition():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")

    assert tasks.index("Remove retired Compose project directories") < tasks.index(
        "Assert no game Compose containers remain"
    )
    assert "assert-no-game-compose-containers.sh" in tasks


def test_retired_data_cleanup_is_not_attempted_inside_unprivileged_lxc():
    tasks = (REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml").read_text(encoding="utf-8")
    variables = (REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml").read_text(encoding="utf-8")

    assert "retired_docker_data_paths" not in variables
    assert "Validate retired Docker data paths" not in tasks
    assert "Remove retired Docker data paths" not in tasks
