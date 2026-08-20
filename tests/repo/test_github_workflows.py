import re

import yaml

from tests.helpers import REPO_ROOT


WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def workflow_data() -> dict:
    return yaml.load(workflow_text(), Loader=yaml.BaseLoader)


def run_steps(job: dict) -> list[dict]:
    return [step for step in job["steps"] if "run" in step]


def step_running(job: dict, fragment: str) -> dict:
    matches = [step for step in run_steps(job) if fragment in step["run"]]
    assert len(matches) == 1, fragment
    return matches[0]


def step_named(job: dict, name: str) -> dict:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def checkout_steps(job: dict) -> list[dict]:
    return [
        step for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    ]


def test_one_workflow_has_all_events_and_explicit_release_inputs() -> None:
    data = workflow_data()
    assert sorted(
        path.name for path in WORKFLOW_PATH.parent.iterdir()
        if path.suffix in {".yml", ".yaml"}
    ) == ["ci.yml"]
    assert set(data["on"]) == {
        "pull_request", "push", "schedule", "repository_dispatch", "workflow_dispatch"
    }
    assert data["on"]["repository_dispatch"]["types"] == ["openclaw-promoted"]
    assert data["on"]["schedule"] == [{"cron": "17 18 * * *"}]
    inputs = data["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "components", "openclaw_config_commit", "openclaw_gateway_ref",
        "openclaw_ctf_ref", "openclaw_runtime_sha256", "openclaw_config_sha256",
        "tofu_force_unlock_id",
    }
    assert inputs["components"]["required"] == "true"
    assert inputs["components"]["default"] == ""
    assert "apps_projects" not in inputs


def test_plan_precedes_validation_and_binds_every_job_to_exact_sha() -> None:
    jobs = workflow_data()["jobs"]
    plan = jobs["deployment_plan"]
    validation = jobs["validation"]
    assert validation["needs"] == "deployment_plan"
    assert plan["outputs"] == {
        "validation_scope": "${{ steps.scope.outputs.validation_scope }}",
        "components": "${{ steps.plan.outputs.components }}",
        "openclaw_setup_commit": "${{ steps.plan.outputs.openclaw_setup_commit }}",
        "openclaw_gateway_ref": "${{ steps.plan.outputs.openclaw_gateway_ref }}",
        "openclaw_ctf_ref": "${{ steps.plan.outputs.openclaw_ctf_ref }}",
        "openclaw_runtime_sha256": "${{ steps.plan.outputs.openclaw_runtime_sha256 }}",
        "openclaw_config_sha256": "${{ steps.plan.outputs.openclaw_config_sha256 }}",
        "openclaw_builds": "${{ steps.plan.outputs.openclaw_builds }}",
        "t3_build": "${{ steps.plan.outputs.t3_build }}",
    }
    for job in jobs.values():
        for checkout in checkout_steps(job):
            if checkout.get("with", {}).get("repository"):
                assert checkout["with"]["ref"] == (
                    "${{ needs.deployment_plan.outputs.openclaw_setup_commit }}"
                )
            else:
                assert checkout["with"]["ref"] == "${{ github.sha }}"
    selector = step_running(plan, "select_deployment_components.py \"$GITHUB_OUTPUT\"")
    assert "repository_dispatch" in selector["if"]
    assert "schedule" not in selector["if"]


def test_release_input_jobs_read_prod_configuration_without_deploying() -> None:
    jobs = workflow_data()["jobs"]
    configuration_only = {"name": "prod", "deployment": "false"}

    assert jobs["deployment_plan"]["environment"] == configuration_only
    assert jobs["openclaw_build"]["environment"] == configuration_only
    assert jobs["prod_mutation"]["environment"] == "prod"
    assert jobs["maintenance"]["environment"] == "prod"
    assert "environment" not in jobs["validation"]
    assert "environment" not in jobs["t3_build"]


