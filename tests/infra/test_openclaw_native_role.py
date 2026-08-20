from pathlib import Path

import yaml

from tests.helpers import REPO_ROOT


ROLE = REPO_ROOT / "infra/ansible/roles/openclaw_native"
TASKS = ROLE / "tasks/main.yml"
VARS = REPO_ROOT / "infra/ansible/inventory/prod/group_vars/svc_openclaw.yml"
SITE = REPO_ROOT / "infra/ansible/playbooks/site.yml"
VALIDATE = REPO_ROOT / "infra/ansible/playbooks/validate.yml"
COMPOSE = REPO_ROOT / "infra/openclaw/runtime/compose.yml"
DEPLOYER = REPO_ROOT / "scripts/ci/deploy_openclaw_release.py"
UPLOADER = REPO_ROOT / "scripts/ci/deploy-openclaw-via-ssh.sh"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_native_role_is_now_a_compact_opaque_runtime_reconciler() -> None:
    text = _text(TASKS)
    assert len(text.splitlines()) < 450
    assert "npm install" not in text
    assert "node-v" not in text
    assert "docker build" not in text
    assert "plugins install" not in text
    assert "openclaw-release-deployer" not in text
    assert "deploy_openclaw_release.py" in text
    assert "openclaw_release.py" in text
    assert "immutable_image_release.py" in text
    for retired_activation_input in (
        "openclaw_release_deploy",
        "openclaw_release_source_sha",
        "openclaw_release_manifest_local",
        "openclaw_runtime_bundle_local",
        "openclaw_config_bundle_local",
    ):
        assert retired_activation_input not in text


def test_routine_host_reconciliation_does_not_upgrade_or_refresh_unconditionally() -> None:
    tasks = yaml.safe_load(_text(TASKS))
    apt_tasks = [task["ansible.builtin.apt"] for task in tasks if "ansible.builtin.apt" in task]
    assert apt_tasks
    routine_render = _text(TASKS)
    assert "'latest' if (homelab_maintenance_upgrade" in routine_render
    assert "update_cache: true\n  when: openclaw_docker_repository.changed" in routine_render
    assert all(package.get("state") != "latest" for package in apt_tasks)


def test_role_materializes_only_root_owned_group_readable_gateway_secrets() -> None:
    text = _text(TASKS)
    for path in (
        "openclaw_gateway_token_path",
        "openclaw_discord_bot_token_path",
        "openclaw_exa_api_key_path",
    ):
        assert path in text
    assert 'owner: root\n    group: "{{ openclaw_group }}"\n    mode: "0440"' in text
    assert 'openclaw_skill_sync_github_token_path' in text
    assert 'group: root\n    mode: "0600"' in text
    workflow = _text(REPO_ROOT / ".github/workflows/ci.yml")
    assert "openclaw-gateway-access-probe" in workflow
    assert '"0440", str(config_file)' in workflow
    assert "test -r /probe/config/openclaw.json" in workflow


def test_compose_uses_two_exact_digest_inputs_and_no_build_surface() -> None:
    compose = yaml.safe_load(_text(COMPOSE))
    gateway = compose["services"]["gateway"]
    assert gateway["image"].startswith("${OPENCLAW_GATEWAY_REF:")
    assert gateway["environment"]["OPENCLAW_CTF_IMAGE"].startswith("${OPENCLAW_CTF_REF:")
    assert "build" not in gateway
    assert gateway["read_only"] is True
    assert gateway["network_mode"] == "host"
    assert gateway["cap_drop"] == ["ALL"]
    assert gateway["group_add"] == ["${OPENCLAW_DOCKER_GID:?numeric host Docker group is required}"]
    socket_mount = next(
        mount for mount in gateway["volumes"]
        if mount.get("source") == "/var/run/docker.sock"
    )
    assert socket_mount["read_only"] is True
    state_mounts = [
        mount for mount in gateway["volumes"]
        if mount.get("source") == "/var/lib/openclaw"
    ]
    assert {mount["target"] for mount in state_mounts} == {
        "/home/node/.openclaw",
        "/var/lib/openclaw",
    }
    assert all(mount.get("read_only", False) is False for mount in state_mounts)
    assert all(mount["bind"]["create_host_path"] is False for mount in state_mounts)
    assert "/readyz" in " ".join(gateway["healthcheck"]["test"])
    assert "/healthz" not in " ".join(gateway["healthcheck"]["test"])


