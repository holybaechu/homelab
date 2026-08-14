import json
import runpy

import pytest

from tests.helpers import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts" / "ci" / "write_ansible_extra_vars.py"


def seed_required_environment(monkeypatch, module):
    for environment_name in module["REQUIRED_ENV"].values():
        monkeypatch.setenv(environment_name, "test-value")
    monkeypatch.setenv("ARCANE_ENCRYPTION_KEY", "ab" * 32)
    monkeypatch.setenv("ARCANE_JWT_SECRET", "j" * 32)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "cd" * 32)
    monkeypatch.setenv(
        "COPYPARTY_USERS_JSON",
        json.dumps([{"name": "test", "password": "test-password"}]),
    )


def test_arcane_secrets_are_included_only_after_shape_validation(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)

    mapping = module["build_mapping"]()

    assert mapping["arcane_encryption_key"] == "ab" * 32
    assert mapping["arcane_jwt_secret"] == "j" * 32


def test_arcane_secret_transport_newlines_are_removed(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv("ARCANE_ENCRYPTION_KEY", f"{'ab' * 32}\r\n")
    monkeypatch.setenv("ARCANE_JWT_SECRET", f"{'j' * 32}\n")

    mapping = module["build_mapping"]()

    assert mapping["arcane_encryption_key"] == "ab" * 32
    assert mapping["arcane_jwt_secret"] == "j" * 32


def test_openclaw_gateway_token_is_mapped_after_shape_validation(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", f"{'cd' * 32}\r\n")

    mapping = module["build_mapping"]()

    assert mapping["openclaw_gateway_token"] == "cd" * 32


def test_optional_ctf_discord_secret_and_boolean_are_mapped(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv("OPENCLAW_CTF_DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setenv("OPENCLAW_CTF_DISCORD_ENABLED", "true")

    mapping = module["build_mapping"]()

    assert mapping["openclaw_ctf_discord_bot_token"] == "discord-token"
    assert mapping["openclaw_ctf_discord_enabled"] is True


def test_invalid_ctf_discord_boolean_is_rejected(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv("OPENCLAW_CTF_DISCORD_ENABLED", "enabled")

    with pytest.raises(SystemExit, match="must be true or false"):
        module["build_mapping"]()


def test_retired_hermes_environment_is_not_mapped():
    module = runpy.run_path(str(SCRIPT))
    all_environment_names = set(module["REQUIRED_ENV"].values()) | set(
        module["OPTIONAL_ENV"].values()
    )

    assert not {
        "HERMES_DISCORD_BOT_TOKEN",
        "HERMES_DISCORD_ALLOWED_USERS",
        "HERMES_DISCORD_HOME_CHANNEL",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
        "HONCHO_API_KEY",
        "OP_SERVICE_ACCOUNT_TOKEN",
    } & all_environment_names


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ARCANE_ENCRYPTION_KEY", "not-hex", "64 hexadecimal characters"),
        ("ARCANE_JWT_SECRET", "too-short", "at least 32 characters"),
        (
            "OPENCLAW_GATEWAY_TOKEN",
            "not-hex",
            "OPENCLAW_GATEWAY_TOKEN must be exactly 64 hexadecimal characters",
        ),
    ],
)
def test_invalid_arcane_secret_is_rejected(monkeypatch, name, value, message):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit, match=message):
        module["build_mapping"]()
