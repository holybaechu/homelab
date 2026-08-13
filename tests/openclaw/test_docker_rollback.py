from pathlib import Path

import yaml

from tests.helpers import REPO_ROOT


NATIVE_ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_native/tasks/main.yml"
SOURCE_ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_foundation/tasks/main.yml"
FENCE = REPO_ROOT / "infra/ansible/playbooks/fence-openclaw-docker-before-native.yml"
VALIDATE = REPO_ROOT / "infra/ansible/playbooks/validate.yml"
ALL_VARS = REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml"
ROUTE = REPO_ROOT / "apps/compose/platform/dynamic/routes.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def task_by_name(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_tracked_gateway_ownership_and_static_route_form_one_phase_tuple():
    variables = yaml.safe_load(read(ALL_VARS))
    native_variables = yaml.safe_load(read(
        REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"
    ))
    route = yaml.safe_load(read(ROUTE))
    workflow = read(REPO_ROOT / ".github/workflows/cd.yml")
    backend = route["http"]["services"]["openclaw"]["loadBalancer"]["servers"]

    assert isinstance(variables["openclaw_docker_rollback_activate"], bool)
    assert "http://openclaw-rollback:18789" in variables["openclaw_traefik_backend_url"]
    assert "http://' + openclaw_ip + ':18789" in variables["openclaw_traefik_backend_url"]
    assert not (
        native_variables["openclaw_native_activate"]
        and variables["openclaw_docker_rollback_activate"]
    )
    if 'OPENCLAW_NATIVE_TRANSITION: "true"' in workflow:
        assert native_variables["openclaw_native_activate"] is False
        assert variables["openclaw_docker_rollback_activate"] is False
        assert backend == [{"url": "http://192.168.0.5:18789"}]
    elif variables["openclaw_docker_rollback_activate"]:
        assert native_variables["openclaw_native_activate"] is False
        assert backend == [{"url": "http://openclaw-rollback:18789"}]
    else:
        assert native_variables["openclaw_native_activate"] is True
        assert backend == [{"url": "http://192.168.0.5:18789"}]


def test_native_role_enforces_mutual_exclusion_and_a_hard_rollback_fence():
    tasks = yaml.safe_load(read(NATIVE_ROLE))
    contract = task_by_name(tasks, "Require the pinned native OpenClaw deployment contract")
    requirements = " ".join(contract["ansible.builtin.assert"]["that"])
    assert "not ((openclaw_native_activate | bool) and (openclaw_docker_rollback_activate | bool))" in requirements

    preserve = task_by_name(tasks, "Preserve the validated transitional native OpenClaw service")
    assert "not (openclaw_docker_rollback_activate | bool)" in preserve["when"]
    activate = task_by_name(tasks, "Activate only the native OpenClaw system service")
    assert "not (openclaw_docker_rollback_activate | bool)" in activate["when"]
    stop = task_by_name(tasks, "Stop and disable native OpenClaw for tracked Docker rollback")
    assert stop["when"] == "openclaw_docker_rollback_activate | bool"
    assert stop["ansible.builtin.systemd_service"]["enabled"] is False
    assert stop["ansible.builtin.systemd_service"]["state"] == "stopped"
    proof = task_by_name(tasks, "Prove tracked Docker rollback has no native OpenClaw listener")
    proof_text = proof["ansible.builtin.shell"]
    assert "! systemctl is-enabled" in proof_text
    assert "! systemctl is-active" in proof_text
    assert "! ss -H -ltn" in proof_text
    assert tasks.index(stop) < tasks.index(proof)


def test_source_role_preserves_marker_and_starts_only_exact_retained_container():
    tasks = yaml.safe_load(read(SOURCE_ROLE))
    rendered = read(SOURCE_ROLE)
    marker = task_by_name(tasks, "Require the permanent source hold before tracked Docker rollback")
    assert marker["when"] == "openclaw_docker_rollback_activate | bool"
    assert marker["ansible.builtin.assert"]["that"] == [
        "openclaw_native_cutover_marker.stat.exists | default(false)"
    ]

    rollback = task_by_name(tasks, "Activate the exact retained Docker Gateway for tracked rollback")
    rollback_text = yaml.safe_dump(rollback, sort_keys=False)
    for retained_asset in (
        "openclaw_setup_root",
        "openclaw_config_path",
        "openclaw_state_root",
        "openclaw_auth_profile_secret_root",
        "openclaw_gateway_token_path",
    ):
        assert retained_asset in rollback_text
    assert "docker compose ps --all -q openclaw-gateway" in rollback_text
    assert "docker compose config --images" in rollback_text
    assert "com.docker.compose.project" in rollback_text
    assert "com.docker.compose.service" in rollback_text
    assert "docker\n    - compose\n    - start\n    - openclaw-gateway" in rollback_text
    assert "docker network connect --alias openclaw-rollback homelab_proxy" in rendered
    assert "http://127.0.0.1:{{ openclaw_gateway_port }}/readyz" in rollback_text
    assert "config validate --json" in rollback_text
    assert "secrets audit --check --json" in rendered
    assert "openclaw_native_cutover_marker_path" in rollback_text
    assert "state: absent" not in rollback_text
    assert "docker compose up" not in rollback_text
    assert "--force-recreate" not in rollback_text
    assert "docker compose pull" not in rollback_text
    assert "docker compose down" not in rollback_text
    assert "rm -f" not in rollback_text
    assert "native-cutover-validated" not in rendered.split(
        "Activate the exact retained Docker Gateway for tracked rollback", 1
    )[1].split("- name: Inspect the private OpenClaw repository", 1)[0].replace(
        "openclaw_native_cutover_marker_path", ""
    )


