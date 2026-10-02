from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeVar

import yaml

from tests.helpers import REPO_ROOT


WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
T = TypeVar("T")


@dataclass(frozen=True)
class Workflow:
    path: Path
    source: str
    data: dict


def workflows() -> tuple[Workflow, ...]:
    records = []
    for path in sorted((*WORKFLOW_ROOT.glob("*.yml"), *WORKFLOW_ROOT.glob("*.yaml"))):
        source = path.read_text(encoding="utf-8")
        records.append(Workflow(path, source, yaml.load(source, Loader=yaml.BaseLoader)))
    assert records, "no workflow definitions were discovered"
    return tuple(records)


def sole(values: Iterable[T], capability: str) -> T:
    iterator = iter(values)
    try:
        value = next(iterator)
    except StopIteration:
        raise AssertionError(f"missing workflow capability: {capability}") from None
    try:
        duplicate = next(iterator)
    except StopIteration:
        return value
    raise AssertionError(f"ambiguous workflow capability {capability!r}: {value!r}, {duplicate!r}")


def workflow_steps(workflow: Workflow) -> Iterable[tuple[str, dict, int, dict]]:
    for job_id, job in workflow.data["jobs"].items():
        for index, step in enumerate(job.get("steps", [])):
            yield job_id, job, index, step


def job_commands(job: dict) -> str:
    return "\n".join(step["run"] for step in job.get("steps", []) if "run" in step)


def workflow_commands(workflow: Workflow) -> str:
    return "\n".join(job_commands(job) for job in workflow.data["jobs"].values())


def step_with(job: dict, predicate: Callable[[dict], bool], capability: str) -> tuple[int, dict]:
    return sole(
        ((index, step) for index, step in enumerate(job.get("steps", [])) if predicate(step)),
        capability,
    )


def job_with(workflow: Workflow, predicate: Callable[[dict], bool], capability: str) -> tuple[str, dict]:
    return sole(
        ((job_id, job) for job_id, job in workflow.data["jobs"].items() if predicate(job)),
        capability,
    )


def uses_action(step: dict, slug: str) -> bool:
    action = step.get("uses", "")
    return action.partition("@")[0] == slug


def bundle_target(command: str) -> str | None:
    if not re.search(r"\bcompose_release_engine\.py\s+bundle\b", command):
        return None
    match = re.search(r"--target\s+([a-z][a-z0-9-]*)\b", command)
    assert match, "a release bundle must declare its target"
    return match.group(1)


def primary_capability(workflow: Workflow) -> str | None:
    targets = {
        target
        for _, _, _, step in workflow_steps(workflow)
        if (target := bundle_target(step.get("run", "")))
    }
    if targets:
        return f"release:{sole(targets, 'release target')}"
    events = set(workflow.data["on"])
    commands = workflow_commands(workflow)
    if {"pull_request", "merge_group"} <= events and "python -m pytest" in commands:
        return "validation"
    if "ansible-playbook" in commands:
        return "infrastructure"
    return None


def lanes() -> dict[str, Workflow]:
    discovered: dict[str, Workflow] = {}
    for workflow in workflows():
        capability = primary_capability(workflow)
        assert capability is not None, f"unclassified workflow: {workflow.path}"
        assert capability not in discovered, f"duplicate workflow capability: {capability}"
        discovered[capability] = workflow
    assert set(discovered) == {"release:apps", "infrastructure", "validation"}
    return discovered


def effective_condition(job: dict, step: dict | None = None) -> str:
    conditions = [job.get("if", ""), step.get("if", "") if step else ""]
    return " && ".join(condition for condition in conditions if condition)


def production_mutation(job: dict) -> bool:
    if job.get("environment") != "prod":
        return False
    command = job_commands(job)
    return bool(
        re.search(r"\bdeploy-release-via-ssh\.sh\s+(?:deploy|sync-secrets)\b", command)
        or "ansible-playbook" in command
    )


def cli_options(command: str) -> dict[str, str]:
    tokens = shlex.split(command.replace("\\\n", " "))
    return {
        token[2:]: tokens[index + 1]
        for index, token in enumerate(tokens[:-1])
        if token.startswith("--")
    }


def freshness_scope(command: str) -> set[str] | None:
    match = re.search(
        r'git\s+diff\s+--quiet\s+"?\$GITHUB_SHA"?\s+FETCH_HEAD\s+--\s*(.*?)\s*;\s*then',
        command,
        flags=re.DOTALL,
    )
    return set(shlex.split(match.group(1).replace("\\\n", " "))) if match else None


