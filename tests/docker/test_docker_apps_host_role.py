"""Protect the application host's data, DNS, and deployment prerequisites."""

import json
from pathlib import PurePosixPath

import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/docker_apps_host"


def host_tasks():
    return yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))


def test_durable_directories_are_guarded_by_the_pve_mount():
    tasks = host_tasks()
    mount_guard = next(
        index for index, task in enumerate(tasks)
        if task.get("ansible.builtin.command") == "mountpoint -q /srv/homelab"
    )
    directory_tasks = [
        (index, task) for index, task in enumerate(tasks)
        if any(
            (item if isinstance(item, str) else item.get("path", "")).startswith("/srv/homelab/")
            for item in task.get("loop", [])
        )
    ]
    assert directory_tasks
    assert any(task["ansible.builtin.file"].get("recurse") for _, task in directory_tasks)
    created = set()
    for index, task in directory_tasks:
        assert mount_guard < index
        assert task["ansible.builtin.file"]["state"] == "directory"
        created.update(
            PurePosixPath(item if isinstance(item, str) else item["path"])
            for item in task["loop"]
        )
        if task["ansible.builtin.file"].get("recurse"):
            assert "mode" not in task["ansible.builtin.file"]
            assert task["ansible.builtin.file"]["follow"] is False

    topology = json.loads(
        (REPO_ROOT / "infra/ansible/inventory/prod/topology.json").read_text(encoding="utf-8")
    )
    mount = topology["all"]["children"]["debian"]["hosts"]["docker_apps"]["lxc_mounts"]["mp0"]
    assert mount["source_owner"] == mount["source_group"] == 100000

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
        assert source in created, source

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

    certificate = next(task for task in tasks if "ansible.builtin.slurp" in task)
    assert certificate["delegate_to"] == "pve"
    assert certificate["ansible.builtin.slurp"]["src"] == "/etc/pve/pve-root-ca.pem"
    assert "/etc/ssl/certs/homelab-pve-root-ca.pem" in copies
