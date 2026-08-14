from pathlib import Path

import yaml

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_ctf_executor_is_a_dedicated_unprivileged_lxc_before_the_gateway():
    topology = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")

    assert "ctf_executor = {" in topology
    assert "vmid             = 119" in topology
    assert 'hostname         = "ctf-executor"' in topology
    assert 'ip_address       = "192.168.0.6/24"' in topology
    assert "root_disk_gb     = 64" in topology
    assert "cores            = 4" in topology
    assert "memory_mb        = 8192" in topology
    assert "swap_mb          = 2048" in topology
    assert "startup_order    = 3" in topology
    assert "openclaw_ctf_shared_host_path" in all_vars
    assert "openclaw_ctf_sandbox_skills_host_path" in all_vars
    assert "openclaw_ctf_workspace_root: /srv/openclaw-ctf" in all_vars
    assert (
        "openclaw_ctf_sandbox_skills_root: "
        "/var/lib/openclaw-ctf/sandbox/skills-workspaces"
    ) in all_vars
    assert "openclaw_ctf_uid: 1001" in all_vars
    assert "openclaw_ctf_gid: 1001" in all_vars
    assert "openclaw_ctf_docker_network: openclaw-ctf" in all_vars
    assert "openclaw_ctf_docker_network_cidr: 172.30.0.0/24" in all_vars
    assert "ctf_executor_lxc_allocation:" in all_vars
    assert "required_storage_gb: 64" in all_vars

    preflight = read("infra/ansible/playbooks/preflight-ctf-executor-lxc.yml")
    assert "preflight-openclaw-lxc.py" in preflight
    assert "ctf_executor_lxc_allocation.vmid" in preflight
    assert "ctf_executor_lxc_allocation.required_storage_gb" in preflight
    assert "--role-tag role-ctf-executor" in preflight
    assert "--required-feature nesting" in preflight
    assert "--required-feature keyctl" in preflight
    assert "--expected-bind-mount" in preflight
    assert "openclaw_ctf_sandbox_skills_host_path" in preflight
    assert "--allow-missing-expected-bind-mounts" in preflight


def test_only_the_ctf_executor_gets_docker_lxc_features_and_the_ctf_mount():
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")
    openclaw = all_vars.split("  - vmid: 118", 1)[1].split("  - vmid: 119", 1)[0]
    executor = all_vars.split("  - vmid: 119", 1)[1].split(
        "pve_lxc_access_bootstrap:", 1
    )[0]

    assert "bind_mount_source_owner" in openclaw
    assert "openclaw_ctf_uid" in openclaw
    assert "openclaw_ctf_gid" in openclaw
    assert "bind_mount_source_mode: \"0700\"" in openclaw
    assert "-mp0 {{ openclaw_ctf_shared_host_path }},mp={{ openclaw_ctf_workspace_root }}" in openclaw
    assert "-mp1 {{ openclaw_ctf_sandbox_skills_host_path }},mp={{ openclaw_ctf_sandbox_skills_root }}" in openclaw
    assert "nesting or keyctl features" in openclaw
    assert "nesting=1" not in openclaw
    assert "enable nesting for the isolated CTF Docker executor" in executor
    assert "enable keyctl for the isolated CTF Docker executor" in executor
    assert "-mp0 {{ openclaw_ctf_shared_host_path }},mp={{ openclaw_ctf_workspace_root }}" in executor
    assert "-mp1 {{ openclaw_ctf_sandbox_skills_host_path }},mp={{ openclaw_ctf_sandbox_skills_root }}" in executor
    assert "TUN device passthrough" in executor


