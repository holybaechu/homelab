import os
import runpy
import shutil
import subprocess

import pytest
import yaml

from tests.helpers import REPO_ROOT


def test_cd_workflow_uses_step_scoped_service_secrets_and_extra_vars_script():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    script = (REPO_ROOT / "scripts" / "ci" / "write_ansible_extra_vars.py").read_text(
        encoding="utf-8"
    )

    job_env = workflow.split("    env:", maxsplit=1)[1].split("    steps:", maxsplit=1)[0]
    for secret_name in (
        "PROXMOX_API_TOKEN",
        "DEPLOY_SSH_PRIVATE_KEY",
        "CLOUDFLARE_TRAEFIK_TOKEN",
        "PROTON_WIREGUARD_PRIVATE_KEY",
        "HERMES_DISCORD_BOT_TOKEN",
        "COPYPARTY_USERS_JSON",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_DISCORD_BOT_TOKEN",
    ):
        assert f"{secret_name}:" not in job_env

    assert "python3 scripts/ci/write_ansible_extra_vars.py" in workflow
    assert workflow.index("Validate and write Ansible service secrets") < workflow.index("OpenTofu plan")
    assert "${{ runner.temp }}/ansible-extra-vars.json" in workflow
    assert "ADGUARD_ADMIN_PASSWORD:" in workflow
    assert "COPYPARTY_USERS_JSON:" in workflow
    assert "ARCANE_ENCRYPTION_KEY:" in workflow
    assert "ARCANE_JWT_SECRET:" in workflow
    assert "OPENCLAW_GATEWAY_TOKEN: ${{ secrets.OPENCLAW_GATEWAY_TOKEN }}" in workflow
    assert "OPENCLAW_CTF_GATEWAY_TOKEN" not in workflow
    assert "OPENCLAW_CTF_OPENAI_API_KEY" not in workflow
    assert "OPENCLAW_DISCORD_BOT_TOKEN: ${{ secrets.OPENCLAW_DISCORD_BOT_TOKEN }}" in workflow
    assert "OPENCLAW_DISCORD_ENABLED" not in workflow
    secret_step = workflow.split(
        "- name: Validate and write Ansible service secrets", maxsplit=1
    )[1].split("- name: Prepare one-time lowest-ID cutover", maxsplit=1)[0]
    assert "OPENCLAW_GATEWAY_TOKEN:" in secret_step
    assert "OPENCLAW_DISCORD_BOT_TOKEN:" in secret_step
    assert workflow.count("OPENCLAW_GATEWAY_TOKEN:") == 1
    assert workflow.count("OPENCLAW_DISCORD_BOT_TOKEN:") == 1
    assert "OPENCLAW_GATEWAY_TOKEN must be exactly 64 hexadecimal characters" in script
    assert "OPENCLAW_CTF_GATEWAY_TOKEN" not in script
    assert "OPENCLAW_CTF_OPENAI_API_KEY" not in script
    assert "64 hexadecimal characters" in script
    assert "at least 32 characters" in script
    assert "COPYPARTY_PASSWORD_HASH_SALT:" not in workflow
    assert "os.open" in script
    assert "0o600" in script
    assert "copyparty_password_hash_salt" not in script
    assert '"password" not in user' in script
    assert "must use plaintext password, not password_hash" in script


def test_github_actions_runbook_documents_pinned_ssh_known_hosts_secret():
    runbook = (REPO_ROOT / "docs" / "runbooks" / "github-actions.md").read_text(
        encoding="utf-8"
    )

    assert "DEPLOY_SSH_KNOWN_HOSTS" in runbook
    assert "192.168.0.2,pve,pve.home.hchu.me" in runbook
    assert "ssh_host_ed25519_key.pub" in runbook
    assert "LXC SSH host keys" in runbook


def test_cd_workflow_runs_bootstrap_before_site_deploy():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    bootstrap = workflow.index("infra/ansible/playbooks/bootstrap.yml")
    site = workflow.index("scripts/ci/run-ansible-parallel.sh site")

    assert bootstrap < site


