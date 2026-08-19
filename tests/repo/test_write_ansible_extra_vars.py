import json
import os
import runpy
import stat

import pytest

from tests.helpers import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts" / "ci" / "write_ansible_extra_vars.py"
SCHEMA = REPO_ROOT / "infra" / "deployment" / "secrets.json"
VALID_ENVIRONMENT = {
    "CLOUDFLARE_TRAEFIK_TOKEN": "traefik-token",
    "CLOUDFLARE_DDNS_TOKEN": "ddns-token",
    "ADGUARD_ADMIN_PASSWORD": "adguard-password",
    "QBITTORRENT_WEBUI_PASSWORD": "qbittorrent-password",
    "COPYPARTY_USERS_JSON": json.dumps(
        [{"name": "test", "password": "test-password"}]
    ),
    "ADGUARD_ADMIN_USERNAME": "operator",
    "TAILSCALE_AUTH_KEY": "tskey-auth-test",
    "OPENCLAW_GATEWAY_TOKEN": "cd" * 32,
    "OPENCLAW_DISCORD_BOT_TOKEN": "discord-token",
    "OPENCLAW_EXA_API_KEY": "exa-test-token",
    "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN": "github-token-" + "x" * 24,
}


def load_module():
    return runpy.run_path(str(SCRIPT))


def clear_schema_environment(monkeypatch, module):
    for entry in module["SECRET_SCHEMA"]["entries"]:
        monkeypatch.delenv(entry["github_env"], raising=False)
    monkeypatch.delenv("OPENCLAW_SETUP_COMMIT", raising=False)


def seed_components(monkeypatch, module, *components, include_optional=True):
    clear_schema_environment(monkeypatch, module)
    selected = set(components)
    for entry in module["SECRET_SCHEMA"]["entries"]:
        if entry["component"] not in selected:
            continue
        if entry["kind"] == "optional" and not include_optional:
            continue
        monkeypatch.setenv(entry["github_env"], VALID_ENVIRONMENT[entry["github_env"]])


def test_schema_is_the_single_component_secret_contract():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert schema["version"] == 1
    assert schema["components"] == ["apps", "tailnet", "openclaw"]
    assert all(
        set(entry)
        == {
            "github_env",
            "ansible_variable",
            "component",
            "kind",
            "validation",
        }
        for entry in schema["entries"]
    )
    assert len({entry["github_env"] for entry in schema["entries"]}) == len(
        schema["entries"]
    )
    assert len({entry["ansible_variable"] for entry in schema["entries"]}) == len(
        schema["entries"]
    )

    by_component = {
        component: {
            (entry["github_env"], entry["ansible_variable"], entry["kind"])
            for entry in schema["entries"]
            if entry["component"] == component
        }
        for component in schema["components"]
    }
    assert by_component["apps"] == {
        ("CLOUDFLARE_TRAEFIK_TOKEN", "cloudflare_traefik_token", "required"),
        ("CLOUDFLARE_DDNS_TOKEN", "cloudflare_ddns_token", "required"),
        ("ADGUARD_ADMIN_PASSWORD", "adguard_admin_password", "required"),
        (
            "QBITTORRENT_WEBUI_PASSWORD",
            "qbittorrent_webui_password",
            "required",
        ),
        ("COPYPARTY_USERS_JSON", "copyparty_users", "required"),
        ("ADGUARD_ADMIN_USERNAME", "adguard_admin_username", "optional"),
    }
    assert by_component["tailnet"] == {
        ("TAILSCALE_AUTH_KEY", "tailscale_auth_key", "required")
    }
    assert by_component["openclaw"] == {
        ("OPENCLAW_GATEWAY_TOKEN", "openclaw_gateway_token", "required"),
        (
            "OPENCLAW_DISCORD_BOT_TOKEN",
            "openclaw_discord_bot_token",
            "required",
        ),
        ("OPENCLAW_EXA_API_KEY", "openclaw_exa_api_key", "required"),
        (
            "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN",
            "openclaw_skill_sync_github_token",
            "required",
        ),
    }


