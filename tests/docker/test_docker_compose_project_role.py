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
    assert "docker compose up -d --build" in tasks
    assert "--force-recreate" in tasks
    assert "docker_compose_force_recreate_projects" in tasks
    assert "config_templates" in tasks
    assert 'dest: "{{ item.dest }}/.env"' in tasks
    assert 'mode: "0600"' in tasks
    assert "no_log: true" in tasks
    assert 'owner: "{{ service_uid }}"' in tasks
    assert 'group: "{{ service_gid }}"' in tasks
    assert variables.index("name: platform") < variables.index("name: media")
    assert variables.index("name: media") < variables.index("name: code")


def test_compose_role_manages_the_qbittorrent_config_before_startup():
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
    assert "service_name: qbittorrent-vpn" not in variables
    assert 'loop: "{{ qbittorrent_instances }}"' in tasks
    assert "qbittorrent_config_compare.results" in tasks
    assert tasks.index("Render Compose project environment files") < tasks.index(
        "Stop qBittorrent instances before replacing changed configuration"
    )
    assert tasks.index("Require the direct qBittorrent peer port to be available") < tasks.index(
        "Stop qBittorrent instances before replacing changed configuration"
    )
    assert (
        'docker ps --no-trunc -q --filter "publish={{ qbittorrent_direct_peer_port }}"'
        in tasks
    )
    assert 'docker ps -q --filter "publish={{ qbittorrent_direct_peer_port }}"' not in tasks
    assert 'test "${owner}" = "${direct_container}"' in tasks
    assert tasks.index("Build and start Compose projects in dependency order") < tasks.index(
        "Assert retired Compose service containers are absent"
    )
    assert "flush_handlers" not in tasks
    assert "current_network_interface" not in tasks


def test_compose_role_recreates_only_projects_with_changed_inputs():
    tasks_path = REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    tasks_text = tasks_path.read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)

    assert not (
        REPO_ROOT / "infra/ansible/roles/docker_compose_project/handlers/main.yml"
    ).exists()
    assert "notify: Restart Compose project" not in tasks_text

    copy_task = next(
        task for task in tasks if task["name"] == "Copy tracked Compose project files"
    )
    env_task = next(
        task for task in tasks if task["name"] == "Render Compose project environment files"
    )
    config_task = next(
        task for task in tasks if task["name"] == "Render managed application configuration"
    )
    assert copy_task["register"] == "docker_compose_project_file_copy"
    assert env_task["register"] == "docker_compose_project_environment_render"
    assert config_task["register"] == "docker_compose_project_config_render"

    selection_task = next(
        task
        for task in tasks
        if task["name"] == "Select Compose projects that require forced recreation"
    )
    selection = selection_task["ansible.builtin.set_fact"][
        "docker_compose_force_recreate_projects"
    ]
    assert "docker_compose_project_file_copy.results" in selection
    assert "docker_compose_project_environment_render.results" in selection
    assert "docker_compose_project_config_render.results" in selection
    assert "map(attribute='item.name')" in selection
    assert "map(attribute='item.0.name')" in selection
    assert "| unique | list" in selection
    assert selection_task["changed_when"] is False
    assert selection_task["no_log"] is True

    validation_task = next(
        task
        for task in tasks
        if task["name"] == "Validate Compose project forced recreation selection"
    )
    validation = "\n".join(validation_task["ansible.builtin.assert"]["that"])
    assert "difference" in validation
    assert "map(attribute='name')" in validation
    assert "unique" in validation

    start_task = next(
        task
        for task in tasks
        if task["name"] == "Build and start Compose projects in dependency order"
    )
    command = start_task["ansible.builtin.command"]["cmd"]
    assert "--force-recreate" in command
    assert "item.name in docker_compose_force_recreate_projects" in command
    assert start_task["loop"] == "{{ docker_compose_projects }}"
    assert "docker_compose_up.stdout + docker_compose_up.stderr" in start_task[
        "changed_when"
    ]
    assert "item.name in docker_compose_force_recreate_projects" in start_task[
        "changed_when"
    ]
    assert "qbittorrent_config_compare" not in selection


def test_compose_role_removes_retired_vpn_runtime_artifacts_after_orphans():
    tasks = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    ).read_text(encoding="utf-8")
    variables = (
        REPO_ROOT
        / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    ).read_text(encoding="utf-8")
    parsed_variables = yaml.safe_load(variables)

    compose_up = tasks.index("Build and start Compose projects in dependency order")
    assert compose_up < tasks.index("Assert retired Compose service containers are absent")
    assert compose_up < tasks.index("Remove retired Docker volumes")
    assert compose_up < tasks.index("Remove retired Docker networks")
    assert compose_up < tasks.index("Remove retired local Docker images")
    assert parsed_variables["retired_docker_compose_services"] == [
        {"project": "media", "service": "gluetun"},
        {"project": "media", "service": "qbittorrent-vpn"},
    ]
    assert parsed_variables["retired_docker_volumes"] == ["homelab_gluetun_data"]
    assert parsed_variables["retired_docker_networks"] == ["media_default"]
    assert "qmcgaw/gluetun:v3.41.3" in parsed_variables["retired_docker_images"]


def test_compose_role_verifies_adguard_safe_search_runtime_without_logging_secrets():
    tasks_path = REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    tasks_text = tasks_path.read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)
    runtime_check = next(
        task
        for task in tasks
        if task["name"] == "Verify AdGuard runtime Safe Search is disabled"
    )

    assert tasks_text.index("Build and start Compose projects in dependency order") < tasks_text.index(
        "Verify AdGuard runtime Safe Search is disabled"
    )
    assert runtime_check["ansible.builtin.uri"]["url"].endswith(
        "/control/safesearch/status"
    )
    assert runtime_check["ansible.builtin.uri"]["force_basic_auth"] is True
    assert runtime_check["ansible.builtin.uri"]["url_password"] == "{{ adguard_admin_password }}"
    assert runtime_check["no_log"] is True
    assert any("json.enabled" in condition for condition in runtime_check["until"])


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
    assert parsed_variables["retired_docker_images"] == [
        "homelab/hermes-agent:local",
        "qmcgaw/gluetun:v3.41.3",
    ]

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
