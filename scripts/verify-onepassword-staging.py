#!/usr/bin/env python3
"""Read-only administrative byte/ownership checks; never display Secret data."""
import argparse
import base64
import json
import re
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
    "headscale-oidc": {
        "namespace": "identity", "name": "headscale-connect",
        "item_id": "vyenqrf6ko2aficwt3dyaao2de",
        "allowed": {"oidc-client-secret", "gateway-auth-key", "notesPlain"},
        "sources": {"headscale-oidc": {"client-secret": "oidc-client-secret"}},
    },
    "headscale-gateway": {
        "namespace": "networking", "name": "headscale-connect",
        "item_id": "vyenqrf6ko2aficwt3dyaao2de",
        "allowed": {"oidc-client-secret", "gateway-auth-key", "notesPlain"},
        "sources": {"headscale-gateway": {"authkey": "gateway-auth-key"}},
    },
    "zipline-connect": {
        "namespace": "homelab", "name": "zipline-connect",
        "item_id": "ia7seg53btlz2t4xluwkzmfjly",
        "allowed": {"core-secret", "oidc-client-secret", "hermes-api-token", "notesPlain"},
        "sources": {"zipline-env": {
            "CORE_SECRET": "core-secret", "OAUTH_OIDC_CLIENT_SECRET": "oidc-client-secret",
        }},
    },
    "langfuse-connect": {
        "namespace": "homelab", "name": "langfuse-connect",
        "item_id": "77j3ul6ntreertxl3bjkdko4h4",
        "allowed": {"database-url", "postgresql-password", "clickhouse-password", "redis-auth",
                    "minio-root-password", "salt", "encryption-key", "nextauth-secret",
                    "oidc-client-secret", "hermes-project-public-key",
                    "hermes-project-secret-key", "notesPlain"},
        "sources": {"langfuse-env": {
            "DATABASE_URL": "database-url", "POSTGRES_PASSWORD": "postgresql-password",
            "CLICKHOUSE_PASSWORD": "clickhouse-password", "REDIS_AUTH": "redis-auth",
            "MINIO_ROOT_PASSWORD": "minio-root-password", "SALT": "salt",
            "ENCRYPTION_KEY": "encryption-key", "NEXTAUTH_SECRET": "nextauth-secret",
            "AUTH_AUTHENTIK_CLIENT_SECRET": "oidc-client-secret",
            "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "hermes-project-public-key",
            "LANGFUSE_INIT_PROJECT_SECRET_KEY": "hermes-project-secret-key",
        }},
    },
}


VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"
# Reviewed field IDs and section membership from the pre-edit metadata export.
SOURCE_LABELS = {
    "vyenqrf6ko2aficwt3dyaao2de": ("Headscale", {
        "3u3ughywinbmkybgdopzodjnxy": ("oidc-client-secret", "add more"),
        "biwyjxnpkpvmrmy42ffpt45fpe": ("gateway-auth-key", "add more"),
    }),
    "ia7seg53btlz2t4xluwkzmfjly": ("Zipline", {
        "eteoaoehfwqkierraxfgbx5xru": ("core-secret", "add more"),
        "6rvlh45inzbb55zwnyi53spfne": ("oidc-client-secret", "add more"),
        "mq3jr7zyvy5zyff2zzpz7zrmgq": ("hermes-api-token", None),
    }),
    "77j3ul6ntreertxl3bjkdko4h4": ("Langfuse", {
        "postgres_password": ("postgresql-password", None),
        "database_url": ("database-url", None),
        "clickhouse_password": ("clickhouse-password", None),
        "redis_auth": ("redis-auth", None),
        "minio_root_password": ("minio-root-password", None),
        "salt": ("salt", None),
        "encryption_key": ("encryption-key", None),
        "nextauth_secret": ("nextauth-secret", None),
        "langfuse_init_project_public_key": ("hermes-project-public-key", None),
        "langfuse_init_project_secret_key": ("hermes-project-secret-key", None),
        "oidc_client_secret": ("oidc-client-secret", None),
    }),
}


def operator_key(label):
    # Operator v1.12.1 kubernetes_secrets_builder.go preserves ASCII case.
    label = re.sub(r"^[^a-zA-Z0-9-._]+|[^a-zA-Z0-9-._]+$", "", label)
    return re.sub(r"[^a-zA-Z0-9-._]+", "-", label)[:253]


def read_source(item_id):
    result = subprocess.run(
        ["op", "item", "get", item_id, "--vault", VAULT, "--format=json"],
        stdin=subprocess.DEVNULL, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def verify_source(source, replacement, contract):
    item_id = contract["item_id"]
    title, labels = SOURCE_LABELS[item_id]
    if (source["id"] != item_id or source["vault"]["id"] != VAULT
            or source["title"] != title or source.get("files") or source.get("urls")):
        raise ValueError("source identity, files or URLs changed")
    fields = {f["id"]: f for f in source["fields"]}
    if len(fields) != len(source["fields"]) or set(fields) != {*labels, "notesPlain"}:
        raise ValueError("source field IDs changed")
    sections = source.get("sections", [])
    expected_sections = {section for _, section in labels.values() if section}
    if (len(sections) != len(expected_sections)
            or {s["id"] for s in sections} != expected_sections
            or any(s.get("label") for s in sections)):
        raise ValueError("source sections changed")
    expected, keys = {}, set()
    for field_id, field in fields.items():
        label, section = labels.get(field_id, ("notesPlain", None))
        notes = field_id == "notesPlain"
        if (field["label"] != label
                or field["type"] != ("STRING" if notes else "CONCEALED")
                or field.get("purpose") != ("NOTES" if notes else None)
                or (field.get("section") or {}).get("id") != section):
            raise ValueError("source field metadata changed")
        key = operator_key(label)
        if not key or key in keys:
            raise ValueError("source key is empty or collides")
        keys.add(key)
        value = field.get("value", "")
        if not isinstance(value, str) or (not notes and not value):
            raise ValueError("source credential empty or invalid")
        if value:
            expected[key] = value.encode("utf-8")
    actual = {key: base64.b64decode(value, validate=True)
              for key, value in replacement.get("data", {}).items()}
    if expected != actual:
        raise ValueError("source and replacement key sets or bytes differ")


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
    if {"cloudflare-dns", "cloudflare-ddns"} & set(args.checks):
        parser.error("Cloudflare is blocked: built-in label is uneditable; keep ESO")
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
            if contract["item_id"] in SOURCE_LABELS:
                verify_source(read_source(contract["item_id"]), replacement, contract)
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError, AttributeError):
            print(f"FAIL {check}: access, ownership, key-set or byte check failed", file=sys.stderr)
            return 1
        scope = "source metadata, source/replacement/ESO bytes" if contract["item_id"] in SOURCE_LABELS else "allowed keys and exact bytes"
        print(f"PASS {check}: Ready, ownership, source path, {scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
