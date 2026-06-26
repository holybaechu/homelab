import re

import yaml




from tests.helpers import REPO_ROOT
def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_yaml(relative_path: str):
    return yaml.safe_load(read(relative_path))


def caddy_site_block(content: str, hostname: str) -> str | None:
    match = re.search(
        rf"^{re.escape(hostname)} \{{\n(?P<body>.*?)(?=^\S|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None
    return match.group("body")


def test_caddyfile_does_not_expose_hermes_gateway_route():
    caddyfile = read("apps/edge/Caddyfile")

    assert caddy_site_block(caddyfile, "hermes.home.hchu.me") is None


def test_caddyfile_exposes_only_hmac_hermes_config_webhook_route():
    caddyfile = read("apps/edge/Caddyfile")
    block = caddy_site_block(caddyfile, "hermes-config-webhook.hchu.me")

    assert block is not None
    assert "import secure_headers" in block
    assert "reverse_proxy 192.168.0.9:8787" in block
    assert "hermes-gateway" not in block
    assert "private_only" not in block


def test_site_playbook_applies_hermes_role():
    site = read_yaml("infra/ansible/playbooks/site.yml")
    hermes_play = next(
        (play for play in site if play.get("name") == "Configure hermes LXC"),
        None,
    )

    assert hermes_play is not None
    assert hermes_play.get("hosts") == "svc_hermes"
    roles = hermes_play.get("roles") or []
    role_names = [
        role if isinstance(role, str) else role.get("role")
        for role in roles
        if isinstance(role, (str, dict))
    ]
    assert "hermes" in role_names


def test_validate_playbook_checks_hermes_gateway_service_and_browser_cli():
    validate = read_yaml("infra/ansible/playbooks/validate.yml")
    edge_play = next(
        (play for play in validate if play.get("name") == "Validate edge"),
        None,
    )
    hermes_edge_task = next(
        (
            task
            for task in (edge_play or {}).get("tasks", [])
            if "Hermes" in task.get("name", "")
        ),
        None,
    )

    assert edge_play is not None
    assert hermes_edge_task is None

    hermes_play = next(
        (play for play in validate if play.get("name") == "Validate hermes"),
        None,
    )

    assert hermes_play is not None
    assert hermes_play.get("hosts") == "svc_hermes"
    hermes_tasks = yaml.safe_dump(hermes_play.get("tasks", []), sort_keys=True)
    assert "systemctl is-active hermes-gateway" in hermes_tasks
    assert "node_modules/.bin/agent-browser" in hermes_tasks
    assert "hermes_browser_browsers_path" in hermes_tasks
    assert "chrome-" in hermes_tasks
    assert "op --version" in hermes_tasks
    assert "skills/security/1password/SKILL.md" in hermes_tasks
    assert "op whoami" in hermes_tasks
    assert "runuser -u" in hermes_tasks
    assert "OP_SERVICE_ACCOUNT_TOKEN" in hermes_tasks
    assert "Check Hermes 1Password CLI config file mode" in hermes_tasks
    assert "path.is_symlink()" in hermes_tasks
    assert "stat.S_IMODE(st.st_mode)" in hermes_tasks
    assert "0o600" in hermes_tasks
    assert "Check Hermes Newrrow points skill" in hermes_tasks
    assert "skills/newrrow-points-automation/SKILL.md" in hermes_tasks
    assert "scripts/newrrow-login.sh" in hermes_tasks
    assert "plugins/newrrow-browser-login/plugin.yaml" in hermes_tasks
    assert "Check Hermes config synchronization services" in hermes_tasks
    assert "hermes-config-watch.service" in hermes_tasks
    assert "hermes-config-webhook.service" in hermes_tasks
    assert "hermes-config-sync.timer" in hermes_tasks
    assert "readlink -f" in hermes_tasks
    assert "newrrow-browser-login plugin is not enabled" in hermes_tasks
    assert "discord platform" in hermes_tasks
    assert "browser toolset" in hermes_tasks
    assert "chromium-" not in hermes_tasks
    assert "hermes-webui" not in hermes_tasks
    assert "hermes_webui_port" not in hermes_tasks


def test_secrets_readme_documents_hermes_discord_web_browser_and_1password_secrets():
    secrets = read("secrets/README.md")

    assert "hermes_discord_bot_token" in secrets
    assert "hermes_discord_allowed_users" in secrets
    assert "hermes_parallel_api_key" in secrets
    assert "hermes_firecrawl_api_key" in secrets
    assert "hermes_browserbase_api_key" in secrets
    assert "hermes_browserbase_project_id" in secrets
    assert "hermes_1password_service_account_token" in secrets
    assert "hermes_config_repo_token" in secrets
    assert "hermes_config_webhook_secret" in secrets
    assert "PARALLEL_API_KEY" in secrets
    assert "FIRECRAWL_API_KEY" in secrets
    assert "BROWSERBASE_API_KEY" in secrets
    assert "BROWSERBASE_PROJECT_ID" in secrets
    assert "OP_SERVICE_ACCOUNT_TOKEN" in secrets
    assert "hermes_webui_password" not in secrets
    for forbidden_key in ("HERMES_API_KEY", "API_SERVER_KEY", "OPENAI_API_KEY"):
        assert forbidden_key not in secrets


def test_hermes_runbook_documents_discord_gateway_web_search_browser_automation_1password_and_fresh_lxc_rebuild():
    runbook = read("docs/runbooks/hermes-agent-discord.md")

    assert "Hermes Agent Discord gateway" in runbook
    assert "infra/ansible/playbooks/bootstrap.yml" in runbook
    assert "infra/ansible/playbooks/site.yml" in runbook
    assert "infra/ansible/playbooks/validate.yml" in runbook
    assert "HERMES_DISCORD_BOT_TOKEN" in runbook
    assert "HERMES_DISCORD_ALLOWED_USERS" in runbook
    assert "PARALLEL_API_KEY" in runbook
    assert "FIRECRAWL_API_KEY" in runbook
    assert "BROWSERBASE_API_KEY" in runbook
    assert "BROWSERBASE_PROJECT_ID" in runbook
    assert "OP_SERVICE_ACCOUNT_TOKEN" in runbook
    assert "HERMES_CONFIG_REPO_TOKEN" in runbook
    assert "HERMES_CONFIG_WEBHOOK_SECRET" in runbook
    assert "hermes-config" in runbook
    assert "restart handler" in runbook
    assert "search_backend: parallel" in runbook
    assert "extract_backend: firecrawl" in runbook
    assert "cloud_provider: browserbase" in runbook
    assert "auto_local_for_private_urls: true" in runbook
    assert "agent-browser" in runbook
    assert "local browser runtime" in runbook
    assert "/var/lib/hermes/.agent-browser/browsers" in runbook
    assert "HOME=/var/lib/hermes" in runbook
    assert "1Password" in runbook
    assert "official/security/1password" in runbook
    assert "op read" in runbook
    assert "op run" in runbook
    assert ".config/op" in runbook
    assert "0600" in runbook
    assert "newrrow-points-automation" in runbook
    assert "newrrow-browser-login" in runbook
    assert "newrrow_browser_login" in runbook
    assert "browser_navigate" in runbook
    assert "browser_*" in runbook
    assert "NEWRROW_USERNAME_REF" in runbook
    assert "NEWRROW_PASSWORD_REF" in runbook
    assert "NEWRROW_BASE_URL" not in runbook
    assert "NEWRROW_HOME_URL" not in runbook
    assert "NEWRROW_LOGIN_URL" not in runbook
    assert "op://" in runbook
    assert "Do not use bare `agent-browser open`, `agent-browser auth save`" in runbook
    assert "bare `agent-browser" in runbook
    assert "threshold: 0.85" in runbook
    assert "codex_gpt55_autoraise: false" in runbook
    assert "auxiliary:" in runbook
    assert "provider: main" in runbook
    assert "timeout: 300" in runbook
    assert "Codex auxiliary Responses stream exceeded 120.0s" in runbook
    assert "rebuild_hermes_lxc" not in runbook
    assert 'module.lxc["hermes"].proxmox_virtual_environment_container.this' not in runbook
    assert "/workspace" in runbook
    assert "provider/model setup" not in runbook
    assert "https://hermes.home.hchu.me" not in runbook
