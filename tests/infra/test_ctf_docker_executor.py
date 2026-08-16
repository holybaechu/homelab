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
    assert "openclaw_ctf_workspace_root: /var/lib/openclaw/workspaces/ctf" in all_vars
    assert "openclaw_ctf_sandbox_skills_root: /var/lib/openclaw/sandbox/skills-workspaces" in all_vars
    assert 'openclaw_ctf_uid: "{{ service_uid }}"' in all_vars
    assert 'openclaw_ctf_gid: "{{ service_gid }}"' in all_vars
    assert "openclaw_ctf_docker_network: openclaw-ctf" in all_vars


def test_executor_validation_reports_only_nonsecret_contract_metadata_on_failure():
    validation = read("infra/ansible/playbooks/validate.yml")

    assert (
        "hostname=%s uid=%s gid=%s docker_active=%s docker_enabled=%s "
        "socket=%s workspace=%s skills=%s"
    ) in validation
    assert "stat -c '%u:%g %a' '{{ openclaw_ctf_workspace_root }}'" in validation
    assert "stat -c '%u:%g %a' '{{ openclaw_ctf_sandbox_skills_root }}'" in validation


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


def test_forced_docker_transport_key_is_root_managed_but_ssh_readable():
    tasks = yaml.safe_load(
        read("infra/ansible/roles/openclaw_ctf_executor/tasks/main.yml")
    )
    home_task_definition = next(
        task
        for task in tasks
        if task["name"]
        == "Lock down the CTF Docker transport user's home and authorized-key directory"
    )
    home_task = home_task_definition["ansible.builtin.file"]
    authorized_keys_task = next(
        task
        for task in tasks
        if task["name"] == "Reserve the root-managed CTF Docker transport authorized-keys file"
    )["ansible.builtin.file"]

    assert home_task["owner"] == "root"
    assert home_task["group"] == "{{ openclaw_ctf_docker_user }}"
    assert home_task_definition["loop"] == [
        {"path": "/var/lib/{{ openclaw_ctf_docker_user }}", "mode": "0750"},
        {
            "path": "/var/lib/{{ openclaw_ctf_docker_user }}/.ssh",
            "mode": "0750",
        },
    ]
    assert authorized_keys_task["owner"] == "root"
    assert authorized_keys_task["group"] == "{{ openclaw_ctf_docker_user }}"
    assert authorized_keys_task["mode"] == "0640"

    validation = read("infra/ansible/playbooks/validate.yml")
    assert "root:{{ openclaw_ctf_docker_user }} 750" in validation
    assert "root:{{ openclaw_ctf_docker_user }} 640" in validation
    assert (
        "/usr/sbin/runuser -u {{ openclaw_ctf_docker_user }} -- /usr/bin/test -r"
        in validation
    )
    assert (
        "! /usr/sbin/runuser -u {{ openclaw_ctf_docker_user }} -- /usr/bin/test -w"
        in validation
    )