def test_validation_is_path_scoped_and_exhaustive_tooling_is_full_only() -> None:
    job = workflow_data()["jobs"]["validation"]
    fast = {
        "openclaw": step_running(job, "tests/repo/test_openclaw_release.py"),
        "repo": step_running(job, "tests/repo/test_deployment_components.py"),
    }
    for scope, step in fast.items():
        assert step["if"] == f"env.VALIDATION_SCOPE == '{scope}'"
    app_model = step_running(job, "tests/docker/test_homelab_compose.py")
    assert app_model["if"] == (
        "env.VALIDATION_SCOPE == 'apps-model' || env.VALIDATION_SCOPE == 'apps'"
    )
    app_tooling = step_running(job, "tests/repo/test_deploy_compose_release.py")
    assert app_tooling["if"] == "env.VALIDATION_SCOPE == 'apps'"
    assert "test_traefik_config.py" in app_model["run"]
    exhaustive = next(
        step for step in run_steps(job)
        if step["name"] == "Run exhaustive repository tests"
    )
    assert exhaustive["if"] == "env.VALIDATION_SCOPE == 'full'"
    for fragment in ("install-opentofu.sh", "ansible-galaxy", "--syntax-check"):
        assert step_running(job, fragment)["if"] == "env.VALIDATION_SCOPE == 'full'"
    syntax = step_running(job, "--syntax-check")["run"]
    assert "topology.json" in syntax
    for playbook in (
        "bootstrap.yml", "maintenance.yml", "preflight-openclaw-lxc.yml",
        "site.yml", "trust-docker-apps.yml", "trust-openclaw-lxc.yml", "validate.yml",
    ):
        assert playbook in syntax
    assert "hosts.yml" not in workflow_text()
    assert "render_ansible_inventory.py" not in workflow_text()


def test_one_release_job_serializes_all_selected_production_mutations() -> None:
    jobs = workflow_data()["jobs"]
    assert set(jobs) == {
        "deployment_plan", "validation", "openclaw_build", "t3_build",
        "prod_mutation", "maintenance",
    }
    release = jobs["prod_mutation"]
    assert release["needs"] == [
        "deployment_plan", "validation", "openclaw_build", "t3_build"
    ]
    assert "always()" in release["if"]
    assert "needs.validation.result == 'success'" in release["if"]
    assert "needs.openclaw_build.result" in release["if"]
    assert "needs.t3_build.result" in release["if"]
    assert "repository_dispatch" in release["if"]
    assert release["concurrency"] == {
        "group": "prod-mutation",
        "cancel-in-progress": "false",
    }
    assert release["environment"] == "prod"
    assert release["permissions"] == {
        "contents": "read",
        "deployments": "write",
        "id-token": "write",
    }
    for retired in ("provisioning", "openclaw", "apps"):
        assert retired not in jobs

    mutation_steps = {
        "OpenTofu apply": "env.TOFU_SELECTED == 'true'",
        "Bootstrap Proxmox and LXC access": "env.BOOTSTRAP_SELECTED == 'true'",
        "Reconcile bootstrap-owned host configuration": "env.BOOTSTRAP_SELECTED == 'true'",
        "Reconcile isolated tailnet": "env.TAILNET_SELECTED == 'true'",
        "Deploy exact OpenClaw release directly over SSH": "env.OPENCLAW_SELECTED == 'true'",
        "Deploy the only Compose project directly over SSH": "env.APPS_SELECTED == 'true'",
    }
    for name, selector in mutation_steps.items():
        assert selector in step_named(release, name)["if"]

    maintenance = jobs["maintenance"]
    assert maintenance["concurrency"] == release["concurrency"]
    assert maintenance["environment"] == "prod"
    for build in (jobs["openclaw_build"], jobs["t3_build"]):
        assert build.get("concurrency", {}).get("group") != "prod-mutation"


def test_latest_main_gates_bracket_mutations_and_success_watermark() -> None:
    jobs = workflow_data()["jobs"]
    release = jobs["prod_mutation"]
    mutation_gate = step_named(
        release, "Verify exact main revision immediately before release mutations"
    )
    watermark_gate = step_named(
        release, "Reverify exact main revision immediately before release watermark"
    )
    watermark = step_named(release, "Record exact successful prod-release watermark")
    mutation_names = (
        "OpenTofu apply",
        "Bootstrap Proxmox and LXC access",
        "Reconcile bootstrap-owned host configuration",
        "Reconcile isolated tailnet",
        "Deploy exact OpenClaw release directly over SSH",
        "Deploy the only Compose project directly over SSH",
    )

    expected_gate = (
        "git fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main"
    )
    expected_compare = (
        'test "$(git rev-parse refs/remotes/origin/main)" = "$GITHUB_SHA"'
    )
    for gate in (mutation_gate, watermark_gate):
        assert expected_gate in gate["run"]
        assert expected_compare in gate["run"]
    assert mutation_gate["if"] == "env.DEPLOY_COMPONENTS != ''"
    assert watermark_gate["if"] == "github.event_name == 'push'"
    assert watermark["if"] == "github.event_name == 'push'"

    mutation_indexes = [
        release["steps"].index(step_named(release, name)) for name in mutation_names
    ]
    assert release["steps"].index(mutation_gate) < min(mutation_indexes)
    assert max(mutation_indexes) < release["steps"].index(watermark_gate)
    assert release["steps"].index(watermark_gate) + 1 == release["steps"].index(
        watermark
    )

    command = watermark["run"]
    assert 'f"/repos/{repository}/deployments"' in command
    assert '"ref": sha' in command
    assert command.count('"environment": "prod-release"') == 2
    assert '"state": "success"' in command
    assert '"auto_merge": False' in command
    assert '"required_contexts": []' in command

    plan = jobs["deployment_plan"]
    deployed = step_running(plan, '"environment": "prod-release"')
    assert plan["permissions"]["deployments"] == "read"
    assert "statuses[0].get(\"state\") != \"success\"" in deployed["run"]
    assert '["git", "merge-base", "--is-ancestor", sha, current]' in deployed["run"]


