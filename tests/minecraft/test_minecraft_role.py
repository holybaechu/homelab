import json
import tomllib
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "infra" / "ansible" / "roles" / "minecraft"
TEMPLATES = ROLE / "templates"


def jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["to_json"] = json.dumps
    return env


def minecraft_vars(**overrides):
    values = {
        "minecraft_bedrock_port": 19132,
        "minecraft_floodgate_username_prefix": 'bedrock "prefix" \\ value',
        "minecraft_java_port": 25565,
        "minecraft_max_players": 20,
        "minecraft_motd": 'quoted "motd" \\ spawn',
        "minecraft_paper_bind_address": "127.0.0.1",
        "minecraft_paper_port": 25566,
        "minecraft_velocity_forwarding_secret": 'forward "secret" \\ value: #hash',
    }
    values.update(overrides)
    return values


def render(template_name: str, **overrides) -> str:
    return jinja_env().get_template(template_name).render(**minecraft_vars(**overrides))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_role_yaml(relative_path: str):
    return yaml.safe_load(read(ROLE / relative_path))


def role_task(tasks: list[dict], name: str) -> dict:
    for task in tasks:
        if task["name"] == name:
            return task
    raise AssertionError(f"missing role task {name!r}")


def role_task_index(tasks: list[dict], name: str) -> int:
    for index, task in enumerate(tasks):
        if task["name"] == name:
            return index
    raise AssertionError(f"missing role task {name!r}")


def test_paper_server_properties_blocks_direct_public_access_and_enforces_allowlist():
    template = read(ROLE / "templates" / "server.properties.j2")

    assert "server-ip={{ minecraft_paper_bind_address }}" in template
    assert "server-port={{ minecraft_paper_port }}" in template
    assert "online-mode=false" in template
    assert "white-list=true" in template
    assert "enforce-whitelist=true" in template


def test_paper_global_enables_velocity_modern_forwarding():
    template = read(ROLE / "templates" / "paper-global.yml.j2")

    assert "proxies:" in template
    assert "velocity:" in template
    assert "enabled: true" in template
    assert "online-mode: true" in template
    assert "secret: {{ minecraft_velocity_forwarding_secret | to_json }}" in template


def test_paper_global_renders_parseable_yaml_with_escaped_secret():
    secret = 'forward "secret" \\ value: #hash'
    rendered = render("paper-global.yml.j2", minecraft_velocity_forwarding_secret=secret)

    data = yaml.safe_load(rendered)

    assert data["proxies"]["velocity"]["secret"] == secret


def test_velocity_template_uses_modern_forwarding_and_private_backend():
    template = read(ROLE / "templates" / "velocity.toml.j2")

    assert 'bind = {{ ("0.0.0.0:" ~ minecraft_java_port) | to_json }}' in template
    assert "motd = {{ minecraft_motd | to_json }}" in template
    assert "online-mode = true" in template
    assert 'player-info-forwarding-mode = "modern"' in template
    assert 'forwarding-secret-file = "forwarding.secret"' in template
    assert (
        'paper = {{ (minecraft_paper_bind_address ~ ":" ~ minecraft_paper_port) | to_json }}'
        in template
    )
    assert 'try = ["paper"]' in template


def test_velocity_renders_parseable_toml_with_escaped_motd_and_backend():
    motd = 'quoted "motd" \\ spawn'
    rendered = render("velocity.toml.j2", minecraft_motd=motd)

    data = tomllib.loads(rendered)

    assert data["bind"] == "0.0.0.0:25565"
    assert data["motd"] == motd
    assert data["servers"]["paper"] == "127.0.0.1:25566"
    assert data["player-info-forwarding-mode"] == "modern"
    assert data["forwarding-secret-file"] == "forwarding.secret"


def test_geyser_and_floodgate_templates_enable_bedrock_floodgate_auth():
    geyser = read(ROLE / "templates" / "geyser-config.yml.j2")
    floodgate = read(ROLE / "templates" / "floodgate-config.yml.j2")

    assert "address: 0.0.0.0" in geyser
    assert "port: {{ minecraft_bedrock_port }}" in geyser
    assert "auth-type: floodgate" in geyser
    assert "key-file-name: key.pem" in floodgate
    assert "username-prefix: {{ minecraft_floodgate_username_prefix | to_json }}" in floodgate
    assert "send-floodgate-data: false" in floodgate
    assert "config-version: 3" in floodgate


