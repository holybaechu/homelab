from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment
import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_native"
RECONCILE = REPO_ROOT / "infra/ansible/playbooks/reconcile.yml"
COMPOSE = REPO_ROOT / "infra/openclaw/runtime/compose.yml"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _walk_tasks(tasks: list[dict[str, Any]]):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            nested = task.get(section, [])
            if isinstance(nested, list):
                yield from _walk_tasks(nested)


def _render_boolean(expression: str, **context: Any) -> bool:
    environment = Environment()
    environment.filters["from_json"] = json.loads
    environment.filters["bool"] = bool
    rendered = environment.from_string("{{ " + expression + " }}").render(**context)
    return bool(yaml.safe_load(rendered))


def _assertions_pass(clauses: list[str], **context: Any) -> bool:
    return all(_render_boolean(clause, **context) for clause in clauses)


def _command_argv(task: dict[str, Any]) -> list[str] | None:
    command = task.get("ansible.builtin.command")
    if not isinstance(command, dict):
        return None
    argv = command.get("argv")
    return argv if isinstance(argv, list) and all(isinstance(item, str) for item in argv) else None


def _condition_text(task: dict[str, Any]) -> str:
    condition = task.get("when", "")
    return " ".join(condition) if isinstance(condition, list) else str(condition)


def test_compose_uses_exact_image_descriptors_and_preserves_lxc_isolation() -> None:
    compose = load_yaml(COMPOSE)
    gateway = compose["services"]["gateway"]

    assert gateway["image"].startswith("${OPENCLAW_GATEWAY_REF:")
    assert gateway["environment"]["OPENCLAW_CTF_IMAGE"].startswith(
        "${OPENCLAW_CTF_REF:"
    )
    assert "build" not in gateway
    assert gateway["read_only"] is True
    assert gateway["network_mode"] == "host"
    assert gateway["cap_drop"] == ["ALL"]
    assert gateway["group_add"] == [
        "${OPENCLAW_DOCKER_GID:?numeric host Docker group is required}"
    ]

    socket_mount = next(
        mount
        for mount in gateway["volumes"]
        if mount.get("source") == "/var/run/docker.sock"
    )
    assert socket_mount["read_only"] is True
    writable_state = [
        mount
        for mount in gateway["volumes"]
        if mount.get("source") == "/var/lib/openclaw"
    ]
    assert writable_state
    assert all(mount.get("read_only", False) is False for mount in writable_state)
    assert all(mount["bind"]["create_host_path"] is False for mount in writable_state)
    assert "/readyz" in " ".join(gateway["healthcheck"]["test"])


def test_runtime_secret_surface_is_exactly_the_gateway_contract() -> None:
    compose = load_yaml(COMPOSE)
    gateway = compose["services"]["gateway"]
    secret_mounts = [
        mount
        for mount in gateway["volumes"]
        if mount["target"].startswith("/run/secrets/openclaw/")
    ]

    assert {Path(mount["target"]).name for mount in secret_mounts} == {
        "gateway_token",
        "discord_bot_token",
        "exa_api_key",
    }
    assert all(mount["source"].startswith("${OPENCLAW_SECRET_ROOT") for mount in secret_mounts)
    assert all(mount["read_only"] is True for mount in secret_mounts)
    assert all(mount["bind"]["create_host_path"] is False for mount in secret_mounts)


