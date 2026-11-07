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


class CheckFailure(ValueError):
    """Only fixed categories and reviewed contract identifiers may enter messages."""


def key_summary(expected, actual, reviewed):
    known = sorted(set(actual) & reviewed)
    return (f"expected={','.join(sorted(expected)) or 'none'} "
            f"actual-reviewed={','.join(known) or 'none'} "
            f"unreviewed-count={len(set(actual) - reviewed)}")


def decode_data(resource):
    try:
        return {key: base64.b64decode(value, validate=True)
                for key, value in resource.get("data", {}).items()}
    except (ValueError, TypeError):
        raise CheckFailure("secret-data-invalid") from None


def read_source(item_id):
    result = subprocess.run(
        ["op", "item", "get", item_id, "--vault", VAULT, "--format=json"],
        stdin=subprocess.DEVNULL, capture_output=True, check=True,
    )
    source = json.loads(result.stdout)
    if not isinstance(source, dict):
        raise ValueError("invalid source response")
    return source


def verify_source(source, replacement, contract):
    item_id = contract["item_id"]
    title, labels = SOURCE_LABELS[item_id]
    if (source["id"] != item_id or source["vault"]["id"] != VAULT
            or source["title"] != title or source.get("files") or source.get("urls")):
        raise CheckFailure("source-identity-or-extra-content")
    fields = {f["id"]: f for f in source["fields"]}
    if len(fields) != len(source["fields"]) or set(fields) != {*labels, "notesPlain"}:
        raise CheckFailure("source-field-ids")
    sections = source.get("sections", [])
    expected_sections = {section for _, section in labels.values() if section}
    if (len(sections) != len(expected_sections)
            or {s["id"] for s in sections} != expected_sections
            or any(s.get("label") for s in sections)):
        raise CheckFailure("source-sections")
    expected, keys = {}, set()
    for field_id, field in fields.items():
        label, section = labels.get(field_id, ("notesPlain", None))
        notes = field_id == "notesPlain"
        if field["label"] != label:
            raise CheckFailure(f"source-label field={field_id} expected-label={label}")
        if field["type"] != ("STRING" if notes else "CONCEALED"):
            raise CheckFailure(f"source-field-type field={field_id}")
        if field.get("purpose") != ("NOTES" if notes else None):
            raise CheckFailure(f"source-field-purpose field={field_id}")
        if (field.get("section") or {}).get("id") != section:
            raise CheckFailure(f"source-field-section field={field_id}")
        key = operator_key(label)
        if not key or key in keys:
            raise CheckFailure("source-key-empty-or-collision")
        keys.add(key)
        value = field.get("value", "")
        if not isinstance(value, str) or (not notes and not value):
            raise CheckFailure(f"source-credential-empty-or-invalid field={field_id}")
        if value:
            expected[key] = value.encode("utf-8")
    actual = decode_data(replacement)
    if set(expected) != set(actual):
        raise CheckFailure("source-replacement-keys " +
                           key_summary(expected, actual, contract["allowed"]))
    if expected != actual:
        different = sorted(key for key in expected if expected[key] != actual[key])
        raise CheckFailure("source-replacement-bytes keys=" + ",".join(different))


def read_resource(kind, name, namespace):
    result = subprocess.run(
        ["kubectl", "-n", namespace, "get", kind, name, "-o", "json"],
        capture_output=True, check=True,
    )
    resource = json.loads(result.stdout)
    if not isinstance(resource, dict):
        raise ValueError("invalid resource response")
    return resource