def test_cd_workflow_fail_closes_openclaw_allocation_before_cutover_and_tofu():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    collections = workflow.index("- name: Install Ansible collections")
    core_preflight = workflow.index("- name: Preflight dedicated OpenClaw LXC allocation")
    ctf_preflight = workflow.index("- name: Preflight isolated CTF executor LXC allocation")
    cutover = workflow.index("- name: Prepare one-time lowest-ID cutover")
    plan = workflow.index("- name: OpenTofu plan")
    apply = workflow.index("- name: OpenTofu apply")

    assert collections < core_preflight < ctf_preflight < cutover < plan < apply
    core_preflight_step = workflow[core_preflight:ctf_preflight]
    ctf_preflight_step = workflow[ctf_preflight:].split(
        "- name: Trust Docker application LXC access for identity proof", maxsplit=1
    )[0]
    assert "steps.scope.outputs.deployment_scope == 'full'" in core_preflight_step
    assert "infra/ansible/playbooks/preflight-openclaw-lxc.yml" in core_preflight_step
    assert "PVE_ROOT_DATASTORE_ID: ${{ vars.PVE_ROOT_DATASTORE_ID }}" in core_preflight_step
    assert '"openclaw_root_datastore_id=${PVE_ROOT_DATASTORE_ID}"' in core_preflight_step
    assert "infra/ansible/playbooks/preflight-ctf-executor-lxc.yml" in ctf_preflight_step
    assert "steps.scope.outputs.deployment_scope == 'full'" in ctf_preflight_step
    assert "ANSIBLE_EXTRA_VARS_PATH" not in core_preflight_step
    assert "ANSIBLE_EXTRA_VARS_PATH" not in ctf_preflight_step
    for secret_name in (
        "PROXMOX_API_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_DISCORD_BOT_TOKEN",
        "CLOUDFLARE_TRAEFIK_TOKEN",
    ):
        assert secret_name not in core_preflight_step
        assert secret_name not in ctf_preflight_step


def test_ci_syntax_checks_the_native_openclaw_recovery_playbooks():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "infra/ansible/playbooks/finalize-openclaw-native-cutover.yml "
        "--syntax-check"
    ) in workflow
    assert (
        "infra/ansible/playbooks/rebaseline-openclaw-retained-rollback.yml "
        "--syntax-check"
    ) in workflow