def test_pre_native_fence_and_static_route_tuple_run_before_any_site_mutation():
    play = yaml.safe_load(read(FENCE))[0]
    tasks = play["tasks"]
    workflow = read(REPO_ROOT / ".github/workflows/cd.yml")
    ownership = task_by_name(tasks, "Require mutually exclusive tracked Gateway ownership")
    ownership_text = yaml.safe_dump(ownership, sort_keys=False)
    assert "openclaw_native_activate" in ownership_text
    assert "openclaw_docker_rollback_activate" in ownership_text
    assert "apps/compose/platform/dynamic/routes.yml" in ownership_text
    assert "openclaw_traefik_backend_url" in ownership_text
    stop = task_by_name(tasks, "Stop the retained Docker Gateway before native-primary reconciliation")
    assert stop["ansible.builtin.command"]["argv"] == [
        "docker",
        "compose",
        "stop",
        "--timeout",
        "30",
        "openclaw-gateway",
    ]
    assert "openclaw_native_activate" in " ".join(stop["when"])
    assert "not (openclaw_docker_rollback_activate | bool)" in stop["when"]
    assert workflow.index("Fence retained Docker OpenClaw before native reconciliation") < workflow.index(
        "- name: Deploy services"
    )
    assert "env.OPENCLAW_NATIVE_TRANSITION != 'true'" in workflow.split(
        "- name: Fence retained Docker OpenClaw before native reconciliation", 1
    )[1].split("      - name:", 1)[0]


def test_full_validation_covers_both_mutually_exclusive_steady_states():
    validation = read(VALIDATE)
    for contract in (
        "Require mutually exclusive tracked OpenClaw ownership and route",
        "Validate tracked Docker rollback while preserving native cutover holds",
        "Require the permanent source marker and exact retained rollback container",
        "Require Arcane OpenClaw sync to remain retired during rollback",
        "Check the certificate-valid Docker rollback path through Traefik",
    ):
        assert contract in validation
    assert "openclaw-rollback" in validation
    assert "not ((openclaw_native_activate | bool) and (openclaw_docker_rollback_activate | bool))" in validation
    assert "[{'url': openclaw_traefik_backend_url}]" in validation
    rollback_validation = validation.split(
        "Validate tracked Docker rollback while preserving native cutover holds", 1
    )[1].split("- name: Validate the Arcane control project", 1)[0]
    assert "--insecure" not in rollback_validation


def test_runbook_uses_an_atomic_three_file_rollback_and_keeps_permanent_holds():
    runbook = read(REPO_ROOT / "docs/runbooks/openclaw-native-migration.md")
    rollback = runbook.split("## Rollback", 1)[1]
    for tracked_path in (
        "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml",
        "infra/ansible/inventory/prod/group_vars/all.yml",
        "apps/compose/platform/dynamic/routes.yml",
    ):
        assert rollback.count(tracked_path) >= 2
    for value in (
        "openclaw_native_activate: false",
        "openclaw_docker_rollback_activate: true",
        "http://openclaw-rollback:18789",
        "openclaw_native_activate: true",
        "openclaw_docker_rollback_activate: false",
        "http://192.168.0.5:18789",
    ):
        assert value in rollback
    assert "native-cutover-validated" in rollback
    assert "Do not re-register OpenClaw with Arcane" in rollback
    assert "never remove the permanent source" in rollback
    assert "Prepare retained Docker OpenClaw rollback" in rollback
    assert '"trustedProxies"' in rollback
    assert '"allowedOrigins"' in rollback
    assert "docker compose run -T --rm --no-deps --entrypoint node" in rollback
    assert "config validate --json" in rollback
    assert "secrets audit --check --json" in rollback
    assert "core.hooksPath=/dev/null" in rollback
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml" in rollback
    assert "https://openclaw.home.hchu.me/readyz" in rollback
