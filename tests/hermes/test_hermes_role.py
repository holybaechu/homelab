
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
    assert "hermes_config_repo_token is defined" in tasks
    assert "hermes_config_webhook_secret is defined" in tasks
    assert "hermes_newrrow_username_ref is defined" in tasks
    assert "hermes_newrrow_password_ref is defined" in tasks
    assert "- bash" in tasks
    assert "- git" in tasks
    assert "- inotify-tools" in tasks
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


def test_hermes_role_installs_1password_cli_and_validates_live_config_skill_for_secret_access():
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

    required_artifacts_task = find_task(tasks, "Check Hermes config required skills and plugins")
    required_artifacts_shell = required_artifacts_task["ansible.builtin.shell"]
    assert "{{ hermes_home }}/skills/security/1password/SKILL.md" in required_artifacts_shell
    assert "{{ hermes_home }}/skills/newrrow-points-automation/SKILL.md" in required_artifacts_shell
    assert "{{ hermes_home }}/plugins/newrrow-browser-login/plugin.yaml" in required_artifacts_shell
    assert "hermes skills install official/security/1password" not in read(
        "infra/ansible/roles/hermes/tasks/main.yml"
    )

    ownership_task = find_task(tasks, "Allow Hermes service to manage skills")
    assert ownership_task["ansible.builtin.file"]["path"] == "{{ hermes_config_dir }}/skills"
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
    required_artifacts_index = tasks.index(required_artifacts_task)
    ownership_index = tasks.index(ownership_task)
    op_config_index = tasks.index(op_config_task)
    op_config_file_check_index = tasks.index(op_config_file_check_task)
    op_config_file_index = tasks.index(op_config_file_task)
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert runtime_package_index < key_index < repo_index < op_index
    assert (
        agent_pip_index
        < ownership_index
        < op_config_index
        < op_config_file_check_index
        < op_config_file_index
        < required_artifacts_index
        < service_index
    )