def test_cd_workflow_can_prove_same_sha_workload_identity_without_affecting_pushes():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "verify_workload_identity:" in workflow
    assert "default: false" in workflow
    configure_ssh = workflow.index("- name: Configure SSH")
    install_collections = workflow.index("- name: Install Ansible collections")
    trust = workflow.index("Trust Docker application LXC access for identity proof")
    capture = workflow.index("Capture long-lived workload container identities")
    deploy = workflow.index("- name: Deploy services")
    retire = workflow.index("- name: Retire the archived source pair")
    prove = workflow.index(
        "Prove long-lived workload container identities are unchanged"
    )
    assert configure_ssh < install_collections < trust < capture < deploy < retire < prove

    for step_name in (
        "Trust Docker application LXC access for identity proof",
        "Capture long-lived workload container identities",
        "Prove long-lived workload container identities are unchanged",
    ):
        step = workflow.split(f"- name: {step_name}", maxsplit=1)[1].split(
            "      - name:", maxsplit=1
        )[0]
        assert "github.event_name == 'workflow_dispatch'" in step
        assert "inputs.verify_workload_identity == true" in step
        assert "steps.scope.outputs.deployment_scope == 'full'" in step

    trust_step = workflow.split(
        "- name: Trust Docker application LXC access for identity proof", maxsplit=1
    )[1].split("      - name:", maxsplit=1)[0]
    assert "infra/ansible/playbooks/trust-docker-apps.yml" in trust_step
    trust_playbook = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "trust-docker-apps.yml"
    ).read_text(encoding="utf-8")
    assert "pct" in trust_playbook
    assert "ssh_host_ed25519_key.pub" in trust_playbook
    assert "ansible.builtin.known_hosts" in trust_playbook
    assert "docker_apps_lxc_candidates | length == 1" in trust_playbook
    assert "address.version == 4 and address.is_private" in trust_playbook
    assert "docker_apps_host_key.stdout_lines | length == 1" in trust_playbook
    assert "^ssh-ed25519 " in trust_playbook
    assert "ssh-keygen" in trust_playbook
    assert "docker_apps_known_host_check.stdout_lines" in trust_playbook
    assert "ansible.builtin.shell" not in trust_playbook
    assert "ansible.builtin.raw" not in trust_playbook
    assert "ssh-keyscan" not in trust_playbook

    assert workflow.count("verify-compose-container-identities.sh") == 3
    assert "diff -u \"${WORKLOAD_IDENTITY_BASELINE}\"" in workflow
    assert workflow.count("StrictHostKeyChecking=yes") == 3
    assert workflow.count("UserKnownHostsFile=") == 3
    assert workflow.count("IdentitiesOnly=yes") == 3
    assert workflow.count('-i "${HOME}/.ssh/id_ed25519"') == 3
    assert workflow.count("timeout --signal=TERM --kill-after=10s 60s ssh") == 3
    assert workflow.count("umask 077") >= 2
    assert "ipaddress.ip_address(value)" in workflow
    assert "address.version != 4 or not address.is_private" in workflow
    assert "id: workload_identity_capture" in workflow
    assert "id: workload_identity_proof" in workflow
    assert "always()" in workflow
    assert "steps.workload_identity_capture.outcome == 'success'" in workflow
    assert "Require the requested workload identity proof to pass" in workflow
    assert workflow.count("sh -s -- snapshot platform media code openclaw") == 2
    assert "sh -s -- health platform media code" in workflow
    assert "WORKLOAD_IDENTITY_CAPTURE_OUTCOME:" in workflow
    assert "WORKLOAD_IDENTITY_PROOF_OUTCOME:" in workflow
    cleanup = workflow.split("- name: Remove Ansible extra vars", maxsplit=1)[1].split(
        "      - name:", maxsplit=1
    )[0]
    assert "compose-identities.before.tsv" in cleanup
    assert "compose-identities.after.tsv" in cleanup


def test_cd_workflow_limits_retained_gateway_recovery_to_explicit_manual_dispatch():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    extra_vars_script = (
        REPO_ROOT / "scripts" / "ci" / "write_ansible_extra_vars.py"
    ).read_text(encoding="utf-8")

    approvals = {
        "approve_openclaw_retained_gateway_rebaseline": (
            "openclaw_retained_gateway_rebaseline_approved"
        ),
        "approve_openclaw_retained_gateway_image_pull": (
            "openclaw_retained_gateway_image_pull_approved"
        ),
    }
    dispatch_inputs = workflow.split("  workflow_dispatch:", maxsplit=1)[1].split(
        "  push:", maxsplit=1
    )[0]
    input_lines = dispatch_inputs.splitlines()

    for input_name in approvals:
        input_start = input_lines.index(f"      {input_name}:")
        input_end = next(
            (
                index
                for index, line in enumerate(input_lines[input_start + 1 :], input_start + 1)
                if line.startswith("      ") and not line.startswith("       ")
            ),
            len(input_lines),
        )
        approval_input = "\n".join(input_lines[input_start:input_end])
        assert "default: false" in approval_input
        assert "type: boolean" in approval_input

    bootstrap = workflow.index("infra/ansible/playbooks/bootstrap.yml")
    recovery = workflow.index("- name: Rebaseline retained Docker OpenClaw rollback assets")
    fence = workflow.index("- name: Fence retained Docker OpenClaw before native reconciliation")
    assert bootstrap < recovery < fence

    recovery_step = workflow[recovery:fence]
    assert "github.event_name == 'workflow_dispatch'" in recovery_step
    for input_name in approvals:
        assert f"inputs.{input_name} == true" in recovery_step
    assert "steps.scope.outputs.deployment_scope == 'full'" in recovery_step
    assert (
        "infra/ansible/playbooks/rebaseline-openclaw-retained-rollback.yml"
        in recovery_step
    )
    assert '--extra-vars @"${ANSIBLE_EXTRA_VARS_PATH}"' in recovery_step
    for approval_var in approvals.values():
        assert f"--extra-vars '{{\"{approval_var}\":true}}'" in recovery_step
    recovery_command = recovery_step.split("        run: >-", maxsplit=1)[1]
    for input_name in approvals:
        assert f"inputs.{input_name}" not in recovery_command
        assert f"${{{{ inputs.{input_name} }}}}" not in recovery_command

    # The privileged booleans are supplied only to the dedicated recovery
    # playbook: neither is an ordinary deployment secret nor a push input.
    for approval_var in approvals.values():
        assert approval_var not in extra_vars_script
        assert approval_var not in workflow.replace(recovery_step, "")
    push_trigger = workflow.split("  push:", maxsplit=1)[1].split(
        "permissions:", maxsplit=1
    )[0]
    for input_name in approvals:
        assert input_name not in push_trigger


