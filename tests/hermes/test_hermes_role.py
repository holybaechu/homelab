
import yaml




from tests.helpers import REPO_ROOT
def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_yaml(relative_path: str):
    return yaml.safe_load(read(relative_path))


def find_task(tasks, name: str):
    return next(task for task in tasks if task.get("name") == name)


def test_hermes_role_installs_native_gateway_without_webui_or_model_provider_keys():
    tasks = read("infra/ansible/roles/hermes/tasks/main.yml")

    assert "hermes_discord_bot_token is defined" in tasks
    assert "hermes_discord_allowed_users is defined" in tasks
    assert "hermes_parallel_api_key is defined" in tasks
    assert "hermes_firecrawl_api_key is defined" in tasks
    assert "hermes_browserbase_api_key is defined" in tasks
    assert "hermes_browserbase_project_id is defined" in tasks
    assert "hermes_1password_service_account_token is defined" in tasks
    assert "- bash" in tasks
    assert "- nodejs" in tasks
    assert "- npm" in tasks
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


def test_hermes_role_installs_1password_cli_and_skill_for_secret_access():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    key_task = find_task(tasks, "Download 1Password apt signing key")
    assert key_task["ansible.builtin.get_url"]["url"] == (
        "https://downloads.1password.com/linux/keys/1password.asc"
    )
    assert key_task["ansible.builtin.get_url"]["dest"] == (
        "/usr/share/keyrings/1password-archive-keyring.asc"
    )

    repo_task = find_task(tasks, "Configure 1Password apt repository")
    assert "downloads.1password.com/linux/debian/amd64" in repo_task["ansible.builtin.copy"]["content"]
    assert repo_task["ansible.builtin.copy"]["dest"] == "/etc/apt/sources.list.d/1password.list"

    op_task = find_task(tasks, "Install 1Password CLI")
    assert op_task["ansible.builtin.apt"]["name"] == "1password-cli"
    assert op_task["ansible.builtin.apt"]["update_cache"] is True

    skill_check_task = find_task(tasks, "Check Hermes 1Password skill")
    assert skill_check_task["ansible.builtin.stat"]["path"] == (
        "{{ hermes_home }}/skills/security/1password/SKILL.md"
    )
    assert skill_check_task["register"] == "hermes_1password_skill"

    skill_task = find_task(tasks, "Install Hermes 1Password skill")
    assert skill_task["ansible.builtin.command"]["cmd"] == (
        "{{ hermes_venv_path }}/bin/hermes skills install official/security/1password --yes"
    )
    assert skill_task["environment"] == {
        "HERMES_HOME": "{{ hermes_home }}",
        "HOME": "{{ hermes_home }}",
    }
    assert skill_task["when"] == "not hermes_1password_skill.stat.exists"
    assert skill_task["notify"] == "Restart hermes-gateway"

    ownership_task = find_task(tasks, "Allow Hermes service to manage skills")
    assert ownership_task["ansible.builtin.file"]["path"] == "{{ hermes_home }}/skills"
    assert ownership_task["ansible.builtin.file"]["owner"] == "{{ hermes_user }}"
    assert ownership_task["ansible.builtin.file"]["group"] == "{{ hermes_group }}"
    assert ownership_task["ansible.builtin.file"]["recurse"] is True

    op_config_task = find_task(tasks, "Allow Hermes service to manage 1Password CLI config")
    assert op_config_task["ansible.builtin.file"]["path"] == "{{ hermes_home }}/.config/op"
    assert op_config_task["ansible.builtin.file"]["state"] == "directory"
    assert op_config_task["ansible.builtin.file"]["owner"] == "{{ hermes_user }}"
    assert op_config_task["ansible.builtin.file"]["group"] == "{{ hermes_group }}"
    assert op_config_task["ansible.builtin.file"]["mode"] == "0700"
    assert "recurse" not in op_config_task["ansible.builtin.file"]

    op_config_file_check_task = find_task(tasks, "Check Hermes 1Password CLI config file")
    assert op_config_file_check_task["ansible.builtin.stat"]["path"] == (
        "{{ hermes_home }}/.config/op/config"
    )
    assert op_config_file_check_task["ansible.builtin.stat"]["follow"] is False
    assert op_config_file_check_task["register"] == "hermes_op_config_file"

    op_config_file_task = find_task(tasks, "Secure Hermes 1Password CLI config file")
    assert op_config_file_task["ansible.builtin.file"]["path"] == (
        "{{ hermes_home }}/.config/op/config"
    )
    assert op_config_file_task["ansible.builtin.file"]["state"] == "file"
    assert op_config_file_task["ansible.builtin.file"]["owner"] == "{{ hermes_user }}"
    assert op_config_file_task["ansible.builtin.file"]["group"] == "{{ hermes_group }}"
    assert op_config_file_task["ansible.builtin.file"]["mode"] == "0600"
    assert op_config_file_task["ansible.builtin.file"]["follow"] is False
    assert op_config_file_task["when"] == "hermes_op_config_file.stat.exists"

    runtime_package_index = tasks.index(find_task(tasks, "Install Hermes runtime packages"))
    key_index = tasks.index(key_task)
    repo_index = tasks.index(repo_task)
    op_index = tasks.index(op_task)
    agent_pip_index = tasks.index(find_task(tasks, "Install Hermes Agent messaging extras into virtualenv"))
    skill_check_index = tasks.index(skill_check_task)
    skill_index = tasks.index(skill_task)
    ownership_index = tasks.index(ownership_task)
    op_config_index = tasks.index(op_config_task)
    op_config_file_check_index = tasks.index(op_config_file_check_task)
    op_config_file_index = tasks.index(op_config_file_task)
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert runtime_package_index < key_index < repo_index < op_index
    assert (
        agent_pip_index
        < skill_check_index
        < skill_index
        < ownership_index
        < op_config_index
        < op_config_file_check_index
        < op_config_file_index
        < service_index
    )