def test_geyser_and_floodgate_render_parseable_yaml_with_escaped_prefix():
    prefix = 'bedrock "prefix" \\ value'
    geyser = yaml.safe_load(render("geyser-config.yml.j2"))
    floodgate = yaml.safe_load(
        render("floodgate-config.yml.j2", minecraft_floodgate_username_prefix=prefix)
    )

    assert geyser["bedrock"]["address"] == "0.0.0.0"
    assert geyser["bedrock"]["port"] == 19132
    assert geyser["remote"]["auth-type"] == "floodgate"
    assert floodgate["key-file-name"] == "key.pem"
    assert floodgate["username-prefix"] == prefix
    assert floodgate["send-floodgate-data"] is False
    assert floodgate["disconnect"]["invalid-key"] == "Invalid Floodgate key."
    assert floodgate["disconnect"]["invalid-arguments-length"] == (
        "Expected {} arguments, got {}. Is Geyser up-to-date?"
    )
    assert "disconnect-message" not in floodgate
    assert floodgate["config-version"] == 3


def test_systemd_units_run_as_minecraft_user():
    paper = read(ROLE / "templates" / "minecraft-paper.service.j2")
    velocity = read(ROLE / "templates" / "minecraft-velocity.service.j2")

    assert "User={{ minecraft_user }}" in paper
    assert "Group={{ minecraft_group }}" in paper
    assert "WorkingDirectory={{ minecraft_paper_dir }}" in paper
    assert "-jar paper.jar --nogui" in paper
    assert "User={{ minecraft_user }}" in velocity
    assert "Group={{ minecraft_group }}" in velocity
    assert "WorkingDirectory={{ minecraft_velocity_dir }}" in velocity
    assert "-jar velocity.jar" in velocity
    assert "Requires=minecraft-paper.service" in velocity


def test_systemd_units_include_basic_hardening():
    paper = read(ROLE / "templates" / "minecraft-paper.service.j2")
    velocity = read(ROLE / "templates" / "minecraft-velocity.service.j2")

    for unit in (paper, velocity):
        assert "NoNewPrivileges=true" in unit
        assert "PrivateTmp=true" in unit
        assert "ProtectHome=true" in unit


def test_minecraft_role_downloads_expected_artifacts_to_correct_plugin_paths():
    tasks = load_role_yaml("tasks/main.yml")
    downloads = {
        (
            task["ansible.builtin.get_url"]["url"],
            task["ansible.builtin.get_url"]["dest"],
        )
        for task in tasks
        if "ansible.builtin.get_url" in task
    }

    assert (
        "https://api.papermc.io/v2/projects/paper/versions/{{ minecraft_paper_version }}/builds/{{ minecraft_paper_build }}/downloads/paper-{{ minecraft_paper_version }}-{{ minecraft_paper_build }}.jar",
        "{{ minecraft_paper_dir }}/paper.jar",
    ) in downloads
    assert (
        "https://api.papermc.io/v2/projects/velocity/versions/{{ minecraft_velocity_version }}/builds/{{ minecraft_velocity_build }}/downloads/velocity-{{ minecraft_velocity_version }}-{{ minecraft_velocity_build }}.jar",
        "{{ minecraft_velocity_dir }}/velocity.jar",
    ) in downloads
    assert (
        "https://download.geysermc.org/v2/projects/geyser/versions/{{ minecraft_geyser_version }}/builds/{{ minecraft_geyser_build }}/downloads/velocity",
        "{{ minecraft_velocity_plugins_dir }}/Geyser-Velocity.jar",
    ) in downloads
    assert (
        "https://download.geysermc.org/v2/projects/floodgate/versions/{{ minecraft_floodgate_version }}/builds/{{ minecraft_floodgate_build }}/downloads/velocity",
        "{{ minecraft_velocity_plugins_dir }}/floodgate-velocity.jar",
    ) in downloads
    assert (
        "{{ minecraft_viaversion_url }}",
        "{{ minecraft_paper_plugins_dir }}/ViaVersion.jar",
    ) in downloads