def test_cd_workflow_fast_tracks_known_workloads_through_arcane():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    selector = (REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py").read_text(
        encoding="utf-8"
    )
    selector_module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ci" / "select-deployment-scope.py")
    )

    assert "fetch-depth: 0" in workflow
    assert "contents: write" in workflow
    assert "select-deployment-scope.py" in workflow
    assert "ANSIBLE_DEPLOYMENT_SCOPE:" in workflow
    assert "infra/ansible/playbooks/trust-docker-apps.yml" in workflow
    assert "steps.scope.outputs.deployment_scope == 'full'" in workflow
    assert 'return "arcane"' in selector
    assert "arcane_projects=" in selector
    assert "arcane_build_projects=" not in selector
    assert "apps/compose/platform/dynamic/routes.yml" in selector_module[
        "FULL_DEPLOYMENT_PATHS"
    ]
    assert selector_module["classify_paths"](
        ["apps/compose/platform/dynamic/routes.yml"]
    ) == "full"
    assert "deploy-with-arcane.py" in workflow
    assert workflow.index("Deploy changed workloads with Arcane") < workflow.index(
        "Install tooling"
    )
    arcane_step = workflow.split(
        "- name: Deploy changed workloads with Arcane", maxsplit=1
    )[1].split("- name: Install tooling", maxsplit=1)[0]
    assert "steps.scope.outputs.deployment_scope == 'arcane'" in arcane_step
    assert "ARCANE_API_KEY" not in arcane_step
    assert "ARCANE_ADMIN_STATIC_API_KEY" not in workflow
    assert "id-token: write" in workflow
    assert "192.168.0.3 arcane.home.hchu.me" in arcane_step
    assert "refs/heads/arcane-deploy" in workflow
    assert workflow.index("Pin the serialized Arcane deployment ref") < workflow.index(
        "Connect Tailscale"
    )
    secret_step = workflow.split(
        "- name: Validate and write Ansible service secrets", maxsplit=1
    )[1].split("- name: Prepare one-time lowest-ID cutover", maxsplit=1)[0]
    assert "steps.scope.outputs.deployment_scope == 'full'" in secret_step
    deploy_step = workflow.split("- name: Deploy services", maxsplit=1)[1].split(
        "- name: Validate services", maxsplit=1
    )[0]
    assert "steps.scope.outputs.deployment_scope == 'full'" in deploy_step


