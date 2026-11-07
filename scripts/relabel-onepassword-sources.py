#!/usr/bin/env python3
"""Administrative, label-only migration using official op JSON stdin editing."""
import argparse
import copy
import json
import subprocess
import sys


VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"
# item ID: (display name, field ID: (reviewed old label, required label))
SOURCES = {
    "x62vzi4s2sdnerrjev6pp5ndnu": ("Cloudflare DNS", {
        "credential": ("\uc790\uaca9 \uc99d\uba85", "api_token"),
    }),
    "vyenqrf6ko2aficwt3dyaao2de": ("Headscale", {
        "3u3ughywinbmkybgdopzodjnxy": ("OIDC \ud074\ub77c\uc774\uc5b8\ud2b8 \uc2a4\ud06c\ub9bf", "oidc_client_secret"),
        "biwyjxnpkpvmrmy42ffpt45fpe": ("\uac8c\uc774\ud2b8\uc6e8\uc774 \uc778\uc99d \ud0a4", "authkey"),
    }),
    "n2olk4p3ngumyuttl6b7itutba": ("AdGuard Home", {
        "vu2pnknisjne6k4t5sh5laxf3e": ("\ube44\ubc00\ubc88\ud638 \ud574\uc2dc", "password_hash"),
    }),
    "ztl6gih3jqixgb5xufbv4xq4bi": ("qBittorrent", {
        "t25w4tvxducf5bqt5owlk4mvjy": ("\ube44\ubc00\ubc88\ud638 \ud574\uc2dc", "password_hash"),
    }),
    "ltncwd5wyxwknf5xfqwxraqmtm": ("Immich", {
        "trytrwmlxrksnvjujdd57hqk7a": ("\ub370\uc774\ud130\ubca0\uc774\uc2a4 \ube44\ubc00\ubc88\ud638", "database_password"),
        "sf4ch3lie57owvqxdwvl3cl6he": ("OIDC \ud074\ub77c\uc774\uc5b8\ud2b8 \uc2dc\ud06c\ub9bf", "oidc_client_secret"),
        "kpjesisize67f6kdidzwdhrriq": ("Hermes API \ud0a4", "hermes_api_key"),
    }),
    "ia7seg53btlz2t4xluwkzmfjly": ("Zipline", {
        "eteoaoehfwqkierraxfgbx5xru": ("\ucf54\uc5b4 \uc2dc\ud06c\ub9bf", "core_secret"),
        "6rvlh45inzbb55zwnyi53spfne": ("OIDC \ud074\ub77c\uc774\uc5b8\ud2b8 \uc2dc\ud06c\ub9bf", "oidc_client_secret"),
        "mq3jr7zyvy5zyff2zzpz7zrmgq": ("Hermes API token", "hermes_api_token"),
    }),
    "3galz5zqqwdrqoj2mjk4wcxfdy": ("Obsidian Sync", {
        "auth_token": ("\uc778\uc99d \ud1a0\ud070", "auth_token"),
    }),
    "d7ginmnpodzvevvb472v26jvmq": ("authentik [Admin]", {
        "pinyekye2iau23hchko7js763a": ("\uc2dc\ud06c\ub9bf \ud0a4", "secret_key"),
        "fhw4wuych7l2y5fs62p5leldtm": ("\ub370\uc774\ud130\ubca0\uc774\uc2a4 \ube44\ubc00\ubc88\ud638", "database_password"),
        "c6t67ly54dysrlik57ft332464": ("\uc774\uba54\uc77c", "email"),
    }),
    "77j3ul6ntreertxl3bjkdko4h4": ("Langfuse", {
        "postgres_password": ("\ub370\uc774\ud130\ubca0\uc774\uc2a4 \ube44\ubc00\ubc88\ud638", "postgres_password"),
        "database_url": ("\ub370\uc774\ud130\ubca0\uc774\uc2a4 \uc5f0\uacb0 \uc8fc\uc18c", "database_url"),
        "clickhouse_password": ("ClickHouse \ube44\ubc00\ubc88\ud638", "clickhouse_password"),
        "redis_auth": ("Redis \ube44\ubc00\ubc88\ud638", "redis_auth"),
        "minio_root_password": ("S3 \uc800\uc7a5\uc18c \ube44\ubc00\ubc88\ud638", "minio_root_password"),
        "salt": ("\ud574\uc2dc \uc194\ud2b8", "salt"),
        "encryption_key": ("\uc554\ud638\ud654 \ud0a4", "encryption_key"),
        "nextauth_secret": ("\ub85c\uadf8\uc778 \uc138\uc158 \uc2dc\ud06c\ub9bf", "nextauth_secret"),
        "langfuse_init_project_public_key": ("Hermes \uacf5\uac1c \ud0a4", "langfuse_init_project_public_key"),
        "langfuse_init_project_secret_key": ("Hermes \uc2dc\ud06c\ub9bf \ud0a4", "langfuse_init_project_secret_key"),
        "oidc_client_secret": ("Authentik \ud074\ub77c\uc774\uc5b8\ud2b8 \uc2dc\ud06c\ub9bf", "oidc_client_secret"),
    }),
}


