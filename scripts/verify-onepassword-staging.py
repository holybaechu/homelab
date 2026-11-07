#!/usr/bin/env python3
"""Read-only administrative byte/ownership checks; never display Secret data."""
import argparse
import base64
import json
import subprocess
import sys


CONTRACTS = {
    "mysql-connect": {
        "namespace": "homelab", "name": "mysql-connect",
        "item_id": "awdei3gge3fnlyv3qyymied62u",
        "allowed": {"username", "password", "root_password", "notesPlain"},
        "sources": {"mysql-env": {
            "username": "username", "password": "password",
            "root-password": "root_password",
        }},
    },
    "n8n-connect": {
        "namespace": "homelab", "name": "n8n-connect",
        "item_id": "6esl52kqp2arngw72jrteqdbge",
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
    "cloudflare-dns": {
        "namespace": "cert-manager", "name": "cloudflare-connect",
        "item_id": "x62vzi4s2sdnerrjev6pp5ndnu",
        "allowed": {"api_token", "notesPlain"},
        "sources": {"cloudflare-dns": {"api-token": "api_token"}},
    },
    "cloudflare-ddns": {
        "namespace": "homelab", "name": "cloudflare-connect",
        "item_id": "x62vzi4s2sdnerrjev6pp5ndnu",
        "allowed": {"api_token", "notesPlain"},
        "sources": {"cloudflare-ddns": {"CLOUDFLARE_API_TOKEN": "api_token"}},
    },
    "headscale-oidc": {
        "namespace": "identity", "name": "headscale-connect",
        "item_id": "vyenqrf6ko2aficwt3dyaao2de",
        "allowed": {"oidc_client_secret", "authkey", "notesPlain"},
        "sources": {"headscale-oidc": {"client-secret": "oidc_client_secret"}},
    },
    "headscale-gateway": {
        "namespace": "networking", "name": "headscale-connect",
        "item_id": "vyenqrf6ko2aficwt3dyaao2de",
        "allowed": {"oidc_client_secret", "authkey", "notesPlain"},
        "sources": {"headscale-gateway": {"authkey": "authkey"}},
    },
    "zipline-connect": {
        "namespace": "homelab", "name": "zipline-connect",
        "item_id": "ia7seg53btlz2t4xluwkzmfjly",
        "allowed": {"core_secret", "oidc_client_secret", "hermes_api_token", "notesPlain"},
        "sources": {"zipline-env": {
            "CORE_SECRET": "core_secret", "OAUTH_OIDC_CLIENT_SECRET": "oidc_client_secret",
        }},
    },
    "langfuse-connect": {
        "namespace": "homelab", "name": "langfuse-connect",
        "item_id": "77j3ul6ntreertxl3bjkdko4h4",
        "allowed": {"database_url", "postgres_password", "clickhouse_password", "redis_auth",
                    "minio_root_password", "salt", "encryption_key", "nextauth_secret",
                    "oidc_client_secret", "langfuse_init_project_public_key",
                    "langfuse_init_project_secret_key", "notesPlain"},
        "sources": {"langfuse-env": {
            "DATABASE_URL": "database_url", "POSTGRES_PASSWORD": "postgres_password",
            "CLICKHOUSE_PASSWORD": "clickhouse_password", "REDIS_AUTH": "redis_auth",
            "MINIO_ROOT_PASSWORD": "minio_root_password", "SALT": "salt",
            "ENCRYPTION_KEY": "encryption_key", "NEXTAUTH_SECRET": "nextauth_secret",
            "AUTH_AUTHENTIK_CLIENT_SECRET": "oidc_client_secret",
            "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "langfuse_init_project_public_key",
            "LANGFUSE_INIT_PROJECT_SECRET_KEY": "langfuse_init_project_secret_key",
        }},
    },
}


def read_resource(kind, name, namespace):
    result = subprocess.run(
        ["kubectl", "-n", namespace, "get", kind, name, "-o", "json"],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def verify(item, replacement, sources, contract):
    name = item["metadata"]["name"]
    expected_path = f"vaults/3ryhjdiydkomgh3oxfl5yrg5j4/items/{contract['item_id']}"
    if item["spec"]["itemPath"] != expected_path:
        raise ValueError("unexpected source item")
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="*", metavar="CHECK",
                        help="check names; default: all staged replacements")
    args = parser.parse_args(argv)
    if set(args.checks) - CONTRACTS.keys():
        parser.error("unknown check; choose from " + ", ".join(CONTRACTS))
    for check in args.checks or CONTRACTS:
        contract = CONTRACTS[check]
        name, namespace = contract["name"], contract["namespace"]
        try:
            item = read_resource("onepassworditem", name, namespace)
            replacement = read_resource("secret", name, namespace)
            sources = {n: read_resource("secret", n, namespace) for n in contract["sources"]}
            verify(item, replacement, sources, contract)
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            print(f"FAIL {check}: access, ownership, key-set or byte check failed", file=sys.stderr)
            return 1
        print(f"PASS {check}: Ready, ownership, source path, allowed keys and exact bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
