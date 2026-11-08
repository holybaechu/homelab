#!/usr/bin/env python3
"""Read-only administrative byte/ownership checks; never display Secret data."""
import argparse
import base64
import json
import re
import subprocess
import sys


CONTRACTS = {
    "hermes-runtime-connect": {
        "namespace": "homelab", "name": "hermes-runtime-connect",
        "item_id": "rvmf543rf6sefjrqiu7csfa2ra",
        "allowed": {"discord-bot-token", "discord-allowed-users", "discord-home-channel",
                    "exa-api-key", "browser-use-api-key", "perplexity-api-key", "notesPlain"},
        "sources": {
            "hermes-discord": {"DISCORD_BOT_TOKEN": "discord-bot-token",
                               "DISCORD_ALLOWED_USERS": "discord-allowed-users",
                               "DISCORD_HOME_CHANNEL": "discord-home-channel"},
            "hermes-web": {"EXA_API_KEY": "exa-api-key"},
            "hermes-browser": {"BROWSER_USE_API_KEY": "browser-use-api-key"},
        },
    },
    "opencloud-connect": {
        "namespace": "homelab", "name": "opencloud-connect",
        "item_id": "kxywehnrosbeoxfby2ujyntdo4",
        "allowed": {"username", "password", "notesPlain"}, "sources": {},
        "composite": ("hermes-services", {
            "OPENCLOUD_USERNAME": ("opencloud-connect", "username"),
            "OPENCLOUD_APP_TOKEN": ("opencloud-connect", "password"),
            "ZIPLINE_TOKEN": ("zipline-connect", "hermes-api-token"),
            "QBITTORRENT_USERNAME": ("qbittorrent-connect", "username"),
            "QBITTORRENT_PASSWORD": ("qbittorrent-connect", "password"),
        }),
    },
    "github-connect": {
        "namespace": "homelab", "name": "github-connect",
        "item_id": "ioa2eqgh3qmg36ecdjvwx7bpo4",
        "allowed": {"username", "credential", "type", "filename", "valid-from",
                    "expires", "hostname", "notesPlain"}, "sources": {},
        "required_fields": {"credential"},
        "composite": ("hermes-coder-git", {
            "github-token": ("github-connect", "credential"),
            "signing-key": ("signing-connect", "private-key"),
        }),
    },
    "signing-connect": {
        "namespace": "homelab", "name": "signing-connect",
        "item_id": "fizdpxpyp5ommiouutzdlsgmnu",
        "allowed": {"public-key", "private-key", "key-type", "fingerprint", "notesPlain"},
        "sources": {},
        "references": {
            "op://Homelab/fizdpxpyp5ommiouutzdlsgmnu/private_key?ssh-format=openssh": "private-key",
        },
        "composite": ("hermes-coder-git", {
            "github-token": ("github-connect", "credential"),
            "signing-key": ("signing-connect", "private-key"),
        }),
    },
    "cloudflare-dns": {
        "namespace": "cert-manager", "name": "cloudflare-connect",
        "item_id": "x62vzi4s2sdnerrjev6pp5ndnu",
        "allowed": {"notes-plain", "username", "credential", "type", "filename", "valid-from", "expires", "hostname"},
        "sources": {"cloudflare-dns": {"api-token": "credential"}},
        "notes_label": "notes-plain", "required_fields": {"credential"},
    },
    "cloudflare-ddns": {
        "namespace": "homelab", "name": "cloudflare-connect",
        "item_id": "x62vzi4s2sdnerrjev6pp5ndnu",
        "allowed": {"notes-plain", "username", "credential", "type", "filename", "valid-from", "expires", "hostname"},
        "sources": {"cloudflare-ddns": {"CLOUDFLARE_API_TOKEN": "credential"}},
        "notes_label": "notes-plain", "required_fields": {"credential"},
    },
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
        "required_fields": {"username", "encryption_key", "hermes_webhook_secret", "owner_password_hash"},
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
        "references": {
            "op://Homelab/Zipline/mq3jr7zyvy5zyff2zzpz7zrmgq": "hermes-api-token",
        },
    },
    "adguard-connect": {
        "namespace": "networking", "name": "adguard-connect",
        "item_id": "n2olk4p3ngumyuttl6b7itutba",
        "allowed": {"username", "password", "password-hash", "notesPlain"},
        "sources": {},
        "rendered": ("adguard-credentials", "adguard-config-staging", "AdGuardHome.yaml"),
    },
    "qbittorrent-connect": {
        "namespace": "homelab", "name": "qbittorrent-connect",
        "item_id": "ztl6gih3jqixgb5xufbv4xq4bi",
        "allowed": {"username", "password", "password-hash", "notesPlain"},
        "sources": {},
        "rendered": ("qbittorrent-credentials", "qbittorrent-config-staging", "managed.conf"),
    },
    "immich-connect": {
        "namespace": "homelab", "name": "immich-connect",
        "item_id": "ltncwd5wyxwknf5xfqwxraqmtm",
        "allowed": {"database-password", "oidc-client-secret", "hermes-api-key", "notesPlain"},
        "sources": {"hermes-immich": {"IMMICH_API_KEY": "hermes-api-key"}},
        "immich_config": True,
    },
    "authentik-connect": {
        "namespace": "identity", "name": "authentik-connect",
        "item_id": "d7ginmnpodzvevvb472v26jvmq",
        "allowed": {"username", "password", "email", "secret-key", "database-password", "notesPlain"},
        "sources": {"authentik-postgresql": {"POSTGRES_PASSWORD": "database-password"}},
        "authentik_env": True,
    },
    "obsidian-connect": {
        "namespace": "homelab", "name": "obsidian-connect",
        "item_id": "3galz5zqqwdrqoj2mjk4wcxfdy",
        "allowed": {"auth-token", "vault-password", "vault-name", "notesPlain"},
        "sources": {"hermes-obsidian": {"OBSIDIAN_AUTH_TOKEN": "auth-token"}},
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
        }, "hermes-langfuse": {
            "HERMES_LANGFUSE_PUBLIC_KEY": "hermes-project-public-key",
            "HERMES_LANGFUSE_SECRET_KEY": "hermes-project-secret-key",
        }},
    },
}


VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"
# Reviewed field IDs and section membership from the pre-edit metadata export.
SOURCE_LABELS = {
    "rvmf543rf6sefjrqiu7csfa2ra": ("Hermes Runtime", {
        "op3m7hudiprbm63fk4wcxqglmi": ("discord-bot-token", None),
        "fn446i6ddovmn7n4jcmck5afg4": ("discord-allowed-users", None),
        "7ooupb36fhrns3ylsnros4fj6e": ("discord-home-channel", None),
        "dhkzzpalgt33qdmvlm6knod72a": ("exa-api-key", None),
        "52qljnwq55ysuf3hb2r4vxawni": ("browser-use-api-key", None),
        "t4554lneydfcucvqsqjuck2zdi": ("perplexity-api-key", None),
    }),
    "kxywehnrosbeoxfby2ujyntdo4": ("OpenCloud", {
        "username": ("username", None), "password": ("password", None),
    }),
    "ioa2eqgh3qmg36ecdjvwx7bpo4": ("GitHub Hermes", {
        "username": ("username", None), "credential": ("credential", None),
        "type": ("type", None), "filename": ("filename", None),
        "validFrom": ("valid from", None), "expires": ("expires", None),
        "hostname": ("hostname", None),
    }),
    "fizdpxpyp5ommiouutzdlsgmnu": ("Hermes Coder Signing", {
        "public_key": ("public key", None), "private_key": ("private key", None),
        "key_type": ("key type", None), "fingerprint": ("fingerprint", None),
    }),
    "6esl52kqp2arngw72jrteqdbge": ("n8n", {
        "username": ("username", None), "password": ("password", None),
        "encryption_key": ("encryption_key", None),
        "hermes_webhook_secret": ("hermes_webhook_secret", None),
        "owner_password_hash": ("owner_password_hash", None),
    }),
    "x62vzi4s2sdnerrjev6pp5ndnu": ("Cloudflare DNS", {
        "username": ("username", None), "credential": ("credential", None),
        "type": ("type", None), "filename": ("filename", None),
        "validFrom": ("valid-from", None), "expires": ("expires", None),
        "hostname": ("hostname", None),
    }),
    "n2olk4p3ngumyuttl6b7itutba": ("AdGuard Home", {
        "username": ("username", None), "password": ("password", None),
        "vu2pnknisjne6k4t5sh5laxf3e": ("password-hash", "add more"),
    }),
    "ztl6gih3jqixgb5xufbv4xq4bi": ("qBittorrent", {
        "username": ("username", None), "password": ("password", None),
        "t25w4tvxducf5bqt5owlk4mvjy": ("password-hash", "add more"),
    }),
    "ltncwd5wyxwknf5xfqwxraqmtm": ("Immich", {
        "trytrwmlxrksnvjujdd57hqk7a": ("database-password", None),
        "sf4ch3lie57owvqxdwvl3cl6he": ("oidc-client-secret", None),
        "kpjesisize67f6kdidzwdhrriq": ("hermes-api-key", None),
    }),
    "d7ginmnpodzvevvb472v26jvmq": ("authentik [Admin]", {
        "username": ("username", None), "password": ("password", None),
        "c6t67ly54dysrlik57ft332464": ("email", "add more"),
        "pinyekye2iau23hchko7js763a": ("secret-key", "add more"),
        "fhw4wuych7l2y5fs62p5leldtm": ("database-password", "add more"),
    }),
    "3galz5zqqwdrqoj2mjk4wcxfdy": ("Obsidian Sync", {
        "auth_token": ("auth-token", None), "vault_password": ("vault-password", None),
        "vault_name": ("vault-name", None),
    }),
    "vyenqrf6ko2aficwt3dyaao2de": ("Headscale", {
        "3u3ughywinbmkybgdopzodjnxy": ("oidc-client-secret", "add more"),
        "biwyjxnpkpvmrmy42ffpt45fpe": ("gateway-auth-key", "add more"),
    }),
    "ia7seg53btlz2t4xluwkzmfjly": ("Zipline", {
        "eteoaoehfwqkierraxfgbx5xru": ("core-secret", "add more"),
        "6rvlh45inzbb55zwnyi53spfne": ("oidc-client-secret", "add more"),
        "mq3jr7zyvy5zyff2zzpz7zrmgq": ("hermes-api-token", "add more"),
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


def field_metadata(item_id, field_id):
    if field_id == "notesPlain":
        return "STRING", "NOTES"
    if item_id == "rvmf543rf6sefjrqiu7csfa2ra" and field_id in {
            "fn446i6ddovmn7n4jcmck5afg4", "7ooupb36fhrns3ylsnros4fj6e"}:
        return "STRING", None
    if item_id == "fizdpxpyp5ommiouutzdlsgmnu":
        return ("SSHKEY" if field_id == "private_key" else "STRING"), None
    if item_id in {"x62vzi4s2sdnerrjev6pp5ndnu", "ioa2eqgh3qmg36ecdjvwx7bpo4"}:
        return ({"credential": "CONCEALED", "type": "MENU", "validFrom": "DATE", "expires": "DATE"}
                .get(field_id, "STRING")), None
    if item_id in {"d7ginmnpodzvevvb472v26jvmq", "n2olk4p3ngumyuttl6b7itutba",
                   "kxywehnrosbeoxfby2ujyntdo4", "6esl52kqp2arngw72jrteqdbge",
                   "ztl6gih3jqixgb5xufbv4xq4bi"}:
        if field_id == "username":
            return "STRING", "USERNAME"
        if field_id == "password":
            return "CONCEALED", "PASSWORD"
        if field_id == "c6t67ly54dysrlik57ft332464":
            return "EMAIL", None
    if item_id == "3galz5zqqwdrqoj2mjk4wcxfdy" and field_id == "vault_name":
        return "STRING", None
    return "CONCEALED", None


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


def value_shape(field):
    value = field.get("value")
    return ("absent" if "value" not in field else "null" if value is None else
            "empty-string" if value == "" else "nonempty-string" if isinstance(value, str) else
            "number-zero" if type(value) in (int, float) and value == 0 else
            "number-nonzero" if type(value) in (int, float) else "other")


def private_key_format(value):
    if not value:
        return "empty"
    for header, kind in ((b"-----BEGIN OPENSSH PRIVATE KEY-----", "openssh"),
                         (b"-----BEGIN PRIVATE KEY-----", "pkcs8"),
                         (b"-----BEGIN ENCRYPTED PRIVATE KEY-----", "encrypted-pkcs8"),
                         (b"-----BEGIN RSA PRIVATE KEY-----", "pkcs1-rsa"),
                         (b"-----BEGIN EC PRIVATE KEY-----", "sec1-ec")):
        if value.startswith(header + b"\n") or value.startswith(header + b"\r\n"):
            return kind
    return "unrecognized"


def require_replacement_keys(data, keys, name):
    missing = set(keys) - set(data)
    if missing:
        raise CheckFailure(f"replacement-missing-keys Secret/{name} keys=" + ",".join(sorted(missing)))


def source_metadata_summary(source, contract):
    """Fixed structural diagnostics, never arbitrary source strings or values."""
    item_id = contract["item_id"]
    title, labels = SOURCE_LABELS[item_id]
    fields = source["fields"]
    sections = source.get("sections") or []
    expected_sections = {section for _, section in labels.values() if section}
    if not isinstance(fields, list) or not isinstance(sections, list):
        raise ValueError("invalid source metadata")
    lines = [
        "identity " + " ".join(f"{key}-match={str(matches).lower()}" for key, matches in (
            ("item", source["id"] == item_id),
            ("vault", source["vault"]["id"] == VAULT),
            ("title", source["title"] == title))),
        f"content files-count={len(source.get('files') or [])} "
        f"urls-count={len(source.get('urls') or [])} "
        f"fields-count={len(fields)} sections-count={len(sections)} "
        f"unreviewed-fields-count={sum(f['id'] not in {*labels, 'notesPlain'} for f in fields)}",
    ]
    field_keys = {operator_key(f.get("label", "")) for f in fields}
    for index, url in enumerate(source.get("urls") or []):
        label = url.get("label") or ""
        key = operator_key(label)
        lines.append(f"url-index={index} " + " ".join(
            f"{name}={str(value).lower()}" for name, value in (
                ("label-empty", not label), ("operator-key-empty", not key),
                ("field-key-collision", bool(key) and key in field_keys),
                ("key-reviewed", bool(key) and key in contract["allowed"]),
                ("href-present", bool(url.get("href"))))))
    for index, section in enumerate(sections):
        known = section["id"] in expected_sections
        lines.append(f"section-index={index} id-reviewed={str(known).lower()} "
                     f"label-present={str(bool(section.get('label'))).lower()}")
    for field_id, (label, expected_section) in {
            **labels, "notesPlain": (contract.get("notes_label", "notesPlain"), None)}.items():
        matches = [f for f in fields if f["id"] == field_id]
        lines.append(f"field={field_id} occurrences={len(matches)}")
        if len(matches) != 1:
            continue
        field = matches[0]
        section_id = (field.get("section") or {}).get("id")
        indexes = [str(i) for i, s in enumerate(sections) if s["id"] == section_id]
        expected_type, expected_purpose = field_metadata(item_id, field_id)
        lines.append(f"field={field_id} " + " ".join(
            f"{key}-match={str(match).lower()}" for key, match in (
                ("label", field.get("label") == label),
                ("type", field.get("type") == expected_type),
                ("purpose", field.get("purpose") == expected_purpose),
                ("section", section_id == expected_section))) +
            f" section-present={str(section_id is not None).lower()} "
            f"section-index={','.join(indexes) or 'none'}")
        if expected_type == "DATE":
            lines.append(f"field={field_id} value-shape={value_shape(field)}")
        if expected_type == "SSHKEY":
            value = field.get("value")
            shape = private_key_format(value.encode("utf-8")) if isinstance(value, str) else "invalid-type"
            lines.append(f"field={field_id} private-key-format={shape}")
    return lines


def verify_source(source, replacement, contract):
    item_id = contract["item_id"]
    title, labels = SOURCE_LABELS[item_id]
    urls = source.get("urls") or []
    # Exact reviewed labels which Operator 1.12.1 skips, not arbitrary URLs.
    empty_label_sources = {"77j3ul6ntreertxl3bjkdko4h4", "ioa2eqgh3qmg36ecdjvwx7bpo4",
                           "6esl52kqp2arngw72jrteqdbge"}
    skipped_url = (
        item_id in empty_label_sources | {"d7ginmnpodzvevvb472v26jvmq",
                    "n2olk4p3ngumyuttl6b7itutba", "ztl6gih3jqixgb5xufbv4xq4bi"}
        and isinstance(urls, list)
        and len(urls) == 1 and isinstance(urls[0], dict)
        and (urls[0].get("label") in (None, "") if item_id in empty_label_sources
             else urls[0].get("label") == "\uc6f9\uc0ac\uc774\ud2b8")
        and operator_key(urls[0].get("label") or "") == ""
        and isinstance(urls[0].get("href"), str) and bool(urls[0]["href"])
    )
    if (source["id"] != item_id or source["vault"]["id"] != VAULT
            or source["title"] != title or source.get("files")
            or (urls and not skipped_url)):
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
        label, section = labels.get(field_id, (contract.get("notes_label", "notesPlain"), None))
        expected_type, expected_purpose = field_metadata(item_id, field_id)
        if field["label"] != label:
            raise CheckFailure(f"source-label field={field_id} expected-label={label}")
        if field["type"] != expected_type:
            raise CheckFailure(f"source-field-type field={field_id}")
        if field.get("purpose") != expected_purpose:
            raise CheckFailure(f"source-field-purpose field={field_id}")
        if (field.get("section") or {}).get("id") != section:
            raise CheckFailure(f"source-field-section field={field_id}")
        key = operator_key(label)
        if not key or key in keys:
            raise CheckFailure("source-key-empty-or-collision")
        keys.add(key)
        value = field.get("value", "")
        required = field_id in contract.get("required_fields", set(labels))
        if value is None and not required and item_id in {
                "x62vzi4s2sdnerrjev6pp5ndnu", "ioa2eqgh3qmg36ecdjvwx7bpo4"}:
            value = ""
        if item_id == "x62vzi4s2sdnerrjev6pp5ndnu" and expected_type == "DATE" and value:
            raise CheckFailure(f"source-date-not-unset field={field_id}")
        if not isinstance(value, str) or (required and not value):
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


def verify_references(replacement, contract):
    data = decode_data(replacement)
    for reference, key in contract.get("references", {}).items():
        result = subprocess.run(
            ["op", "read", "--no-newline", reference],
            stdin=subprocess.DEVNULL, capture_output=True, check=True,
        )
        if not result.stdout or result.stdout != data.get(key):
            detail = ""
            if contract["name"] == "signing-connect":
                detail = (f" replacement-format={private_key_format(data.get(key))}"
                          f" reference-format={private_key_format(result.stdout)}")
            raise CheckFailure(f"source-reference-bytes key={key}" + detail)


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


def verify_authentik_env(original, replacements):
    projections = {
        "AUTHENTIK_SECRET_KEY": ("authentik-connect", "secret-key"),
        "AUTHENTIK_POSTGRESQL__PASSWORD": ("authentik-connect", "database-password"),
        "AUTHENTIK_BOOTSTRAP_EMAIL": ("authentik-connect", "email"),
        "AUTHENTIK_BOOTSTRAP_PASSWORD": ("authentik-connect", "password"),
        "HEADSCALE_OIDC_CLIENT_SECRET": ("headscale-connect", "oidc-client-secret"),
        "ZIPLINE_OIDC_CLIENT_SECRET": ("zipline-connect", "oidc-client-secret"),
        "IMMICH_OIDC_CLIENT_SECRET": ("immich-connect", "oidc-client-secret"),
        "LANGFUSE_OIDC_CLIENT_SECRET": ("langfuse-connect", "oidc-client-secret"),
    }
    old = decode_data(original)
    if set(old) != set(projections):
        raise CheckFailure("eso-keys Secret/authentik-env")
    for key, (name, replacement_key) in projections.items():
        data = decode_data(replacements[name])
        require_replacement_keys(data, {replacement_key}, name)
        value = data[replacement_key]
        if not value or value != old[key]:
            raise CheckFailure(f"eso-replacement-bytes Secret/authentik-env key={key}")


def verify_composite(original, replacements, contract):
    name, projections = contract["composite"]
    old = decode_data(original)
    if set(old) != set(projections):
        raise CheckFailure(f"eso-keys Secret/{name}")
    for key, (replacement, field) in projections.items():
        data = decode_data(replacements[replacement])
        require_replacement_keys(data, {field}, replacement)
        value = data[field]
        if not value or value != old[key]:
            raise CheckFailure(f"eso-replacement-bytes Secret/{name} key={key}")


def verify_rendered_config(replacement, original, template, contract):
    _, _, key = contract["rendered"]
    old, new = decode_data(original), decode_data(replacement)
    if set(old) != {key}:
        raise CheckFailure("eso-rendered-keys")
    require_replacement_keys(new, {"username", "password-hash"}, contract["name"])
    username = new["username"].decode("utf-8")
    password_hash = new["password-hash"].decode("utf-8")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        raise CheckFailure("rendered-username-invalid")
    adguard = key == "AdGuardHome.yaml"
    pattern = (r"\$2[aby]\$(0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}" if adguard else
               r"@ByteArray\([A-Za-z0-9+/]{22}==:[A-Za-z0-9+/]{86}==\)")
    if not re.fullmatch(pattern, password_hash):
        raise CheckFailure("rendered-password-hash-invalid")
    text = template["data"][key]
    start = "schema_version: 34\n" if adguard else "[Preferences]\n"
    body = text[text.index(start):]
    body = body.replace("{{ .username | toJson }}" if adguard else "{{ .username }}",
                        json.dumps(username) if adguard else username)
    body = body.replace("{{ .password_hash | toJson }}", json.dumps(password_hash))
    if "{{" in body or "}}" in body or body.encode("utf-8") != old[key]:
        raise CheckFailure("eso-rendered-config-bytes")


def verify_immich_config(replacement, original, template):
    old = decode_data(original)
    new = decode_data(replacement)
    if set(old) != {"DB_PASSWORD", "immich.json"}:
        raise CheckFailure("eso-keys Secret/immich")
    require_replacement_keys(new, {"database-password", "oidc-client-secret"}, "immich-connect")
    if not new.get("database-password") or old["DB_PASSWORD"] != new["database-password"]:
        raise CheckFailure("eso-replacement-bytes Secret/immich key=DB_PASSWORD")
    config = json.loads(template["data"]["immich.json"])
    if not isinstance(config.get("oauth"), dict) or "clientSecret" in config["oauth"]:
        raise CheckFailure("immich-public-template-invalid")
    secret = new["oidc-client-secret"].decode("utf-8")
    if not secret:
        raise CheckFailure("immich-oidc-empty")
    config["oauth"]["clientSecret"] = secret
    if json.loads(old["immich.json"]) != config:
        raise CheckFailure("immich-config-content")


def verify_signing_identity(raw, reference, public):
    from cryptography.hazmat.primitives import serialization as s

    if (private_key_format(raw) != "pkcs8"
            or private_key_format(reference) != "openssh"):
        raise CheckFailure("signing-container-format")
    key = s.load_pem_private_key(raw, password=None)
    old = s.load_ssh_private_key(reference, password=None)
    encoded = key.private_bytes(s.Encoding.PEM, s.PrivateFormat.OpenSSH, s.NoEncryption())
    converted = s.load_ssh_private_key(encoded, password=None)
    private = lambda k: k.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())
    if private(key) != private(old) or private(key) != private(converted):
        raise CheckFailure("signing-private-identity")
    supplied_public = s.load_ssh_public_key(public)
    public_bytes = lambda k: k.public_bytes(s.Encoding.DER, s.PublicFormat.SubjectPublicKeyInfo)
    if public_bytes(supplied_public) != public_bytes(key.public_key()):
        raise CheckFailure("signing-public-identity")
    return encoded


def signing_conversion_gate():
    stage = "dependency"
    try:
        import cryptography  # Only this opt-in gate requires the serialization library.
        replacements = {}
        for name in ("github-connect", "signing-connect"):
            contract = CONTRACTS[name]
            stage = "read-" + name
            item = read_resource("onepassworditem", name, "homelab")
            replacement = read_resource("secret", name, "homelab")
            source = read_source(contract["item_id"])
            stage = "verify-" + name
            verify(item, replacement, {}, contract)
            verify_source(source, replacement, contract)
            replacements[name] = decode_data(replacement)
        stage = "retained-contract"
        original = decode_data(read_resource("secret", "hermes-coder-git", "homelab"))
        reference = next(iter(CONTRACTS["signing-connect"]["references"]))
        result = subprocess.run(["op", "read", "--no-newline", reference],
                                stdin=subprocess.DEVNULL, capture_output=True, check=True)
        if (set(original) != {"github-token", "signing-key"}
                or not result.stdout or original["signing-key"] != result.stdout
                or original["github-token"] != replacements["github-connect"]["credential"]):
            raise CheckFailure("retained-reference-bytes")
        stage = "conversion-identity"
        verify_signing_identity(replacements["signing-connect"]["private-key"], result.stdout,
                                replacements["signing-connect"]["public-key"])
    except Exception:
        # Library/CLI errors may embed key data; expose only fixed execution stages.
        print(f"FAIL signing-conversion: stage={stage} no-writes", file=sys.stderr)
        return 1
    print("PASS signing-conversion: Ready, ownership, source metadata/raw bytes, "
          "retained GitHub/OpenSSH bytes, private/public identity and OpenSSH conversion; "
          "no writes or consumer cutover")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="*", metavar="CHECK",
                        help="check names; default: all staged replacements")
    parser.add_argument("--source-metadata", action="store_true",
                        help="read only 1Password structure; no Kubernetes or byte checks")
    parser.add_argument("--signing-conversion", action="store_true",
                        help="read-only PKCS8/OpenSSH identity gate; requires cryptography")
    args = parser.parse_args(argv)
    if args.signing_conversion:
        if args.source_metadata or args.checks:
            parser.error("--signing-conversion cannot be combined with other checks")
        return signing_conversion_gate()
    if args.source_metadata and (not args.checks or any(
            name not in CONTRACTS or CONTRACTS[name]["item_id"] not in SOURCE_LABELS
            for name in args.checks)):
        parser.error("--source-metadata requires explicit source-reviewed check names")

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

        if args.source_metadata:
            source = attempt(f"read 1Password/Item/{contract['item_id']}",
                             read_source, contract["item_id"])
            if source is not None:
                lines = attempt("source-metadata", source_metadata_summary, source, contract)
                if lines is not None:
                    for line in lines:
                        print(f"INFO {check}: metadata {line}")
            if errors:
                failed = True
                for error in errors:
                    print(error, file=sys.stderr)
            continue

        item_target = f"Kubernetes/{namespace}/OnePasswordItem/{name}"
        secret_target = f"Kubernetes/{namespace}/Secret/{name}"
        item = attempt("read " + item_target, read_resource, "onepassworditem", name, namespace)
        replacement = attempt("read " + secret_target, read_resource, "secret", name, namespace)
        sources = {n: attempt(f"read Kubernetes/{namespace}/Secret/{n}",
                              read_resource, "secret", n, namespace)
                   for n in contract["sources"]}
        if contract.get("composite"):
            old_name, projections = contract["composite"]
            original = attempt(f"read Kubernetes/{namespace}/Secret/{old_name}",
                               read_resource, "secret", old_name, namespace)
            replacements = {name: replacement}
            for child in sorted({target for target, _ in projections.values()} - {name}):
                child_contract = CONTRACTS[child]
                child_item = attempt(f"read Kubernetes/{namespace}/OnePasswordItem/{child}",
                                     read_resource, "onepassworditem", child, namespace)
                child_secret = attempt(f"read Kubernetes/{namespace}/Secret/{child}",
                                       read_resource, "secret", child, namespace)
                child_source = attempt(f"read 1Password/Item/{child_contract['item_id']}",
                                       read_source, child_contract["item_id"])
                replacements[child] = child_secret
                if child_item is not None and child_secret is not None:
                    attempt(f"verify Kubernetes/{namespace}/OnePasswordItem/{child}",
                            verify, child_item, child_secret, {}, {**child_contract, "sources": {}})
                if child_source is not None and child_secret is not None:
                    attempt(f"verify 1Password/Item/{child_contract['item_id']}+Secret/{child}",
                            verify_source, child_source, child_secret, child_contract)
                    if child_contract.get("references"):
                        attempt(f"verify source-reference/{child_contract['item_id']}",
                                verify_references, child_secret, child_contract)
            if original is not None and all(s is not None for s in replacements.values()):
                attempt(f"verify complete-{old_name}", verify_composite, original, replacements, contract)
        if contract.get("authentik_env"):
            original = attempt("read Kubernetes/identity/Secret/authentik-env",
                               read_resource, "secret", "authentik-env", namespace)
            replacements = {name: replacement}
            for child in ("headscale-oidc", "zipline-connect", "immich-connect", "langfuse-connect"):
                child_contract = {**CONTRACTS[child], "namespace": namespace, "sources": {}}
                child_name = child_contract["name"]
                child_item = attempt(f"read Kubernetes/identity/OnePasswordItem/{child_name}",
                                     read_resource, "onepassworditem", child_name, namespace)
                child_secret = attempt(f"read Kubernetes/identity/Secret/{child_name}",
                                       read_resource, "secret", child_name, namespace)
                child_source = attempt(f"read 1Password/Item/{child_contract['item_id']}",
                                       read_source, child_contract["item_id"])
                replacements[child_name] = child_secret
                if child_item is not None and child_secret is not None:
                    attempt(f"verify Kubernetes/identity/OnePasswordItem/{child_name}",
                            verify, child_item, child_secret, {}, child_contract)
                if child_source is not None and child_secret is not None:
                    attempt(f"verify 1Password/Item/{child_contract['item_id']}+identity/Secret/{child_name}",
                            verify_source, child_source, child_secret, child_contract)
            if original is not None and all(s is not None for s in replacements.values()):
                attempt("verify complete-authentik-env", verify_authentik_env, original, replacements)
        if contract.get("rendered"):
            old_name, template_name, _ = contract["rendered"]
            original = attempt(f"read Kubernetes/{namespace}/Secret/{old_name}",
                               read_resource, "secret", old_name, namespace)
            template = attempt(f"read Kubernetes/{namespace}/ConfigMap/{template_name}",
                               read_resource, "configmap", template_name, namespace)
            if replacement is not None and original is not None and template is not None:
                attempt("verify complete-rendered-config", verify_rendered_config,
                        replacement, original, template, contract)
        if contract.get("immich_config"):
            original = attempt("read Kubernetes/homelab/Secret/immich",
                               read_resource, "secret", "immich", namespace)
            template = attempt("read Kubernetes/homelab/ConfigMap/immich-config-template",
                               read_resource, "configmap", "immich-config-template", namespace)
            if replacement is not None and original is not None and template is not None:
                attempt("verify immich database+complete-config",
                        verify_immich_config, replacement, original, template)
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
            if contract.get("references"):
                attempt("verify source-reference/" + contract['item_id'],
                        verify_references, replacement, contract)
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
