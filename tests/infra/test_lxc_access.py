from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment
import yaml

from tests.helpers import REPO_ROOT


ROLE_TASKS = REPO_ROOT / "infra/ansible/roles/pve_lxc_access/tasks/main.yml"
RECONCILE = REPO_ROOT / "infra/ansible/playbooks/reconcile.yml"
APPLY_GATE = [
    "homelab_unit == 'pve'",
    "pve_lxc_reconcile_mode | default('apply') == 'apply'",
]


def load_reconcile() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validation, selected = yaml.safe_load(RECONCILE.read_text(encoding="utf-8"))
    return validation["tasks"], selected


def task_with_module(tasks: list[dict[str, Any]], module: str) -> dict[str, Any]:
    matches = [task for task in tasks if module in task]
    assert len(matches) == 1, module
    return matches[0]


def test_one_explicit_unit_selects_one_inventory_boundary() -> None:
    validation, selected = load_reconcile()
    assertion = task_with_module(validation, "ansible.builtin.assert")
    clauses = assertion["ansible.builtin.assert"]["that"]

    assert "homelab_unit is defined" in clauses
    assert "homelab_unit in ['pve', 'tailnet', 'apps-host', 'openclaw-host']" in clauses
    assert selected["gather_facts"] == "{{ homelab_unit != 'pve' }}"

    included = [
        (task["ansible.builtin.include_role"]["name"], task["when"])
        for task in selected["tasks"]
        if "ansible.builtin.include_role" in task
    ]
    assert ("pve_lxc_access", APPLY_GATE) in included
    assert ("common_debian", "homelab_unit != 'pve'") in included
    assert ("release_launcher", "homelab_unit in ['apps-host', 'openclaw-host']") in included


def test_pve_access_reconciles_every_declared_lxc_idempotently() -> None:
    tasks = yaml.safe_load(ROLE_TASKS.read_text(encoding="utf-8"))
    shell = task_with_module(tasks, "ansible.builtin.shell")

    assert shell["loop"] == "{{ groups['debian'] }}"
    assert shell["changed_when"] == "'changed=yes' in pve_lxc_access_results.stdout"
    program = shell["ansible.builtin.shell"]
    assert "hostvars[item].vmid" in program
    assert "hostvars[item].os_type" in program
    assert "HOMELAB_AUTHORIZED_KEYS_B64" in program
    assert "/root/.ssh/authorized_keys" in program
    assert "openssh-server python3" in program


def test_component_secret_contracts_are_exact_and_never_logged() -> None:
    _, selected = load_reconcile()
    guarded = [
        task
        for task in selected["pre_tasks"]
        if task.get("no_log") is True
    ]
    assert guarded
    assert all(
        task.get("when")
        for task in guarded
    )

    assertions = [
        clause
        for task in guarded
        for clause in task.get("ansible.builtin.assert", {}).get("that", [])
    ]
    flattened = " ".join(str(clause).replace("\n", " ") for clause in assertions)
    assert "['component', 'values', 'version']" in flattened
    assert "['deploy_ssh_public_keys']" in flattened
    assert "['tailscale_auth_key']" in flattened


def test_bundle_version_gate_rejects_json_boole_even_though_bool_is_an_int() -> None:
    _, selected = load_reconcile()
    clauses = [
        clause
        for task in selected["pre_tasks"]
        for clause in task.get("ansible.builtin.assert", {}).get("that", [])
    ]
    expression = next(str(clause) for clause in clauses if "type_debug" in str(clause))
    environment = Environment()
    environment.filters["type_debug"] = lambda value: type(value).__name__
    gate = environment.from_string("{{ " + expression + " }}")

    assert gate.render(homelab_loaded_secret_bundle={"version": 1}) == "True"
    assert gate.render(homelab_loaded_secret_bundle={"version": True}) == "False"