def test_one_gateway_is_the_only_remote_docker_client_and_blocks_local_sockets():
    service = read("infra/ansible/roles/openclaw_native/templates/openclaw-gateway.service.j2")
    wrapper = read("infra/ansible/roles/openclaw_native/templates/openclaw-ctf-docker-ssh.j2")
    transport = read("infra/ansible/roles/openclaw_ctf_transport/tasks/main.yml")
    transport_tasks = yaml.safe_load(transport)

    for required in (
        "Environment=DOCKER_HOST={{ openclaw_ctf_docker_host }}",
        "Environment=PATH={{ openclaw_ctf_docker_ssh_shim_dir }}:{{ openclaw_node_current_root }}/bin:{{ openclaw_current_root }}/bin:/usr/local/bin:/usr/bin:/bin",
        "LoadCredential=ctf_docker_client_key:",
        "LoadCredential=ctf_docker_known_hosts:",
        "InaccessiblePaths=-/run/docker.sock",
        "InaccessiblePaths=-/var/run/docker.sock",
        "ReadWritePaths={{ openclaw_ctf_workspace_root }}",
    ):
        assert required in service
    for required in (
        "StrictHostKeyChecking=yes",
        "IdentitiesOnly=yes",
        "CREDENTIALS_DIRECTORY",
        '[ "$#" -ne 7 ]',
        '[ "$2" != "{{ openclaw_ctf_docker_user }}" ]',
        '[ "$6" != "{{ ctf_executor_ip }}" ]',
        '[ "$7" != "docker system dial-stdio" ]',
        '-o IdentityAgent=none',
        '-l "{{ openclaw_ctf_docker_user }}"',
        '-- "{{ ctf_executor_ip }}"',
    ):
        assert required in wrapper
    assert '  "$@"' not in wrapper
    assert "DOCKER_SSH_COMMAND" not in service
    assert "DOCKER_SSH_COMMAND" not in transport
    assert "docker system dial-stdio" in transport
    assert "no-port-forwarding" in transport
    assert "openclaw_ctf_user" not in transport
    installed_key = next(
        task
        for task in transport_tasks
        if task["name"] == "Install the Gateway-only forced Docker transport key"
    )["ansible.builtin.copy"]
    assert installed_key["owner"] == "root"
    assert installed_key["group"] == "{{ openclaw_ctf_docker_user }}"
    assert installed_key["mode"] == "0640"

    names = [task["name"] for task in transport_tasks]
    smoke_index = names.index("Validate the restricted remote CTF Docker transport")
    refresh_index = names.index(
        "Refresh the one Gateway CTF Docker credential snapshot after verification"
    )
    assert smoke_index < refresh_index
    refresh = transport_tasks[refresh_index]["ansible.builtin.systemd_service"]
    assert refresh == {"name": "openclaw-gateway.service", "state": "restarted"}
    assert "Restart OpenClaw Gateway after transport update" not in read(
        "infra/ansible/roles/openclaw_ctf_transport/handlers/main.yml"
    )


def test_gateway_skill_transport_validation_uses_the_executor_image_scope():
    plays = yaml.safe_load(read("infra/ansible/playbooks/validate.yml"))
    native_play = next(
        play
        for play in plays
        if play["name"] == "Validate the dedicated native OpenClaw host"
    )
    skill_transport = next(
        task
        for task in native_play["tasks"]
        if task["name"]
        == "Prove remote Docker receives generated CTF sandbox skills at the pinned path"
    )
    shell = skill_transport["ansible.builtin.shell"]

    assert "{{ hostvars['ctf_executor'].openclaw_ctf_executor_image }}" in shell
    assert "{{ openclaw_ctf_executor_image }}" not in shell


def test_gateway_skill_transport_validation_preserves_inner_shell_variables():
    plays = yaml.safe_load(read("infra/ansible/playbooks/validate.yml"))
    native_play = next(
        play
        for play in plays
        if play["name"] == "Validate the dedicated native OpenClaw host"
    )
    skill_transport = next(
        task
        for task in native_play["tasks"]
        if task["name"]
        == "Prove remote Docker receives generated CTF sandbox skills at the pinned path"
    )
    shell = skill_transport["ansible.builtin.shell"]

    # systemd-run expands a single $ before it invokes /bin/sh.  Keep these
    # variables escaped so the shell that owns the temporary fixture receives
    # them intact.
    for required in (
        'fixture="$$(mktemp -d {{ openclaw_ctf_sandbox_skills_root }}/validate.XXXXXX)"',
        'cleanup() { rm -rf "$${fixture}"; }',
        'skill_dir="$${fixture}/.openclaw/sandbox-skills/skills/ctf-artifacts"',
        'mkdir -p "$${skill_dir}"',
        'printf "%s\\\\n" ctf-sandbox-skill-transport > "$${skill_dir}/SKILL.md"',
        '--volume "$${fixture}/.openclaw/sandbox-skills/skills:/workspace/.openclaw/sandbox-skills/skills:ro"',
    ):
        assert required in shell


def test_one_gateway_limit_is_explicitly_documented_and_not_overclaimed():
    runbook = read("docs/runbooks/openclaw-ctf.md")

    assert "DOCKER_HOST" in runbook
    assert "CREDENTIALS_DIRECTORY" in runbook
    assert "DOCKER_SSH_COMMAND" not in runbook
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
