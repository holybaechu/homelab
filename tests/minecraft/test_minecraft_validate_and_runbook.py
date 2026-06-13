from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]

VIA_VERSION_JAR = "{{ minecraft_paper_plugins_dir }}/ViaVersion.jar"
GEYSER_JAR = "{{ minecraft_velocity_plugins_dir }}/Geyser-Velocity.jar"
FLOODGATE_JAR = "{{ minecraft_velocity_plugins_dir }}/floodgate-velocity.jar"


def load_playbook(relative_path: str) -> list[dict]:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as handle:
        playbook = yaml.safe_load(handle)

    assert isinstance(playbook, list)
    return playbook


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


def test_site_playbook_applies_minecraft_role():
    play = find_play(load_playbook("infra/ansible/playbooks/site.yml"), "Configure minecraft LXC")

    assert play["hosts"] == "minecraft"
    assert play["gather_facts"] is True
    assert "minecraft" in role_names(play)


def test_validate_playbook_checks_minecraft_services_and_public_java_port():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    assert play["hosts"] == "minecraft"
    assert play["gather_facts"] is False

    paper_service = find_task(play, "Check Paper service")
    assert paper_service["ansible.builtin.command"]["cmd"] == "systemctl is-active minecraft-paper"

    velocity_service = find_task(play, "Check Velocity service")
    assert velocity_service["ansible.builtin.command"]["cmd"] == "systemctl is-active minecraft-velocity"

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
    assert 'grep -Fx "0.0.0.0:{{ minecraft_java_port }}"' in command
    assert public_bind["changed_when"] is False


def test_validate_playbook_rejects_non_loopback_paper_backend_listeners():
    play = find_play(
        load_playbook("infra/ansible/playbooks/validate.yml"),
        "Validate minecraft service",
    )

    task = find_task(play, "Check Paper backend is bound only to localhost")
    command = task["ansible.builtin.shell"]["cmd"]

    assert 'ss -H -ltn "sport = :{{ minecraft_paper_port }}"' in command
    assert "awk '{ print $4 }'" in command
    assert "grep -Ev" in command
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
    assert 'grep -F ":{{ minecraft_bedrock_port }}"' in command


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
