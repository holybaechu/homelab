import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_yaml(relative_path: str):
    return yaml.safe_load(read(relative_path))


def caddy_site_block(content: str, hostname: str) -> str:
    match = re.search(
        rf"^{re.escape(hostname)} \{{\n(?P<body>.*?)(?=^\S|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{hostname} route not found"
    return match.group("body")


def test_caddyfile_exposes_hermes_as_private_route():
    caddyfile = read("apps/edge/Caddyfile")

    block = caddy_site_block(caddyfile, "hermes.home.hchu.me")
    assert "import private_only" in block
    assert "import secure_headers" in block
    assert "reverse_proxy 192.168.0.9:8787" in block


def test_site_playbook_applies_hermes_role():
    site = read_yaml("infra/ansible/playbooks/site.yml")
    hermes_play = next(
        (play for play in site if play.get("name") == "Configure hermes LXC"),
        None,
    )

    assert hermes_play is not None
    assert hermes_play.get("hosts") == "hermes"
    roles = hermes_play.get("roles") or []
    role_names = [
        role if isinstance(role, str) else role.get("role")
        for role in roles
        if isinstance(role, (str, dict))
    ]
    assert "hermes" in role_names


def test_validate_playbook_checks_hermes_service_health_and_caddy_route():
    validate = read_yaml("infra/ansible/playbooks/validate.yml")
    edge_play = next(
        (play for play in validate if play.get("name") == "Validate edge"),
        None,
    )
    hermes_edge_task = next(
        (
            task
            for task in (edge_play or {}).get("tasks", [])
            if task.get("name") == "Check Hermes WebUI is served through Caddy TLS route"
        ),
        None,
    )

    assert edge_play is not None
    assert hermes_edge_task is not None
    hermes_edge_shell = hermes_edge_task.get("ansible.builtin.shell") or {}
    hermes_edge_cmd = (
        hermes_edge_shell.get("cmd", "")
        if isinstance(hermes_edge_shell, dict)
        else hermes_edge_shell
    )
    assert "hermes.home.hchu.me:443:{{ edge_ip }}" in hermes_edge_cmd
    assert "https://hermes.home.hchu.me/login" in hermes_edge_cmd
    assert "Via: 1.1 Caddy" in hermes_edge_cmd

    hermes_play = next(
        (play for play in validate if play.get("name") == "Validate hermes"),
        None,
    )

    assert hermes_play is not None
    assert hermes_play.get("hosts") == "hermes"
    hermes_tasks = yaml.safe_dump(hermes_play.get("tasks", []), sort_keys=True)
    assert "systemctl is-active hermes-webui" in hermes_tasks
    assert "http://127.0.0.1:{{ hermes_webui_port }}/health" in hermes_tasks


def test_secrets_readme_documents_hermes_password():
    secrets = read("secrets/README.md")

    assert "hermes_webui_password" in secrets
    for forbidden_key in ("HERMES_API_KEY", "API_SERVER_KEY", "OPENAI_API_KEY"):
        assert forbidden_key not in secrets


def test_hermes_runbook_documents_deploy_validate_and_first_login():
    runbook = read("docs/runbooks/hermes-agent-webui.md")

    assert "https://hermes.home.hchu.me" in runbook
    assert "infra/ansible/playbooks/bootstrap.yml" in runbook
    assert "infra/ansible/playbooks/site.yml" in runbook
    assert "infra/ansible/playbooks/validate.yml" in runbook
    assert "HERMES_WEBUI_PASSWORD" in runbook
    assert "/workspace" in runbook
    assert "provider/model setup" in runbook
