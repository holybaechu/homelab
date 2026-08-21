import yaml
from jinja2 import Environment

from tests.helpers import REPO_ROOT


def render_maintenance_value(value, enabled):
    environment = Environment()
    environment.filters["bool"] = bool
    rendered = environment.from_string(str(value)).render(
        homelab_maintenance_upgrade=enabled
    )
    return yaml.safe_load(rendered)


def test_docker_engine_role_installs_engine_compose_plugin_and_live_restore():
    tasks = (REPO_ROOT / "infra" / "ansible" / "roles" / "docker_engine" / "tasks" / "main.yml").read_text(encoding="utf-8")
    reconcile = (REPO_ROOT / "infra/ansible/playbooks/reconcile.yml").read_text(encoding="utf-8")

    assert "https://download.docker.com/linux/debian" in tasks
    assert "docker-ce" in tasks
    assert "docker-compose-plugin" in tasks
    assert "docker-buildx-plugin" not in tasks
    assert "enabled: true" in tasks
    assert "content: \"{{ docker_engine_daemon_config | to_nice_json }}\\n\"" in tasks
    assert reconcile.count("live-restore: true") == 2
    assert reconcile.count("max-size: 10m") == 2


def test_docker_packages_upgrade_only_during_explicit_maintenance():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "roles"
            / "docker_engine"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")
    )
    package_tasks = [
        task
        for task in tasks
        if (apt := task.get("ansible.builtin.apt")) and "name" in apt
    ]

    assert len(package_tasks) == 2
    for task in package_tasks:
        apt = task["ansible.builtin.apt"]
        assert render_maintenance_value(apt["state"], False) == "present"
        assert render_maintenance_value(apt["update_cache"], False) is False
        assert render_maintenance_value(apt["state"], True) == "latest"
        assert render_maintenance_value(apt["update_cache"], True) is True
        assert render_maintenance_value(apt["cache_valid_time"], False) is None
        assert render_maintenance_value(apt["cache_valid_time"], True) == 3600

    engine = next(
        task
        for task in package_tasks
        if "docker-ce" in task["ansible.builtin.apt"]["name"]
    )
    assert engine["notify"] == "Restart Docker"


def test_apps_host_selects_the_debian_13_dnsutils_provider_only_where_needed():
    tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "infra"
            / "ansible"
            / "roles"
            / "docker_engine"
            / "tasks"
            / "main.yml"
        ).read_text(encoding="utf-8")
    )
    prerequisites = next(
        task for task in tasks
        if task["name"] == "Install host-specific Docker prerequisites without routine upgrades"
    )["ansible.builtin.apt"]
    assert prerequisites["name"] == "{{ docker_engine_host_packages }}"

    reconcile = yaml.safe_load(
        (REPO_ROOT / "infra/ansible/playbooks/reconcile.yml").read_text(encoding="utf-8")
    )[1]["tasks"]
    apps = next(task for task in reconcile if task["name"] == "Reconcile the Docker application host runtime")
    openclaw = next(task for task in reconcile if task["name"] == "Reconcile the OpenClaw Docker runtime")
    assert apps["vars"]["docker_engine_host_packages"] == ["bind9-dnsutils", "systemd-resolved"]
    assert openclaw["vars"]["docker_engine_host_packages"] == []