def test_ctf_image_and_private_workspace_share_one_numeric_write_identity() -> None:
    dockerfile = (REPO_ROOT / "infra/openclaw/ctf/Dockerfile").read_text(
        encoding="utf-8"
    )
    all_vars = load_yaml(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml")
    topology = load_yaml(REPO_ROOT / "infra/ansible/inventory/prod/topology.json")
    openclaw = topology["all"]["children"]["debian"]["hosts"]["openclaw"]

    assert all_vars["openclaw_ctf_uid"] == all_vars["openclaw_ctf_gid"] == 1000
    assert "USER 1000:1000" in dockerfile
    assert all(
        mount["source_mode"] == "0700"
        for mount in openclaw["lxc_mounts"].values()
    )


def test_openclaw_host_reconcile_is_explicit_and_installs_no_release_payload() -> None:
    plays = load_yaml(RECONCILE)
    role_tasks = [
        task
        for play in plays
        for task in play.get("tasks", [])
        if task.get("ansible.builtin.include_role", {}).get("name") == "openclaw_native"
    ]
    assert len(role_tasks) == 1
    role_condition = _condition_text(role_tasks[0])
    assert "homelab_unit" in role_condition and "openclaw-host" in role_condition

    tasks = list(_walk_tasks(load_yaml(ROLE / "tasks/main.yml")))
    installed_packages = {
        package
        for task in tasks
        for package in task.get("ansible.builtin.apt", {}).get("name", [])
    }
    assert {"iptables", "nftables"} <= installed_packages

    forbidden_payload_modules = {
        "ansible.builtin.git",
        "ansible.builtin.get_url",
        "ansible.builtin.unarchive",
    }
    assert all(forbidden_payload_modules.isdisjoint(task) for task in tasks)
    assert all(
        "docker compose" not in yaml.safe_dump(task).lower()
        for task in tasks
    )

    # Destructive absence/disable operations belong only to explicit bounded
    # drift repair, not recurring account/unit/file discovery loops.
    assert all("ansible.builtin.find" not in task for task in tasks)
    for task in tasks:
        for module in (
            "ansible.builtin.file",
            "ansible.builtin.user",
            "ansible.builtin.group",
        ):
            assert task.get(module, {}).get("state") != "absent"
        assert task.get("ansible.builtin.systemd_service", {}).get("enabled") is not False


def test_ctf_network_drift_reconciliation_preserves_the_isolation_contract() -> None:
    tasks = load_yaml(ROLE / "tasks/main.yml")
    probe_tasks = [task for task in tasks if task.get("register") == "openclaw_ctf_network_probe"]
    assert len(probe_tasks) == 1
    probe_argv = _command_argv(probe_tasks[0])
    assert probe_argv is not None
    assert probe_argv[:3] == ["docker", "network", "inspect"]
    assert "{{ openclaw_ctf_docker_network }}" in probe_argv
    assert "[0, 1]" in str(probe_tasks[0].get("failed_when"))

    drift_tasks = [
        task
        for task in tasks
        if "openclaw_ctf_network_drift" in task.get("ansible.builtin.set_fact", {})
    ]
    assert len(drift_tasks) == 1
    drift_expression = drift_tasks[0]["ansible.builtin.set_fact"]["openclaw_ctf_network_drift"]
    template = Environment().from_string(drift_expression)
    all_vars = load_yaml(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml")
    network_cidr = all_vars["openclaw_ctf_docker_network_cidr"]
    bridge_name = all_vars["openclaw_ctf_docker_bridge"]
    canonical = {
        "Driver": "bridge",
        "IPAM": {"Config": [{"Subnet": network_cidr}]},
        "Options": {
            "com.docker.network.bridge.name": bridge_name,
            "com.docker.network.bridge.enable_icc": "false",
        },
        "Containers": {},
    }

    def is_drift(document: dict, rc: int = 0) -> bool:
        return bool(
            yaml.safe_load(
                template.render(
                    openclaw_ctf_network_probe={"rc": rc},
                    openclaw_ctf_network_document=document,
                    openclaw_ctf_docker_network_cidr=network_cidr,
                    openclaw_ctf_docker_bridge=bridge_name,
                )
            )
        )

    assert is_drift(canonical) is False
    assert is_drift({}, rc=1) is False
    for mutation in (
        lambda value: value.update(Driver="overlay"),
        lambda value: value["IPAM"]["Config"][0].update(Subnet="172.31.0.0/24"),
        lambda value: value["Options"].update(
            {"com.docker.network.bridge.name": "wrong0"}
        ),
        lambda value: value["Options"].update(
            {"com.docker.network.bridge.enable_icc": "true"}
        ),
    ):
        candidate = yaml.safe_load(yaml.safe_dump(canonical))
        mutation(candidate)
        assert is_drift(candidate) is True

    drift_guards = [
        task
        for task in tasks
        if "ansible.builtin.assert" in task
        and "openclaw_ctf_network_drift" in _condition_text(task)
        and "openclaw_ctf_network_document" in yaml.safe_dump(task)
    ]
    assert len(drift_guards) == 1
    guard_clauses = drift_guards[0]["ansible.builtin.assert"]["that"]
    assert _assertions_pass(
        guard_clauses,
        openclaw_ctf_network_document=canonical,
    )
    occupied = yaml.safe_load(yaml.safe_dump(canonical))
    occupied["Containers"] = {"container-id": {}}
    assert not _assertions_pass(
        guard_clauses,
        openclaw_ctf_network_document=occupied,
    )

    drift_commands = [
        (task, argv)
        for task in tasks
        if "openclaw_ctf_network_drift" in _condition_text(task)
        if (argv := _command_argv(task)) is not None
    ]
    remove_commands = [item for item in drift_commands if "rm" in item[1]]
    assert len(remove_commands) == 1
    assert "{{ openclaw_ctf_docker_network }}" in remove_commands[0][1]

    create_tasks = [
        task
        for task in tasks
        if (argv := _command_argv(task)) is not None
        and argv[:3] == ["docker", "network", "create"]
    ]
    assert len(create_tasks) == 1
    create_argv = _command_argv(create_tasks[0])
    assert create_argv is not None
    assert create_argv[create_argv.index("--driver") + 1] == "bridge"
    assert create_argv[create_argv.index("--subnet") + 1] == "{{ openclaw_ctf_docker_network_cidr }}"
    create_options = {
        create_argv[index + 1]
        for index, value in enumerate(create_argv[:-1])
        if value == "--opt"
    }
    assert {
        "com.docker.network.bridge.name={{ openclaw_ctf_docker_bridge }}",
        "com.docker.network.bridge.enable_icc=false",
    } <= create_options
    create_condition = _condition_text(create_tasks[0])
    assert _render_boolean(
        create_condition,
        openclaw_ctf_network_probe={"rc": 1},
        openclaw_ctf_network_drift=False,
    )
    assert _render_boolean(
        create_condition,
        openclaw_ctf_network_probe={"rc": 0},
        openclaw_ctf_network_drift=True,
    )
    assert not _render_boolean(
        create_condition,
        openclaw_ctf_network_probe={"rc": 0},
        openclaw_ctf_network_drift=False,
    )

    verification_tasks = [
        task
        for task in tasks
        if "ansible.builtin.assert" in task
        and "openclaw_ctf_network_verified" in yaml.safe_dump(task)
    ]
    assert len(verification_tasks) == 1
    verification_clauses = verification_tasks[0]["ansible.builtin.assert"]["that"]

    def verified(document: Any) -> bool:
        return _assertions_pass(
            verification_clauses,
            openclaw_ctf_network_verified={"stdout": json.dumps([document])},
            openclaw_ctf_docker_network_cidr=network_cidr,
            openclaw_ctf_docker_bridge=bridge_name,
        )

    assert verified(canonical)
    assert not verified({"Driver": "overlay"})
    assert not verified({**canonical, "IPAM": {"Config": []}})
