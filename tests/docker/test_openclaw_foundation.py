import yaml

from tests.helpers import REPO_ROOT


IMAGE = (
    "ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:"
    "8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac"
)
COMPOSE_PATH = REPO_ROOT / "apps/compose/openclaw/compose.yml"
ROLE_PATH = REPO_ROOT / "infra/ansible/roles/openclaw_foundation/tasks/main.yml"


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_retained_cli_version_contract_matches_the_pinned_image_release():
    variables = yaml.safe_load(
        read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")
    )
    assert variables["openclaw_retained_cli_version_output"] == (
        "OpenClaw 2026.7.1-2 (0790d9f)"
    )
    assert variables["openclaw_retained_image_ref"] == IMAGE
    assert variables["openclaw_retained_gateway_identity_path"] == (
        "{{ openclaw_control_root }}/retained-gateway-identity.json"
    )
    assert variables["openclaw_retained_gateway_verifier_path"] == (
        "/usr/local/libexec/openclaw-retained-gateway"
    )
    assert ":2026.7.1-2@sha256:" in IMAGE
    assert "openclaw_retained_cli_version_output" in ROLE_PATH.read_text(
        encoding="utf-8"
    )


def test_openclaw_compose_is_pinned_local_only_and_hardened():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    gateway = compose["services"]["openclaw-gateway"]

    assert compose["name"] == "openclaw"
    assert gateway["image"] == IMAGE
    assert gateway["platform"] == "linux/amd64"
    assert gateway["user"] == "${PUID:?set PUID}:${PGID:?set PGID}"
    assert gateway["restart"] == "unless-stopped"
    assert gateway["read_only"] is True
    assert "init" not in gateway  # The pinned image already enters through tini.
    assert "command" not in gateway
    assert gateway["ports"] == ["127.0.0.1:18789:18789"]
    assert gateway["cap_drop"] == ["ALL"]
    assert gateway["security_opt"] == ["no-new-privileges:true"]
    assert any(value.startswith("/tmp:rw,noexec,nosuid,nodev") for value in gateway["tmpfs"])
    assert "com.getarcaneapp.arcane.updater=false" in gateway["labels"]
    assert all(not label.startswith("traefik.") for label in gateway["labels"])
    assert "networks" not in gateway


def test_openclaw_compose_keeps_config_secret_and_state_separate():
    gateway = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"][
        "openclaw-gateway"
    ]
    mounts = {mount["target"]: mount for mount in gateway["volumes"]}

    assert gateway["environment"]["OPENCLAW_CONFIG_PATH"] == (
        "/etc/openclaw/openclaw.json"
    )
    assert gateway["environment"]["OPENCLAW_STATE_DIR"] == "/home/node/.openclaw"
    assert gateway["environment"]["OPENCLAW_DISABLE_BONJOUR"] == "1"
    assert "OPENCLAW_GATEWAY_TOKEN" not in gateway["environment"]

    assert mounts["/etc/openclaw/openclaw.json"]["source"] == (
        "/opt/homelab-compose/openclaw-setup/config/openclaw.json"
    )
    assert mounts["/etc/openclaw/openclaw.json"]["read_only"] is True
    assert mounts["/home/node/.openclaw"]["source"] == (
        "/srv/homelab/docker-apps/openclaw/state"
    )
    assert "read_only" not in mounts["/home/node/.openclaw"]
    assert mounts["/home/node/.config/openclaw"]["source"] == (
        "/srv/homelab/docker-apps/openclaw/auth-profile-secrets"
    )
    assert mounts["/run/secrets/openclaw_gateway_token"]["source"] == (
        "/opt/homelab-control/openclaw/secrets/gateway_token"
    )
    assert mounts["/run/secrets/openclaw_gateway_token"]["read_only"] is True
    for mount in mounts.values():
        assert mount["bind"]["create_host_path"] is False

    serialized = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in serialized
    assert "OPENCLAW_GATEWAY_TOKEN=" not in serialized