def test_hermes_role_wires_live_hermes_config_git_sync_with_restart_handler():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    sync_template = read("infra/ansible/roles/hermes/templates/hermes-config-git-sync.sh.j2")
    apply_template = read("infra/ansible/roles/hermes/templates/hermes-config-apply.py.j2")
    webhook_template = read("infra/ansible/roles/hermes/templates/hermes-config-webhook.py.j2")
    sync_env_template = read("infra/ansible/roles/hermes/templates/hermes-config-sync.env.j2")
    watch_service = read("infra/ansible/roles/hermes/templates/hermes-config-watch.service.j2")
    webhook_service = read("infra/ansible/roles/hermes/templates/hermes-config-webhook.service.j2")
    sync_service = read("infra/ansible/roles/hermes/templates/hermes-config-sync.service.j2")
    sync_timer = read("infra/ansible/roles/hermes/templates/hermes-config-sync.timer.j2")

    assert group_vars["hermes_config_repo"] == "https://github.com/holybaechu/hermes-config.git"
    assert group_vars["hermes_config_branch"] == "main"
    assert group_vars["hermes_config_webhook_host"] == "0.0.0.0"
    assert group_vars["hermes_config_webhook_port"] == 8787
    assert group_vars["hermes_config_commit_user_name"] == "holybaechu"
    assert group_vars["hermes_config_commit_user_email"] == "holybaechu@proton.me"
    assert group_vars["hermes_config_dir"] == "{{ hermes_home }}/hermes-config"
    assert "^config/" in group_vars["hermes_config_restart_patterns"]
    assert "^profiles/" in group_vars["hermes_config_restart_patterns"]
    assert "^plugins/" in group_vars["hermes_config_restart_patterns"]
    assert "^rules/" in group_vars["hermes_config_restart_patterns"]

    for name in (
        "Install Hermes config sync environment",
        "Install Hermes config git askpass helper",
        "Install Hermes config apply helper",
        "Install Hermes config git sync helper",
        "Install Hermes config webhook receiver",
        "Install Hermes config sync service",
        "Install Hermes config sync timer",
        "Install Hermes config watch service",
        "Install Hermes config webhook service",
        "Synchronize live Hermes config repository",
        "Enable Hermes config synchronization services",
    ):
        find_task(tasks, name)

    package_task = find_task(tasks, "Install Hermes runtime packages")
    assert "inotify-tools" in package_task["ansible.builtin.apt"]["name"]

    sync_task = find_task(tasks, "Synchronize live Hermes config repository")
    assert sync_task["ansible.builtin.command"]["cmd"] == "{{ hermes_config_git_sync_path }} --source ansible"
    assert "apply_changed=true" in sync_task["changed_when"]
    assert sync_task["no_log"] is True

    enable_task = find_task(tasks, "Enable Hermes config synchronization services")
    assert enable_task["loop"] == [
        "hermes-config-sync.timer",
        "hermes-config-watch.service",
        "hermes-config-webhook.service",
    ]

    assert "HERMES_CONFIG_GIT_TOKEN={{ hermes_config_repo_token | quote }}" in sync_env_template
    assert "HERMES_CONFIG_WEBHOOK_SECRET={{ hermes_config_webhook_secret | quote }}" in sync_env_template
    assert "HERMES_CONFIG_COMMIT_USER_NAME={{ hermes_config_commit_user_name | quote }}" in sync_env_template
    assert "HERMES_CONFIG_COMMIT_USER_EMAIL={{ hermes_config_commit_user_email | quote }}" in sync_env_template
    assert "git reset --hard" not in sync_template
    assert "rebase" in sync_template
    assert "push origin" in sync_template
    assert "systemctl try-restart" in sync_template
    assert "restart_handler=try-restart" in sync_template
    assert "HERMES_CONFIG_RESTART_PATTERNS" in sync_template
    assert "git_cmd config user.name \"$HERMES_CONFIG_COMMIT_USER_NAME\"" in sync_template
    assert "git_cmd config user.email \"$HERMES_CONFIG_COMMIT_USER_EMAIL\"" in sync_template
    assert "Hermes Config Bot" not in sync_template
    assert "hermes-config@hchu.me" not in sync_template
    assert "HOMELAB_OWNED_DEFAULT_CONFIG_PATHS" in apply_template
    assert "reject_homelab_owned_default_config(desired)" in apply_template
    assert '("platform_toolsets", "discord")' in apply_template
    assert '("plugins", "enabled")' in apply_template
    assert "X-Hub-Signature-256" in webhook_template
    assert "hmac.compare_digest" in webhook_template
    assert "inotifywait" in watch_service
    assert "EnvironmentFile={{ hermes_config_sync_env_path }}" in webhook_service
    assert "ExecStart={{ hermes_config_git_sync_path }} --source systemd" in sync_service
    assert "OnCalendar={{ hermes_config_sync_timer_schedule }}" in sync_timer

    sync_index = tasks.index(sync_task)
    required_artifacts_index = tasks.index(find_task(tasks, "Check Hermes config required skills and plugins"))
    service_index = tasks.index(find_task(tasks, "Enable Hermes config synchronization services"))
    gateway_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert sync_index < required_artifacts_index < service_index < gateway_index


def test_hermes_role_uses_hermes_config_for_newrrow_skill_and_plugin():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    env_template = read("infra/ansible/roles/hermes/templates/hermes-gateway.env.j2")
    tasks_text = read("infra/ansible/roles/hermes/tasks/main.yml")

    assert "hermes_newrrow_base_url" not in group_vars
    assert "hermes_newrrow_home_url" not in group_vars
    assert "hermes_newrrow_login_url" not in group_vars
    assert group_vars["hermes_newrrow_username_ref"] == "op://Hermes/Newrrow/username"
    assert group_vars["hermes_newrrow_password_ref"] == "op://Hermes/Newrrow/password"

    assert "NEWRROW_BASE_URL" not in env_template
    assert "NEWRROW_HOME_URL" not in env_template
    assert "NEWRROW_LOGIN_URL" not in env_template
    assert "NEWRROW_USERNAME_REF={{ hermes_newrrow_username_ref | quote }}" in env_template
    assert "NEWRROW_PASSWORD_REF=" in env_template
    assert "hermes_newrrow_password_ref | quote" in env_template

    assert "src: skills/newrrow-points-automation/" not in tasks_text
    assert "src: plugins/newrrow-browser-login/" not in tasks_text
    assert "Check Hermes config required skills and plugins" in tasks_text
    assert "{{ hermes_home }}/skills/newrrow-points-automation/SKILL.md" in tasks_text
    assert "{{ hermes_home }}/plugins/newrrow-browser-login/plugin.yaml" in tasks_text

    login_helper_task = find_task(tasks, "Make Hermes config Newrrow login helper executable")
    assert login_helper_task["ansible.builtin.file"]["path"] == (
        "{{ hermes_home }}/skills/newrrow-points-automation/scripts/newrrow-login.sh"
    )
    assert login_helper_task["ansible.builtin.file"]["mode"] == "0755"

    sync_index = tasks.index(find_task(tasks, "Synchronize live Hermes config repository"))
    check_index = tasks.index(find_task(tasks, "Check Hermes config required skills and plugins"))
    helper_index = tasks.index(login_helper_task)
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert sync_index < check_index < helper_index < service_index

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
    assert group_vars["hermes_auxiliary_compression_timeout"] == 360
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
    assert 'set_value(compression, "model", "")' in script
    assert 'set_value(compression, "timeout", DESIRED_COMPRESSION_TIMEOUT)' in script
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
    assert "360" in hermes_tasks


