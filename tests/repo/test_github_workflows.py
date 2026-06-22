from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cd_workflow_uses_plaintext_adguard_secret_and_copyparty_json():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "ADGUARD_ADMIN_PASSWORD:" in workflow
    assert "ADGUARD_ADMIN_USERNAME:" in workflow
    assert "ADGUARD_ADMIN_PASSWORD_HASH" not in workflow
    assert "COPYPARTY_USERS_JSON:" in workflow
    assert 'adguard_admin_username = os.environ.get("ADGUARD_ADMIN_USERNAME")' in workflow
    assert 'mapping["adguard_admin_username"] = adguard_admin_username' in workflow
    assert '"adguard_admin_password": os.environ["ADGUARD_ADMIN_PASSWORD"]' in workflow
    assert '"copyparty_users": json.loads(os.environ["COPYPARTY_USERS_JSON"])' in workflow


def test_cd_workflow_runs_bootstrap_before_site_deploy():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    bootstrap = workflow.index("infra/ansible/playbooks/bootstrap.yml")
    site = workflow.index("scripts/ci/run-ansible-parallel.sh site")

    assert bootstrap < site


def test_cd_workflow_parallelizes_service_deploy_and_validate():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "./scripts/ci/run-ansible-parallel.sh site" in workflow
    assert "./scripts/ci/run-ansible-parallel.sh validate" in workflow
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --extra-vars @/tmp/ansible-extra-vars.json" not in workflow
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml" not in workflow


def test_parallel_ansible_runner_limits_each_service_and_waits_for_failures():
    runner = (REPO_ROOT / "scripts" / "ci" / "run-ansible-parallel.sh").read_text(
        encoding="utf-8"
    )

    assert 'TARGETS="edge dns tailnet downloads files minecraft hermes"' in runner
    assert '--limit "${target}"' in runner
    assert " &" in runner
    assert "wait" in runner
    assert "failed=1" in runner


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


def test_cd_workflow_does_not_pin_tailscale_version():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    connect_tailscale = workflow.split("- name: Connect Tailscale", maxsplit=1)[1]
    connect_tailscale = connect_tailscale.split("- name: Install tooling", maxsplit=1)[0]

    assert "version:" not in connect_tailscale


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
    assert "-var=" not in plan_script
    assert "write_tofu_vars.py" in plan_script
    assert "terraform.tfvars.example" not in plan_script
    assert "tofu plan -input=false -out=prod.tfplan" in plan_script
    assert "test -f ci.auto.tfvars.json" in apply_script
    assert "tofu apply -input=false -auto-approve prod.tfplan" in apply_script


def test_cd_workflow_only_deploys_prod_from_main():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "jobs:\n  deploy:\n    if: github.ref == 'refs/heads/main'" in workflow


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

    assert "prevent_destroy = true" in module
    assert "tofu show -json prod.tfplan" in plan_script
    assert "check_tofu_plan_safe.py" in plan_script
    assert "ALLOW_TOFU_DESTROY" in guard_script
    assert '"delete" in actions' in guard_script


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