def normalized_push_scope(workflow: Workflow) -> set[str]:
    return {path.removesuffix("/**") for path in workflow.data["on"]["push"]["paths"]}


def test_workflows_are_discovered_as_complete_coarse_lanes_with_one_mutation_lock() -> None:
    by_capability = lanes()
    assert {"push", "workflow_dispatch"} <= set(by_capability["release:apps"].data["on"])
    assert {"schedule", "workflow_dispatch"} <= set(by_capability["infrastructure"].data["on"])
    assert {"pull_request", "merge_group", "workflow_dispatch"} <= set(
        by_capability["validation"].data["on"]
    )

    mutation_locks = set()
    for workflow in workflows():
        for job in workflow.data["jobs"].values():
            if not production_mutation(job):
                continue
            assert job.get("environment") == "prod"
            lock = job.get("concurrency", workflow.data.get("concurrency"))
            assert lock, f"production mutation lacks concurrency control: {workflow.path}"
            assert lock["cancel-in-progress"] == "false"
            assert lock["queue"] == "max"
            mutation_locks.add(lock["group"])
    sole(mutation_locks, "shared production mutation lock")


def test_actions_runners_and_checkouts_enforce_supply_chain_identity() -> None:
    for workflow in workflows():
        for job in workflow.data["jobs"].values():
            assert job["runs-on"] == "ubuntu-24.04"
            for step in job.get("steps", []):
                action = step.get("uses")
                if not action:
                    continue
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action
                if uses_action(step, "actions/checkout"):
                    checkout = step.get("with", {})
                    assert checkout.get("persist-credentials") == "false"
                    if "repository" in checkout:
                        assert "ssh-key" in checkout and "token" not in checkout
                    else:
                        assert checkout.get("ref") == "${{ github.sha }}"
        for line in workflow.source.splitlines():
            if re.match(r"\s*uses:\s*", line):
                assert re.search(r"\s#\s+v?\d+(?:\.\d+){0,2}\s*$", line), line


def test_job_permissions_are_least_privilege_for_discovered_capabilities() -> None:
    for workflow in workflows():
        for job in workflow.data["jobs"].values():
            required = {"contents": "read"}
            if production_mutation(job):
                required["id-token"] = "write"
            actual = job.get("permissions", workflow.data.get("permissions", {}))
            assert actual == required, (workflow.path, required, actual)


def test_apps_release_orders_validation_bundle_freshness_and_mutation_by_capability() -> None:
    workflow = lanes()["release:apps"]
    _, job = job_with(
        workflow,
        lambda candidate: bundle_target(job_commands(candidate)) == "apps",
        "apps release job",
    )
    validate_index, _ = step_with(
        job,
        lambda step: "python -m pytest" in step.get("run", "")
        and "validate-compose.sh" in step.get("run", ""),
        "apps package validation",
    )
    bundle_index, bundle = step_with(
        job, lambda step: bundle_target(step.get("run", "")) == "apps", "apps release bundle"
    )
    freshness_index, freshness = step_with(
        job,
        lambda step: freshness_scope(step.get("run", "")) is not None,
        "apps desired-state freshness gate",
    )
    deploy_index, deploy = step_with(
        job,
        lambda step: bool(re.search(r"\bdeploy-release-via-ssh\.sh\s+deploy\s+apps\b", step.get("run", ""))),
        "apps release mutation",
    )
    assert validate_index < bundle_index < freshness_index < deploy_index
    assert freshness_scope(freshness["run"]) == normalized_push_scope(workflow)
    assert "refs/heads/main" in freshness["run"]
    assert "workflow_dispatch" in freshness["if"] and "!=" in freshness["if"]
    assert cli_options(bundle["run"])["output"] in deploy["run"]
    assert "ansible-playbook" not in job_commands(job)
    assert "docker build" not in job_commands(job)