def test_openclaw_has_an_isolated_ansible_role_and_arcane_registration():
    tasks_text = ROLE_PATH.read_text(encoding="utf-8")
    tasks = yaml.safe_load(tasks_text)
    variables = yaml.safe_load(
        read("infra/ansible/inventory/prod/group_vars/svc_docker_apps.yml")
    )
    site = read("infra/ansible/playbooks/site.yml")

    assert "docker_compose_projects" not in tasks_text
    assert site.index("role: docker_compose_project") < site.index(
        "role: openclaw_foundation"
    ) < site.index("role: arcane_manager")
    assert "openclaw" not in [project["name"] for project in variables["docker_compose_projects"]]
    assert {"name": "openclaw", "compose_path": "apps/compose/openclaw/compose.yml"} in (
        variables["arcane_gitops_projects"]
    )
    assert "openclaw_image" not in variables
    assert "docker compose config --images" in tasks_text
    for boundary in (
        "openclaw_compose_root != openclaw_runtime_root",
        "openclaw_compose_root != openclaw_control_root",
        "openclaw_setup_root != openclaw_runtime_root",
        "openclaw_setup_root != openclaw_control_root",
        "openclaw_runtime_root != openclaw_control_root",
        "openclaw_state_root == openclaw_runtime_root + '/state'",
        "openclaw_secret_root == openclaw_control_root + '/secrets'",
    ):
        assert boundary in tasks_text
    assert "openclaw_compose_up.stdout ~ openclaw_compose_up.stderr" in tasks_text
    assert variables["openclaw_native_cutover_marker_path"] == (
        "/opt/homelab-control/openclaw/native-cutover-validated"
    )
    assert variables["openclaw_native_transition_marker_value"] == (
        "homelab-openclaw-native-migration-v1"
    )

    token_task = next(
        task
        for task in tasks
        if task["name"] == "Install the OpenClaw Gateway token outside Git"
    )
    token_copy = token_task["ansible.builtin.copy"]
    assert token_copy["dest"] == "{{ openclaw_gateway_token_path }}"
    assert token_copy["owner"] == "{{ openclaw_uid }}"
    assert token_copy["group"] == "{{ openclaw_gid }}"
    assert token_copy["mode"] == "0600"
    assert token_task["no_log"] is True
    assert token_task["diff"] is False

    for task in tasks:
        module = task.get("ansible.builtin.command") or task.get("ansible.builtin.shell")
        if not module:
            continue
        command = module if isinstance(module, str) else module.get("cmd", "")
        if "docker compose" in command:
            assert task.get("args", module if isinstance(module, dict) else {}).get(
                "chdir"
            ) == "{{ openclaw_compose_root }}"


def test_openclaw_foundation_holds_retained_gateway_after_validated_native_cutover():
    tasks = yaml.safe_load(ROLE_PATH.read_text(encoding="utf-8"))
    tasks_text = ROLE_PATH.read_text(encoding="utf-8")

    exact_marker = next(
        task
        for task in tasks
        if task["name"] == "Require the exact validated native OpenClaw cutover marker"
    )
    assert exact_marker["when"] == (
        "openclaw_native_cutover_marker.stat.exists | default(false)"
    )
    assert "stat.isreg" in " ".join(exact_marker["ansible.builtin.assert"]["that"])
    assert "stat.islnk" in " ".join(exact_marker["ansible.builtin.assert"]["that"])
    assert "stat.uid == 0" in " ".join(exact_marker["ansible.builtin.assert"]["that"])
    assert "stat.gid == 0" in " ".join(exact_marker["ansible.builtin.assert"]["that"])
    assert "stat.mode == '0600'" in " ".join(
        exact_marker["ansible.builtin.assert"]["that"]
    )
    assert "openclaw_native_transition_marker_value + '\\n'" in " ".join(
        exact_marker["ansible.builtin.assert"]["that"]
    )

    hold = next(
        task
        for task in tasks
        if task["name"]
        == "Preserve the validated native cutover and hold Docker Gateway stopped"
    )
    hold_text = yaml.safe_dump(hold, sort_keys=False)
    assert "http://{{ openclaw_ip }}:{{ openclaw_gateway_port }}/readyz" in hold_text
    stop = next(
        task
        for task in hold["block"]
        if task["name"] == "Stop the retained Docker OpenClaw Gateway"
    )
    assert stop["ansible.builtin.command"]["argv"][:3] == [
        "docker",
        "compose",
        "stop",
    ]
    assert "docker compose ps --status running -q openclaw-gateway" in hold_text

    start = next(
        task for task in tasks if task["name"] == "Start only the OpenClaw Compose project"
    )
    assert start["when"] == (
        "not (openclaw_native_cutover_marker.stat.exists | default(false))"
    )
    assert "marker exists but is malformed" in tasks_text

    half_commit_probe = next(
        task
        for task in tasks
        if task["name"] == "Check for a half-committed native OpenClaw listener"
    )
    assert half_commit_probe["ansible.builtin.wait_for"] == {
        "host": "{{ openclaw_ip }}",
        "port": "{{ openclaw_gateway_port }}",
        "state": "stopped",
        "connect_timeout": 1,
        "timeout": 2,
    }
    assert half_commit_probe["failed_when"] is False
    reject_half_commit = next(
        task
        for task in tasks
        if task["name"]
        == "Reject an unmarked native OpenClaw listener before Docker startup"
    )
    assert reject_half_commit["ansible.builtin.assert"]["that"] == [
        "openclaw_unmarked_native_listener_probe.msg is not defined"
    ]
    assert tasks.index(reject_half_commit) < tasks.index(start)


