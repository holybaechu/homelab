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
        "CLOUDFLARE_CADDY_TOKEN",
        "PROTON_WIREGUARD_PRIVATE_KEY",
        "HERMES_DISCORD_BOT_TOKEN",
        "COPYPARTY_USERS_JSON",
        "COPYPARTY_PASSWORD_HASH_SALT",
    ):
        assert f"{secret_name}:" not in job_env

    assert "python3 scripts/ci/write_ansible_extra_vars.py" in workflow
    assert "${{ runner.temp }}/ansible-extra-vars.json" in workflow
    assert "ADGUARD_ADMIN_PASSWORD:" in workflow
    assert "COPYPARTY_USERS_JSON:" in workflow
    assert "COPYPARTY_PASSWORD_HASH_SALT:" in workflow
    assert "os.open" in script
    assert "0o600" in script
    assert "password_hash" in script
    assert "copyparty_password_hash_salt" in script
    assert '"password" not in user' in script


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

    assert 'TARGETS="edge:svc_edge dns:svc_dns tailnet:svc_tailnet downloads:svc_downloads files:svc_files minecraft:svc_minecraft hermes:svc_hermes"' in runner
    assert '--limit "${limit}"' in runner
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


def test_cd_workflow_pins_actions_and_tailscale_version():
    workflow = (REPO_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in workflow
    assert "tailscale/github-action@306e68a486fd2350f2bfc3b19fcd143891a4a2d8" in workflow
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in ci
    assert "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405" in ci

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

    assert "prevent_destroy = true" in module
    assert "tofu show -json prod.tfplan" in plan_script
    assert "check_tofu_plan_safe.py" in plan_script
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

    for group in ("svc_edge", "svc_dns", "svc_tailnet", "svc_downloads", "svc_files", "svc_minecraft", "svc_hermes"):
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