def test_ctf_image_and_private_workspace_share_one_numeric_write_identity() -> None:
    dockerfile = _text(REPO_ROOT / "infra/openclaw/ctf/Dockerfile")
    all_vars = yaml.safe_load(
        _text(REPO_ROOT / "infra/ansible/inventory/prod/group_vars/all.yml")
    )
    topology = _text(REPO_ROOT / "infra/ansible/inventory/prod/topology.json")
    workflow = _text(REPO_ROOT / ".github/workflows/ci.yml")

    assert all_vars["openclaw_ctf_uid"] == 1000
    assert all_vars["openclaw_ctf_gid"] == 1000
    assert "USER 1000:1000" in dockerfile
    assert 'bind_mount_source_mode\": \"0700' in topology
    assert "openclaw-ctf-write-probe" in workflow
    assert ".identity-probe" in workflow


def test_compose_exposes_only_gateway_secrets_not_skill_promotion_credential() -> None:
    compose = _text(COMPOSE)
    assert "gateway_token" in compose
    assert "discord_bot_token" in compose
    assert "exa_api_key" in compose
    assert "skill_sync_github_token" not in compose
    assert "/var/run/docker.sock" in compose


def test_role_preserves_least_privilege_autonomous_skill_promotion() -> None:
    tasks = _text(TASKS)
    service = _text(ROLE / "templates/openclaw-skill-sync.service.j2")
    assert "openclaw_skill_sync.py" in tasks
    assert "openclaw-skill-sync.timer" in tasks
    assert "LoadCredential=github_token:{{ openclaw_skill_sync_github_token_path }}" in service
    assert "User={{ openclaw_skill_sync_user }}" in service
    assert "ReadOnlyPaths={{ openclaw_workspace_root }}/skills" in service
    assert "ReadOnlyPaths={{ openclaw_ctf_workspace_root }}/skills" in service
    assert "ReadWritePaths={{ openclaw_skill_sync_state_root }}" in service


def test_role_retires_legacy_native_gateway_only_after_runtime_is_ready() -> None:
    tasks = yaml.safe_load(_text(TASKS))
    names = [task["name"] for task in tasks]
    retirement_names = [
        "Inspect legacy native Gateway unit",
        "Stop and disable legacy native Gateway unit",
        "Remove legacy native Gateway unit",
        "Reload systemd after retiring legacy native Gateway unit",
    ]
    assert names[-4:] == retirement_names
    assert names.index("Install immutable release utilities") < names.index(
        retirement_names[0]
    )

    inspect, stop, remove, reload = tasks[-4:]
    unit_path = "/etc/systemd/system/openclaw-gateway.service"
    assert inspect["ansible.builtin.stat"]["path"] == unit_path
    assert stop["ansible.builtin.systemd_service"] == {
        "name": "openclaw-gateway.service",
        "enabled": False,
        "state": "stopped",
    }
    assert stop["when"] == "openclaw_legacy_gateway_unit.stat.exists"
    assert remove["ansible.builtin.file"] == {"path": unit_path, "state": "absent"}
    assert remove["when"] == "openclaw_legacy_gateway_unit.stat.exists"
    assert reload["ansible.builtin.systemd_service"] == {"daemon_reload": True}
    assert reload["when"] == (
        "openclaw_legacy_gateway_unit_removed.changed | default(false)"
    )


def test_activation_consumes_only_the_exact_immutable_release() -> None:
    text = _text(DEPLOYER) + _text(UPLOADER)
    assert "legacy-recovery.json" not in text
    assert "--legacy-recovery-manifest" not in text
    assert "--manifest" in text
    assert "--runtime-archive" in text
    assert "--config-archive" in text


def test_site_has_one_openclaw_role_and_no_docker_apps_foundation() -> None:
    site = _text(SITE)
    assert site.count("role: openclaw_native") == 1
    for retired in (
        "openclaw_ctf_local_docker",
        "openclaw_foundation",
        "openclaw_ctf_gateway",
        "openclaw_discord_relay",
    ):
        assert f"role: {retired}" not in site


def test_validation_audits_exact_release_without_duplicate_auth_smoke() -> None:
    text = _text(VALIDATE)
    immutable = text.split("- name: Validate the dedicated immutable OpenClaw runtime", 1)[1]
    immutable = immutable.split("- name: Validate Docker Compose application host", 1)[0]
    assert "deploy_openclaw_release.py" in immutable
    assert "audit" in immutable
    assert "/readyz" not in immutable  # URL comes from the one group variable.
    assert "openclaw_readiness_url" in immutable
    assert "Authorization" not in immutable
    assert "auth smoke" in immutable


def test_openclaw_vars_use_topology_hostvars_and_no_transition_state_machine() -> None:
    text = _text(VARS)
    assert "hostvars['openclaw'].ansible_host" in text
    assert "openclaw_native_activate" not in text
    assert "openclaw_docker_rollback_activate" not in text
    assert "transition_marker" not in text
    assert "/readyz" in text