def test_public_repo_contains_only_the_openclaw_deployment_interface():
    public = REPO_ROOT / "apps/compose/openclaw"
    assert sorted(path.name for path in public.iterdir()) == [
        ".env.example",
        "README.md",
        "compose.yml",
    ]
    assert not list(public.rglob("openclaw.json"))
    for private_name in (
        "agents",
        "shared-skills",
        "workspaces",
        "sessions",
        "auth",
        "credentials",
    ):
        assert not (public / private_name).exists()


def test_openclaw_live_validation_covers_the_completion_contract():
    validation = read("infra/ansible/playbooks/validate.yml")
    assert 'git_safe --no-pager grep -F -- "$gateway_token"' in validation
    for hardened_git_contract in (
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_SYSTEM: /dev/null",
        "GIT_CONFIG_GLOBAL: /dev/null",
        "core.hooksPath",
        "core.fsmonitor=false",
        "core.attributesFile=/dev/null",
        "--no-ext-diff --no-textconv",
        "test ! -x .git/config",
    ):
        assert hardened_git_contract in validation

    for marker in (
        "Check Docker Engine reboot persistence",
        "Validate Arcane sees the private OpenClaw repository read-only",
        "Require the OpenClaw Gateway container to be running and healthy",
        "Read the immutable OpenClaw image from Compose",
        "Validate OpenClaw Git, path, and permission boundaries",
        "Validate OpenClaw container hardening and mount ownership",
        "Validate the OpenClaw host listener is loopback-only",
        "Check OpenClaw liveness and readiness endpoints",
        "Check the OpenClaw CLI version",
        "Check the active OpenClaw config path",
        "Validate the active OpenClaw config schema",
        "Audit the active OpenClaw secrets",
        "Probe the running OpenClaw Gateway RPC endpoint",
    ):
        assert marker in validation