def test_hermes_role_configures_parallel_search_and_firecrawl_extract():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script = read("infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2")

    assert group_vars["hermes_web_search_backend"] == "parallel"
    assert group_vars["hermes_web_extract_backend"] == "firecrawl"

    assert "DESIRED_WEB_SEARCH_BACKEND" in script
    assert "DESIRED_WEB_EXTRACT_BACKEND" in script
    assert 'set_value(web, "search_backend", DESIRED_WEB_SEARCH_BACKEND)' in script
    assert 'set_value(web, "extract_backend", DESIRED_WEB_EXTRACT_BACKEND)' in script

    configure_task = find_task(tasks, "Configure Hermes runtime settings")
    assert configure_task["notify"] == "Restart hermes-gateway"





def test_hermes_role_configures_5_plus_1_profiles_and_kanban_dispatcher():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script = read("infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2")

    expected_profiles = [
        "orchestrator",
        "homelab",
        "dev",
        "research",
        "sandbox",
        "browser-protected",
    ]
    assert [profile["name"] for profile in group_vars["hermes_profiles"]] == expected_profiles
    assert [board["slug"] for board in group_vars["hermes_kanban_boards"]] == [
        "default",
        "homelab",
        "research",
        "automation",
    ]
    assert group_vars["hermes_discord_home_channel"] == "{{ hermes_discord_allowed_users }}"
    assert group_vars["hermes_kanban_diagnostics_cron"] == {
        "name": "homelab-kanban-daily-diagnostics",
        "schedule": "0 9 * * *",
        "deliver": "discord",
        "script": "hermes-kanban-diagnostics.sh",
    }
    assert {item["name"] for item in group_vars["hermes_profile_bundled_skill_sources"]} == {
        "plan",
        "hermes-agent",
        "github-pr-workflow",
        "requesting-code-review",
        "systematic-debugging",
        "test-driven-development",
        "arxiv",
        "youtube-content",
        "spike",
    }
    assert "model" not in yaml.safe_dump(group_vars["hermes_profiles"])
    assert "provider" not in yaml.safe_dump(group_vars["hermes_profiles"])
    assert "Evidence required" in group_vars["hermes_kanban_card_template"]
    assert "review-required" in group_vars["hermes_kanban_review_required_policy"]
    assert "control plane" in group_vars["hermes_default_control_plane_guidance"]
    assert group_vars["hermes_kanban"] == {
        "dispatch_in_gateway": True,
        "dispatch_interval_seconds": 60,
        "failure_limit": 2,
        "max_spawn": 3,
        "max_in_progress": 3,
        "max_in_progress_per_profile": 1,
        "auto_decompose": True,
        "auto_decompose_per_tick": 2,
        "orchestrator_profile": "orchestrator",
        "default_assignee": "orchestrator",
        "dispatch_stale_timeout_seconds": 14400,
    }

    required_skill_task = find_task(tasks, "Install Hermes bundled profile required skills")
    assert required_skill_task["ansible.builtin.copy"]["src"] == "{{ hermes_agent_dir }}/skills/{{ item.path }}/"
    assert required_skill_task["ansible.builtin.copy"]["dest"] == "{{ hermes_home }}/skills/{{ item.path }}/"
    assert required_skill_task["ansible.builtin.copy"]["remote_src"] is True
    assert required_skill_task["loop"] == "{{ hermes_profile_bundled_skill_sources }}"
    manage_required_skills_task = find_task(tasks, "Allow Hermes service to manage required profile skills")
    assert manage_required_skills_task["ansible.builtin.file"]["path"] == "{{ hermes_home }}/skills"
    assert manage_required_skills_task["ansible.builtin.file"]["recurse"] is True

    create_task = find_task(tasks, "Create Hermes 5+1 profile directories")
    assert create_task["ansible.builtin.command"]["argv"] == [
        "{{ hermes_venv_path }}/bin/hermes",
        "profile",
        "create",
        "{{ item.name }}",
        "--no-alias",
        "--description",
        "{{ item.description }}",
    ]
    assert create_task["ansible.builtin.command"]["creates"] == (
        "{{ hermes_home }}/profiles/{{ item.name }}"
    )
    assert create_task["become_user"] == "{{ hermes_user }}"
    assert create_task["loop"] == "{{ hermes_profiles }}"

    kanban_init_task = find_task(tasks, "Initialize Hermes Kanban board")
    assert kanban_init_task["ansible.builtin.command"]["argv"] == [
        "{{ hermes_venv_path }}/bin/hermes",
        "kanban",
        "init",
    ]
    assert kanban_init_task["ansible.builtin.command"]["creates"] == "{{ hermes_home }}/kanban.db"
    assert kanban_init_task["become_user"] == "{{ hermes_user }}"

    env_template = read("infra/ansible/roles/hermes/templates/hermes-gateway.env.j2")
    assert "DISCORD_HOME_CHANNEL={{ hermes_discord_home_channel | quote }}" in env_template
    assert 'DESIRED_DISCORD_TOOLSETS = ["hermes-discord", "browser", "kanban"]' in script
    assert "DESIRED_KANBAN" in script
    assert "DESIRED_PROFILES" in script
    assert "DESIRED_DEFAULT_CONTROL_PLANE" in script
    assert "DESIRED_KANBAN_CARD_TEMPLATE" in script
    assert "DESIRED_KANBAN_REVIEW_REQUIRED_POLICY" in script
    assert "DESIRED_KANBAN_BOARDS" in script
    assert "ensure_default_soul" in script
    assert "ensure_policy_files" in script
    assert "ensure_profile_required_skills" in script
    assert "missing required skill source" in script
    assert "apply_profile_runtime" in script
    assert 'set_value(kanban, "dispatch_in_gateway", False)' in script
    assert 'config["platform_toolsets"] = {"cli": desired_cli_toolsets}' in script
    assert "BROWSERBASE_PROXIES" in script
    assert "key in seen" in script
    assert "MANAGED_SOUL_START" in script
    assert "MANAGED_DEFAULT_SOUL_START" in script

    browser_profile = next(
        profile for profile in group_vars["hermes_profiles"] if profile["name"] == "browser-protected"
    )
    normal_profiles = [
        profile for profile in group_vars["hermes_profiles"] if profile["name"] != "browser-protected"
    ]
    assert browser_profile["browserbase_proxies"] is True
    assert all(profile["browserbase_proxies"] is False for profile in normal_profiles)

    orchestrator = group_vars["hermes_profiles"][0]
    assert orchestrator["cli_toolsets"] == [
        "kanban",
        "skills",
        "session_search",
        "todo",
        "memory",
    ]
    assert orchestrator["required_skills"] == ["plan", "hermes-agent"]
    assert orchestrator["routing"]["default_board"] == "default"
    assert "terminal" not in orchestrator["cli_toolsets"]
    assert "file" not in orchestrator["cli_toolsets"]
    homelab = next(profile for profile in group_vars["hermes_profiles"] if profile["name"] == "homelab")
    assert homelab["routing"]["default_board"] == "homelab"
    assert homelab["review_required_for_changes"] is True
    assert "github-pr-workflow" in homelab["required_skills"]
    browser_protected = next(profile for profile in group_vars["hermes_profiles"] if profile["name"] == "browser-protected")
    assert browser_protected["routing"]["default_board"] == "automation"
    assert "newrrow-points-automation" in browser_protected["required_skills"]

    boards_task = find_task(tasks, "Configure Hermes Kanban boards")
    assert "kanban_db as kb" in boards_task["ansible.builtin.shell"]
    assert "kb.create_board" in boards_task["ansible.builtin.shell"]
    script_dir_task = find_task(tasks, "Create Hermes scripts directory")
    assert script_dir_task["ansible.builtin.file"]["path"] == "{{ hermes_home }}/scripts"
    diagnostics_script_task = find_task(tasks, "Install Hermes Kanban diagnostics script")
    assert diagnostics_script_task["ansible.builtin.template"]["src"] == "hermes-kanban-diagnostics.sh.j2"
    cron_task = find_task(tasks, "Configure Hermes Kanban diagnostics cron")
    assert "create_job" in cron_task["ansible.builtin.shell"]
    assert "no_agent=True" in cron_task["ansible.builtin.shell"]
    diagnostics_template = read("infra/ansible/roles/hermes/templates/hermes-kanban-diagnostics.sh.j2")
    assert "kanban boards list" in diagnostics_template
    assert "kanban --board" in diagnostics_template
    assert "diagnostics" in diagnostics_template

    kanban_state_task = find_task(tasks, "Allow Hermes service to manage Kanban state")
    assert kanban_state_task["ansible.builtin.file"]["path"] == "{{ hermes_home }}/kanban"
    assert kanban_state_task["ansible.builtin.file"]["recurse"] is True

    configure_index = tasks.index(find_task(tasks, "Configure Hermes runtime settings"))
    required_skill_index = tasks.index(required_skill_task)
    manage_required_skill_index = tasks.index(manage_required_skills_task)
    create_index = tasks.index(create_task)
    kanban_state_index = tasks.index(kanban_state_task)
    init_index = tasks.index(kanban_init_task)
    boards_index = tasks.index(boards_task)
    script_index = tasks.index(diagnostics_script_task)
    cron_index = tasks.index(cron_task)
    service_index = tasks.index(find_task(tasks, "Enable Hermes gateway"))
    assert required_skill_index < manage_required_skill_index < create_index < configure_index < kanban_state_index < init_index < boards_index < script_index < cron_index < service_index