def test_openclaw_common_path_is_direct_exact_bundle_deployment() -> None:
    job = workflow_data()["jobs"]["prod_mutation"]
    bundle = step_named(job, "Materialize exact OpenClaw release bundles and manifest")
    deploy = step_named(job, "Deploy exact OpenClaw release directly over SSH")
    assert bundle["if"] == "env.OPENCLAW_SELECTED == 'true'"
    assert deploy["if"] == "env.OPENCLAW_SELECTED == 'true'"
    commands = bundle["run"] + "\n" + deploy["run"]
    for forbidden in ("ansible-playbook", "ansible-galaxy", "tofu", "docker build", "npm "):
        assert forbidden not in commands
    assert "openclaw_release.py bundle" in bundle["run"]
    assert "openclaw_release.py manifest" in bundle["run"]
    assert "deploy-openclaw-via-ssh.sh" in deploy["run"]
    assert "OPENCLAW_LEGACY_RECOVERY_MANIFEST" not in commands
    config_checkouts = [
        step for step in checkout_steps(job)
        if step.get("with", {}).get("repository") == "holybaechu/openclaw-setup"
    ]
    assert len(config_checkouts) == 1
    assert config_checkouts[0]["if"] == "env.OPENCLAW_SELECTED == 'true'"
    assert config_checkouts[0]["with"]["persist-credentials"] == "false"
    assert config_checkouts[0]["with"]["ssh-key"] == (
        "${{ secrets.OPENCLAW_CONFIG_READ_SSH_KEY }}"
    )
    assert "token" not in config_checkouts[0]["with"]
    ssh = step_running(job, "configure-ssh.sh")
    refresh = step_named(job, "Refresh OpenClaw trust after bootstrap")
    assert refresh["if"] == (
        "env.BOOTSTRAP_SELECTED == 'true' && env.OPENCLAW_SELECTED == 'true'"
    )
    assert job["steps"].index(ssh) < job["steps"].index(deploy)


def test_app_common_path_is_one_tool_free_homelab_upload() -> None:
    job = workflow_data()["jobs"]["prod_mutation"]
    deploy = step_named(job, "Deploy the only Compose project directly over SSH")
    assert deploy["if"] == "env.APPS_SELECTED == 'true'"
    assert deploy["run"].count("deploy-compose-via-ssh.sh") == 1
    assert 'deploy-compose-via-ssh.sh "$GITHUB_SHA"' in deploy["run"]
    for obsolete in ("$APPS_PROJECTS", "platform", "media", "code", "migrate-homelab"):
        assert obsolete not in deploy["run"]
    for forbidden in ("setup-python", "pip install", "ansible-playbook", "tofu", "docker build"):
        assert forbidden not in deploy["run"]
    assert set(deploy["env"]) == {
        "DOCKER_APPS_HOST", "RUNTIME_CONFIG_ROOT", "T3_IMAGE_REF", "T3_SOURCE_SHA"
    }
    assert job["steps"].index(step_running(job, "configure-ssh.sh")) < job["steps"].index(deploy)
    refresh = step_named(job, "Refresh application trust after bootstrap")
    assert refresh["if"] == (
        "env.BOOTSTRAP_SELECTED == 'true' && env.APPS_SELECTED == 'true'"
    )
    assert step_named(job, "Set up provisioning Python")["if"] == (
        "env.PROVISIONING_SELECTED == 'true'"
    )
    assert step_named(job, "Install only selected provisioning tooling")["if"] == (
        "env.PROVISIONING_SELECTED == 'true'"
    )