def test_hermes_role_installs_agent_browser_node_dependencies():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")

    package_task = find_task(tasks, "Install Hermes runtime packages")
    assert "nodejs" in package_task["ansible.builtin.apt"]["name"]
    assert "npm" in package_task["ansible.builtin.apt"]["name"]

    checkout_task = find_task(tasks, "Clone Hermes Agent")
    assert checkout_task["register"] == "hermes_agent_checkout"

    stat_task = find_task(tasks, "Check Hermes browser automation CLI")
    assert stat_task["ansible.builtin.stat"]["path"] == (
        "{{ hermes_agent_dir }}/node_modules/.bin/agent-browser"
    )
    assert stat_task["register"] == "hermes_agent_browser_cli"

    npm_task = find_task(tasks, "Install Hermes browser automation Node dependencies")
    assert npm_task["ansible.builtin.command"]["cmd"] == (
        "npm ci --omit=dev --silent --workspaces=false"
    )
    assert npm_task["ansible.builtin.command"]["chdir"] == "{{ hermes_agent_dir }}"
    assert npm_task["when"] == (
        "hermes_agent_checkout.changed or not hermes_agent_browser_cli.stat.exists"
    )
    assert npm_task["notify"] == "Restart hermes-gateway"

    checkout_index = tasks.index(checkout_task)
    stat_index = tasks.index(stat_task)
    npm_index = tasks.index(npm_task)
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert checkout_index < stat_index < npm_index < service_index


def test_hermes_role_installs_local_browser_runtime_for_private_url_routing():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")

    assert group_vars["hermes_browser_browsers_path"] == "{{ hermes_home }}/.agent-browser/browsers"

    directory_task = find_task(tasks, "Create Hermes local browser runtime directory")
    assert directory_task["ansible.builtin.file"]["path"] == "{{ hermes_browser_browsers_path }}"
    assert directory_task["ansible.builtin.file"]["state"] == "directory"
    assert directory_task["ansible.builtin.file"]["owner"] == "{{ hermes_user }}"
    assert directory_task["ansible.builtin.file"]["group"] == "{{ hermes_group }}"

    find_task_result = find_task(tasks, "Check Hermes local browser runtime")
    assert find_task_result["ansible.builtin.find"]["paths"] == "{{ hermes_browser_browsers_path }}"
    assert find_task_result["ansible.builtin.find"]["patterns"] == [
        "chrome-*",
    ]
    assert find_task_result["register"] == "hermes_local_browser_builds"

    install_task = find_task(tasks, "Install Hermes local browser runtime for private URL routing")
    assert install_task["ansible.builtin.command"]["cmd"] == (
        "{{ hermes_agent_dir }}/node_modules/.bin/agent-browser install --with-deps"
    )
    assert install_task["ansible.builtin.command"]["chdir"] == "{{ hermes_agent_dir }}"
    assert install_task["environment"] == {"HOME": "{{ hermes_home }}"}
    assert install_task["when"] == (
        "hermes_browser_auto_local_for_private_urls | bool and "
        "(hermes_agent_checkout.changed or "
        "not hermes_agent_browser_cli.stat.exists or "
        "hermes_local_browser_builds.matched | int == 0)"
    )
    assert install_task["notify"] == "Restart hermes-gateway"

    ownership_task = find_task(tasks, "Allow Hermes service to manage local browser runtime")
    assert ownership_task["ansible.builtin.file"]["path"] == "{{ hermes_browser_browsers_path }}"
    assert ownership_task["ansible.builtin.file"]["owner"] == "{{ hermes_user }}"
    assert ownership_task["ansible.builtin.file"]["group"] == "{{ hermes_group }}"
    assert ownership_task["ansible.builtin.file"]["recurse"] is True

    npm_index = tasks.index(find_task(tasks, "Install Hermes browser automation Node dependencies"))
    directory_index = tasks.index(directory_task)
    find_index = tasks.index(find_task_result)
    install_index = tasks.index(install_task)
    ownership_index = tasks.index(ownership_task)
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert npm_index < directory_index < find_index < install_index < ownership_index < service_index


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


