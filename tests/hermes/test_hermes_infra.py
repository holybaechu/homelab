import re

import yaml




from tests.helpers import REPO_ROOT
def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def tfvars_container_body(tfvars_text: str, name: str) -> str:
    match = re.search(
        rf"^  {name} = \{{(?P<body>.*?)^  \}}",
        tfvars_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name} container block not found"
    return match.group("body")


def numeric_value(container_body: str, key: str) -> int:
    match = re.search(rf"^\s+{key}\s+=\s+(\d+)$", container_body, re.MULTILINE)
    assert match is not None, f"{key} not found"
    return int(match.group(1))


def test_hermes_lxc_is_declared_in_tracked_topology_tfvars():
    tfvars = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    body = tfvars_container_body(tfvars, "hermes")

    assert 'hostname         = "hermes"' in body
    assert 'description      = "Hermes Agent Discord gateway managed by OpenTofu and Ansible"' in body
    assert 'tags             = ["homelab", "managed-by-opentofu", "role-hermes"]' in body
    assert 'template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"' in body
    assert 'os_type          = "debian"' in body
    assert 'ip_address       = "192.168.0.9/24"' in body
    assert 'mac_address      = "02:00:00:BA:EC:09"' in body
    assert numeric_value(body, "vmid") == 116
    assert numeric_value(body, "root_disk_gb") == 16
    assert numeric_value(body, "cores") == 2
    assert numeric_value(body, "memory_mb") == 2048
    assert numeric_value(body, "swap_mb") == 1024
    assert numeric_value(body, "startup_order") == 7


def test_hermes_inventory_is_debian_host_and_role_group():
    hosts = read("infra/ansible/inventory/prod/hosts.yml")

    assert re.search(
        r"debian:\s*\n\s+hosts:.*hermes:\s*\n\s+ansible_host: 192\.168\.0\.9",
        hosts,
        re.DOTALL,
    )
    assert re.search(r"svc_hermes:\s*\n\s+hosts:\s*\n\s+hermes:", hosts)


def test_hermes_all_group_vars_define_ip_ids_bootstrap_and_mounts():
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")

    assert re.search(r"^hermes_ip: \"\{\{ hostvars\['hermes'\]\.ansible_host \}\}\"$", all_vars, re.MULTILINE)
    assert re.search(r"^hermes_service_uid: 1200$", all_vars, re.MULTILINE)
    assert re.search(r"^hermes_service_gid: 1200$", all_vars, re.MULTILINE)
    assert "  - vmid: 116\n    name: hermes\n    os_family: debian" in all_vars
    assert "  - vmid: 116\n    name: hermes" in all_vars
    assert "bind_mount_sources:" in all_vars
    assert "      - /var/lib/homelab/hermes/home" in all_vars
    assert "      - /var/lib/homelab/hermes/workspace" in all_vars
    assert "mp=/var/lib/hermes" in all_vars
    assert "mp=/workspace" in all_vars


def test_hermes_group_vars_are_non_secret_service_settings():
    group_vars = read("infra/ansible/inventory/prod/group_vars/svc_hermes.yml")

    assert "hermes_user: hermes" in group_vars
    assert "hermes_group: hermes" in group_vars
    assert "hermes_home: /var/lib/hermes" in group_vars
    assert "hermes_workspace: /workspace" in group_vars
    assert "https://github.com/NousResearch/hermes-agent.git" in group_vars
    assert "hermes_webui" not in group_vars
    assert "hermes_webui_password:" not in group_vars
    assert "hermes_discord_bot_token:" not in group_vars
    assert "hermes_discord_allowed_users:" not in group_vars
    assert "hermes_browserbase_api_key:" not in group_vars
    assert "hermes_browserbase_project_id:" not in group_vars
    assert "hermes_honcho_api_key:" not in group_vars
    assert "hermes_memory_provider: \"honcho\"" in group_vars
    assert "hermes_honcho_environment: production" in group_vars
    assert "hermes_config_repo: https://github.com/holybaechu/hermes-config.git" in group_vars
    assert "hermes_config_repo_token:" not in group_vars
    assert "hermes_config_webhook_secret:" not in group_vars
    assert "hermes_discord_require_mention: true" in group_vars
    assert "hermes_discord_ignore_no_mention: true" in group_vars
    assert "API_SERVER_KEY" not in group_vars