def test_cd_workflow_and_extra_vars_have_no_hermes_secret_dependencies():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    script = (REPO_ROOT / "scripts" / "ci" / "write_ansible_extra_vars.py").read_text(
        encoding="utf-8"
    )

    for removed_name in (
        "HERMES_DISCORD_BOT_TOKEN",
        "HERMES_DISCORD_ALLOWED_USERS",
        "HERMES_DISCORD_HOME_CHANNEL",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
        "HONCHO_API_KEY",
        "OP_SERVICE_ACCOUNT_TOKEN",
    ):
        assert removed_name not in workflow
        assert removed_name not in script


def test_cd_workflow_and_extra_vars_have_no_retired_proton_secret_dependency():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    script = (REPO_ROOT / "scripts" / "ci" / "write_ansible_extra_vars.py").read_text(
        encoding="utf-8"
    )

    assert "PROTON_WIREGUARD_PRIVATE_KEY" not in workflow
    assert "PROTON_WIREGUARD_PRIVATE_KEY" not in script
    assert "proton_wireguard_private_key" not in script


def test_fast_path_trusts_only_the_docker_lxc_via_proxmox():
    playbook = yaml.safe_load(
        (REPO_ROOT / "infra" / "ansible" / "playbooks" / "trust-docker-apps.yml").read_text(
            encoding="utf-8"
        )
    )
    play = playbook[0]
    by_name = {task["name"]: task for task in play["tasks"]}

    assert play["hosts"] == "pve_hosts"
    read_key = by_name["Read the Docker application LXC SSH host key through Proxmox"]
    assert "{{ docker_apps_lxc.vmid }}" in read_key["ansible.builtin.command"]["argv"]
    assert "pve_lxc_access_bootstrap" in play["vars"]["docker_apps_lxc_candidates"]
    select_mapping = by_name["Select the Docker application LXC inventory mapping"]
    assert "docker_apps_lxc_candidates | first" in select_mapping["ansible.builtin.set_fact"]["docker_apps_lxc"]
    assert "/etc/ssh/ssh_host_ed25519_key.pub" in read_key["ansible.builtin.command"]["argv"]
    trust = by_name["Trust the Docker application LXC SSH host key"]
    assert trust["delegate_to"] == "localhost"
    wait = by_name["Wait for the Docker application LXC SSH port"]
    assert wait["delegate_to"] == "localhost"
    assert wait["ansible.builtin.wait_for"]["timeout"] == 60


def test_cd_workflow_parallelizes_service_deploy_and_validate():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "./scripts/ci/run-ansible-parallel.sh site" in workflow
    assert "./scripts/ci/run-ansible-parallel.sh validate" in workflow
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --extra-vars @/tmp/ansible-extra-vars.json" not in workflow
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml" not in workflow


