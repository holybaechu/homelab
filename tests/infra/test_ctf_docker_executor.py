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
    assert "openclaw_ctf_sandbox_skills_root: /var/lib/openclaw/sandbox/skills-workspaces" in all_vars
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
    assert "USER 1000:1000" in dockerfile
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


def test_gateway_uses_credential_scoped_remote_docker_without_a_socket():
    service = read(
        "infra/ansible/roles/openclaw_native/templates/openclaw-gateway.service.j2"
    )
    wrapper = read(
        "infra/ansible/roles/openclaw_native/templates/openclaw-ctf-docker-ssh.j2"
    )
    transport = read("infra/ansible/roles/openclaw_ctf_transport/tasks/main.yml")

    assert "Environment=DOCKER_HOST={{ openclaw_ctf_docker_host }}" in service
    assert "Environment=DOCKER_SSH_COMMAND={{ openclaw_ctf_docker_ssh_wrapper_path }}" in service
    assert "LoadCredential=ctf_docker_client_key:" in service
    assert "LoadCredential=ctf_docker_known_hosts:" in service
    assert "ReadWritePaths={{ openclaw_ctf_workspace_root }}" in service
    assert "/var/run/docker.sock" not in service
    assert "StrictHostKeyChecking=yes" in wrapper
    assert "IdentitiesOnly=yes" in wrapper
    assert "CREDENTIALS_DIRECTORY" in wrapper
    assert "no-port-forwarding" in transport
    assert "docker system dial-stdio" in transport
    assert "/var/run/docker.sock" not in transport


def test_transport_key_is_a_real_newline_terminated_ed25519_authorized_key():
    tasks = yaml.safe_load(
        read("infra/ansible/roles/openclaw_ctf_transport/tasks/main.yml")
    )
    key_type_check = next(
        task
        for task in tasks
        if task["name"] == "Require the Gateway CTF Docker transport public-key type"
    )
    install = next(
        task
        for task in tasks
        if task["name"] == "Install the Gateway-only forced Docker transport key"
    )

    expression = key_type_check["ansible.builtin.assert"]["that"][0]
    content = install["ansible.builtin.copy"]["content"]
    assert r"[^\s]+" in expression
    assert "[:space:]" not in expression
    assert content.endswith("\n")
    assert content.count("docker system dial-stdio") == 1


def test_ctf_executor_deploys_before_gateway_and_transport_links_after_it():
    plays = yaml.safe_load(read("infra/ansible/playbooks/site.yml"))
    names = [play["name"] for play in plays]

    executor_index = names.index("Configure the isolated CTF Docker executor")
    gateway_index = names.index("Stage or activate the dedicated native OpenClaw Gateway")
    transport_index = names.index(
        "Connect the native OpenClaw Gateway to the isolated CTF executor"
    )
    assert executor_index < gateway_index < transport_index
    assert plays[executor_index]["hosts"] == "svc_ctf_executor"
    assert plays[transport_index]["hosts"] == "svc_ctf_executor"
    assert plays[executor_index]["roles"][0]["role"] == "openclaw_ctf_executor"
    assert plays[transport_index]["roles"][0]["role"] == "openclaw_ctf_transport"
