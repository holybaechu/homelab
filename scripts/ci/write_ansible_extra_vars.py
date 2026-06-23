#!/usr/bin/env python3
"""Write Ansible extra-vars containing deployment secrets with 0600 permissions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REQUIRED_ENV = {
    "cloudflare_caddy_token": "CLOUDFLARE_CADDY_TOKEN",
    "cloudflare_zone_id": "CLOUDFLARE_ZONE_ID",
    "cloudflare_ddns_token": "CLOUDFLARE_DDNS_TOKEN",
    "cloudflare_adguard_acme_token": "CLOUDFLARE_ADGUARD_ACME_TOKEN",
    "adguard_admin_password": "ADGUARD_ADMIN_PASSWORD",
    "tailscale_auth_key": "TAILSCALE_AUTH_KEY",
    "proton_wireguard_private_key": "PROTON_WIREGUARD_PRIVATE_KEY",
    "qbittorrent_webui_password": "QBITTORRENT_WEBUI_PASSWORD",
    "hermes_discord_bot_token": "HERMES_DISCORD_BOT_TOKEN",
    "hermes_discord_allowed_users": "HERMES_DISCORD_ALLOWED_USERS",
    "hermes_parallel_api_key": "PARALLEL_API_KEY",
    "hermes_firecrawl_api_key": "FIRECRAWL_API_KEY",
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


def build_mapping() -> dict[str, Any]:
    mapping = {var_name: require_env(env_name) for var_name, env_name in REQUIRED_ENV.items()}
    mapping["copyparty_users"] = load_copyparty_users()

    adguard_admin_username = os.environ.get("ADGUARD_ADMIN_USERNAME")
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
    if len(argv) != 2:
        raise SystemExit("usage: write_ansible_extra_vars.py OUTPUT_JSON")

    write_private_json(Path(argv[1]), build_mapping())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