def test_manual_secret_rotation_bypasses_release_artifacts_by_operation() -> None:
    for target in ("apps",):
        workflow = lanes()[f"release:{target}"]
        operation = workflow.data["on"]["workflow_dispatch"]["inputs"]["operation"]
        assert set(operation["options"]) == {"deploy", "sync-secrets"}
        assert operation["default"] == "deploy"
        _, sync_job, _, sync_step = sole(
            (
                item
                for item in workflow_steps(workflow)
                if re.search(
                    rf"\bdeploy-release-via-ssh\.sh\s+sync-secrets\s+{re.escape(target)}\b",
                    item[3].get("run", ""),
                )
            ),
            f"{target} component-bundle rotation",
        )
        condition = effective_condition(sync_job, sync_step)
        assert "workflow_dispatch" in condition and "sync-secrets" in condition
        assert "bundle" not in sync_step["run"] and ".tar" not in sync_step["run"]
        for _, job, _, step in workflow_steps(workflow):
            command = step.get("run", "")
            if bundle_target(command) == target or re.search(
                rf"\bdeploy-release-via-ssh\.sh\s+deploy\s+{re.escape(target)}\b", command
            ):
                release_condition = effective_condition(job, step)
                assert "sync-secrets" in release_condition and "!=" in release_condition


def test_infrastructure_and_validation_derive_real_units_and_playbook() -> None:
    by_capability = lanes()
    infrastructure = by_capability["infrastructure"]
    inputs = infrastructure.data["on"]["workflow_dispatch"]["inputs"]
    units = set(inputs["unit"]["options"])
    assert units == {"pve", "tailnet", "apps-host"}
    assert set(inputs["pve_mode"]["options"]) == {"plan", "audit", "apply"}
    for approval in ("allow_destructive_vmid", "allow_replacement_vmid"):
        assert inputs[approval]["default"] == "" and inputs[approval]["required"] == "false"
    _, job = job_with(
        infrastructure,
        lambda candidate: "ansible-playbook" in job_commands(candidate),
        "infrastructure reconciliation",
    )
    configure_index, configure = step_with(job, lambda step: "configure-ssh.sh" in step.get("run", ""), "SSH setup")
    materialize_index, materialize = step_with(job, lambda step: '"component": "pve"' in step.get("run", ""), "PVE bundle")
    bind_index, bind = step_with(job, lambda step: "verify_pve_access_bundle.py" in step.get("run", ""), "PVE key binding")
    reconcile_index, reconcile = step_with(job, lambda step: "ansible-playbook" in step.get("run", ""), "unit reconcile")
    assert configure_index < materialize_index < bind_index < reconcile_index
    assert configure["env"]["DEPLOY_SSH_PRIVATE_KEY"] == "${{ secrets.DEPLOY_SSH_PRIVATE_KEY }}"
    assert materialize["if"] == bind["if"]
    assert all(term in bind["if"] for term in ("matrix.unit", "pve", "pve_mode", "apply"))
    assert "--private-key" in bind["run"] and "--bundle" in bind["run"]
    assert '"ssh-keygen", "-y"' in materialize["run"]
    assert ".ssh/id_ed25519" in materialize["run"]
    _, tailnet = step_with(job, lambda step: '"component": "tailnet"' in step.get("run", ""), "tailnet bundle")
    assert tailnet["env"]["TAILNET_AUTH_KEY"] == "${{ secrets.TAILSCALE_AUTH_KEY }}"
    assert 'destination.chmod(0o600)' in materialize["run"] and 'destination.chmod(0o600)' in tailnet["run"]
    assert 'homelab_unit=$UNIT' in reconcile["run"]
    playbook = sole((REPO_ROOT / "infra" / "ansible" / "playbooks").glob("*.yml"), "Ansible playbook")
    relative_playbook = playbook.relative_to(REPO_ROOT).as_posix()
    assert relative_playbook in reconcile["run"]
    matrix = str(job["strategy"]["matrix"]["unit"])
    scheduled, manual = matrix.split("||", maxsplit=1)
    assert all(f'"{unit}"' in scheduled for unit in units - {"pve"})
    assert '"pve"' not in scheduled and "inputs.unit" in manual

    validation_command = workflow_commands(by_capability["validation"])
    assert "python -m pytest" in validation_command
    assert "validate-compose.sh" in validation_command
    assert relative_playbook in validation_command
    assert all(unit in validation_command for unit in units)


def test_runtime_lanes_expose_only_their_component_and_transport_secrets() -> None:
    common = {"DEPLOY_SSH_KNOWN_HOSTS", "DEPLOY_SSH_PRIVATE_KEY", "TS_AUDIENCE", "TS_OAUTH_CLIENT_ID"}
    lane_specific = {
        "apps": {"APPS_SECRET_BUNDLE"},
    }
    for target, expected in lane_specific.items():
        exposed = set(re.findall(r"secrets\.([A-Z0-9_]+)", lanes()[f"release:{target}"].source))
        assert exposed == common | expected