def test_openclaw_live_validation_is_transition_marker_aware():
    validation_path = REPO_ROOT / "infra/ansible/playbooks/validate.yml"
    plays = yaml.safe_load(validation_path.read_text(encoding="utf-8"))
    docker_play = next(
        play
        for play in plays
        if play["name"] == "Validate Docker Compose application host"
    )
    tasks = docker_play["tasks"]

    marker_assertion = next(
        task
        for task in tasks
        if task["name"]
        == "Reject an ambiguous Docker-to-native OpenClaw cutover marker"
    )
    requirements = " ".join(marker_assertion["ansible.builtin.assert"]["that"])
    assert "stat.isreg" in requirements
    assert "stat.islnk" in requirements
    assert "stat.uid == 0" in requirements
    assert "stat.gid == 0" in requirements
    assert "stat.mode == '0600'" in requirements
    assert "openclaw_native_transition_marker_value + '\\n'" in requirements

    transition = next(
        task
        for task in tasks
        if task["name"]
        == "Validate the completed native cutover keeps Docker OpenClaw stopped"
    )
    transition_text = yaml.safe_dump(transition, sort_keys=False)
    assert "http://{{ openclaw_ip }}:{{ openclaw_gateway_port }}/readyz" in (
        transition_text
    )
    assert "docker compose ps --status running -q openclaw-gateway" in (
        transition_text
    )
    assert "docker compose ps --all -q openclaw-gateway" in transition_text
    for retained_path in (
        "openclaw_setup_root",
        "openclaw_config_path",
        "openclaw_state_root",
        "openclaw_auth_profile_secret_root",
        "openclaw_gateway_token_path",
    ):
        assert retained_path in transition_text
    assert "docker image inspect" in transition_text
    assert "SELECT COUNT(*) FROM gitops_syncs WHERE name = ?" in (
        validation_path.read_text(encoding="utf-8")
    )
    assert "https://{{ openclaw_hostname }}/" in transition_text
    assert "--insecure" not in transition_text
    validation_text = validation_path.read_text(encoding="utf-8")
    assert "Validate the exact staged OpenClaw Traefik route" in validation_text
    assert "Host(`openclaw.home.hchu.me`)" in validation_text
    assert "['private-only', 'secure-headers']" in validation_text
    assert "openclaw_traefik_backend_url" in validation_text

    normal_condition = (
        "not (openclaw_native_cutover_marker_validation.stat.exists | "
        "default(false))"
    )
    legacy_tasks = {
        "Validate the OpenClaw Compose project",
        "Read the immutable OpenClaw image from Compose",
        "Require the OpenClaw Gateway container to be running and healthy",
        "Validate OpenClaw Git, path, and permission boundaries",
        "Validate OpenClaw container hardening and mount ownership",
        "Validate the OpenClaw host listener is loopback-only",
        "Check OpenClaw liveness and readiness endpoints",
        "Check the OpenClaw CLI version",
        "Check the active OpenClaw config path",
        "Validate the active OpenClaw config schema",
        "Audit the active OpenClaw secrets",
        "Probe the running OpenClaw Gateway RPC endpoint",
    }
    for task in tasks:
        if task["name"] in legacy_tasks:
            assert task["when"] == normal_condition
            legacy_tasks.remove(task["name"])
    assert not legacy_tasks


def test_arcane_retires_only_the_openclaw_gitops_sync_after_native_cutover():
    tasks_path = REPO_ROOT / "infra/ansible/roles/arcane_manager/tasks/main.yml"
    tasks = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))
    tasks_text = tasks_path.read_text(encoding="utf-8")

    marker = next(
        task
        for task in tasks
        if task["name"]
        == "Require the exact native OpenClaw cutover marker for Arcane"
    )
    requirements = " ".join(marker["ansible.builtin.assert"]["that"])
    assert "stat.isreg" in requirements
    assert "stat.islnk" in requirements
    assert "stat.uid == 0" in requirements
    assert "stat.gid == 0" in requirements
    assert "stat.mode == '0600'" in requirements
    assert "openclaw_native_transition_marker_value + '\\n'" in requirements

    selection = next(
        task
        for task in tasks
        if task["name"] == "Select marker-aware Arcane GitOps projects"
    )["ansible.builtin.set_fact"]
    assert "rejectattr('name', 'equalto', 'openclaw')" in (
        selection["arcane_effective_gitops_projects"]
    )
    retired = selection["arcane_effective_retired_gitops_projects"]
    assert "'name': 'openclaw'" in retired
    assert "'compose_path': 'apps/compose/openclaw/compose.yml'" in retired
    assert "arcane_openclaw_cutover_marker.stat.exists" in retired

    assert "{% for project in arcane_effective_gitops_projects %}" in tasks_text
    assert (
        "{% for project in arcane_effective_retired_gitops_projects %}"
        in tasks_text
    )
    assert "docker compose down" not in tasks_text
