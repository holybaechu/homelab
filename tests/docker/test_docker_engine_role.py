"""Protect the effective host policy, rather than its YAML layout."""

import json
import os
import shutil
import subprocess

from jinja2 import Environment
import pytest
import yaml

from tests.helpers import REPO_ROOT


def test_docker_runtime_is_selected_only_for_the_two_docker_hosts():
    plays = yaml.safe_load(
        (REPO_ROOT / "infra/ansible/playbooks/reconcile.yml").read_text(encoding="utf-8")
    )
    invocations = [
        task for task in plays[1]["tasks"]
        if task.get("ansible.builtin.include_role", {}).get("name") == "docker_engine"
    ]
    environment = Environment()
    for unit in ("pve", "tailnet", "apps-host"):
        selected = [
            task for task in invocations
            if environment.from_string("{{ " + task["when"] + " }}").render(
                homelab_unit=unit
            ) == "True"
        ]
        assert len(selected) == (1 if unit in {"apps-host"} else 0)


@pytest.mark.skipif(os.name == "nt", reason="Ansible's controller CLI requires POSIX")
def test_inventory_supplies_dns_and_isolation_policy_to_the_docker_role():
    executable = shutil.which("ansible-inventory")
    if executable is None:
        pytest.skip("ansible-inventory is unavailable")
    result = subprocess.run(
        [executable, "-i", str(REPO_ROOT / "infra/ansible/inventory/prod/topology.json"), "--list"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    hosts = json.loads(result.stdout)["_meta"]["hostvars"]
    apps = hosts["docker_apps"]
    assert apps["docker_engine_disable_dns_stub"] is True
    assert {"bind9-dnsutils", "systemd-resolved"} <= set(apps["docker_engine_host_packages"])
    for host in (apps,):
        policy = host["docker_engine_daemon_config"]
        assert policy["live-restore"] is True
        assert policy["log-opts"]["max-size"] and policy["log-opts"]["max-file"]
    assert "docker_engine_daemon_config" not in hosts["tailnet"]