def op(*args, item=None):
    result = subprocess.run(
        ["op", *args, "--format=json"],
        input=json.dumps(item, ensure_ascii=False).encode("utf-8") if item is not None else None,
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def relabel(original, item_id, title, labels):
    if (original["id"] != item_id or original["vault"]["id"] != VAULT
            or original["title"] != title or original.get("files")):
        raise ValueError("unexpected item identity or attachments")
    updated = copy.deepcopy(original)
    fields = {field["id"]: field for field in updated["fields"]}
    if len(fields) != len(updated["fields"]):
        raise ValueError("duplicate field IDs")
    for field_id, (old, new) in labels.items():
        if fields[field_id]["label"] not in (old, new):
            raise ValueError("source label changed; review before editing")
        fields[field_id]["label"] = new
    return updated


def content(item):
    # These properties are writable through the CLI template; reference is derived.
    fields = {
        f["id"]: {k: v for k, v in f.items() if k != "reference"}
        for f in item["fields"]
    }
    return {
        "id": item["id"], "vault": item["vault"]["id"],
        "title": item["title"], "category": item["category"], "fields": fields,
        **{k: item.get(k) or [] for k in ("sections", "urls", "tags", "files")},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write after all read-only previews pass")
    parser.add_argument("--confirm-no-passkeys", action="store_true",
                        help="confirm all nine items have no passkeys or other non-JSON credentials")
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_no_passkeys:
        parser.error("--apply requires --confirm-no-passkeys; inspect all nine items in the 1Password app")
    try:
        plans = []
        for item_id, (title, labels) in SOURCES.items():
            original = op("item", "get", item_id, "--vault", VAULT)
            updated = relabel(original, item_id, title, labels)
            if content(original) != content(updated):
                preview = op("item", "edit", item_id, "--vault", VAULT,
                             "--dry-run", item=updated)
                if content(preview) != content(updated):
                    raise ValueError("CLI preview changed non-label content")
                plans.append((item_id, title, original, updated))
            print(f"PASS preview {title}: identity, field IDs and label-only content")
        if not args.apply:
            print("DRY RUN: no items written; inspect passkeys before applying")
            return 0
        for item_id, title, original, updated in plans:
            current = op("item", "get", item_id, "--vault", VAULT)
            if current != original:
                raise ValueError("item changed since preview; rerun after review")
            op("item", "edit", item_id, "--vault", VAULT, item=updated)
            actual = op("item", "get", item_id, "--vault", VAULT)
            if content(actual) != content(updated):
                raise ValueError("read-back mismatch; inspect item history before continuing")
            print(f"PASS applied {title}: read-back labels, IDs and exact values unchanged")
        print("PASS all sources: label-only migration verified (including already-correct items)")
        return 0
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        print("FAIL: authentication, source drift, preview or read-back check; "
              "stop and review. Earlier PASS applied items remain changed; safe to rerun after review.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
