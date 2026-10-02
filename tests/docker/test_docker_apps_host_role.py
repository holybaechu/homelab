"""Protect the application host's data, DNS, and deployment prerequisites."""

import json
from pathlib import PurePosixPath

from jinja2 import Environment
import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/docker_apps_host"


def host_tasks():
    return yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))


def test_apps_host_preparation_runs_only_for_the_application_unit():
    plays = yaml.safe_load(
        (REPO_ROOT / "infra/ansible/playbooks/reconcile.yml").read_text(encoding="utf-8")
    )
    invocations = [
        task for task in plays[1]["tasks"]
        if task.get("ansible.builtin.include_role", {}).get("name") == "docker_apps_host"
    ]
    assert invocations
    environment = Environment()
    for unit in ("pve", "tailnet", "apps-host"):
        selected = [
            task for task in invocations
            if environment.from_string("{{ " + task["when"] + " }}").render(
                homelab_unit=unit
            ) == "True"
        ]
        assert len(selected) == (1 if unit == "apps-host" else 0)


def test_durable_directories_are_guarded_by_the_pve_mount():
    tasks = host_tasks()
    mount_guard = next(
        index for index, task in enumerate(tasks)
        if task.get("ansible.builtin.command") == "mountpoint -q /srv/homelab"
    )
    directory_tasks = [
        (index, task) for index, task in enumerate(tasks)
        if any(item.get("path", "").startswith("/srv/homelab/") for item in task.get("loop", []))
    ]
    assert directory_tasks
    created = set()
    for index, task in directory_tasks:
        assert mount_guard < index
        assert task["ansible.builtin.file"]["state"] == "directory"
        created.update(PurePosixPath(item["path"]) for item in task["loop"])

    model = yaml.safe_load(
        (REPO_ROOT / "apps/compose/homelab/compose.yml").read_text(encoding="utf-8")
    )
    mounted = {
        PurePosixPath(volume.split(":", 1)[0])
        for service in model["services"].values()
        for volume in service.get("volumes", [])
        if volume.startswith("/srv/homelab/")
    }
    for source in mounted:
        assert any(source == path or source in path.parents or path in source.parents for path in created), source

    private = next(
        task["ansible.builtin.file"] for task in tasks
        if task.get("ansible.builtin.file", {}).get("path") == "/etc/homelab/secrets"
    )
    assert private["owner"] == private["group"] == "root"
    assert private["mode"] == "0700" and private["follow"] is False


def test_host_preparation_preserves_dns_and_release_prerequisites():
    tasks = host_tasks()
    copies = {
        task["ansible.builtin.copy"]["dest"]: task["ansible.builtin.copy"]
        for task in tasks if "ansible.builtin.copy" in task
    }
    resolver = copies["/etc/systemd/resolved.conf.d/adguardhome.conf"]["content"]
    assert "DNSStubListener=no" in resolver
    assert any(
        task.get("ansible.builtin.file", {}).get("src") == "/run/systemd/resolve/resolv.conf"
        and task["ansible.builtin.file"].get("dest") == "/etc/resolv.conf"
        for task in tasks
    )
    policy = json.loads(copies["/etc/docker/daemon.json"]["content"])
    assert policy["live-restore"] is True
    assert policy["log-driver"] == "json-file"
    assert policy["log-opts"]["max-size"] and policy["log-opts"]["max-file"]

    launcher = copies["/usr/local/libexec/homelab-release"]
    assert launcher["mode"] == "0755"
    assert launcher["src"].endswith("/scripts/ci/release_launcher.py")
    certificate = next(task for task in tasks if "ansible.builtin.slurp" in task)
    assert certificate["delegate_to"] == "pve"
    assert certificate["ansible.builtin.slurp"]["src"] == "/etc/pve/pve-root-ca.pem"
    assert "/etc/ssl/certs/homelab-pve-root-ca.pem" in copies