def test_component_key_grammars_reject_options_comments_and_multiline_values() -> None:
    _, selected = load_reconcile()
    clauses = [
        str(clause)
        for task in selected["pre_tasks"]
        for clause in task.get("ansible.builtin.assert", {}).get("that", [])
    ]

    pve_clause = next(clause for clause in clauses if "ssh-ed25519" in clause)
    tailnet_clause = next(clause for clause in clauses if "tskey-auth-" in clause)

    def embedded_pattern(clause: str) -> re.Pattern[str]:
        start = clause.index("'^.") + 1 if "'^." in clause else clause.index("'^") + 1
        end = clause.index("'", start)
        return re.compile(clause[start:end])

    pve = embedded_pattern(pve_clause)
    tailnet = embedded_pattern(tailnet_clause)
    valid_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEhvbWVsYWJrZXk="
    assert pve.fullmatch(valid_key)
    assert pve.fullmatch("ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY=")
    assert pve.fullmatch("rsa-sha2-512 AAAAB3NzaC1yc2EAAAADAQABAAABAQ==")
    for invalid in (
        "from=192.0.2.1 " + valid_key,
        valid_key + " operator@example",
        valid_key + "\nssh-ed25519 AAAA",
        valid_key.replace(" ", "  ", 1),
        "ssh-rsa AAAA",
    ):
        assert pve.fullmatch(invalid) is None

    assert tailnet.fullmatch("tskey-auth-k12345-example")
    for invalid in (
        "tskey-api-k12345-example",
        "tskey-auth-k12345-example comment",
        "tskey-auth-k12345-example\nsecond-line",
        " tskey-auth-k12345-example",
    ):
        assert tailnet.fullmatch(invalid) is None


def test_pve_host_key_handoff_is_derived_from_pct_results_and_apply_only() -> None:
    _, selected = load_reconcile()
    tasks = selected["tasks"]
    command = next(
        task
        for task in tasks
        if task["name"] == "Read managed LXC SSH host keys through pct"
    )
    known_hosts = task_with_module(tasks, "ansible.builtin.known_hosts")

    assert command["when"] == APPLY_GATE
    assert command["loop"] == "{{ groups['debian'] }}"
    argv = command["ansible.builtin.command"]["argv"]
    assert argv[:2] == ["pct", "exec"]
    assert "{{ hostvars[item].vmid }}" in argv
    assert argv[-1] == "/etc/ssh/ssh_host_ed25519_key.pub"

    assert known_hosts["when"] == APPLY_GATE
    assert known_hosts["delegate_to"] == "localhost"
    assert known_hosts["loop"] == "{{ pve_lxc_host_key_results.results | default([]) }}"
    contract = known_hosts["ansible.builtin.known_hosts"]
    assert "hostvars[item.item].ansible_host" in contract["name"]
    assert "item.stdout" in contract["key"]


def test_pve_apply_proves_batchmode_authentication_to_every_managed_lxc() -> None:
    _, selected = load_reconcile()
    tasks = selected["tasks"]
    authentication = next(
        task
        for task in tasks
        if task["name"]
        == "Authenticate the configured deploy identity to every managed LXC"
    )
    argv = authentication["ansible.builtin.command"]["argv"]

    assert authentication["when"] == APPLY_GATE
    assert authentication["delegate_to"] == "localhost"
    assert authentication["loop"] == "{{ groups['debian'] }}"
    assert authentication["changed_when"] is False
    assert authentication["until"] == "pve_lxc_batch_authentication.rc == 0"
    assert authentication["retries"] == 60
    assert argv[:3] == ["ssh", "-n", "-T"]
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "PreferredAuthentications=publickey" in argv
    assert "IdentitiesOnly=yes" in argv
    assert "IdentityFile={{ lookup('env', 'HOME') }}/.ssh/id_ed25519" in argv
    assert "root@{{ hostvars[item].ansible_host }}" in argv
    assert argv[-1] == "true"
    assert not any("ansible.builtin.wait_for" in task for task in tasks)

    names = [task["name"] for task in tasks]
    assert names.index("Reconcile managed LXC host keys on the controller") < names.index(
        authentication["name"]
    )