def test_empty_push_can_advance_watermark_without_deployment_tooling() -> None:
    job = workflow_data()["jobs"]["prod_mutation"]
    assert "github.event_name == 'push'" in job["if"]
    assert "needs.deployment_plan.outputs.components != ''" in job["if"]

    deployment_setup = (
        "Connect Tailscale for selected production components",
        "Configure pinned SSH trust before release preparation",
        "Set up provisioning Python",
        "Install only selected provisioning tooling",
        "Install Ansible collections when selected",
        "OpenTofu plan",
        "Materialize all bootstrap-owned runtime inputs",
        "Write isolated tailnet input",
        "Materialize exact OpenClaw release bundles and manifest",
        "Verify exact main revision immediately before release mutations",
    )
    for name in deployment_setup:
        assert step_named(job, name).get("if")

    watermark_gate = step_named(
        job, "Reverify exact main revision immediately before release watermark"
    )
    watermark = step_named(job, "Record exact successful prod-release watermark")
    assert watermark_gate["if"] == "github.event_name == 'push'"
    assert watermark["if"] == "github.event_name == 'push'"
    assert "pip install" not in watermark["run"]
    assert "setup-python" not in watermark["run"]


def test_routine_workflow_has_no_legacy_recovery_gate() -> None:
    text = workflow_text()
    assert "OPENCLAW_LEGACY_RECOVERY_MANIFEST" not in text
    assert "legacy-recovery.json" not in text


def test_image_jobs_use_same_build_metadata_and_minimal_permissions() -> None:
    jobs = workflow_data()["jobs"]
    for name in ("openclaw_build", "t3_build"):
        job = jobs[name]
        assert job["permissions"] == {"contents": "read", "packages": "write"}
        setup = step_named(job, "Configure attestation-capable Buildx")
        assert setup["uses"] == (
            "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e"
        )
        assert job["steps"].index(setup) < job["steps"].index(
            step_running(job, "docker login ghcr.io")
        )
        commands = "\n".join(step["run"] for step in run_steps(job))
        assert "immutable_image_release.py" in commands
        assert "--build-metadata" in commands
        assert "docker login ghcr.io" in commands
        assert "imagetools" not in commands
    assert jobs["t3_build"]["outputs"] == {
        "t3_image_ref": "${{ steps.approve.outputs.t3_image_ref }}",
        "t3_source_sha": "${{ steps.approve.outputs.t3_source_sha }}",
    }


def test_provisioning_owns_host_setup_and_component_secret_materialization() -> None:
    job = workflow_data()["jobs"]["prod_mutation"]
    commands = "\n".join(step["run"] for step in run_steps(job))
    assert "tofu-plan.sh" in commands and "tofu-apply.sh" in commands
    assert "playbooks/bootstrap.yml" in commands
    assert "tailnet,openclaw,apps" in commands
    assert "playbooks/site.yml" in commands
    assert "topology.json" in commands
    writer = step_running(job, "tailnet,openclaw,apps")
    assert writer["if"] == "env.BOOTSTRAP_SELECTED == 'true'"
    expected_secrets = {
        "TAILSCALE_AUTH_KEY", "OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_DISCORD_BOT_TOKEN",
        "OPENCLAW_EXA_API_KEY", "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN",
        "CLOUDFLARE_TRAEFIK_TOKEN", "CLOUDFLARE_DDNS_TOKEN",
        "ADGUARD_ADMIN_PASSWORD", "QBITTORRENT_WEBUI_PASSWORD", "COPYPARTY_USERS_JSON",
    }
    assert expected_secrets.issubset(writer["env"])


def test_schedule_only_maintains_then_exhaustively_validates() -> None:
    job = workflow_data()["jobs"]["maintenance"]
    assert "github.event_name == 'schedule'" in job["if"]
    maintenance = step_running(job, "playbooks/maintenance.yml")
    validate = step_running(job, "playbooks/validate.yml")
    assert job["steps"].index(maintenance) < job["steps"].index(validate)
    commands = "\n".join(step["run"] for step in run_steps(job))
    assert "tofu-plan.sh" not in commands
    assert "select_deployment_components.py" not in commands
    assert "--limit" not in validate["run"]


def test_actions_are_commit_pinned_and_retired_orchestration_is_absent() -> None:
    for job in workflow_data()["jobs"].values():
        for step in job.get("steps", []):
            if action := step.get("uses"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action
    for obsolete in (
        "select-deployment-scope.py", "run-ansible-parallel.sh", "deploy-with-arcane",
        "fence-openclaw", "rebaseline-openclaw", "openclaw-native-watchdog",
        "platform,media,code",
    ):
        assert obsolete not in workflow_text()


def test_configure_ssh_uses_only_pinned_known_hosts() -> None:
    text = (REPO_ROOT / "scripts/ci/configure-ssh.sh").read_text(encoding="utf-8")
    assert "DEPLOY_SSH_PRIVATE_KEY" in text
    assert "DEPLOY_SSH_KNOWN_HOSTS" in text
    assert "ssh-keyscan" not in text
    assert "StrictHostKeyChecking accept-new" not in text
