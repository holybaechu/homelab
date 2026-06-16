from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_yaml(relative_path: str):
    return yaml.safe_load(read(relative_path))


def find_task(tasks, name: str):
    return next(task for task in tasks if task.get("name") == name)


def test_hermes_role_installs_native_service_without_docker_or_provider_keys():
    tasks = read("infra/ansible/roles/hermes/tasks/main.yml")

    assert "hermes_webui_password is defined" in tasks
    assert "hermes_webui_password | length > 0" in tasks
    assert "- bash" in tasks
    assert "python3-venv" in tasks
    assert "python3-pip" in tasks
    assert "build-essential" in tasks
    assert "shell: /bin/bash" in tasks
    assert "repo: \"{{ hermes_agent_repo }}\"" in tasks
    assert "version: \"{{ hermes_agent_ref }}\"" in tasks
    assert "repo: \"{{ hermes_webui_repo }}\"" in tasks
    assert "version: \"{{ hermes_webui_ref }}\"" in tasks
    assert "requirements: \"{{ hermes_webui_dir }}/requirements.txt\"" in tasks
    assert "src: hermes-webui.env.j2" in tasks
    assert "src: hermes-webui.service.j2" in tasks
    assert "docker" not in tasks.lower()
    assert "API_SERVER_KEY" not in tasks


def test_hermes_role_installs_bash_for_webui_terminal():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    package_task = find_task(tasks, "Install Hermes runtime packages")
    assert "bash" in package_task["ansible.builtin.apt"]["name"]


def test_hermes_role_uses_bash_for_service_user_terminal():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    user_task = find_task(tasks, "Create Hermes user")
    assert user_task["ansible.builtin.user"]["shell"] == "/bin/bash"


def test_hermes_role_keeps_packaging_tools_present_without_pypi_drift():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    packaging_task = find_task(tasks, "Upgrade Hermes virtualenv packaging tools")
    assert packaging_task["ansible.builtin.pip"]["state"] == "present"


def test_hermes_role_enables_webui_service_with_systemd():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    task = find_task(tasks, "Enable Hermes WebUI")

    systemd = task["ansible.builtin.systemd"]
    assert systemd["name"] == "hermes-webui.service"
    assert systemd["daemon_reload"] is True
    assert systemd["enabled"] is True
    assert systemd["state"] == "started"


def test_hermes_env_template_contains_webui_runtime_settings_only():
    env_template = read("infra/ansible/roles/hermes/templates/hermes-webui.env.j2")

    assert "HERMES_HOME={{ hermes_home | quote }}" in env_template
    assert "HERMES_CONFIG_PATH={{ (hermes_home + '/config.yaml') | quote }}" in env_template
    assert "HERMES_WEBUI_AGENT_DIR={{ hermes_agent_dir | quote }}" in env_template
    assert "HERMES_WEBUI_PYTHON={{ (hermes_venv_path + '/bin/python') | quote }}" in env_template
    assert "HERMES_WEBUI_HOST={{ hermes_webui_host | quote }}" in env_template
    assert "HERMES_WEBUI_PORT={{ hermes_webui_port | string | quote }}" in env_template
    assert "HERMES_WEBUI_STATE_DIR={{ hermes_webui_state_dir | quote }}" in env_template
    assert "HERMES_WEBUI_DEFAULT_WORKSPACE={{ hermes_webui_default_workspace | quote }}" in env_template
    assert "HERMES_WEBUI_PASSWORD={{ hermes_webui_password | quote }}" in env_template
    assert "SHELL=/bin/bash" in env_template
    assert "API_SERVER_KEY" not in env_template
    assert "OPENAI_API_KEY" not in env_template


def test_hermes_service_template_runs_webui_from_agent_venv():
    service = read("infra/ansible/roles/hermes/templates/hermes-webui.service.j2")

    assert "Description=Hermes Agent WebUI" in service
    assert "User={{ hermes_user }}" in service
    assert "Group={{ hermes_group }}" in service
    assert "EnvironmentFile={{ hermes_env_path }}" in service
    assert "WorkingDirectory={{ hermes_agent_dir }}" in service
    assert "ExecStart={{ hermes_venv_path }}/bin/python {{ hermes_webui_dir }}/server.py" in service
    assert "Restart=on-failure" in service
    assert "WantedBy=multi-user.target" in service


def test_hermes_handler_restarts_webui_service():
    handlers = read_yaml("infra/ansible/roles/hermes/handlers/main.yml")
    handler = next(handler for handler in handlers if handler.get("name") == "Restart hermes-webui")

    systemd = handler["ansible.builtin.systemd"]
    assert systemd["name"] == "hermes-webui.service"
    assert systemd["state"] == "restarted"
    assert systemd["daemon_reload"] is True
