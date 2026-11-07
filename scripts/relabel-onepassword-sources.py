#!/usr/bin/env python3
"""Administrative, label-only migration using official op JSON stdin editing."""
import argparse
import copy
import json
import os
import re
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


class CliFailure(Exception):
    pass


def cli_failure(error):
    # Only fixed hints are emitted, never exception text or fragments of CLI output.
    raw = error.stderr or b""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.lower()
    hints = []
    for status in re.findall(r"\b(?:status(?: code)?|http)[ :=]+(\d{3})\b", text):
        if status in {"400", "401", "403", "404", "409", "429", "500", "502", "503", "504"}:
            hints.append(f"HTTP {status}")
    if any(s in text for s in ("not signed in", "sign in", "signin", "session expired",
                                "unauthorized", "authentication", "no accounts configured")):
        hints.append("check account sign-in / desktop app integration")
    if any(s in text for s in ("forbidden", "permission", "access denied")):
        hints.append("check vault read/edit permissions")
    if "connect" in text and "support" in text:
        hints.append("Connect does not support item edit; use an administrative account session")
    if any(s in text for s in ("unknown flag", "unknown command", "invalid json")):
        hints.append("check op version and JSON-stdin support (repo pins 2.41.1)")
    return f"op-command-failed; exit={error.returncode}" + (
        "; " + "; ".join(dict.fromkeys(hints)) if hints else
        "; check CLI authentication, vault access and item history locally; do not share raw output"
    )


def op(*args, item=None):
    try:
        result = subprocess.run(
            ["op", *args, "--format=json"],
            input=json.dumps(item, ensure_ascii=False).encode("utf-8") if item is not None else None,
            stdin=subprocess.DEVNULL if item is None else None,
            capture_output=True, check=True,
        )
    except FileNotFoundError:
        raise CliFailure("op-not-found; install/activate the repo-pinned op CLI with mise") from None
    except subprocess.CalledProcessError as error:
        raise CliFailure(cli_failure(error)) from None
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise CliFailure("invalid-cli-json; exit=0; expected UTF-8 JSON from op --format=json") from None


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


class ContentMismatch(ValueError):
    def __init__(self, expected, actual, scope):
        self.diagnostics = []
        # Never print dynamic keys (including field IDs), labels or scalar values.
        known_keys = {
            "id", "vault", "title", "category", "fields", "sections", "urls", "tags",
            "files", "type", "purpose", "label", "value", "section", "href", "primary",
            "reference", "password_details", "strength", "entropy", "generated", "history",
        }

        def kind(value):
            return {dict: "object", list: "array", str: "string", int: "number",
                    float: "number", bool: "boolean", type(None): "null"}.get(type(value), "unknown")

        def visit(left, right, path):
            if len(self.diagnostics) >= 20:
                return
            if type(left) is not type(right):
                self.diagnostics.append(f"{path}: type expected={kind(left)} actual={kind(right)}")
            elif isinstance(left, dict):
                for index, key in enumerate(dict.fromkeys([*left, *right])):
                    name = key if key in known_keys and path != "$.fields" else f"property[{index}]"
                    child = f"{path}.{name}"
                    if key not in left or key not in right:
                        present = right[key] if key not in left else left[key]
                        self.diagnostics.append(
                            f"{child}: presence expected={'present' if key in left else 'missing'} "
                            f"actual={'present' if key in right else 'missing'} type={kind(present)}"
                            + (f" string={'empty' if present == '' else 'nonempty'}"
                               if isinstance(present, str) else ""))
                    else:
                        visit(left[key], right[key], child)
                    if len(self.diagnostics) >= 20:
                        break
            elif isinstance(left, list):
                for index in range(max(len(left), len(right))):
                    if index >= len(left) or index >= len(right):
                        present = right[index] if index >= len(left) else left[index]
                        self.diagnostics.append(
                            f"{path}[{index}]: presence expected={'present' if index < len(left) else 'missing'} "
                            f"actual={'present' if index < len(right) else 'missing'} type={kind(present)}")
                    else:
                        visit(left[index], right[index], f"{path}[{index}]")
                    if len(self.diagnostics) >= 20:
                        break
            elif left != right:
                self.diagnostics.append(f"{path}: changed type={kind(left)}")

        visit(expected, actual, "$")
        self.scope = scope
        super().__init__("content mismatch")


def content(item):
    # These properties are writable through the CLI template; reference is derived.
    fields = {
        f["id"]: {k: v for k, v in f.items() if k != "reference"}
        for f in item["fields"]
    }
    if len(fields) != len(item["fields"]):
        raise ValueError("duplicate field IDs")
    return {
        "id": item["id"], "vault": item["vault"]["id"],
        "title": item["title"], "category": item["category"], "fields": fields,
        **{k: item.get(k) or [] for k in ("sections", "urls", "tags", "files")},
    }


