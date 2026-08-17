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
    monkeypatch.setenv("OPENCLAW_DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setenv("OPENCLAW_EXA_API_KEY", "exa-test-token")
    monkeypatch.setenv(
        "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN", "github-token-" + "x" * 24
    )
    monkeypatch.setenv(
        "COPYPARTY_USERS_JSON",
        json.dumps([{"name": "test", "password": "test-password"}]),
    )


def test_one_gateway_and_shared_discord_secrets_are_required(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)

    mapping = module["build_mapping"]()

    assert mapping["openclaw_gateway_token"] == "cd" * 32
    assert mapping["openclaw_discord_bot_token"] == "discord-token"
    assert mapping["openclaw_exa_api_key"] == "exa-test-token"
    assert mapping["openclaw_skill_sync_github_token"] == "github-token-" + "x" * 24
    assert "openclaw_ctf_gateway_token" not in mapping
    assert "OPENCLAW_CTF_GATEWAY_TOKEN" not in module["REQUIRED_ENV"].values()
    assert "OPENCLAW_DISCORD_ENABLED" not in module["REQUIRED_ENV"].values()


def test_retained_gateway_recovery_approvals_are_not_persistent_extra_vars(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)

    mapping = module["build_mapping"]()

    for variable, environment in (
        (
            "openclaw_retained_gateway_rebaseline_approved",
            "OPENCLAW_RETAINED_GATEWAY_REBASELINE_APPROVED",
        ),
        (
            "openclaw_retained_gateway_image_pull_approved",
            "OPENCLAW_RETAINED_GATEWAY_IMAGE_PULL_APPROVED",
        ),
    ):
        assert variable not in mapping
        assert environment not in module["REQUIRED_ENV"].values()


def test_gateway_token_transport_newline_is_removed(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", f"{'cd' * 32}\r\n")

    assert module["build_mapping"]()["openclaw_gateway_token"] == "cd" * 32


def test_openclaw_scope_requires_only_openclaw_secrets(monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "cd" * 32)
    monkeypatch.setenv("OPENCLAW_DISCORD_BOT_TOKEN", "discord-token")
    monkeypatch.setenv("OPENCLAW_EXA_API_KEY", "exa-test-token")
    monkeypatch.setenv("OPENCLAW_SKILL_SYNC_GITHUB_TOKEN", "github-token-" + "x" * 24)

    mapping = module["build_mapping"]("openclaw")

    assert set(mapping) == {
        "openclaw_gateway_token",
        "openclaw_discord_bot_token",
        "openclaw_exa_api_key",
        "openclaw_skill_sync_github_token",
    }


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
def test_invalid_required_secret_is_rejected(monkeypatch, name, value, message):
    module = runpy.run_path(str(SCRIPT))
    seed_required_environment(monkeypatch, module)
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit, match=message):
        module["build_mapping"]()
