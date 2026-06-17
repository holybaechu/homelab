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
    site = workflow.index("infra/ansible/playbooks/site.yml")

    assert bootstrap < site


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
    assert "tofu plan -out=prod.tfplan" in plan_script
    assert "test -f ci.auto.tfvars.json" in apply_script
    assert "tofu apply -auto-approve prod.tfplan" in apply_script


def test_generated_tofu_secret_variable_files_are_ignored():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "*.tfvars.json" in gitignore


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
