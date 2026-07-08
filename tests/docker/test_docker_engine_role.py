from tests.helpers import REPO_ROOT


def test_docker_engine_role_installs_engine_compose_plugin_and_live_restore():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_engine" / "tasks" / "main.yml").read_text(encoding="utf-8")
    daemon = (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_engine" / "templates" / "daemon.json.j2").read_text(encoding="utf-8")

    assert "https://download.docker.com/linux/debian" in tasks
    assert "docker-ce" in tasks
    assert "docker-compose-plugin" in tasks
    assert "docker-buildx-plugin" in tasks
    assert "enabled: true" in tasks
    assert '\"live-restore\": true' in daemon
    assert '\"max-size\": \"10m\"' in daemon
