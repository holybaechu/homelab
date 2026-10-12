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


def test_application_maintenance_reads_health_without_reactivation():
    text = (REPO_ROOT / "infra/ansible/playbooks/reconcile.yml").read_text()
    assert "Read application health after host maintenance" in text
    assert "homelab-release" not in text
    assert "--force-recreate" not in text


@pytest.mark.parametrize("frames,passes", (
    (
        [
            [{"Service": "traefik", "State": "running", "Health": "starting"},
             {"Service": "cloudflare-ddns", "State": "running", "Health": ""}],
            [{"Service": "traefik", "State": "running", "Health": "healthy"},
             {"Service": "cloudflare-ddns", "State": "running", "Health": ""}],
        ],
        True,
    ),
    ([[{"Service": "traefik", "State": "running", "Health": "unhealthy"}]], False),
    ([[{"Service": "traefik", "State": "exited", "Health": ""}]], False),
    ([[]], False),
))
def test_real_maintenance_waits_for_health_and_rejects_failed_or_empty_projects(tmp_path, frames, passes):
    import os
    import shutil
    import subprocess
    import sys

    import yaml

    executable = shutil.which("ansible-playbook")
    if executable is None or os.name == "nt":
        pytest.skip("Ansible controller requires POSIX")
    tasks = load_yaml(RECONCILE)[1]["tasks"]
    read = next(task for task in tasks if task.get("register") == "homelab_apps_health")
    require = next(task for task in tasks
                   if task["name"] == "Require running healthy application containers")
    counter = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"assert sys.argv[1:] == {read['ansible.builtin.command']['argv'][1:]!r}\n"
        f"counter = Path({str(counter)!r})\n"
        "calls = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(calls + 1))\n"
        f"frames = {frames!r}\n"
        "for container in frames[min(calls, len(frames) - 1)]:\n"
        "    print(json.dumps(container))\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    read["ansible.builtin.command"]["argv"][0] = str(docker)
    if "until" in read:
        assert 0 < read["retries"] * read["delay"] <= 300
        read.update(retries=2, delay=0)
    play = [{"hosts": "localhost", "gather_facts": False,
             "vars": {"homelab_unit": "apps-host", "homelab_maintenance_upgrade": True},
             "tasks": [read, require]}]
    probe = tmp_path / "maintenance.yml"
    probe.write_text(yaml.safe_dump(play, sort_keys=False), encoding="utf-8")
    result = subprocess.run(
        [executable, "-i", "localhost,", "-c", "local", str(probe)],
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    assert int(counter.read_text()) == (2 if passes else 3)
