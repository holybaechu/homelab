#!/usr/bin/env python3
"""Write Ansible extra-vars containing deployment secrets with 0600 permissions."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_ENV = {
    "cloudflare_traefik_token": "CLOUDFLARE_TRAEFIK_TOKEN",
    "cloudflare_ddns_token": "CLOUDFLARE_DDNS_TOKEN",
    "adguard_admin_password": "ADGUARD_ADMIN_PASSWORD",
    "tailscale_auth_key": "TAILSCALE_AUTH_KEY",
    "qbittorrent_webui_password": "QBITTORRENT_WEBUI_PASSWORD",
    "arcane_encryption_key": "ARCANE_ENCRYPTION_KEY",
    "arcane_jwt_secret": "ARCANE_JWT_SECRET",
    "openclaw_gateway_token": "OPENCLAW_GATEWAY_TOKEN",
    "openclaw_discord_bot_token": "OPENCLAW_DISCORD_BOT_TOKEN",
    "openclaw_skill_sync_github_token": "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN",
}


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise SystemExit(f"{name} is required")
    return value


def load_copyparty_users() -> list[dict[str, Any]]:
    try:
        users = json.loads(require_env("COPYPARTY_USERS_JSON"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"COPYPARTY_USERS_JSON must be valid JSON: {exc}") from exc

    if not isinstance(users, list) or not users:
        raise SystemExit("COPYPARTY_USERS_JSON must be a non-empty JSON list")

    for index, user in enumerate(users):
        if not isinstance(user, dict):
            raise SystemExit(f"copyparty user #{index + 1} must be an object")
        if not isinstance(user.get("name"), str) or not user["name"].strip():
            raise SystemExit(f"copyparty user #{index + 1} must include a non-empty name")
        if "password_hash" in user:
            raise SystemExit(
                "COPYPARTY_USERS_JSON must use plaintext password, not password_hash"
            )
        if "password" not in user or (
            not isinstance(user.get("password"), str) or not user["password"]
        ):
            raise SystemExit(
                f"copyparty user {user.get('name', index + 1)!r} must include password"
            )

    return users


OPENCLAW_ENV = {
    key: value for key, value in REQUIRED_ENV.items() if key.startswith("openclaw_")
}


def build_mapping(scope: str = "full") -> dict[str, Any]:
    if scope not in {"full", "openclaw"}:
        raise SystemExit("deployment scope must be full or openclaw")
    required = REQUIRED_ENV if scope == "full" else OPENCLAW_ENV
    mapping = {var_name: require_env(env_name) for var_name, env_name in required.items()}

    # Secret-setting CLIs commonly read from stdin, where an accidental final
    # CR/LF is transport framing rather than part of these generated values.
    if scope == "full":
        mapping["arcane_encryption_key"] = mapping["arcane_encryption_key"].strip()
        mapping["arcane_jwt_secret"] = mapping["arcane_jwt_secret"].strip()
    mapping["openclaw_gateway_token"] = mapping["openclaw_gateway_token"].strip()
    mapping["openclaw_skill_sync_github_token"] = mapping[
        "openclaw_skill_sync_github_token"
    ].strip()

    if scope == "full" and re.fullmatch(r"[0-9a-fA-F]{64}", mapping["arcane_encryption_key"]) is None:
        raise SystemExit("ARCANE_ENCRYPTION_KEY must be exactly 64 hexadecimal characters")
    if scope == "full" and len(mapping["arcane_jwt_secret"]) < 32:
        raise SystemExit("ARCANE_JWT_SECRET must be at least 32 characters")
    if re.fullmatch(r"[0-9a-fA-F]{64}", mapping["openclaw_gateway_token"]) is None:
        raise SystemExit("OPENCLAW_GATEWAY_TOKEN must be exactly 64 hexadecimal characters")
    if not 20 <= len(mapping["openclaw_skill_sync_github_token"]) <= 4096 or any(
        character.isspace() for character in mapping["openclaw_skill_sync_github_token"]
    ):
        raise SystemExit(
            "OPENCLAW_SKILL_SYNC_GITHUB_TOKEN must be 20-4096 non-whitespace characters"
        )

    if scope == "full":
        mapping["copyparty_users"] = load_copyparty_users()

    adguard_admin_username = os.environ.get("ADGUARD_ADMIN_USERNAME") if scope == "full" else None
    if adguard_admin_username:
        mapping["adguard_admin_username"] = adguard_admin_username

    return mapping


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
    except Exception:
        try:
            path.unlink()
        finally:
            raise
    path.chmod(0o600)


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        raise SystemExit("usage: write_ansible_extra_vars.py OUTPUT_JSON [full|openclaw]")

    scope = argv[2] if len(argv) == 3 else "full"
    payload = build_mapping(scope)
    promoted = os.environ.get("OPENCLAW_SETUP_COMMIT", "")
    if promoted:
        if re.fullmatch(r"[0-9a-f]{40}", promoted) is None:
            raise SystemExit("OPENCLAW_SETUP_COMMIT must be a lowercase 40-character SHA")
        payload["openclaw_setup_expected_commit"] = promoted
    write_private_json(Path(argv[1]), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