def test_secret_documentation_tracks_the_schema_without_legacy_claims():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    documentation = (REPO_ROOT / "secrets" / "README.md").read_text(
        encoding="utf-8"
    )

    for entry in schema["entries"]:
        assert f"`{entry['github_env']}`" in documentation
        assert f"`{entry['ansible_variable']}`" in documentation
    assert "SOPS" not in documentation
    assert "/opt/homelab-control/openclaw" not in documentation
    assert "full|openclaw" not in documentation


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        (
            "apps",
            {
                "cloudflare_traefik_token",
                "cloudflare_ddns_token",
                "adguard_admin_password",
                "qbittorrent_webui_password",
                "copyparty_users",
                "adguard_admin_username",
            },
        ),
        ("tailnet", {"tailscale_auth_key"}),
        (
            "openclaw",
            {
                "openclaw_gateway_token",
                "openclaw_discord_bot_token",
                "openclaw_exa_api_key",
                "openclaw_skill_sync_github_token",
            },
        ),
    ],
)
def test_each_component_loads_only_its_own_environment(
    monkeypatch, component, expected
):
    module = load_module()
    seed_components(monkeypatch, module, component)

    mapping = module["build_mapping"](component)

    assert set(mapping) == expected


def test_apps_optional_username_is_omitted_when_unset(monkeypatch):
    module = load_module()
    seed_components(monkeypatch, module, "apps", include_optional=False)

    mapping = module["build_mapping"]("apps")

    assert "adguard_admin_username" not in mapping


def test_mixed_component_set_is_a_deduplicated_union(monkeypatch):
    module = load_module()
    seed_components(monkeypatch, module, "apps", "openclaw")

    mapping = module["build_mapping"](" openclaw,apps,openclaw ")

    assert "cloudflare_traefik_token" in mapping
    assert "openclaw_gateway_token" in mapping
    assert "tailscale_auth_key" not in mapping
    assert len(mapping) == 10


@pytest.mark.parametrize(
    ("components", "message"),
    [
        ("", "non-empty comma-separated set"),
        (",", "must not contain empty names"),
        ("apps,", "must not contain empty names"),
        ("full", "unknown deployment component.*full"),
        ("apps,unknown", "unknown deployment component.*unknown"),
    ],
)
def test_empty_unknown_and_legacy_component_names_are_rejected(components, message):
    module = load_module()

    with pytest.raises(SystemExit, match=message):
        module["build_mapping"](components)


@pytest.mark.parametrize("component", ["apps", "tailnet", "openclaw"])
def test_each_component_rejects_a_missing_required_value(monkeypatch, component):
    module = load_module()
    seed_components(monkeypatch, module, component)
    required = next(
        entry
        for entry in module["SECRET_SCHEMA"]["entries"]
        if entry["component"] == component and entry["kind"] == "required"
    )
    monkeypatch.delenv(required["github_env"])

    with pytest.raises(SystemExit, match=f"{required['github_env']} is required"):
        module["build_mapping"](component)


