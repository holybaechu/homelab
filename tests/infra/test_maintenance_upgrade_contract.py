import json

import pytest

from tests.helpers import (
    REPO_ROOT, load_yaml, render_ansible, task_enabled, task_with_module,
)


ROLE_ROOT = REPO_ROOT / "infra/ansible/roles"
RECONCILE = REPO_ROOT / "infra/ansible/playbooks/reconcile.yml"


@pytest.mark.parametrize("role_name", ("common_debian", "docker_apps_host", "tailscale_gateway"))
def test_packages_upgrade_only_during_maintenance(role_name):
    packages = [task["ansible.builtin.apt"]
                for task in load_yaml(ROLE_ROOT / role_name / "tasks/main.yml")
                if "name" in task.get("ansible.builtin.apt", {})]
    assert packages
    for package in packages:
        for variables, state, refresh in (
            ({}, "present", False),
            ({"homelab_maintenance_upgrade": False}, "present", False),
            ({"homelab_maintenance_upgrade": True}, "latest", True),
        ):
            assert render_ansible(package["state"], **variables) == state
            assert render_ansible(package["update_cache"], **variables) is refresh


@pytest.mark.parametrize("role_name,variable", (
    ("docker_apps_host", "docker_apt_repository"),
    ("tailscale_gateway", "tailscale_apt_repository"),
))
def test_repository_metadata_refresh_runs_only_after_repository_changes(role_name, variable):
    tasks = load_yaml(ROLE_ROOT / role_name / "tasks/main.yml")
    refresh = task_with_module([task for task in tasks
                                if "name" not in task.get("ansible.builtin.apt", {})],
                               "ansible.builtin.apt", update_cache=True)
    repository = next(task for task in tasks if task.get("register") == variable)
    assert task_enabled(refresh, **{variable: {"changed": True}})
    assert not task_enabled(refresh, **{variable: {"changed": False}})
    assert tasks.index(repository) < tasks.index(refresh)


def test_maintenance_reboots_only_a_selected_guest_that_needs_it():
    tasks = load_yaml(RECONCILE)[1]["tasks"]
    marker = task_with_module(tasks, "ansible.builtin.stat", path="/var/run/reboot-required")
    reboot = task_with_module(tasks, "ansible.builtin.reboot")
    for unit, maintenance, required, inspect, restart in (
        ("pve", True, True, False, False),
        ("tailnet", False, True, False, False),
        ("apps-host", False, True, False, False),
        ("tailnet", True, False, True, False),
        ("tailnet", True, True, True, True),
        ("apps-host", True, True, True, True),
    ):
        variables = dict(homelab_unit=unit, homelab_maintenance_upgrade=maintenance,
                         homelab_unit_reboot_required={"stat": {"exists": required}})
        assert task_enabled(marker, **variables) is inspect
        assert task_enabled(reboot, **variables) is restart
    tailnet = task_with_module(tasks, "ansible.builtin.include_role", name="tailscale_gateway")
    assert tasks.index(tailnet) < tasks.index(marker) < tasks.index(reboot)


def test_maintenance_checks_the_selected_runtime_after_reboot():
    tasks = load_yaml(RECONCILE)[1]["tasks"]
    reboot = task_with_module(tasks, "ansible.builtin.reboot")
    audit = task_with_module(tasks, "ansible.builtin.command",
                             argv=["/usr/local/libexec/homelab-release", "audit", "--target", "apps"])
    status = task_with_module(tasks, "ansible.builtin.command", argv=["tailscale", "status", "--json"])
    health = task_with_module(tasks, "ansible.builtin.assert")
    assert audit["no_log"] is True
    for task, selected in ((audit, "apps-host"), (status, "tailnet"), (health, "tailnet")):
        assert tasks.index(reboot) < tasks.index(task)
        for unit, maintenance, expected in (
            (selected, True, True), (selected, False, False), ("pve", True, False),
            ("tailnet" if selected == "apps-host" else "apps-host", True, False),
        ):
            assert task_enabled(task, homelab_unit=unit, homelab_maintenance_upgrade=maintenance) is expected
    assert tasks.index(status) < tasks.index(health)
    for backend, healthy in (("Running", True), ("Stopped", False)):
        variables = {status["register"]: {"stdout": json.dumps({"BackendState": backend})}}
        assert all(render_ansible("{{ " + clause + " }}", **variables)
                   for clause in health["ansible.builtin.assert"]["that"]) is healthy