def test_ctf_executor_role_builds_a_nonroot_kali_image_without_socket_mounts():
    role_root = REPO_ROOT / "infra/ansible/roles/openclaw_ctf_executor"
    tasks = read("infra/ansible/roles/openclaw_ctf_executor/tasks/main.yml")
    dockerfile = (role_root / "files/Dockerfile").read_text(encoding="utf-8")
    firewall = (role_root / "templates/openclaw-ctf-docker-firewall.sh.j2").read_text(
        encoding="utf-8"
    )
    daemon = (role_root / "templates/daemon.json.j2").read_text(encoding="utf-8")

    assert "docker-ce" in tasks
    assert "docker-ce-cli" in tasks
    assert "openclaw_ctf_docker_network" in tasks
    assert "openclaw_ctf_sandbox_skills_root" in tasks
    assert "com.docker.network.bridge.enable_icc=false" in tasks
    assert "docker.sock" not in tasks
    assert "FROM kalilinux/kali-rolling" in dockerfile
    for package in (
        "python3",
        "git",
        "curl",
        "gdb",
        "gdb-peda",
        "python3-pwntools",
        "binutils",
        "file",
        "libimage-exiftool-perl",
        "binwalk",
        "tshark",
        "nmap",
    ):
        assert package in dockerfile
    assert "USER 1001:1001" in dockerfile
    assert "docker-ce" not in dockerfile
    assert "docker.io" not in dockerfile
    assert "/var/run/docker.sock" not in dockerfile
    for denied_cidr in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ):
        assert denied_cidr in firewall
    assert "DOCKER-USER" in firewall
    assert "OPENCLAW_CTF_INPUT" in firewall
    assert '"1.1.1.1"' in daemon
    assert '"1.0.0.1"' in daemon