def test_parallel_ansible_runner_derives_service_targets_from_topology():
    runner = (REPO_ROOT / "scripts" / "ci" / "run-ansible-parallel.sh").read_text(
        encoding="utf-8"
    )
    target_renderer = (REPO_ROOT / "scripts" / "ci" / "render_ansible_targets.py").read_text(
        encoding="utf-8"
    )

    assert "scripts/ci/render_ansible_targets.py" in runner
    assert 'TARGETS="edge:svc_edge dns:svc_dns tailnet:svc_tailnet downloads:svc_downloads files:svc_files minecraft:svc_minecraft hermes:svc_hermes"' not in runner
    assert '--limit "${limit}"' in runner
    assert " &" in runner
    assert "wait" in runner
    assert "failed=1" in runner
    assert "load_containers" in target_renderer
    assert 'ANSIBLE_DEPLOYMENT_SCOPE:-full' in runner
    assert 'TARGETS="docker_apps:svc_docker_apps"' in runner
    assert "Arcane scope deploys workloads through Arcane" in runner
    assert 'f"{name}:svc_{name}"' in target_renderer
    assert runner.index('tailnet_entry=""') < runner.index('openclaw_entry=""')
    assert runner.index('ctf_executor_entry=""') < runner.index('openclaw_entry=""')
    tailnet_call = '''target="${tailnet_entry%%:*}"
  limit="${tailnet_entry#*:}"
  if run_foreground_target "${target}" "${limit}" "$@"; then'''
    executor_call = '''target="${ctf_executor_entry%%:*}"
  limit="${ctf_executor_entry#*:}"
  if run_foreground_target "${target}" "${limit}" --tags ctf_executor "$@"; then'''
    gateway_call = '''target="${openclaw_entry%%:*}"
  limit="${openclaw_entry#*:}"
  if run_foreground_target "${target}" "${limit}" --tags openclaw_native "$@"; then'''
    transport_call = '''target="${ctf_executor_entry%%:*}"
  limit="${ctf_executor_entry#*:}"
  if run_foreground_target "${target}" "${limit}" --tags ctf_transport "$@"; then'''
    docker_call = '''target="${required_entry%%:*}"
    limit="${required_entry#*:}"
    if run_foreground_target "${target}" "${limit}" "$@"; then'''

    assert tailnet_call in runner
    assert executor_call in runner
    assert gateway_call in runner
    assert transport_call in runner
    assert docker_call in runner
    assert "ctf_gateway" not in runner
    assert "discord_relay" not in runner
    assert runner.index(tailnet_call) < runner.index(executor_call)
    assert runner.index(executor_call) < runner.index(gateway_call)
    assert runner.index(gateway_call) < runner.index(transport_call)
    assert runner.index(transport_call) < runner.index(docker_call)


def test_parallel_validate_runner_includes_pve_drift_validation_target():
    runner = (REPO_ROOT / "scripts" / "ci" / "run-ansible-parallel.sh").read_text(
        encoding="utf-8"
    )

    assert 'TARGETS="pve:pve_hosts ${TARGETS}"' in runner
    assert 'mode" = "validate"' in runner


def _posix_shell_command(script: str) -> list[str]:
    shell = shutil.which("sh")
    if shell:
        return [shell, script]

    git_shell = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "sh.exe"
    )
    if not os.path.exists(git_shell):
        pytest.skip("POSIX shell is unavailable")
    drive, remainder = os.path.splitdrive(script)
    if not drive:
        pytest.skip("cannot translate test script path for Git sh")
    git_path = f"/{drive[0].lower()}{remainder.replace(os.sep, '/')}"
    return [git_shell, git_path]


