from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_yaml(relative_path: str):
    return yaml.safe_load(read(relative_path))


def find_task(tasks, name: str):
    return next(task for task in tasks if task.get("name") == name)


def test_hermes_role_installs_native_gateway_without_webui_or_provider_keys():
    tasks = read("infra/ansible/roles/hermes/tasks/main.yml")

    assert "hermes_discord_bot_token is defined" in tasks
    assert "hermes_discord_allowed_users is defined" in tasks
    assert "- bash" in tasks
    assert "python3-venv" in tasks
    assert "python3-pip" in tasks
    assert "build-essential" in tasks
    assert "shell: /bin/bash" in tasks
    assert "repo: \"{{ hermes_agent_repo }}\"" in tasks
    assert "version: \"{{ hermes_agent_ref }}\"" in tasks
    assert "name: \"{{ hermes_agent_dir }}[messaging]\"" in tasks
    assert "src: hermes-gateway.env.j2" in tasks
    assert "src: hermes-gateway.service.j2" in tasks
    assert "Clone Hermes WebUI" not in tasks
    assert "Install Hermes WebUI requirements into virtualenv" not in tasks
    assert "Enable Hermes WebUI" not in tasks
    assert "docker" not in tasks.lower()
    assert "API_SERVER_KEY" not in tasks


def test_hermes_role_installs_bash_for_gateway_terminal():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    package_task = find_task(tasks, "Install Hermes runtime packages")
    assert "bash" in package_task["ansible.builtin.apt"]["name"]


def test_hermes_role_installs_github_cli_for_workspace_tasks():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    package_task = find_task(tasks, "Install Hermes runtime packages")
    assert "gh" in package_task["ansible.builtin.apt"]["name"]


def test_hermes_role_uses_bash_for_service_user_terminal():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    user_task = find_task(tasks, "Create Hermes user")
    assert user_task["ansible.builtin.user"]["shell"] == "/bin/bash"


def test_hermes_role_keeps_packaging_tools_present_without_pypi_drift():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    packaging_task = find_task(tasks, "Upgrade Hermes virtualenv packaging tools")
    assert packaging_task["ansible.builtin.pip"]["state"] == "present"


def test_hermes_role_keeps_venv_writable_for_lazy_dependency_installs():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    ownership_task = find_task(tasks, "Allow Hermes service to manage lazy venv deps")
    file_task = ownership_task["ansible.builtin.file"]
    assert file_task["path"] == "{{ hermes_venv_path }}"
    assert file_task["owner"] == "{{ hermes_user }}"
    assert file_task["group"] == "{{ hermes_group }}"
    assert file_task["recurse"] is True

    ownership_index = tasks.index(ownership_task)
    agent_pip_index = tasks.index(find_task(tasks, "Install Hermes Agent messaging extras into virtualenv"))
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert agent_pip_index < ownership_index < service_index


def test_hermes_role_requires_persistent_bind_mounts_before_writing_state():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    mount_task = find_task(tasks, "Require Hermes persistent bind mounts")
    assert mount_task["ansible.builtin.command"]["cmd"] == 'mountpoint -q "{{ item }}"'
    assert mount_task["changed_when"] is False
    assert mount_task["loop"] == ["{{ hermes_home }}", "{{ hermes_workspace }}"]

    mount_index = tasks.index(mount_task)
    dirs_index = tasks.index(find_task(tasks, "Create Hermes persistent directories"))
    assert mount_index < dirs_index


def test_hermes_role_removes_old_webui_artifacts():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    service_task = find_task(tasks, "Stop and disable old Hermes WebUI service")
    service = service_task["ansible.builtin.systemd"]
    assert service["name"] == "hermes-webui.service"
    assert service["enabled"] is False
    assert service["state"] == "stopped"
    assert service_task["failed_when"] is False

    cleanup_task = find_task(tasks, "Remove old Hermes WebUI artifacts")
    assert cleanup_task["ansible.builtin.file"]["path"] == "{{ item }}"
    assert cleanup_task["ansible.builtin.file"]["state"] == "absent"
    assert "{{ hermes_install_dir }}/hermes-webui" in cleanup_task["loop"]
    assert "/etc/hermes-webui.env" in cleanup_task["loop"]
    assert "/etc/systemd/system/hermes-webui.service" in cleanup_task["loop"]
    assert "{{ hermes_home }}/webui" in cleanup_task["loop"]


def test_hermes_role_enables_gateway_service_with_systemd():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    task = find_task(tasks, "Enable Hermes gateway")

    systemd = task["ansible.builtin.systemd"]
    assert systemd["name"] == "hermes-gateway.service"
    assert systemd["daemon_reload"] is True
    assert systemd["enabled"] is True
    assert systemd["state"] == "started"


def test_hermes_env_template_contains_discord_gateway_runtime_settings_only():
    env_template = read("infra/ansible/roles/hermes/templates/hermes-gateway.env.j2")

    assert "HERMES_HOME={{ hermes_home | quote }}" in env_template
    assert "HERMES_CONFIG_PATH={{ (hermes_home + '/config.yaml') | quote }}" in env_template
    assert "SHELL=/bin/bash" in env_template
    assert "PYTHONUNBUFFERED=1" in env_template
    assert "DISCORD_BOT_TOKEN={{ hermes_discord_bot_token | quote }}" in env_template
    assert "DISCORD_ALLOWED_USERS={{ hermes_discord_allowed_users | quote }}" in env_template
    assert "DISCORD_REQUIRE_MENTION={{ hermes_discord_require_mention | string | lower | quote }}" in env_template
    assert "DISCORD_IGNORE_NO_MENTION={{ hermes_discord_ignore_no_mention | string | lower | quote }}" in env_template
    assert "HERMES_WEBUI" not in env_template
    assert "API_SERVER_KEY" not in env_template
    assert "OPENAI_API_KEY" not in env_template


def test_hermes_service_template_runs_gateway_from_agent_venv():
    service = read("infra/ansible/roles/hermes/templates/hermes-gateway.service.j2")

    assert "Description=Hermes Agent Discord gateway" in service
    assert "User={{ hermes_user }}" in service
    assert "Group={{ hermes_group }}" in service
    assert "EnvironmentFile={{ hermes_env_path }}" in service
    assert "WorkingDirectory={{ hermes_workspace }}" in service
    assert "ExecStart={{ hermes_venv_path }}/bin/hermes gateway" in service
    assert "Restart=on-failure" in service
    assert "WantedBy=multi-user.target" in service


def test_hermes_handler_restarts_gateway_service():
    handlers = read_yaml("infra/ansible/roles/hermes/handlers/main.yml")
    handler = next(handler for handler in handlers if handler.get("name") == "Restart hermes-gateway")

    systemd = handler["ansible.builtin.systemd"]
    assert systemd["name"] == "hermes-gateway.service"
    assert systemd["state"] == "restarted"
    assert systemd["daemon_reload"] is True
