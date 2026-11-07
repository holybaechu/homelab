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
        "credential": ("자격 증명", "api_token"),
    }),
    "vyenqrf6ko2aficwt3dyaao2de": ("Headscale", {
        "3u3ughywinbmkybgdopzodjnxy": ("OIDC 클라이언트 스크릿", "oidc_client_secret"),
        "biwyjxnpkpvmrmy42ffpt45fpe": ("게이트웨이 인증 키", "authkey"),
    }),
    "n2olk4p3ngumyuttl6b7itutba": ("AdGuard Home", {
        "vu2pnknisjne6k4t5sh5laxf3e": ("비밀번호 해시", "password_hash"),
    }),
    "ztl6gih3jqixgb5xufbv4xq4bi": ("qBittorrent", {
        "t25w4tvxducf5bqt5owlk4mvjy": ("비밀번호 해시", "password_hash"),
    }),
    "ltncwd5wyxwknf5xfqwxraqmtm": ("Immich", {
        "trytrwmlxrksnvjujdd57hqk7a": ("데이터베이스 비밀번호", "database_password"),
        "sf4ch3lie57owvqxdwvl3cl6he": ("OIDC 클라이언트 시크릿", "oidc_client_secret"),
        "kpjesisize67f6kdidzwdhrriq": ("Hermes API 키", "hermes_api_key"),
    }),
    "ia7seg53btlz2t4xluwkzmfjly": ("Zipline", {
        "eteoaoehfwqkierraxfgbx5xru": ("코어 시크릿", "core_secret"),
        "6rvlh45inzbb55zwnyi53spfne": ("OIDC 클라이언트 시크릿", "oidc_client_secret"),
        "mq3jr7zyvy5zyff2zzpz7zrmgq": ("Hermes API token", "hermes_api_token"),
    }),
    "3galz5zqqwdrqoj2mjk4wcxfdy": ("Obsidian Sync", {
        "auth_token": ("인증 토큰", "auth_token"),
    }),
    "d7ginmnpodzvevvb472v26jvmq": ("authentik [Admin]", {
        "pinyekye2iau23hchko7js763a": ("시크릿 키", "secret_key"),
        "fhw4wuych7l2y5fs62p5leldtm": ("데이터베이스 비밀번호", "database_password"),
        "c6t67ly54dysrlik57ft332464": ("이메일", "email"),
    }),
    "77j3ul6ntreertxl3bjkdko4h4": ("Langfuse", {
        "postgres_password": ("데이터베이스 비밀번호", "postgres_password"),
        "database_url": ("데이터베이스 연결 주소", "database_url"),
        "clickhouse_password": ("ClickHouse 비밀번호", "clickhouse_password"),
        "redis_auth": ("Redis 비밀번호", "redis_auth"),
        "minio_root_password": ("S3 저장소 비밀번호", "minio_root_password"),
        "salt": ("해시 솔트", "salt"),
        "encryption_key": ("암호화 키", "encryption_key"),
        "nextauth_secret": ("로그인 세션 시크릿", "nextauth_secret"),
        "langfuse_init_project_public_key": ("Hermes 공개 키", "langfuse_init_project_public_key"),
        "langfuse_init_project_secret_key": ("Hermes 시크릿 키", "langfuse_init_project_secret_key"),
        "oidc_client_secret": ("Authentik 클라이언트 시크릿", "oidc_client_secret"),
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