def test_parallel_ansible_runner_orders_tailnet_openclaw_docker_then_pve_cleanup():
    script = str(REPO_ROOT / "tests" / "repo" / "test_run_ansible_parallel.sh")
    env = os.environ.copy()
    if os.name == "nt":
        git_root = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"), "Git"
        )
        env["PATH"] = os.pathsep.join(
            [
                os.path.join(git_root, "usr", "bin"),
                os.path.join(git_root, "mingw64", "bin"),
                env["PATH"],
            ]
        )
    result = subprocess.run(
        _posix_shell_command(script),
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert result.stdout.index("tailnet complete") < result.stdout.index(
        "CTF executor complete"
    )
    assert result.stdout.index("CTF executor complete") < result.stdout.index(
        "one Gateway complete"
    )
    assert result.stdout.index("one Gateway complete") < result.stdout.index(
        "CTF transport complete"
    )
    assert result.stdout.index("CTF transport complete") < result.stdout.index(
        "docker complete"
    )
    assert result.stdout.index("docker complete") < result.stdout.index("pve complete")
    assert "timed out after 1 seconds" in result.stdout


def test_cd_workflow_configures_remote_tofu_state():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "TOFU_STATE_BUCKET:" in workflow
    assert "TOFU_STATE_ENDPOINT:" in workflow
    assert "AWS_ACCESS_KEY_ID:" in workflow
    assert "AWS_SECRET_ACCESS_KEY:" in workflow


def test_cd_workflow_serializes_prod_deploys():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "concurrency:" in workflow
    assert "group: cd-prod" in workflow
    assert "cancel-in-progress: false" in workflow


def test_cd_workflow_pins_actions_and_tailscale_version():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7" in workflow
    assert "tailscale/github-action@306e68a486fd2350f2bfc3b19fcd143891a4a2d8 # v4" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7" in ci
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7" in ci

    connect_tailscale = workflow.split("- name: Connect Tailscale", maxsplit=1)[1]
    connect_tailscale = connect_tailscale.split("- name: Install tooling", maxsplit=1)[0]
    assert "version:" in connect_tailscale


def test_cd_workflow_disables_tailscale_dns_acceptance():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    connect_tailscale = workflow.split("- name: Connect Tailscale", maxsplit=1)[1]
    connect_tailscale = connect_tailscale.split("- name: Install tooling", maxsplit=1)[0]

    assert "args: --accept-dns=false" in connect_tailscale


def test_cd_tofu_plan_and_apply_use_generated_variable_file():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    plan_script = (REPO_ROOT / "scripts" / "ci" / "tofu-plan.sh").read_text(
        encoding="utf-8"
    )
    apply_script = (REPO_ROOT / "scripts" / "ci" / "tofu-apply.sh").read_text(
        encoding="utf-8"
    )

    assert "PROXMOX_API_TOKEN:" in workflow
    assert "PROXMOX_INSECURE_TLS:" in workflow
    assert "DEPLOY_SSH_PUBLIC_KEYS:" in workflow
    assert "TF_VAR_proxmox_api_token:" not in workflow
    assert "TF_VAR_ssh_public_keys:" not in workflow
    assert "TF_VAR_proxmox_insecure_tls:" not in workflow
    assert "ci.auto.tfvars.json" in workflow
    assert "PROXMOX_API_TOKEN:" in workflow.split("- name: OpenTofu plan", maxsplit=1)[1].split("- name: OpenTofu apply", maxsplit=1)[0]
    assert "TOFU_STATE_KEY:?set TOFU_STATE_KEY" in plan_script
    assert "ALLOW_EMPTY_STATE_BOOTSTRAP" in plan_script
    assert "tofu state list" in plan_script
    assert "-var=" not in plan_script
    assert "write_tofu_vars.py" in plan_script
    assert "terraform.tfvars.example" not in plan_script
    assert "tofu plan -input=false -out=prod.tfplan" in plan_script
    assert "test -f ci.auto.tfvars.json" in apply_script
    assert "tofu apply -input=false -auto-approve prod.tfplan" in apply_script


def test_cd_workflow_only_deploys_prod_from_main_and_infra_paths():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "jobs:\n  deploy:\n    if: github.ref == 'refs/heads/main'" in workflow
    assert "paths:" in workflow
    assert "infra/**" in workflow
    assert "apps/**" in workflow
    assert "docs/**" not in workflow.split("push:", maxsplit=1)[1].split("permissions:", maxsplit=1)[0]


def test_tofu_apply_is_guarded_against_destroying_lxcs():
    module = (
        REPO_ROOT / "infra" / "opentofu" / "modules" / "pve-lxc" / "main.tf"
    ).read_text(encoding="utf-8")
    plan_script = (REPO_ROOT / "scripts" / "ci" / "tofu-plan.sh").read_text(
        encoding="utf-8"
    )
    guard_script = (REPO_ROOT / "scripts" / "ci" / "check_tofu_plan_safe.py").read_text(
        encoding="utf-8"
    )

    assert "prevent_destroy = true" not in module
    assert "tofu show -json prod.tfplan" in plan_script
    assert "check_tofu_plan_safe.py" in plan_script
    assert "APPROVED_LOW_ID_TARGETS" in guard_script
    assert "docker_apps" in guard_script and ": 110" in guard_script
    assert "tailnet" in guard_script and ": 111" in guard_script
    assert "ALLOW_TOFU_DESTROY" in guard_script
    assert "ALLOW_EMPTY_STATE_BOOTSTRAP" in guard_script
    assert '"delete" in actions' in guard_script
    assert "create-only" in guard_script


def test_cd_workflow_does_not_plan_one_time_hermes_lxc_replacement():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    plan_script = (REPO_ROOT / "scripts" / "ci" / "tofu-plan.sh").read_text(
        encoding="utf-8"
    )

    assert "rebuild_hermes_lxc" not in workflow
    assert "REBUILD_HERMES_LXC" not in workflow
    assert 'module.lxc["hermes"].proxmox_virtual_environment_container.this' not in plan_script
    assert "-replace=" not in plan_script


def test_generated_tofu_secret_variable_files_are_ignored_and_topology_is_tracked():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "*.tfvars" in gitignore
    assert "*.tfvars.json" in gitignore
    assert "!infra/opentofu/envs/prod/containers.auto.tfvars" in gitignore


def test_tracked_tofu_container_topology_does_not_include_secrets():
    topology = (
        REPO_ROOT / "infra" / "opentofu" / "envs" / "prod" / "containers.auto.tfvars"
    ).read_text(encoding="utf-8")

    forbidden = (
        "proxmox_api_token",
        "proxmox_endpoint",
        "ssh_public_keys",
        "PRIVATE KEY",
        "PVEAPIToken",
    )
    for marker in forbidden:
        assert marker not in topology


def test_ci_workflow_exists_for_pre_deploy_checks():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "pytest" in workflow
    assert "tofu validate" in workflow
    assert "ansible-playbook" in workflow


def test_inventory_does_not_store_qbittorrent_password():
    inventory = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars"
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in inventory.glob("*.yml"))

    assert "qbittorrent_webui_password:" not in text


