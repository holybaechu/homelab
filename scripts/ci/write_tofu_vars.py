#!/usr/bin/env python3
import json
import os
import stat
import sys
from pathlib import Path


STRING_VARS = {
    "PROXMOX_ENDPOINT": "proxmox_endpoint",
    "PROXMOX_API_TOKEN": "proxmox_api_token",
    "PVE_NODE_NAME": "node_name",
    "PVE_BRIDGE": "bridge",
    "PVE_ROOT_DATASTORE_ID": "root_datastore_id",
}


def _required(env, name):
    value = env.get(name)
    if value is None or value == "":
        raise SystemExit(f"{name} must be set")
    return value


def _json_or_raw(value):
    stripped = value.strip().lstrip("\ufeff").strip()
    if not stripped:
        return ""

    if stripped[0] in '[{"' or stripped in {"true", "false", "null"}:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped

    return stripped


def _string(value, name):
    parsed = _json_or_raw(value)
    if not isinstance(parsed, str):
        raise SystemExit(f"{name} must be a string")
    return parsed


def _bool(value, name):
    parsed = _json_or_raw(value)
    if isinstance(parsed, bool):
        return parsed
    if isinstance(parsed, str):
        normalized = parsed.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise SystemExit(f"{name} must be true or false")


def _public_keys(value):
    parsed = _json_or_raw(value)
    if isinstance(parsed, str):
        keys = [line.strip() for line in parsed.splitlines() if line.strip()]
    elif isinstance(parsed, list):
        keys = parsed
    else:
        raise SystemExit("DEPLOY_SSH_PUBLIC_KEYS must be a JSON list or newline list")

    if not keys or not all(isinstance(key, str) and key.strip() for key in keys):
        raise SystemExit("DEPLOY_SSH_PUBLIC_KEYS must contain at least one public key")

    return keys


def build_vars(env):
    values = {
        target: _string(_required(env, source), source)
        for source, target in STRING_VARS.items()
    }
    values["proxmox_insecure_tls"] = _bool(
        _required(env, "PROXMOX_INSECURE_TLS"), "PROXMOX_INSECURE_TLS"
    )
    values["ssh_public_keys"] = _public_keys(
        _required(env, "DEPLOY_SSH_PUBLIC_KEYS")
    )
    return values


def write_tofu_vars(output_path, env=os.environ):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_vars(env), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: write_tofu_vars.py OUTPUT_PATH")
    write_tofu_vars(argv[1])
    print(f"Wrote OpenTofu variable file: {argv[1]}")


if __name__ == "__main__":
    main(sys.argv)
