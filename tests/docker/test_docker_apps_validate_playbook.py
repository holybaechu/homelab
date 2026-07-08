from tests.helpers import REPO_ROOT


def test_validate_playbook_checks_docker_apps_compose_projects():
    validate = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "validate.yml").read_text(encoding="utf-8")

    assert "- name: Validate Docker apps" in validate
    assert "hosts: svc_docker_apps" in validate
    assert "systemctl is-active docker" in validate
    assert "docker network inspect {{ traefik_proxy_network }}" in validate
    assert "docker compose config" in validate
    assert "docker compose ps --services" in validate
    assert "gluetun qbittorrent copyparty" in validate
    assert "paper velocity" in validate