def test_configure_ssh_uses_pinned_known_hosts_without_keyscan():
    script = (REPO_ROOT / "scripts" / "ci" / "configure-ssh.sh").read_text(encoding="utf-8")

    assert "DEPLOY_SSH_KNOWN_HOSTS" in script
    assert "ssh-keyscan" not in script
    assert "ssh-keygen -l -f" in script


def test_install_opentofu_is_shared_and_verifies_checksums():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    install_tools = (REPO_ROOT / "scripts" / "ci" / "install-tools.sh").read_text(encoding="utf-8")
    install_tofu = (REPO_ROOT / "scripts" / "ci" / "install-opentofu.sh").read_text(encoding="utf-8")

    assert "./scripts/ci/install-opentofu.sh" in ci
    assert "./scripts/ci/install-opentofu.sh" in install_tools
    assert "SHA256SUMS" in install_tofu
    assert "sha256sum -c" in install_tofu
    assert ".opentofu-version" in install_tofu


def test_inventory_uses_service_group_names_to_avoid_host_group_warnings():
    inventory = (REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "hosts.yml").read_text(encoding="utf-8")
    site = (REPO_ROOT / "infra" / "ansible" / "playbooks" / "site.yml").read_text(encoding="utf-8")

    for group in ("svc_tailnet", "svc_openclaw", "svc_docker_apps"):
        assert f"    {group}:" in inventory
        assert f"hosts: {group}" in site
    for old_group in ("edge", "dns", "downloads", "hermes", "minecraft", "tailnet", "files"):
        assert f"    {old_group}:\n      hosts:" not in inventory


def test_ansible_inventory_can_be_rendered_from_tofu_topology():
    script = (REPO_ROOT / "scripts" / "ci" / "render_ansible_inventory.py").read_text(encoding="utf-8")

    assert "containers.auto.tfvars" in script
    assert "svc_" in script
    assert "--check" in script


def test_cd_workflow_requires_pinned_ssh_known_hosts_secret():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")

    configure_ssh = workflow.split("- name: Configure SSH", maxsplit=1)[1].split("- name: OpenTofu plan", maxsplit=1)[0]
    assert "DEPLOY_SSH_KNOWN_HOSTS:" in configure_ssh
    assert "secrets.DEPLOY_SSH_KNOWN_HOSTS" in configure_ssh