def test_hermes_role_routes_auxiliary_compression_to_main_provider_with_longer_timeout():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script_path = REPO_ROOT / "infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2"
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert group_vars["hermes_auxiliary_compression_provider"] == "main"
    assert group_vars["hermes_auxiliary_compression_timeout"] == 300
    assert "hermes_auxiliary_compression_model" not in group_vars

    template_task = find_task(tasks, "Install Hermes runtime configuration helper")
    assert template_task["ansible.builtin.template"]["src"] == "hermes-configure-runtime.py.j2"
    assert template_task["ansible.builtin.template"]["dest"] == "{{ hermes_install_dir }}/configure-runtime.py"
    assert template_task["ansible.builtin.template"]["mode"] == "0755"

    configure_task = find_task(tasks, "Configure Hermes runtime settings")
    assert configure_task["ansible.builtin.command"]["cmd"] == (
        "{{ hermes_venv_path }}/bin/python "
        "{{ hermes_install_dir }}/configure-runtime.py"
    )
    assert configure_task["changed_when"] == 'hermes_runtime_config.stdout == "changed"'
    assert configure_task["notify"] == "Restart hermes-gateway"

    configure_index = tasks.index(configure_task)
    ownership_index = tasks.index(find_task(tasks, "Allow Hermes service to manage lazy venv deps"))
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert ownership_index < configure_index < service_index

    assert "hermes_auxiliary_compression_provider" in script
    assert "hermes_auxiliary_compression_timeout" in script
    assert 'compression["model"] = ""' in script
    assert 'compression["timeout"] = DESIRED_COMPRESSION_TIMEOUT' in script
    assert "gpt-5.5" not in script
    assert "api_key" not in script


def test_validate_playbook_asserts_hermes_compression_timeout():
    validate = read_yaml("infra/ansible/playbooks/validate.yml")
    hermes_play = next(
        (play for play in validate if play.get("name") == "Validate hermes"),
        None,
    )

    assert hermes_play is not None
    hermes_tasks = yaml.safe_dump(hermes_play.get("tasks", []), sort_keys=True)
    assert "Check Hermes compression timeout" in hermes_tasks
    assert "auxiliary.compression.provider" in hermes_tasks
    assert "auxiliary.compression.timeout" in hermes_tasks
    assert "main" in hermes_tasks
    assert "300" in hermes_tasks


def test_hermes_role_configures_parallel_search_and_firecrawl_extract():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script = read("infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2")

    assert group_vars["hermes_web_search_backend"] == "parallel"
    assert group_vars["hermes_web_extract_backend"] == "firecrawl"

    assert "DESIRED_WEB_SEARCH_BACKEND" in script
    assert "DESIRED_WEB_EXTRACT_BACKEND" in script
    assert 'web["search_backend"] = DESIRED_WEB_SEARCH_BACKEND' in script
    assert 'web["extract_backend"] = DESIRED_WEB_EXTRACT_BACKEND' in script

    configure_task = find_task(tasks, "Configure Hermes runtime settings")
    assert configure_task["notify"] == "Restart hermes-gateway"


