import os
import re
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


def test_compose_role_bootstraps_qbittorrent_and_live_reconciles_only_vuetorrent():
    tasks_text = (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    ).read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)
    variables = (
        REPO_ROOT
        / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
    ).read_text(encoding="utf-8")

    assert "qbittorrent_instances" in variables
    assert "service_name: qbittorrent" in variables
    assert "service_name: qbittorrent-vpn" not in variables
    assert 'loop: "{{ qbittorrent_instances }}"' in tasks_text
    assert "cmp -s" not in tasks_text
    assert ".conf.candidate" not in tasks_text
    assert "qbittorrent_config_compare" not in tasks_text
    assert "community.general.ini_file" not in tasks_text
    assert not any(
        "qbittorrent" in str(task).lower()
        and "docker compose stop" in str(task)
        for task in tasks
    )
    assert "include_tasks: reconcile_qbittorrent.yml" not in tasks_text
    assert not (
        REPO_ROOT
        / "infra/ansible/roles/docker_compose_project/tasks/reconcile_qbittorrent.yml"
    ).exists()

    bootstrap = next(
        task
        for task in tasks
        if task["name"]
        == "Bootstrap qBittorrent configuration without replacing application state"
    )
    bootstrap_template = bootstrap["ansible.builtin.template"]
    assert bootstrap_template["src"] == "qBittorrent.conf.j2"
    assert bootstrap_template["force"] is False
    assert bootstrap_template["dest"].endswith(
        "/qBittorrent/qBittorrent.conf"
    )
    assert bootstrap["no_log"] is True

    read = next(
        task
        for task in tasks
        if task["name"] == "Read effective qBittorrent Web UI preferences"
    )
    select = next(
        task
        for task in tasks
        if task["name"]
        == "Select VueTorrent through the live qBittorrent API when required"
    )
    verify_read = next(
        task
        for task in tasks
        if task["name"] == "Re-read effective qBittorrent Web UI preferences"
    )
    api_command = " ".join(str(value) for value in select["ansible.builtin.command"]["argv"])
    assert "/api/v2/app/setPreferences" in api_command
    assert (
        'json={"alternative_webui_enabled":true,'
        '"alternative_webui_path":"/vuetorrent"}'
    ) in api_command
    assert "password" not in api_command.lower()
    assert "listen_port" not in api_command
    assert "save_path" not in api_command
    assert set(re.findall(r'"([a-z_]+)"', api_command)) == {
        "alternative_webui_enabled",
        "alternative_webui_path",
    }
    assert "alternative_webui_enabled" in select["when"]
    assert "alternative_webui_path" in select["when"]
    assert select["changed_when"] is True
    assert select["no_log"] is True
    assert read["no_log"] is True
    assert verify_read["no_log"] is True

    assert tasks_text.index("Render Compose project environment files") < tasks_text.index(
        bootstrap["name"]
    )
    assert tasks_text.index("Require the direct qBittorrent peer port to be available") < tasks_text.index(
        bootstrap["name"]
    )
    assert (
        'docker ps --no-trunc -q --filter "publish={{ qbittorrent_direct_peer_port }}"'
        in tasks_text
    )
    assert 'docker ps -q --filter "publish={{ qbittorrent_direct_peer_port }}"' not in tasks_text
    assert 'test "${owner}" = "${direct_container}"' in tasks_text
    assert tasks_text.index("Build and start Compose projects in dependency order") < tasks_text.index(
        "Assert retired Compose service containers are absent"
    )
    assert tasks_text.index("Build and start Compose projects in dependency order") < tasks_text.index(
        "Restrict qBittorrent configuration metadata after live reconciliation"
    )
    assert tasks_text.index("Build and start Compose projects in dependency order") < tasks_text.index(
        read["name"]
    )
    assert tasks_text.index(read["name"]) < tasks_text.index(select["name"])
    assert tasks_text.index(select["name"]) < tasks_text.index(verify_read["name"])
    assert tasks_text.index(verify_read["name"]) < tasks_text.index(
        "Restrict qBittorrent configuration metadata after live reconciliation"
    )
    assert "flush_handlers" not in tasks_text
    assert "current_network_interface" not in tasks_text


def test_adguard_config_is_root_owned_bootstrap_only_input():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
        ).read_text(encoding="utf-8")
    )
    variables = yaml.safe_load(
        (
            REPO_ROOT
            / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
        ).read_text(encoding="utf-8")
    )

    platform = next(
        project
        for project in variables["docker_compose_projects"]
        if project["name"] == "platform"
    )
    adguard = next(
        config
        for config in platform["config_templates"]
        if config["dest"] == "adguard/AdGuardHome.yaml"
    )
    assert adguard == {
        "src": "AdGuardHome.yaml.j2",
        "dest": "adguard/AdGuardHome.yaml",
        "mode": "0600",
        "owner": "root",
        "group": "root",
        "force": False,
    }

    render = next(
        task
        for task in tasks
        if task["name"] == "Render managed application configuration"
    )["ansible.builtin.template"]
    assert render["force"] == "{{ item.1.force | default(true) }}"
    assert render["owner"] == "{{ item.1.owner | default(service_uid) }}"
    assert render["group"] == "{{ item.1.group | default(service_gid) }}"

    metadata = next(
        task
        for task in tasks
        if task["name"]
        == "Restrict application-owned AdGuard configuration metadata"
    )["ansible.builtin.file"]
    assert metadata["owner"] == "root"
    assert metadata["group"] == "root"
    assert metadata["mode"] == "0600"


def test_compose_role_recreates_only_projects_with_changed_inputs():
    tasks_path = REPO_ROOT / "infra/ansible/roles/docker_compose_project/tasks/main.yml"
    tasks_text = tasks_path.read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)

    assert not (
        REPO_ROOT / "infra/ansible/roles/docker_compose_project/handlers/main.yml"
    ).exists()
    assert "notify: Restart Compose project" not in tasks_text

    copy_task = next(
        task for task in tasks if task["name"] == "Copy tracked Compose runtime files"
    )
    env_task = next(
        task for task in tasks if task["name"] == "Render Compose project environment files"
    )
    config_task = next(
        task for task in tasks if task["name"] == "Render managed application configuration"
    )
    assert copy_task["register"] == "docker_compose_project_file_copy"
    assert copy_task["loop"] == (
        "{{ docker_compose_projects | subelements('runtime_files') }}"
    )
    assert copy_task["ansible.builtin.copy"]["src"].endswith(
        "/{{ item.0.src }}/{{ item.1 }}"
    )
    assert copy_task["ansible.builtin.copy"]["dest"] == (
        "{{ item.0.dest }}/{{ item.1 }}"
    )
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


def test_compose_role_copies_only_declared_runtime_files():
    variables = yaml.safe_load(
        (
            REPO_ROOT
            / "infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml"
        ).read_text(encoding="utf-8")
    )

    projects = {
        project["name"]: project["runtime_files"]
        for project in variables["docker_compose_projects"]
    }
    assert projects == {
        "platform": ["compose.yml", "dynamic.yml", "traefik.yml"],
        "media": ["compose.yml"],
        "code": ["compose.yml", "Dockerfile"],
    }
    assert all("README.md" not in files for files in projects.values())
    assert all(".env.example" not in files for files in projects.values())


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