def verify(item, replacement, sources, contract):
    name = item["metadata"]["name"]
    expected_path = f"vaults/3ryhjdiydkomgh3oxfl5yrg5j4/items/{contract['item_id']}"
    if item["spec"]["itemPath"] != expected_path:
        raise CheckFailure("item-path")
    ready = [c["status"] for c in item.get("status", {}).get("conditions", [])
             if c["type"] == "Ready"]
    if ready != ["True"]:
        state = ready[0] if len(ready) == 1 and ready[0] in {"False", "Unknown"} else (
            "missing" if not ready else "invalid")
        raise CheckFailure(f"operator-not-ready Ready={state}")
    if not any(o.get("apiVersion") == "onepassword.com/v1"
               and o.get("kind") == "OnePasswordItem"
               and o.get("name") == name
               and o.get("uid") == item["metadata"]["uid"]
               for o in replacement["metadata"].get("ownerReferences", [])):
        raise CheckFailure("replacement-ownership")
    if replacement["metadata"].get("annotations", {}).get(
        "operator.1password.io/item-path"
    ) != item["spec"]["itemPath"]:
        raise CheckFailure("replacement-item-path")
    data = replacement.get("data", {})
    if set(data) - contract["allowed"]:
        raise CheckFailure("replacement-keys " +
                           key_summary(contract["allowed"], data, contract["allowed"]))
    for source_name, mapping in contract["sources"].items():
        original = sources[source_name].get("data", {})
        if set(original) != set(mapping):
            raise CheckFailure(f"eso-keys Secret/{source_name} " +
                               key_summary(mapping, original, set(mapping)))
        missing = set(mapping.values()) - set(data)
        if missing:
            raise CheckFailure("replacement-missing-keys keys=" + ",".join(sorted(missing)))
        old_data = decode_data(sources[source_name])
        new_data = decode_data(replacement)
        for old_key, new_key in mapping.items():
            old = old_data[old_key]
            new = new_data[new_key]
            if not old or old != new:
                raise CheckFailure(f"eso-replacement-bytes "
                                   f"Secret/{source_name} key={old_key} replacement-key={new_key}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="*", metavar="CHECK",
                        help="check names; default: all staged replacements")
    args = parser.parse_args(argv)
    if {"cloudflare-dns", "cloudflare-ddns"} & set(args.checks):
        parser.error("Cloudflare is blocked: built-in label is uneditable; keep ESO")
    if set(args.checks) - CONTRACTS.keys():
        parser.error("unknown check; choose from " + ", ".join(CONTRACTS))
    failed = False
    for check in args.checks or CONTRACTS:
        contract = CONTRACTS[check]
        name, namespace = contract["name"], contract["namespace"]
        errors = []

        def attempt(stage, function, *values):
            try:
                return function(*values)
            except CheckFailure as error:
                category = str(error)
            except subprocess.CalledProcessError as error:
                category = "command-failed"
                # Never echo stderr, even when it appears to contain only metadata.
                stderr = error.stderr or b""
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                for marker, hint in (("(Forbidden)", "access-denied"),
                                     ("(Unauthorized)", "unauthorized"),
                                     ("(NotFound)", "not-found")):
                    if stage.startswith("read Kubernetes/") and marker in stderr:
                        category = hint
                        break
                category += f" exit={int(error.returncode)}"
            except FileNotFoundError:
                category = "executable-not-found"
            except OSError:
                category = "execution-error"
            except (ValueError, KeyError, TypeError, AttributeError):
                category = "invalid-response-schema"
            errors.append(f"FAIL {check}: stage={stage} category={category}")
            return None

        item_target = f"Kubernetes/{namespace}/OnePasswordItem/{name}"
        secret_target = f"Kubernetes/{namespace}/Secret/{name}"
        item = attempt("read " + item_target, read_resource, "onepassworditem", name, namespace)
        replacement = attempt("read " + secret_target, read_resource, "secret", name, namespace)
        sources = {n: attempt(f"read Kubernetes/{namespace}/Secret/{n}",
                              read_resource, "secret", n, namespace)
                   for n in contract["sources"]}
        source = None
        has_source = contract["item_id"] in SOURCE_LABELS
        if has_source:
            source = attempt(f"read 1Password/Item/{contract['item_id']}",
                             read_source, contract["item_id"])
        if item is not None and replacement is not None and all(s is not None for s in sources.values()):
            attempt("verify " + item_target + "+Secret/" + name,
                    verify, item, replacement, sources, contract)
        if source is not None and replacement is not None:
            attempt("verify 1Password/Item/" + contract["item_id"] + "+" + secret_target,
                    verify_source, source, replacement, contract)
        if errors:
            failed = True
            for error in errors:
                print(error, file=sys.stderr)
            if isinstance(source, dict) and isinstance(replacement, dict):
                version = source.get("version")
                metadata = replacement.get("metadata")
                annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
                synced = annotations.get("operator.1password.io/item-version") if isinstance(annotations, dict) else None
                state = "unavailable"
                if type(version) is int and isinstance(synced, str) and synced.isascii() and synced.isdecimal():
                    state = "match" if str(version) == synced else "mismatch"
                print(f"INFO {check}: source-replacement-item-version={state}", file=sys.stderr)
            continue
        scope = "source metadata, source/replacement/ESO bytes" if has_source else "allowed keys and exact bytes"
        print(f"PASS {check}: Ready, ownership, source path, {scope}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