def test_hermes_role_configures_browserbase_browser_automation():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script = read("infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2")

    assert group_vars["hermes_browser_cloud_provider"] == "browserbase"
    assert group_vars["hermes_browser_auto_local_for_private_urls"] is True
    assert group_vars["hermes_browser_browsers_path"] == "{{ hermes_home }}/.agent-browser/browsers"
    assert group_vars["hermes_browser_args"] == "--no-sandbox,--disable-dev-shm-usage"
    assert group_vars["hermes_browserbase_proxies"] is True
    assert group_vars["hermes_browserbase_advanced_stealth"] is False

    assert "DESIRED_BROWSER_CLOUD_PROVIDER" in script
    assert "DESIRED_BROWSER_AUTO_LOCAL_FOR_PRIVATE_URLS" in script
    assert 'browser = ensure_dict(config, "browser")' in script
    assert 'browser["cloud_provider"] = DESIRED_BROWSER_CLOUD_PROVIDER' in script
    assert (
        'browser["auto_local_for_private_urls"] = '
        'DESIRED_BROWSER_AUTO_LOCAL_FOR_PRIVATE_URLS'
    ) in script
    assert 'platform_toolsets = ensure_dict(config, "platform_toolsets")' in script
    assert 'DESIRED_DISCORD_TOOLSETS = ["hermes-discord", "browser"]' in script
    assert "discord_toolsets = ensure_list" in script
    assert '"discord"' in script

    configure_task = find_task(tasks, "Configure Hermes runtime settings")
    assert configure_task["notify"] == "Restart hermes-gateway"


def test_hermes_role_configures_compression_threshold_and_codex_gpt55_autoraise():
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script = read("infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2")

    assert group_vars["hermes_compression_threshold"] == 0.85
    assert group_vars["hermes_compression_codex_gpt55_autoraise"] is False

    assert "DESIRED_COMPRESSION_THRESHOLD" in script
    assert "DESIRED_CODEX_GPT55_AUTORAISE" in script
    assert 'runtime_compression = ensure_dict(config, "compression")' in script
    assert 'runtime_compression["threshold"] = DESIRED_COMPRESSION_THRESHOLD' in script
    assert (
        'runtime_compression["codex_gpt55_autoraise"] = '
        'DESIRED_CODEX_GPT55_AUTORAISE'
    ) in script


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


def test_hermes_env_template_contains_discord_gateway_runtime_web_and_browser_credentials():
    env_template = read("infra/ansible/roles/hermes/templates/hermes-gateway.env.j2")

    assert "HERMES_HOME={{ hermes_home | quote }}" in env_template
    assert "HERMES_CONFIG_PATH={{ (hermes_home + '/config.yaml') | quote }}" in env_template
    assert "SHELL=/bin/bash" in env_template
    assert "PYTHONUNBUFFERED=1" in env_template
    assert "DISCORD_BOT_TOKEN={{ hermes_discord_bot_token | quote }}" in env_template
    assert "DISCORD_ALLOWED_USERS={{ hermes_discord_allowed_users | quote }}" in env_template
    assert "DISCORD_REQUIRE_MENTION={{ hermes_discord_require_mention | string | lower | quote }}" in env_template
    assert "DISCORD_IGNORE_NO_MENTION={{ hermes_discord_ignore_no_mention | string | lower | quote }}" in env_template
    assert "PARALLEL_API_KEY={{ hermes_parallel_api_key | quote }}" in env_template
    assert "FIRECRAWL_API_KEY={{ hermes_firecrawl_api_key | quote }}" in env_template
    assert "BROWSERBASE_API_KEY={{ hermes_browserbase_api_key | quote }}" in env_template
    assert "BROWSERBASE_PROJECT_ID={{ hermes_browserbase_project_id | quote }}" in env_template
    assert "BROWSERBASE_PROXIES={{ hermes_browserbase_proxies | string | lower | quote }}" in env_template
    assert "BROWSERBASE_ADVANCED_STEALTH={{ hermes_browserbase_advanced_stealth | string | lower | quote }}" in env_template
    assert "AGENT_BROWSER_ARGS={{ hermes_browser_args | quote }}" in env_template
    assert "OP_SERVICE_ACCOUNT_TOKEN={{ hermes_1password_service_account_token | quote }}" in env_template
    assert "HOME={{ hermes_home | quote }}" in env_template
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
    assert "TimeoutStopSec=210s" in service


def test_hermes_handler_restarts_gateway_service():
    handlers = read_yaml("infra/ansible/roles/hermes/handlers/main.yml")
    handler = next(handler for handler in handlers if handler.get("name") == "Restart hermes-gateway")

    systemd = handler["ansible.builtin.systemd"]
    assert systemd["name"] == "hermes-gateway.service"
    assert systemd["state"] == "restarted"
    assert systemd["daemon_reload"] is True
