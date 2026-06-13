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
    assert "username-prefix: {{ minecraft_floodgate_username_prefix | to_json }}" in floodgate
    assert "send-floodgate-data: false" in floodgate


def test_geyser_and_floodgate_render_parseable_yaml_with_escaped_prefix():
    prefix = 'bedrock "prefix" \\ value'
    geyser = yaml.safe_load(render("geyser-config.yml.j2"))
    floodgate = yaml.safe_load(
        render("floodgate-config.yml.j2", minecraft_floodgate_username_prefix=prefix)
    )

    assert geyser["bedrock"]["address"] == "0.0.0.0"
    assert geyser["bedrock"]["port"] == 19132
    assert geyser["remote"]["auth-type"] == "floodgate"
    assert floodgate["username-prefix"] == prefix
    assert floodgate["send-floodgate-data"] is False


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
