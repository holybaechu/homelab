#!/usr/bin/env python3
"""Convert a copied foundation checkout to the native-LXC deployment contract."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


LEGACY_TOKEN_PATH = "/run/secrets/openclaw_gateway_token"
NATIVE_TOKEN_PATH = "${OPENCLAW_GATEWAY_TOKEN_FILE}"
FORBIDDEN_TOP_LEVEL = {"agents", "models", "channels", "skills"}


def private_ipv4(value: str, label: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an IP address") from exc
    if address.version != 4 or not address.is_private:
        raise ValueError(f"{label} must be a private IPv4 address")
    return str(address)


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("origin must be an HTTPS origin without credentials or a path")
    return value


def converted_config(config: object, openclaw_ip: str, proxy_ip: str, origin: str) -> dict:
    if not isinstance(config, dict):
        raise ValueError("OpenClaw config must be a JSON object")
    forbidden = sorted(FORBIDDEN_TOP_LEVEL & set(config))
    if forbidden:
        raise ValueError(f"foundation config unexpectedly defines: {', '.join(forbidden)}")
    if set(config) != {"secrets", "gateway"}:
        raise ValueError("foundation config must contain only secrets and gateway")

    secrets = config.get("secrets")
    if not isinstance(secrets, dict) or set(secrets) != {"providers"}:
        raise ValueError("unexpected secrets configuration")
    providers = secrets.get("providers")
    if not isinstance(providers, dict) or set(providers) != {"gateway_token_file"}:
        raise ValueError("unexpected secret providers")
    provider = providers.get("gateway_token_file")
    expected_provider = {
        "source": "file",
        "path": LEGACY_TOKEN_PATH,
        "mode": "singleValue",
    }
    if provider != expected_provider:
        raise ValueError("gateway token provider does not match the Docker foundation")

    gateway = config.get("gateway")
    if not isinstance(gateway, dict):
        raise ValueError("gateway must be an object")
    if set(gateway) != {"mode", "port", "bind", "auth", "controlUi"}:
        raise ValueError("gateway contains unexpected foundation settings")
    if gateway.get("mode") != "local" or gateway.get("port") != 18789:
        raise ValueError("gateway mode or port differs from the foundation")
    if gateway.get("bind") != "lan":
        raise ValueError("gateway bind differs from the Docker foundation")

    auth = gateway.get("auth")
    if not isinstance(auth, dict) or set(auth) != {"mode", "token"}:
        raise ValueError("gateway auth contains unexpected foundation settings")
    if auth.get("mode") != "token" or auth.get("token") != {
        "source": "file",
        "provider": "gateway_token_file",
        "id": "value",
    }:
        raise ValueError("gateway auth does not use the expected token SecretRef")

    control_ui = gateway.get("controlUi")
    if not isinstance(control_ui, dict) or set(control_ui) != {"allowedOrigins"}:
        raise ValueError("gateway control UI contains unexpected foundation settings")
    allowed_origins = control_ui.get("allowedOrigins")
    if not isinstance(allowed_origins, list) or not allowed_origins:
        raise ValueError("gateway control UI must already have an origin allowlist")

    provider["path"] = NATIVE_TOKEN_PATH
    gateway["bind"] = "custom"
    gateway["customBindHost"] = openclaw_ip
    auth["rateLimit"] = {
        "maxAttempts": 10,
        "windowMs": 60000,
        "lockoutMs": 300000,
        "exemptLoopback": True,
    }
    auth["allowTailscale"] = False
    control_ui["enabled"] = True
    control_ui["allowedOrigins"] = [origin]
    gateway["trustedProxies"] = [proxy_ip]
    gateway["allowRealIpFallback"] = False
    gateway["tailscale"] = {"mode": "off", "resetOnExit": False}
    gateway["terminal"] = {"enabled": False}

    serialized = json.dumps(config, sort_keys=True)
    for stale_path in (
        LEGACY_TOKEN_PATH,
        "/home/node",
        "/srv/homelab/docker-apps/openclaw",
        "/opt/homelab-compose/openclaw-setup",
    ):
        if stale_path in serialized:
            raise ValueError(f"converted config retains a Docker-only path: {stale_path}")
    if "publicOrigin" in serialized:
        raise ValueError("gateway.publicOrigin is unsupported by the pinned schema")
    return config


README_REPLACEMENTS = {
    "| Deployment, image pin, mounts, and lifecycle | Public `homelab` repository |":
        "| Deployment, pinned native runtime, systemd, firewall, and lifecycle | Public `homelab` repository |",
    "| Runtime state, sessions, logs, and databases | `/srv/homelab/docker-apps/openclaw` |":
        "| Runtime state, sessions, logs, and databases | `/var/lib/openclaw` |",
    "| Gateway credential | `/opt/homelab-control/openclaw/secrets/gateway_token` |":
        "| Gateway credential | `/etc/openclaw/secrets/gateway_token` |",
    "The deployment mounts `config/openclaw.json` as the regular, read-only file\n"
    "`/etc/openclaw/openclaw.json` and sets\n"
    "`OPENCLAW_CONFIG_PATH=/etc/openclaw/openclaw.json`. No symlink is used.":
        "The native systemd service reads the regular file\n"
        "`/home/openclaw/openclaw-setup/config/openclaw.json` and sets\n"
        "`OPENCLAW_CONFIG_PATH=/home/openclaw/openclaw-setup/config/openclaw.json`. "
        "No symlink is used.",
    "4. From `/opt/homelab-compose/openclaw`, run the OpenClaw config validation\n"
    "   and secrets audit through the pinned deployment image.\n"
    "5. Commit the private repository, then redeploy the `openclaw` project from\n"
    "   the public homelab deployment.":
        "4. Run the pinned native OpenClaw CLI as the `openclaw` account with a\n"
        "   short-lived credential copy to validate the config and perform\n"
        "   `secrets audit --check` before committing.\n"
        "5. Commit the private repository, then reconcile the native systemd service\n"
        "   through the public homelab infrastructure deployment.",
}


def converted_readme(text: str) -> str:
    result = text
    for old, new in README_REPLACEMENTS.items():
        if old not in result:
            raise ValueError("private README does not match the expected Docker foundation")
        result = result.replace(old, new, 1)
    for stale in (
        "/srv/homelab/docker-apps/openclaw",
        "/opt/homelab-control/openclaw/secrets/gateway_token",
        "/etc/openclaw/openclaw.json",
        "/opt/homelab-compose/openclaw",
    ):
        if stale in result:
            raise ValueError(f"converted README retains a Docker-only path: {stale}")
    return result


def stage_text(path: Path, text: str, mode: int) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, mode)
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def convert(config_path: Path, readme_path: Path, openclaw_ip: str, proxy_ip: str, origin: str) -> None:
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("config must be a regular file")
    if readme_path.is_symlink() or not readme_path.is_file():
        raise ValueError("README must be a regular file")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    native = converted_config(
        config,
        private_ipv4(openclaw_ip, "OpenClaw address"),
        private_ipv4(proxy_ip, "proxy address"),
        https_origin(origin),
    )
    readme = converted_readme(readme_path.read_text(encoding="utf-8"))

    config_text = json.dumps(native, indent=2, ensure_ascii=False) + "\n"
    staged_config = stage_text(config_path, config_text, 0o640)
    staged_readme = stage_text(readme_path, readme, 0o640)
    try:
        os.replace(staged_config, config_path)
        os.replace(staged_readme, readme_path)
    finally:
        staged_config.unlink(missing_ok=True)
        staged_readme.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--readme", required=True, type=Path)
    parser.add_argument("--openclaw-ip", required=True)
    parser.add_argument("--proxy-ip", required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()

    convert(args.config, args.readme, args.openclaw_ip, args.proxy_ip, args.origin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