def test_openclaw_strict_values_remove_transport_newlines(monkeypatch):
    module = load_module()
    seed_components(monkeypatch, module, "openclaw")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", f"{'cd' * 32}\r\n")
    monkeypatch.setenv("OPENCLAW_EXA_API_KEY", "exa-token\n")
    monkeypatch.setenv(
        "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN", "github-token-" + "x" * 24 + "\n"
    )

    mapping = module["build_mapping"]("openclaw")

    assert mapping["openclaw_gateway_token"] == "cd" * 32
    assert mapping["openclaw_exa_api_key"] == "exa-token"
    assert mapping["openclaw_skill_sync_github_token"] == (
        "github-token-" + "x" * 24
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "OPENCLAW_GATEWAY_TOKEN",
            "not-hex",
            "exactly 64 hexadecimal characters",
        ),
        ("OPENCLAW_EXA_API_KEY", "contains space", "1-4096 non-whitespace"),
        ("OPENCLAW_SKILL_SYNC_GITHUB_TOKEN", "short", "20-4096 non-whitespace"),
    ],
)
def test_invalid_strict_openclaw_secret_is_rejected(
    monkeypatch, name, value, message
):
    module = load_module()
    seed_components(monkeypatch, module, "openclaw")
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit, match=message):
        module["build_mapping"]("openclaw")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-json", "must be valid JSON"),
        ("[]", "must be a non-empty JSON list"),
        (json.dumps({"name": "one"}), "must be a non-empty JSON list"),
        (json.dumps(["one"]), "user #1 must be an object"),
        (json.dumps([{"password": "secret"}]), "must include a non-empty name"),
        (
            json.dumps([{"name": "one", "password_hash": "hash"}]),
            "must use plaintext password",
        ),
        (json.dumps([{"name": "one", "password": ""}]), "must include password"),
    ],
)
def test_invalid_copyparty_json_is_rejected(monkeypatch, value, message):
    module = load_module()
    seed_components(monkeypatch, module, "apps")
    monkeypatch.setenv("COPYPARTY_USERS_JSON", value)

    with pytest.raises(SystemExit, match=message):
        module["build_mapping"]("apps")


def test_openclaw_promotion_commit_is_scoped_and_validated(monkeypatch, tmp_path):
    module = load_module()
    seed_components(monkeypatch, module, "openclaw")
    monkeypatch.setenv("OPENCLAW_SETUP_COMMIT", "a" * 40)
    output = tmp_path / "openclaw.json"

    assert module["main"]([str(SCRIPT), str(output), "openclaw"]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))[
        "openclaw_setup_expected_commit"
    ] == "a" * 40

    seed_components(monkeypatch, module, "tailnet")
    monkeypatch.setenv("OPENCLAW_SETUP_COMMIT", "not-a-sha")
    output = tmp_path / "tailnet.json"
    assert module["main"]([str(SCRIPT), str(output), "tailnet"]) == 0
    assert "openclaw_setup_expected_commit" not in json.loads(
        output.read_text(encoding="utf-8")
    )


def test_private_output_uses_atomic_same_directory_replacement(
    monkeypatch, tmp_path
):
    module = load_module()
    output = tmp_path / "extra-vars.json"
    replacements = []
    real_replace = module["os"].replace

    def record_replace(source, destination):
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(module["os"], "replace", record_replace)
    module["write_private_json"](output, {"one": "two"})

    assert json.loads(output.read_text(encoding="utf-8")) == {"one": "two"}
    assert len(replacements) == 1
    source, destination = map(os.fspath, replacements[0])
    assert os.path.dirname(source) == os.fspath(tmp_path)
    assert destination == os.fspath(output)
    assert list(tmp_path.glob(".extra-vars.json.*")) == []
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_atomic_write_failure_preserves_existing_output(monkeypatch, tmp_path):
    module = load_module()
    output = tmp_path / "extra-vars.json"
    output.write_text("original\n", encoding="utf-8")

    def fail_dump(*_args, **_kwargs):
        raise RuntimeError("injected write failure")

    monkeypatch.setattr(module["json"], "dump", fail_dump)
    with pytest.raises(RuntimeError, match="injected write failure"):
        module["write_private_json"](output, {"one": "two"})

    assert output.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".extra-vars.json.*")) == []


def test_recovery_approvals_are_not_part_of_the_persistent_schema():
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "OPENCLAW_RETAINED_GATEWAY_REBASELINE_APPROVED" not in schema
    assert "OPENCLAW_RETAINED_GATEWAY_IMAGE_PULL_APPROVED" not in schema


def test_cli_requires_an_explicit_component_set(tmp_path):
    module = load_module()

    with pytest.raises(SystemExit, match="OUTPUT_JSON COMPONENT"):
        module["main"]([str(SCRIPT), str(tmp_path / "output.json")])