def test_ctf_gateway_is_a_separate_uid_scoped_to_the_ctf_config_and_workspace():
    tasks = yaml.safe_load(
        read("infra/ansible/roles/openclaw_ctf_gateway/tasks/main.yml")
    )
    service = read(
        "infra/ansible/roles/openclaw_ctf_gateway/"
        "templates/openclaw-ctf-gateway.service.j2"
    )
    contract = next(
        task
        for task in tasks
        if task["name"] == "Require the isolated OpenClaw CTF Gateway contract"
    )
    assertions = contract["ansible.builtin.assert"]["that"]

    for required in (
        "openclaw_ctf_user == 'openclaw-ctf'",
        "openclaw_ctf_group == 'openclaw-ctf'",
        "openclaw_ctf_uid | int == 1001",
        "openclaw_ctf_gid | int == 1001",
        "openclaw_ctf_gateway_port | int == 19789",
        "openclaw_ctf_workspace_root == '/srv/openclaw-ctf'",
        "openclaw_ctf_sandbox_skills_root == openclaw_ctf_state_root + '/sandbox/skills-workspaces'",
            "openclaw_ctf_codex_profile_id == 'openai:ctf'",
            "openclaw_ctf_codex_model == 'openai/gpt-5.5'",
            "openclaw_ctf_codex_plugin_spec == 'npm:@openclaw/codex@2026.7.1-1'",
    ):
        assert required in assertions

    config_contract = next(
        task
        for task in tasks
        if task["name"] == "Require CTF-only Gateway config and narrow relay route"
    )
    config_assertions = " ".join(config_contract["ansible.builtin.assert"]["that"])
    for required in (
        "ctf_gateway_token_file",
        "auth.order.openai == [openclaw_ctf_codex_profile_id]",
        "models.providers.openai ==",
        "'agentRuntime': {'id': 'codex'}",
        "agents.list[0].model.primary == openclaw_ctf_codex_model",
        "agents.list | map(attribute='id') | list == ['ctf']",
        "sandbox.backend == 'docker'",
        "sandbox.scope == 'session'",
        "sandbox.docker.user == (openclaw_ctf_uid | string) + ':' + (openclaw_ctf_gid | string)",
        "get('channels', {}) | length == 0",
        "get('bindings', []) | length == 0",
        "tools.alsoAllow == ['ctf_publish']",
        "tools.sandbox.tools.allow",
        "openclaw-discord-relay-ctf",
        "plugins.entries.codex.enabled",
    ):
        assert required in config_assertions
    assert "no OpenAI API key" in config_contract["ansible.builtin.assert"]["fail_msg"]

    assert (
        "ExecStartPre=/usr/local/libexec/materialize-openclaw-credential --owner "
        "{{ openclaw_ctf_uid }}:{{ openclaw_ctf_gid }} "
        "%d/openclaw_ctf_gateway_token /run/openclaw-ctf-gateway/gateway_token"
    ) in service
    assert "OPENCLAW_CTF_OPENAI_API_KEY_FILE" not in service
    assert "LoadCredential=ctf_openai_api_key:" not in service
    assert "openclaw.plugin.json" in read(
        "infra/ansible/roles/openclaw_ctf_gateway/tasks/main.yml"
    )
    assert "contracts.tools == ['ctf_publish']" in read(
        "infra/ansible/roles/openclaw_ctf_gateway/tasks/main.yml"
    )
    validation_block = next(
        task
        for task in tasks
        if task["name"] == "Validate the isolated CTF config with a service-user token"
    )
    install = next(
        task
        for task in validation_block["block"]
        if task["name"] == "Install the pinned CTF Codex harness"
    )
    assert install["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "install",
        "{{ openclaw_ctf_codex_plugin_spec }}",
        "--pin",
        "--force",
    ]
    install_environment = install["environment"]
    assert install_environment["OPENCLAW_CONFIG_PATH"] == (
        "{{ openclaw_ctf_validation_credential_dir.path }}"
        "/plugin-install-config.json"
    )
    install_config = next(
        task
        for task in validation_block["block"]
        if task["name"] == "Materialize a writable CTF plugin-install config"
    )
    assert install_config["ansible.builtin.copy"]["dest"] == (
        "{{ openclaw_ctf_validation_credential_dir.path }}"
        "/plugin-install-config.json"
    )
    config_validation = next(
        task
        for task in validation_block["block"]
        if task["name"] == "Validate the CTF-only OpenClaw config schema"
    )
    assert config_validation["environment"]["OPENCLAW_CONFIG_PATH"] == (
        "{{ openclaw_ctf_gateway_config_path }}"
    )
    codex_runtime_check = next(
        task
        for task in validation_block["block"]
        if task["name"] == "Inspect the CTF Codex harness runtime before startup"
    )
    assert codex_runtime_check["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "inspect",
        "codex",
        "--runtime",
        "--json",
    ]
    assert ".plugin.id != 'codex'" in codex_runtime_check["failed_when"]
    runtime_check = next(
        task
        for task in validation_block["block"]
        if task["name"] == "Inspect the CTF relay plugin runtime before startup"
    )
    assert runtime_check["ansible.builtin.command"]["argv"][-5:] == [
        "plugins",
        "inspect",
        "openclaw-discord-relay-ctf",
        "--runtime",
        "--json",
    ]
    assert runtime_check["become_user"] == "{{ openclaw_ctf_user }}"
    assert ".plugin.id != 'openclaw-discord-relay-ctf'" in runtime_check["failed_when"]
    assert ".plugin.status != 'loaded'" in runtime_check["failed_when"]
    assert "ctf_publish" in runtime_check["failed_when"]
    assert runtime_check["no_log"] is True


