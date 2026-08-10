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
    assert 'git grep -F -- "$gateway_token"' in validation

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
