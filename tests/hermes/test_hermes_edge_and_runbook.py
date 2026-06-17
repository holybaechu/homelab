from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_caddyfile_exposes_hermes_as_private_route():
    caddyfile = read("apps/edge/Caddyfile")

    block = caddyfile.split("hermes.home.hchu.me {", maxsplit=1)[1].split(
        "pve.home.hchu.me {", maxsplit=1
    )[0]
    assert "import private_only" in block
    assert "import secure_headers" in block
    assert "reverse_proxy 192.168.0.9:8787" in block


def test_site_playbook_applies_hermes_role():
    site = read("infra/ansible/playbooks/site.yml")

    assert "- name: Configure hermes LXC" in site
    assert "hosts: hermes" in site
    assert "    - hermes" in site


def test_validate_playbook_checks_hermes_service_health_and_caddy_route():
    validate = read("infra/ansible/playbooks/validate.yml")

    assert "- name: Validate hermes" in validate
    assert "hosts: hermes" in validate
    assert "cmd: systemctl is-active hermes-webui" in validate
    assert "http://127.0.0.1:{{ hermes_webui_port }}/health" in validate
    assert "hermes.home.hchu.me:443:{{ edge_ip }}" in validate
    assert "https://hermes.home.hchu.me/login" in validate
    assert "Via: 1.1 Caddy" in validate


def test_secrets_readme_documents_hermes_password():
    secrets = read("secrets/README.md")

    assert "hermes_webui_password" in secrets
    assert "Hermes provider" not in secrets


def test_hermes_runbook_documents_deploy_validate_and_first_login():
    runbook = read("docs/runbooks/hermes-agent-webui.md")

    assert "https://hermes.home.hchu.me" in runbook
    assert "infra/ansible/playbooks/bootstrap.yml" in runbook
    assert "infra/ansible/playbooks/site.yml" in runbook
    assert "infra/ansible/playbooks/validate.yml" in runbook
    assert "HERMES_WEBUI_PASSWORD" in runbook
    assert "/workspace" in runbook
    assert "provider/model setup" in runbook