def test_only_the_ctf_gateway_uses_credential_scoped_remote_docker_without_a_socket():
    core_service = read(
        "infra/ansible/roles/openclaw_native/templates/openclaw-gateway.service.j2"
    )
    ctf_service = read(
        "infra/ansible/roles/openclaw_ctf_gateway/"
        "templates/openclaw-ctf-gateway.service.j2"
    )
    wrapper = read(
        "infra/ansible/roles/openclaw_ctf_gateway/"
        "templates/openclaw-ctf-docker-ssh.j2"
    )
    transport = read("infra/ansible/roles/openclaw_ctf_transport/tasks/main.yml")

    for forbidden in (
        "Environment=DOCKER_HOST=",
        "Environment=DOCKER_SSH_COMMAND=",
        "LoadCredential=ctf_docker_client_key:",
        "LoadCredential=ctf_docker_known_hosts:",
        "ReadWritePaths={{ openclaw_ctf_workspace_root }}",
        "LoadCredential=discord_bot_token:",
        "OPENAI_API_KEY=",
    ):
        assert forbidden not in core_service
    assert "InaccessiblePaths={{ openclaw_ctf_workspace_root }}" in core_service
    assert "InaccessiblePaths={{ openclaw_ctf_state_root }}" in core_service
    assert "InaccessiblePaths={{ openclaw_ctf_home }}" in core_service

    for required in (
        "User={{ openclaw_ctf_user }}",
        "Group={{ openclaw_ctf_group }}",
        "Environment=DOCKER_HOST={{ openclaw_ctf_docker_host }}",
        "Environment=DOCKER_SSH_COMMAND={{ openclaw_ctf_docker_ssh_wrapper_path }}",
        "LoadCredential=ctf_docker_client_key:",
        "LoadCredential=ctf_docker_known_hosts:",
        "ReadWritePaths={{ openclaw_ctf_workspace_root }}",
    ):
        assert required in ctf_service
    assert "LoadCredential=discord_bot_token:" not in ctf_service
    assert "/var/run/docker.sock" not in core_service
    assert "/var/run/docker.sock" not in ctf_service
    assert "StrictHostKeyChecking=yes" in wrapper
    assert "IdentitiesOnly=yes" in wrapper
    assert "CREDENTIALS_DIRECTORY" in wrapper
    assert "no-port-forwarding" in transport
    assert "docker system dial-stdio" in transport
    assert "/var/run/docker.sock" not in transport
    assert "openclaw_ctf_docker_cli_path" in transport
    assert "openclaw_ctf_user" in transport


def test_transport_key_is_a_real_newline_terminated_ed25519_authorized_key():
    tasks = yaml.safe_load(
        read("infra/ansible/roles/openclaw_ctf_transport/tasks/main.yml")
    )
    key_type_check = next(
        task
        for task in tasks
        if task["name"]
        == "Require the isolated CTF Gateway Docker transport public-key type"
    )
    install = next(
        task
        for task in tasks
        if task["name"] == "Install the CTF-Gateway-only forced Docker transport key"
    )

    expression = key_type_check["ansible.builtin.assert"]["that"][0]
    content = install["ansible.builtin.copy"]["content"]
    assert r"[^\s]+" in expression
    assert "[:space:]" not in expression
    assert content.endswith("\n")
    assert content.count("docker system dial-stdio") == 1


def test_ctf_executor_transport_precedes_the_discord_relay():
    plays = yaml.safe_load(read("infra/ansible/playbooks/site.yml"))
    names = [play["name"] for play in plays]

    executor_index = names.index("Configure the isolated CTF Docker executor")
    gateway_index = names.index(
        "Stage or activate the separated core and CTF OpenClaw Gateways"
    )
    transport_index = names.index(
        "Connect the isolated CTF Gateway to the isolated CTF executor"
    )
    relay_index = names.index(
        "Configure the sole Discord ingress relay after both Gateways are ready"
    )
    assert executor_index < gateway_index < transport_index < relay_index
    assert plays[executor_index]["hosts"] == "svc_ctf_executor"
    assert plays[gateway_index]["hosts"] == "svc_openclaw"
    assert plays[transport_index]["hosts"] == "svc_ctf_executor"
    assert plays[relay_index]["hosts"] == "svc_openclaw"
    assert plays[executor_index]["roles"][0]["role"] == "openclaw_ctf_executor"
    assert [role["role"] for role in plays[gateway_index]["roles"]] == [
        "openclaw_native",
        "openclaw_ctf_gateway",
    ]
    assert plays[transport_index]["roles"][0]["role"] == "openclaw_ctf_transport"
    assert plays[relay_index]["roles"][0]["role"] == "openclaw_discord_relay"
