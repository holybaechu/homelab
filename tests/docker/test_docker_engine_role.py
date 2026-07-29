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


def test_docker_apt_packages_upgrade_and_restart_docker_on_deploy():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_engine" / "tasks" / "main.yml").read_text(encoding="utf-8")
    prerequisites = tasks.split("- name: Install Docker apt prerequisites", 1)[1].split(
        "- name: Disable systemd-resolved DNS stub for AdGuard port 53", 1
    )[0]
    engine = tasks.split("- name: Install Docker Engine and Compose plugin", 1)[1].split(
        "- name: Configure Docker daemon defaults", 1
    )[0]

    assert "state: latest" in prerequisites
    assert "update_cache: true" in prerequisites
    assert "state: latest" in engine
    assert "update_cache: true" in engine
    assert "notify: Restart Docker" in engine
