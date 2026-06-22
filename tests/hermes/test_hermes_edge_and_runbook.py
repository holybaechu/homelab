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


def test_validate_playbook_checks_hermes_gateway_service_only():
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
    assert "hermes-webui" not in hermes_tasks
    assert "hermes_webui_port" not in hermes_tasks


def test_secrets_readme_documents_hermes_discord_secrets():
    secrets = read("secrets/README.md")

    assert "hermes_discord_bot_token" in secrets
    assert "hermes_discord_allowed_users" in secrets
    assert "hermes_webui_password" not in secrets
    for forbidden_key in ("HERMES_API_KEY", "API_SERVER_KEY", "OPENAI_API_KEY"):
        assert forbidden_key not in secrets


def test_hermes_runbook_documents_discord_gateway_and_fresh_lxc_rebuild():
    runbook = read("docs/runbooks/hermes-agent-discord.md")

    assert "Hermes Agent Discord gateway" in runbook
    assert "infra/ansible/playbooks/bootstrap.yml" in runbook
    assert "infra/ansible/playbooks/site.yml" in runbook
    assert "infra/ansible/playbooks/validate.yml" in runbook
    assert "HERMES_DISCORD_BOT_TOKEN" in runbook
    assert "HERMES_DISCORD_ALLOWED_USERS" in runbook
    assert "rebuild_hermes_lxc" not in runbook
    assert 'module.lxc["hermes"].proxmox_virtual_environment_container.this' not in runbook
    assert "/workspace" in runbook
    assert "provider/model setup" in runbook
    assert "https://hermes.home.hchu.me" not in runbook
