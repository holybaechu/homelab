from pathlib import Path

import yaml

from tests.helpers import REPO_ROOT


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_ctf_executor_remains_a_dedicated_unprivileged_lxc():
    topology = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")

    assert "ctf_executor = {" in topology
    assert 'hostname         = "ctf-executor"' in topology
    assert "vmid             = 119" in topology
    assert "startup_order    = 3" in topology
    assert "openclaw_ctf_workspace_root: /srv/openclaw-ctf" in all_vars
    assert "openclaw_ctf_sandbox_skills_root: /var/lib/openclaw/sandbox/skills-workspaces" in all_vars
    assert 'openclaw_ctf_uid: "{{ service_uid }}"' in all_vars
    assert 'openclaw_ctf_gid: "{{ service_gid }}"' in all_vars
    assert "openclaw_ctf_docker_network: openclaw-ctf" in all_vars


def test_executor_keeps_its_hardened_docker_and_network_contract():
    role_root = REPO_ROOT / "infra/ansible/roles/openclaw_ctf_executor"
    tasks = read("infra/ansible/roles/openclaw_ctf_executor/tasks/main.yml")
    dockerfile = (role_root / "files/Dockerfile").read_text(encoding="utf-8")
    firewall = (role_root / "templates/openclaw-ctf-docker-firewall.sh.j2").read_text(
        encoding="utf-8"
    )

    assert "docker-ce" in tasks
    assert "docker-ce-cli" in tasks
    assert "com.docker.network.bridge.enable_icc=false" in tasks
    assert "/var/run/docker.sock" not in dockerfile
    assert "FROM kalilinux/kali-rolling" in dockerfile
    assert "USER 1001:1001" in dockerfile
    assert "docker system dial-stdio" in read(
        "infra/ansible/roles/openclaw_ctf_executor/templates/60-openclaw-ctf-docker.conf.j2"
    )
    for denied_cidr in ("10.0.0.0/8", "100.64.0.0/10", "172.16.0.0/12", "192.168.0.0/16"):
        assert denied_cidr in firewall


def test_one_gateway_is_the_only_remote_docker_client_and_blocks_local_sockets():
    service = read("infra/ansible/roles/openclaw_native/templates/openclaw-gateway.service.j2")
    wrapper = read("infra/ansible/roles/openclaw_native/templates/openclaw-ctf-docker-ssh.j2")
    transport = read("infra/ansible/roles/openclaw_ctf_transport/tasks/main.yml")

    for required in (
        "Environment=DOCKER_HOST={{ openclaw_ctf_docker_host }}",
        "Environment=DOCKER_SSH_COMMAND={{ openclaw_ctf_docker_ssh_wrapper_path }}",
        "LoadCredential=ctf_docker_client_key:",
        "LoadCredential=ctf_docker_known_hosts:",
        "InaccessiblePaths=/run/docker.sock",
        "InaccessiblePaths=/var/run/docker.sock",
        "ReadWritePaths={{ openclaw_ctf_workspace_root }}",
    ):
        assert required in service
    for required in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "CREDENTIALS_DIRECTORY"):
        assert required in wrapper
    assert "docker system dial-stdio" in transport
    assert "no-port-forwarding" in transport
    assert "openclaw_ctf_user" not in transport
    assert "openclaw-gateway.service" in read(
        "infra/ansible/roles/openclaw_ctf_transport/handlers/main.yml"
    )


def test_one_gateway_limit_is_explicitly_documented_and_not_overclaimed():
    runbook = read("docs/runbooks/openclaw-ctf.md")

    assert "DOCKER_HOST" in runbook
    assert "DOCKER_SSH_COMMAND" in runbook
    assert "process-scoped, not a hard\nper-agent credential boundary" in runbook
    assert "host-exec-capable agent could never\nreach the capability" in runbook
    assert "separate Gateway/process" in runbook


def test_executor_gateway_transport_order_has_no_split_gateway_or_relay():
    plays = yaml.safe_load(read("infra/ansible/playbooks/site.yml"))
    names = [play["name"] for play in plays]

    executor_index = names.index("Configure the isolated CTF Docker executor")
    gateway_index = names.index("Stage or activate the dedicated native OpenClaw Gateway")
    transport_index = names.index("Connect the native OpenClaw Gateway to the isolated CTF executor")
    assert executor_index < gateway_index < transport_index
    roles = [
        role["role"]
        for play in plays
        for role in play.get("roles", [])
        if isinstance(role, dict) and "role" in role
    ]
    assert "openclaw_ctf_executor" in roles
    assert "openclaw_ctf_transport" in roles
    assert "openclaw_ctf_gateway" not in roles
    assert "openclaw_discord_relay" not in roles
