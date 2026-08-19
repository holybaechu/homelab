from pathlib import Path

from jinja2 import Environment
import pytest
import yaml

from tests.helpers import REPO_ROOT


ROLE_ROOT = REPO_ROOT / "infra" / "ansible" / "roles"
MAINTENANCE_PLAYBOOK = (
    REPO_ROOT / "infra" / "ansible" / "playbooks" / "maintenance.yml"
)


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def render_maintenance_value(value, enabled=None):
    environment = Environment()
    environment.filters["bool"] = bool
    context = (
        {}
        if enabled is None
        else {"homelab_maintenance_upgrade": enabled}
    )
    rendered = environment.from_string(str(value)).render(**context)
    return yaml.safe_load(rendered)


@pytest.mark.parametrize(
    "role_name",
    ("common_debian", "docker_engine", "tailscale_gateway"),
)
def test_package_reconciliation_is_stable_until_maintenance(role_name):
    tasks = load_yaml(ROLE_ROOT / role_name / "tasks" / "main.yml")
    package_modules = [
        task["ansible.builtin.apt"]
        for task in tasks
        if "ansible.builtin.apt" in task
        and "name" in task["ansible.builtin.apt"]
    ]

    assert package_modules
    for package in package_modules:
        for routine_value in (None, False):
            assert (
                render_maintenance_value(package["state"], routine_value)
                == "present"
            )
            assert (
                render_maintenance_value(
                    package["update_cache"], routine_value
                )
                is False
            )
            assert (
                render_maintenance_value(
                    package["cache_valid_time"], routine_value
                )
                is None
            )
        assert render_maintenance_value(package["state"], True) == "latest"
        assert render_maintenance_value(package["update_cache"], True) is True
        assert render_maintenance_value(package["cache_valid_time"], True) == 3600


@pytest.mark.parametrize("role_name", ("docker_engine", "tailscale_gateway"))
def test_new_third_party_repository_refresh_does_not_enable_routine_upgrades(
    role_name,
):
    tasks = load_yaml(ROLE_ROOT / role_name / "tasks" / "main.yml")
    cache_only_tasks = [
        task
        for task in tasks
        if (apt := task.get("ansible.builtin.apt"))
        and "name" not in apt
        and apt.get("update_cache") is True
    ]

    assert len(cache_only_tasks) == 1
    condition = str(cache_only_tasks[0]["when"])
    assert condition.endswith("_apt_repository.changed")


def test_maintenance_playbook_enables_upgrades_on_the_three_existing_lxcs():
    plays = load_yaml(MAINTENANCE_PLAYBOOK)
    by_hosts = {play["hosts"]: play for play in plays}

    assert tuple(by_hosts) == (
        "svc_docker_apps",
        "svc_openclaw",
        "svc_tailnet",
    )
    expected_roles = {
        "svc_docker_apps": ["common_debian", "docker_engine"],
        "svc_openclaw": ["common_debian"],
        "svc_tailnet": ["common_debian", "tailscale_gateway"],
    }
    for hosts, roles in expected_roles.items():
        play = by_hosts[hosts]
        assert play["vars"]["homelab_maintenance_upgrade"] is True
        assert play["roles"] == roles


def test_maintenance_reboots_are_marker_gated_and_tailnet_recovers_when_needed():
    plays = load_yaml(MAINTENANCE_PLAYBOOK)

    for play in plays:
        post_tasks = play["post_tasks"]
        marker_checks = [
            task
            for task in post_tasks
            if task.get("ansible.builtin.stat", {}).get("path")
            == "/var/run/reboot-required"
        ]
        reboots = [
            task for task in post_tasks if "ansible.builtin.reboot" in task
        ]
        assert len(marker_checks) == 1
        assert len(reboots) == 1
        marker_fact = marker_checks[0]["register"]
        assert reboots[0]["when"] == f"{marker_fact}.stat.exists"

    assert plays[-1]["hosts"] == "svc_tailnet"
    tailnet_tasks = plays[-1]["post_tasks"]
    reconnects = [
        task
        for task in tailnet_tasks
        if "ansible.builtin.wait_for_connection" in task
    ]
    assert len(reconnects) == 1
    assert reconnects[0]["when"] == (
        "tailscale_restart_scheduled | default(false)"
    )

    scheduled_checks = [
        task
        for task in tailnet_tasks
        if task.get("when") == "tailscale_restart_scheduled | default(false)"
    ]
    assert len(scheduled_checks) >= 4
    assert any(
        "/run/homelab-tailscale-restart.completed"
        in task.get("ansible.builtin.shell", "")
        for task in scheduled_checks
    )

    completion = next(
        task
        for task in scheduled_checks
        if "/run/homelab-tailscale-restart.completed"
        in task.get("ansible.builtin.shell", "")
    )
    assert completion["retries"] == 36
    assert completion["delay"] == 5
    assert completion["until"].endswith(".rc == 0")

    service_result = next(
        task
        for task in scheduled_checks
        if "tailscaled-ansible-restart.service"
        in task.get("ansible.builtin.command", {}).get("argv", [])
    )
    assert service_result["changed_when"] is False
    assert service_result["failed_when"].endswith('.stdout != "success"')

    installed_binary = next(
        task
        for task in scheduled_checks
        if "/proc/${pid}/exe" in task.get("ansible.builtin.shell", "")
    )
    assert installed_binary["changed_when"] is False

    reboot_marker = next(
        task
        for task in tailnet_tasks
        if task.get("ansible.builtin.stat", {}).get("path")
        == "/var/run/reboot-required"
    )
    assert all(
        tailnet_tasks.index(task) < tailnet_tasks.index(reboot_marker)
        for task in (reconnects[0], completion, service_result, installed_binary)
    )


def test_normal_reconciliation_playbooks_do_not_enable_maintenance_mode():
    playbook_root = REPO_ROOT / "infra" / "ansible" / "playbooks"

    for playbook_name in ("site.yml", "bootstrap.yml"):
        playbook = (playbook_root / playbook_name).read_text(encoding="utf-8")
        assert "homelab_maintenance_upgrade" not in playbook