def test_minecraft_role_generates_secret_and_allowlist_without_committing_secrets():
    tasks_text = read(ROLE / "tasks" / "main.yml")
    tasks = yaml.safe_load(tasks_text)
    stat_task = role_task(tasks, "Check Velocity forwarding secret")
    copy_task = role_task(tasks, "Install Velocity forwarding secret")
    slurp_task = role_task(tasks, "Read Velocity forwarding secret")
    set_fact_task = role_task(tasks, "Set Velocity forwarding secret fact")

    assert "Generate Velocity forwarding secret" in tasks_text
    assert "secrets.token_urlsafe(32)" in tasks_text
    assert "ansible.builtin.stat:" in tasks_text
    assert "ansible.builtin.slurp:" in tasks_text
    assert "ansible.builtin.set_fact:" in tasks_text
    assert "dest: \"{{ minecraft_velocity_dir }}/forwarding.secret\"" in tasks_text
    assert "mode: \"0600\"" in tasks_text
    assert "no_log: true" in tasks_text
    assert "minecraft_velocity_forwarding_secret" in tasks_text
    assert stat_task["ansible.builtin.stat"]["path"] == (
        "{{ minecraft_velocity_dir }}/forwarding.secret"
    )
    assert copy_task["ansible.builtin.copy"]["dest"] == (
        "{{ minecraft_velocity_dir }}/forwarding.secret"
    )
    assert copy_task["ansible.builtin.copy"]["mode"] == "0600"
    assert copy_task["no_log"] is True
    assert slurp_task["ansible.builtin.slurp"]["src"] == (
        "{{ minecraft_velocity_dir }}/forwarding.secret"
    )
    assert slurp_task["no_log"] is True
    assert "minecraft_velocity_forwarding_secret" in set_fact_task["ansible.builtin.set_fact"]
    assert set_fact_task["no_log"] is True
    assert "minecraft-resolve-allowlist" in tasks_text
    assert "--bedrock-prefix {{ minecraft_floodgate_username_prefix | quote }}" in tasks_text
    assert (
        "--bedrock {{ (player.gamertag ~ '=' ~ player.uuid) | quote }}"
        in tasks_text
    )
    assert "--bedrock {{ player.gamertag | quote }}" in tasks_text
    assert "player.uuid is defined" in tasks_text
    assert 'changed_when: minecraft_allowlist_result.stdout == "changed"' in tasks_text


def test_paper_forwarding_config_is_installed_restrictively():
    tasks = load_role_yaml("tasks/main.yml")
    paper_global_task = role_task(tasks, "Install Paper global config")

    assert paper_global_task["ansible.builtin.template"]["dest"] == (
        "{{ minecraft_paper_dir }}/config/paper-global.yml"
    )
    assert paper_global_task["ansible.builtin.template"]["mode"] == "0600"
    assert paper_global_task["no_log"] is True


def test_existing_velocity_forwarding_secret_permissions_are_enforced_before_slurp():
    tasks = load_role_yaml("tasks/main.yml")
    copy_index = role_task_index(tasks, "Install Velocity forwarding secret")
    harden_index = role_task_index(tasks, "Set Velocity forwarding secret permissions")
    slurp_index = role_task_index(tasks, "Read Velocity forwarding secret")
    harden_task = tasks[harden_index]

    assert copy_index < harden_index < slurp_index
    assert harden_task["ansible.builtin.file"] == {
        "path": "{{ minecraft_velocity_dir }}/forwarding.secret",
        "state": "file",
        "owner": "{{ minecraft_user }}",
        "group": "{{ minecraft_group }}",
        "mode": "0600",
    }
    assert harden_task["no_log"] is True


def test_minecraft_allowlist_resolver_source_resolves_from_playbook_dir():
    tasks = load_role_yaml("tasks/main.yml")
    resolver_task = role_task(tasks, "Install Minecraft allowlist resolver")
    resolver_src = resolver_task["ansible.builtin.copy"]["src"]
    playbook_dir = REPO_ROOT / "infra" / "ansible" / "playbooks"
    resolved_src = Path(
        resolver_src.replace("{{ playbook_dir }}", str(playbook_dir))
    ).resolve()

    assert resolver_src == (
        "{{ playbook_dir }}/../../../apps/minecraft/scripts/resolve_allowlist.py"
    )
    assert resolved_src == (
        REPO_ROOT / "apps" / "minecraft" / "scripts" / "resolve_allowlist.py"
    ).resolve()
    assert resolved_src.is_file()


def test_minecraft_handlers_restart_both_services_with_systemd():
    handlers_text = read(ROLE / "handlers" / "main.yml")
    handlers = yaml.safe_load(handlers_text)

    assert "name: minecraft-paper.service" in handlers_text
    assert "name: minecraft-velocity.service" in handlers_text
    assert "daemon_reload: true" in handlers_text
    assert "state: restarted" in handlers_text
    assert {
        handler["ansible.builtin.systemd"]["name"]: handler["ansible.builtin.systemd"][
            "state"
        ]
        for handler in handlers
    } == {
        "minecraft-paper.service": "restarted",
        "minecraft-velocity.service": "restarted",
    }
