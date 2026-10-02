from pathlib import Path

from jinja2 import Environment
import pytest
import yaml

from tests.helpers import REPO_ROOT


ROLE_ROOT = REPO_ROOT / "infra" / "ansible" / "roles"
RECONCILE_PLAYBOOK = (
    REPO_ROOT / "infra" / "ansible" / "playbooks" / "reconcile.yml"
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
    ("common_debian", "docker_apps_host", "tailscale_gateway"),
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


@pytest.mark.parametrize("role_name", ("docker_apps_host", "tailscale_gateway"))
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


def test_targeted_maintenance_reboots_only_the_selected_non_pve_unit():
    tasks = load_yaml(RECONCILE_PLAYBOOK)[1]["tasks"]
    marker = next(
        task for task in tasks
        if task.get("ansible.builtin.stat", {}).get("path") == "/var/run/reboot-required"
    )
    reboot = next(task for task in tasks if "ansible.builtin.reboot" in task)
    gate = [
        "homelab_unit != 'pve'",
        "homelab_maintenance_upgrade | default(false) | bool",
    ]
    assert marker["when"] == gate
    assert reboot["when"] == gate + ["homelab_unit_reboot_required.stat.exists"]

    tailnet = next(
        task for task in tasks
        if task.get("ansible.builtin.include_role", {}).get("name") == "tailscale_gateway"
    )
    assert tasks.index(tailnet) < tasks.index(marker)


def test_targeted_maintenance_ends_with_the_selected_runtime_health_contract():
    tasks = load_yaml(RECONCILE_PLAYBOOK)[1]["tasks"]
    by_name = {task["name"]: task for task in tasks}
    maintenance_gate = "homelab_maintenance_upgrade | default(false) | bool"

    compose_audit = by_name["Audit the selected Compose runtime after host maintenance"]
    assert compose_audit["ansible.builtin.command"]["argv"] == [
        "/usr/local/libexec/homelab-release",
        "audit",
        "--target",
        "apps",
    ]
    assert compose_audit["changed_when"] is False
    assert compose_audit["no_log"] is True
    assert compose_audit["when"] == [
        "homelab_unit == 'apps-host'",
        maintenance_gate,
    ]

    tailnet_status = by_name["Read tailnet health after host maintenance"]
    assert tailnet_status["ansible.builtin.command"]["argv"] == [
        "tailscale",
        "status",
        "--json",
    ]
    assert tailnet_status["changed_when"] is False
    assert tailnet_status["when"] == ["homelab_unit == 'tailnet'", maintenance_gate]
    tailnet_gate = by_name["Require a running tailnet after host maintenance"]
    assert tailnet_gate["when"] == tailnet_status["when"]
    assert tailnet_gate["ansible.builtin.assert"]["that"] == [
        "(homelab_tailnet_maintenance_status.stdout | from_json).BackendState == 'Running'"
    ]

    names = [task["name"] for task in tasks]
    assert names.index("Reboot the selected unit after targeted maintenance") < names.index(
        "Audit the selected Compose runtime after host maintenance"
    )
    assert names.index("Reboot the selected unit after targeted maintenance") < names.index(
        "Read tailnet health after host maintenance"
    ) < names.index("Require a running tailnet after host maintenance")
