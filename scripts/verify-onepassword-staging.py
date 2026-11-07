#!/usr/bin/env python3
"""Read-only administrative byte/ownership checks; never display Secret data."""
import base64
import json
import subprocess
import sys


CONTRACTS = {
    "mysql-connect": {
        "allowed": {"username", "password", "root_password", "notesPlain"},
        "sources": {"mysql-env": {
            "username": "username", "password": "password",
            "root-password": "root_password",
        }},
    },
    "n8n-connect": {
        "allowed": {"username", "password", "notesPlain", "encryption_key",
                    "hermes_webhook_secret", "owner_password_hash"},
        "sources": {
            "n8n-env": {
                "N8N_ENCRYPTION_KEY": "encryption_key",
                "N8N_INSTANCE_OWNER_EMAIL": "username",
                "N8N_INSTANCE_OWNER_PASSWORD_HASH": "owner_password_hash",
            },
            "hermes-webhook": {"WEBHOOK_SECRET": "hermes_webhook_secret"},
        },
    },
}


def read_resource(kind, name):
    result = subprocess.run(
        ["kubectl", "-n", "homelab", "get", kind, name, "-o", "json"],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def verify(item, replacement, sources, contract):
    name = item["metadata"]["name"]
    if not any(c["type"] == "Ready" and c["status"] == "True"
               for c in item.get("status", {}).get("conditions", [])):
        raise ValueError("Operator item is not Ready")
    if not any(o.get("apiVersion") == "onepassword.com/v1"
               and o.get("kind") == "OnePasswordItem"
               and o.get("name") == name
               and o.get("uid") == item["metadata"]["uid"]
               for o in replacement["metadata"].get("ownerReferences", [])):
        raise ValueError("replacement ownership mismatch")
    if replacement["metadata"].get("annotations", {}).get(
        "operator.1password.io/item-path"
    ) != item["spec"]["itemPath"]:
        raise ValueError("replacement source path mismatch")
    data = replacement.get("data", {})
    if set(data) - contract["allowed"]:
        raise ValueError("unexpected replacement keys; review source metadata")
    for source_name, mapping in contract["sources"].items():
        original = sources[source_name].get("data", {})
        if set(original) != set(mapping):
            raise ValueError("original key set changed")
        for old_key, new_key in mapping.items():
            old = base64.b64decode(original[old_key], validate=True)
            new = base64.b64decode(data[new_key], validate=True)
            if not old or old != new:
                raise ValueError("empty or unequal credential bytes")


def main():
    for name, contract in CONTRACTS.items():
        try:
            item = read_resource("onepassworditem", name)
            replacement = read_resource("secret", name)
            sources = {n: read_resource("secret", n) for n in contract["sources"]}
            verify(item, replacement, sources, contract)
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            print(f"FAIL {name}: access, ownership, key-set or byte check failed", file=sys.stderr)
            return 1
        print(f"PASS {name}: Ready, ownership, source path, allowed keys and exact bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