def check_content(expected, actual):
    try:
        left, right = content(expected), content(actual)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise ContentMismatch(expected, actual, "raw structure; invalid item shape") from None
    # Cloudflare preview strings may be empty defaults on these optional DATE fields.
    # Accept only absent -> empty string, never a missing field or a removed value.
    if (left["id"] == right["id"] == "x62vzi4s2sdnerrjev6pp5ndnu"
            and left["category"] == right["category"] == "API_CREDENTIAL"):
        for field_id in ("validFrom", "expires"):
            before = left["fields"].get(field_id)
            after = right["fields"].get(field_id)
            if (before is not None and after is not None
                    and before.get("type") == after.get("type") == "DATE"
                    and "value" not in before and after.get("value") == ""):
                del after["value"]
    if left != right:
        raise ContentMismatch(left, right, "compared content")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write after all read-only previews pass")
    parser.add_argument("--confirm-no-passkeys", action="store_true",
                        help="confirm all nine items have no passkeys or other non-JSON credentials")
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_no_passkeys:
        parser.error("--apply requires --confirm-no-passkeys; inspect all nine items in the 1Password app")
    stage, title, item_id = "environment", "none", "none"
    written = verified = 0
    pending_write = False
    try:
        if any(os.environ.get(name) for name in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN")):
            raise CliFailure("connect-environment; OP_CONNECT_HOST or OP_CONNECT_TOKEN is set; "
                             "Connect does not support item edit; use a separate administrative "
                             "personal-account CLI session without Connect variables. "
                             "No environment or credentials were changed")
        plans = []
        for item_id, (title, labels) in SOURCES.items():
            stage = "source-get"
            original = op("item", "get", item_id, "--vault", VAULT)
            stage = "source-validation"
            updated = relabel(original, item_id, title, labels)
            if content(original) != content(updated):
                stage = "preview-edit"
                preview = op("item", "edit", item_id, "--vault", VAULT,
                             "--dry-run", item=updated)
                stage = "preview-comparison"
                check_content(updated, preview)
                plans.append((item_id, title, original, updated))
            print(f"PASS preview {title}: identity, field IDs and label-only content")
        if not args.apply:
            print("DRY RUN: no items written; inspect passkeys before applying")
            return 0
        for item_id, title, original, updated in plans:
            stage = "prewrite-get"
            current = op("item", "get", item_id, "--vault", VAULT)
            stage = "snapshot-comparison"
            if current != original:
                raise ValueError("item changed since preview; rerun after review")
            stage = "apply-edit"
            pending_write = True
            op("item", "edit", item_id, "--vault", VAULT, item=updated)
            written += 1
            pending_write = False
            stage = "readback-get"
            actual = op("item", "get", item_id, "--vault", VAULT)
            stage = "readback-comparison"
            check_content(updated, actual)
            verified += 1
            print(f"PASS applied {title}: read-back labels, IDs and exact values unchanged")
        print("PASS all sources: label-only migration verified (including already-correct items)")
        return 0
    except (CliFailure, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        if isinstance(error, CliFailure):
            detail = str(error)
        elif isinstance(error, OSError):
            detail = "local-process-error; check op execution permissions and OS configuration"
        else:
            detail = {
                "source-validation": "source-drift-or-unsupported-content; review identity, attachments, field IDs and old labels in the app",
                "preview-comparison": "preview-mismatch; preview did not preserve expected content; do not apply",
                "snapshot-comparison": "concurrent-change; source changed since preview; review before rerun",
                "readback-comparison": "readback-mismatch; inspect item history before continuing",
            }.get(stage, "unexpected-item-json; inspect CLI compatibility locally")
        print(f"FAIL item={title} ({item_id}) stage={stage}: {detail}", file=sys.stderr)
        if isinstance(error, ContentMismatch):
            print(f"Structural diff ({error.scope}; at most 20 entries; no values):", file=sys.stderr)
            for diagnostic in error.diagnostics:
                print(f"  {diagnostic}", file=sys.stderr)
        if not written and not pending_write:
            print("No items written; no write commands attempted.", file=sys.stderr)
        else:
            print(f"Successful write commands: {written}; read-back verified: {verified}. "
                  "Inspect item history before rerun; no automatic rollback.", file=sys.stderr)
            if pending_write:
                print(f"Write attempted for {title}; outcome unknown and item may have changed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