def test_hermes_role_configures_browserbase_browser_automation():
    tasks = read_yaml("infra/ansible/roles/hermes/tasks/main.yml")
    group_vars = read_yaml("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")
    script = read("infra/ansible/roles/hermes/templates/hermes-configure-runtime.py.j2")

    assert group_vars["hermes_browser_cloud_provider"] == "browserbase"
    assert group_vars["hermes_browser_auto_local_for_private_urls"] is True
    assert group_vars["hermes_browser_browsers_path"] == "{{ hermes_home }}/.agent-browser/browsers"
    assert group_vars["hermes_browser_args"] == "--no-sandbox,--disable-dev-shm-usage"
    assert group_vars["hermes_browserbase_proxies"] is False
    assert group_vars["hermes_browserbase_advanced_stealth"] is False

    assert "DESIRED_BROWSER_CLOUD_PROVIDER" in script
    assert "DESIRED_BROWSER_AUTO_LOCAL_FOR_PRIVATE_URLS" in script
    assert 'browser = ensure_dict(config, "browser")' in script
    assert 'set_value(browser, "cloud_provider", cloud_provider)' in script
    assert (
        'set_value(browser, "auto_local_for_private_urls", auto_local)'
    ) in script
    assert 'platform_toolsets = ensure_dict(config, "platform_toolsets")' in script
    assert 'plugins = ensure_dict(config, "plugins")' in script
    env_template = read("infra/ansible/roles/hermes/templates/hermes-gateway.env.j2")
    assert "DISCORD_HOME_CHANNEL={{ hermes_discord_home_channel | quote }}" in env_template
    assert 'DESIRED_DISCORD_TOOLSETS = ["hermes-discord", "browser", "kanban"]' in script
    assert 'DESIRED_PLUGINS = ["newrrow-browser-login"]' in script
    assert "changed |= set_list" in script
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
    assert 'set_value(runtime_compression, "threshold", DESIRED_COMPRESSION_THRESHOLD)' in script
    assert (
        'set_value(\n        runtime_compression,\n        "codex_gpt55_autoraise",\n        DESIRED_CODEX_GPT55_AUTORAISE,\n    )'
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
    assert "DISCORD_HOME_CHANNEL={{ hermes_discord_home_channel | quote }}" in env_template
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