def test_hermes_upstream_refs_are_pinned_commit_hashes():
    group_vars = yaml.safe_load(read("infra/ansible/inventory/prod/group_vars/svc_hermes.yml"))

    for key in ("hermes_agent_ref",):
        value = group_vars[key]
        assert re.fullmatch(r"[0-9a-f]{40}", value), f"{key} must be pinned to a commit"
        assert value not in {"main", "master"}


def test_proxmox_storage_role_creates_hermes_host_directories():
    tasks = read("infra/ansible/roles/pve_homelab_storage/tasks/main.yml")

    assert '"${mount_path}/hermes/home"' in tasks
    assert '"${mount_path}/hermes/workspace"' in tasks
    assert "homelab_container_uid_offset + hermes_service_uid" in tasks
    assert "homelab_container_uid_offset + hermes_service_gid" in tasks
    assert '"${mount_path}/hermes"' in tasks


def test_cd_workflow_passes_hermes_discord_web_browser_and_1password_secrets_to_ansible_extra_vars():
    workflow = read(".github/workflows/cd.yml")
    writer = read("scripts/ci/write_ansible_extra_vars.py")

    assert "HERMES_DISCORD_BOT_TOKEN:" in workflow
    assert "HERMES_DISCORD_ALLOWED_USERS:" in workflow
    assert "HERMES_DISCORD_HOME_CHANNEL:" in workflow
    assert "hermes_discord_bot_token" in writer
    assert "HERMES_DISCORD_BOT_TOKEN" in writer
    assert "hermes_discord_allowed_users" in writer
    assert "HERMES_DISCORD_ALLOWED_USERS" in writer
    assert "hermes_discord_home_channel" in writer
    assert "HERMES_DISCORD_HOME_CHANNEL" in writer

    assert "PARALLEL_API_KEY:" in workflow
    assert "FIRECRAWL_API_KEY:" in workflow
    assert "hermes_parallel_api_key" in writer
    assert "PARALLEL_API_KEY" in writer
    assert "hermes_firecrawl_api_key" in writer
    assert "FIRECRAWL_API_KEY" in writer

    assert "BROWSERBASE_API_KEY:" in workflow
    assert "BROWSERBASE_PROJECT_ID:" in workflow
    assert "hermes_browserbase_api_key" in writer
    assert "BROWSERBASE_API_KEY" in writer
    assert "hermes_browserbase_project_id" in writer
    assert "BROWSERBASE_PROJECT_ID" in writer

    assert "HONCHO_API_KEY:" in workflow
    assert "hermes_honcho_api_key" in writer
    assert "HONCHO_API_KEY" in writer

    assert "OP_SERVICE_ACCOUNT_TOKEN:" in workflow
    assert "hermes_1password_service_account_token" in writer
    assert "OP_SERVICE_ACCOUNT_TOKEN" in writer

    assert "HERMES_CONFIG_REPO_TOKEN:" in workflow
    assert "HERMES_CONFIG_WEBHOOK_SECRET:" in workflow
    assert "hermes_config_repo_token" in writer
    assert "hermes_config_webhook_secret" in writer
    assert "HERMES_CONFIG_REPO_TOKEN" in writer
    assert "HERMES_CONFIG_WEBHOOK_SECRET" in writer

    assert "HERMES_WEBUI_PASSWORD:" not in workflow
    assert "HERMES_API_KEY" not in workflow
    assert "API_SERVER_KEY" not in workflow


def test_cd_workflow_does_not_offer_destructive_hermes_lxc_rebuild():
    workflow = read(".github/workflows/cd.yml")
    plan_script = read("scripts/ci/tofu-plan.sh")

    assert "rebuild_hermes_lxc" not in workflow
    assert "REBUILD_HERMES_LXC" not in workflow
    assert 'module.lxc["hermes"].proxmox_virtual_environment_container.this' not in plan_script
    assert "-replace=" not in plan_script
