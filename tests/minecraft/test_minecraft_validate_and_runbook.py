import re

import yaml



from tests.helpers import REPO_ROOT
VIA_VERSION_JAR = "{{ minecraft_paper_plugins_dir }}/ViaVersion.jar"
GEYSER_JAR = "{{ minecraft_velocity_plugins_dir }}/Geyser-Velocity.jar"
FLOODGATE_JAR = "{{ minecraft_velocity_plugins_dir }}/floodgate-velocity.jar"


def load_playbook(relative_path: str) -> list[dict]:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as handle:
        playbook = yaml.safe_load(handle)

    assert isinstance(playbook, list)
    return playbook


def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def find_play(plays: list[dict], name: str) -> dict:
    matches = [play for play in plays if play.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def find_task(play: dict, name: str) -> dict:
    matches = [task for task in play.get("tasks", []) if task.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def role_names(play: dict) -> list[str]:
    roles = play.get("roles", [])
    names = []
    for role in roles:
        if isinstance(role, str):
            names.append(role)
        else:
            names.append(role["role"])
    return names


def assert_export_immediately_precedes_command(runbook: str, command: str) -> None:
    pattern = re.compile(
        r"(?m)^[ \t]*export ANSIBLE_CONFIG=infra/ansible/ansible\.cfg[ \t]*\n"
        rf"^[ \t]*{re.escape(command)}[ \t]*$"
    )
    assert pattern.search(runbook)


def test_site_playbook_applies_minecraft_role():
    play = find_play(load_playbook("infra/ansible/playbooks/site.yml"), "Configure minecraft LXC")

    assert play["hosts"] == "svc_minecraft"
    assert play["gather_facts"] is True
    assert "minecraft" in role_names(play)


def test_validate_playbook_checks_minecraft_services_and_public_java_port():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    assert play["hosts"] == "svc_minecraft"
    assert play["gather_facts"] is False

    paper_service = find_task(play, "Check Paper service")
    assert paper_service["ansible.builtin.command"]["cmd"] == "systemctl is-active minecraft-paper"
    assert paper_service["register"] == "minecraft_paper_service_status"
    assert paper_service["until"] == 'minecraft_paper_service_status.stdout == "active"'
    assert paper_service["retries"] >= 6
    assert paper_service["delay"] > 0

    velocity_service = find_task(play, "Check Velocity service")
    assert velocity_service["ansible.builtin.command"]["cmd"] == "systemctl is-active minecraft-velocity"
    assert velocity_service["register"] == "minecraft_velocity_service_status"
    assert velocity_service["until"] == 'minecraft_velocity_service_status.stdout == "active"'
    assert velocity_service["retries"] >= 6
    assert velocity_service["delay"] > 0

    java_port = find_task(play, "Check Velocity Java port")
    wait_for = java_port["ansible.builtin.wait_for"]
    assert wait_for["host"] == "0.0.0.0"
    assert wait_for["port"] == "{{ minecraft_java_port }}"
    assert isinstance(wait_for["timeout"], int)
    assert wait_for["timeout"] > 0

    public_bind = find_task(play, "Check Velocity Java port is publicly bound")
    command = public_bind["ansible.builtin.shell"]["cmd"]
    assert 'ss -H -ltn "sport = :{{ minecraft_java_port }}"' in command
    assert "awk '{ print $4 }'" in command
    assert "grep -Eq" in command
    assert "0\\.0\\.0\\.0" in command
    assert "\\*" in command
    assert "\\[::\\]" in command
    assert "::" in command
    assert public_bind["changed_when"] is False

    paper_port = find_task(play, "Check Paper backend port")
    paper_wait_for = paper_port["ansible.builtin.wait_for"]
    assert paper_wait_for["host"] == "127.0.0.1"
    assert paper_wait_for["port"] == "{{ minecraft_paper_port }}"
    assert isinstance(paper_wait_for["timeout"], int)
    assert paper_wait_for["timeout"] > 0


def test_validate_playbook_rejects_non_loopback_paper_backend_listeners():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    task = find_task(play, "Check Paper backend is bound only to localhost")
    command = task["ansible.builtin.shell"]["cmd"]

    assert 'ss -H -ltn "sport = :{{ minecraft_paper_port }}"' in command
    assert "awk '{ print $4 }'" in command
    assert "paper_loopback_pattern=" in command
    assert "::ffff:127\\.0\\.0\\.1" in command
    assert "0:0:0:0:0:0:0:1" in command
    assert "Paper backend listeners are not loopback-only" in command
    assert "127\\.0\\.0\\.1" in command
    assert "\\[::1\\]" in command
    assert "exit 1" in command


def test_validate_playbook_checks_geyser_udp_port():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    task = find_task(play, "Check Geyser Bedrock UDP port")
    command = task["ansible.builtin.shell"]["cmd"]

    assert 'ss -H -lun "sport = :{{ minecraft_bedrock_port }}"' in command
    assert "Geyser Bedrock UDP listener is not active" in command
    assert "journalctl -u minecraft-velocity.service" in command


def test_validate_playbook_stats_design_plugin_jars():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    task = find_task(play, "Check Minecraft plugin jars")

    assert task["ansible.builtin.stat"]["path"] == "{{ item }}"
    assert task["loop"] == [VIA_VERSION_JAR, GEYSER_JAR, FLOODGATE_JAR]


def test_validate_playbook_asserts_plugins_exist_with_guarded_size_check():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    task = find_task(play, "Require Minecraft plugin jars")

    assert task["loop"] == "{{ minecraft_plugin_stats.results }}"
    assert task["ansible.builtin.assert"]["that"] == [
        "(item.stat.exists | default(false)) and ((item.stat.size | default(0) | int) > 0)"
    ]
    assert "{{ item.item }}" in task["ansible.builtin.assert"]["fail_msg"]


def test_bootstrap_trusts_all_lxc_inventory_host_keys():
    play = find_play(
        load_playbook("infra/ansible/playbooks/bootstrap.yml"),
        "Trust LXC SSH host keys",
    )
    task = find_task(play, "Add LXC SSH host keys to known_hosts")
    loop = task["loop"]

    assert loop == "{{ pve_lxc_host_key_results.results }}"
    assert "hostvars[item.item.name].ansible_host" in task["ansible.builtin.known_hosts"]["name"]
    assert "hostvars[item.item.name].ansible_host" in task["ansible.builtin.known_hosts"]["key"]
    assert "item.stdout" in task["ansible.builtin.known_hosts"]["key"]


def test_minecraft_runbook_documents_dns_ports_and_join_checks():
    runbook = read("docs/runbooks/minecraft-server.md").replace("\r\n", "\n")
    bootstrap_command = (
        "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml "
        "infra/ansible/playbooks/bootstrap.yml --limit pve,minecraft"
    )
    ansible_commands = [
        bootstrap_command,
        "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml "
        "infra/ansible/playbooks/site.yml --limit minecraft",
        "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml "
        "infra/ansible/playbooks/validate.yml --limit minecraft",
    ]

    assert "_minecraft._tcp.hchu.me" in runbook
    assert "home.hchu.me" in runbook
    assert (
        "| SRV | `_minecraft._tcp.hchu.me` | `0` | `0` | `25565` | `home.hchu.me` |"
        in runbook
    )
    assert "TCP 25565" in runbook
    assert "UDP 19132" in runbook
    assert "192.168.0.8" in runbook
    assert "infra/opentofu/envs/prod/containers.auto.tfvars" in runbook
    assert "infra/opentofu/envs/prod/terraform.tfvars" in runbook
    assert "minecraft block is tracked" in runbook
    assert "Changing only terraform.tfvars.example does not deploy the LXC." not in runbook
    assert "- TCP 25565 -> 192.168.0.8:25565" in runbook
    assert "- UDP 19132 -> 192.168.0.8:19132" in runbook
    assert "ssh-keygen -R 192.168.0.8" in runbook
    assert "ssh-keyscan -H -T 10 192.168.0.8" in runbook
    assert "Whitelist: disabled" in runbook
    assert "any authenticated Java or Floodgate Bedrock player can join" in runbook
    assert "This server does not source-control a Minecraft player allowlist." in runbook
    assert "Paper operators:" not in runbook
    assert "unlisted Java" not in runbook
    assert "unlisted Bedrock" not in runbook
    assert "Join from Java using `hchu.me`." in runbook
    assert "Join from Bedrock using `home.hchu.me`, port `19132`." in runbook
    assert "Join from Bedrock using `hchu.me`" not in runbook
    assert "Floodgate global linking is enabled" in runbook
    assert "Local linking on this server is disabled" in runbook
    assert "link.geysermc.org" in runbook
    assert "Java: `link.geysermc.org`, port `25565`." in runbook
    assert "Bedrock: `link.geysermc.org`, port `19132`." in runbook
    assert "After linking, Floodgate uses" in runbook
    assert "the Java account's player data when the Bedrock account joins" in runbook
    assert "run `/linkaccount`" in runbook
    assert "with `/linkaccount <code>`" in runbook
    assert "run `/unlinkaccount`" in runbook
    assert "Geyser cache" not in runbook
    assert "failed to resolve Minecraft allowlist" not in runbook
    assert "failed to resolve Bedrock player <gamertag>" not in runbook
    assert "apps/minecraft/allowed-players.yml" not in runbook
    assert "ANSIBLE_CONFIG=infra/ansible/ansible.cfg" in runbook
    assert bootstrap_command in runbook
    for ansible_command in ansible_commands:
        assert_export_immediately_precedes_command(runbook, ansible_command)
    assert "tofu -chdir=infra/opentofu/envs/prod plan" in runbook
    assert "tofu -chdir=infra/opentofu/envs/prod apply" in runbook
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --limit minecraft" in runbook
    assert "ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml --limit minecraft" in runbook
