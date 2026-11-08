#!/usr/bin/env python3
"""Administrator-only narrow Cloudflare creation after privately confirmed recovery."""
import argparse
import base64
import json
import os
import re
import subprocess
import sys


VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"
SOURCE = "x62vzi4s2sdnerrjev6pp5ndnu"
TITLE = "Cloudflare DNS Runtime"
SOURCE_FIELDS = {
    "notesPlain": ("notesPlain", "STRING", "NOTES"),
    "username": ("\uc0ac\uc6a9\uc790\uba85", "STRING", None),
    "credential": ("\uc790\uaca9 \uc99d\uba85", "CONCEALED", None),
    "type": ("\uc720\ud615", "MENU", None),
    "filename": ("\ud30c\uc77c \uc774\ub984", "STRING", None),
    "validFrom": ("\ucde8\ub4dd\uc77c", "DATE", None),
    "expires": ("\ub9cc\ub8cc\uc77c", "DATE", None),
    "hostname": ("\ud638\uc2a4\ud2b8 \uc774\ub984", "STRING", None),
}


class Blocked(ValueError):
    pass


def failure_category(error):
    # CLI output and exception messages can contain credentials; emit fixed hints only.
    if isinstance(error, Blocked):
        return str(error)
    if isinstance(error, subprocess.CalledProcessError):
        raw = error.stderr or b""
        text = (raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw).lower()
        hints = []
        for status in re.findall(r"\b(?:status(?: code)?|http)[ :=]+(\d{3})\b", text):
            if status in {"400", "401", "403", "404", "409", "429", "500", "502", "503", "504"}:
                hints.append("http-" + status)
        for phrases, hint in (
            (("not signed in", "sign in", "signin", "session expired", "unauthorized",
              "authentication", "no accounts configured"), "account-auth-hint"),
            (("forbidden", "permission", "access denied"), "vault-access-hint"),
            (("locked", "could not connect to the 1password app", "connecting to desktop app"), "desktop-lock-or-connection-hint"),
            (("unknown flag", "unknown command"), "cli-compatibility-hint"),
        ):
            if any(phrase in text for phrase in phrases):
                hints.append(hint)
        return f"cli-command-failed exit={int(error.returncode)} hints={','.join(dict.fromkeys(hints)) or 'none'}"
    if isinstance(error, subprocess.TimeoutExpired):
        return "cli-timeout"
    if isinstance(error, FileNotFoundError):
        return "cli-executable-not-found"
    if isinstance(error, PermissionError):
        return "cli-execution-permission-denied"
    if isinstance(error, UnicodeError):
        return "response-encoding-invalid"
    if isinstance(error, json.JSONDecodeError):
        return "response-invalid-json"
    if isinstance(error, OSError):
        return "cli-os-error"
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, (KeyError, TypeError, AttributeError)):
        return "response-schema-invalid"
    return "execution-or-response-error"


def run(arguments, payload=None):
    result = subprocess.run(
        ["op", *arguments], input=payload, capture_output=True, check=True, timeout=60,
        stdin=None if payload is not None else subprocess.DEVNULL,
    )
    return json.loads(result.stdout)


def source_template(source):
    if (source["id"] != SOURCE or source["vault"]["id"] != VAULT
            or source["title"] != "Cloudflare DNS" or source["category"] != "API_CREDENTIAL"
            or source.get("sections") or source.get("files") or source.get("urls")):
        raise Blocked("recovered-source-identity-or-content")
    fields = {field["id"]: field for field in source["fields"]}
    if len(fields) != len(source["fields"]) or set(fields) != set(SOURCE_FIELDS):
        raise Blocked("recovered-source-field-ids")
    for field_id, (label, kind, purpose) in SOURCE_FIELDS.items():
        field = fields[field_id]
        if (field.get("label") != label or field.get("type") != kind
                or field.get("purpose") != purpose or field.get("section")):
            raise Blocked("recovered-source-field-metadata")
    # Absence is intentional: never accept a materialized DATE, even empty/null/zero.
    if any("value" in fields[key] for key in ("validFrom", "expires")):
        raise Blocked("recovered-source-date-present")
    value = fields["credential"].get("value")
    if not isinstance(value, str) or not value:
        raise Blocked("source-credential-empty")
    return {"title": TITLE, "category": "SECURE_NOTE", "fields": [
        {"id": "credential", "label": "credential", "type": "CONCEALED", "value": value},
        {"id": "notesPlain", "label": "notesPlain", "type": "STRING", "purpose": "NOTES", "value": ""},
    ]}


def verify_runtime(item, template):
    if (item["title"] != TITLE or item["category"] != "SECURE_NOTE"
            or item["vault"]["id"] != VAULT or item.get("sections")
            or item.get("files") or item.get("urls")):
        raise Blocked("runtime-identity-or-content")
    actual = {field["id"]: field for field in item["fields"]}
    expected = {field["id"]: field for field in template["fields"]}
    if len(actual) != len(item["fields"]) or set(actual) != set(expected):
        raise Blocked("runtime-field-ids")
    for field_id, field in actual.items():
        wanted = expected[field_id]
        if (any(field.get(key) != wanted.get(key) for key in ("label", "type", "purpose"))
                or field.get("section") or field.get("value", "") != wanted["value"]):
            raise Blocked("runtime-field-metadata-or-bytes")


def verify_consumers(template):
    value = template["fields"][0]["value"].encode("utf-8")
    for namespace, name, key in (("cert-manager", "cloudflare-dns", "api-token"),
                                 ("homelab", "cloudflare-ddns", "CLOUDFLARE_API_TOKEN")):
        result = subprocess.run(
            ["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"],
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60,
        )
        data = {k: base64.b64decode(v, validate=True)
                for k, v in json.loads(result.stdout)["data"].items()}
        if data != {key: value}:
            raise Blocked("retained-eso-key-set-or-bytes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-confirmed", action="store_true",
                        help="administrator privately compared history/current populated values, approved recovery, and confirmed both dates unset")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="explicitly approve creating the narrow item once, after a successful preview")
    mode.add_argument("--diagnose-source", action="store_true",
                      help="source GET and strict validation only; no create, preview, discovery or Kubernetes reads")
    args = parser.parse_args(argv)
    stage, attempted = "recovery-authorization", False
    item_id = None
    try:
        if not args.recovery_confirmed:
            raise Blocked("private-recovery-not-confirmed")
        stage = "environment"
        if any(os.environ.get(name) for name in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN")):
            raise Blocked("personal-session-required")
        stage = "source-get"
        source = run(["item", "get", SOURCE, "--vault", VAULT, "--format=json"])
        stage = "source-validation"
        template = source_template(source)
        if args.diagnose_source:
            print("INFO cloudflare-runtime: source GET and strict recovered-source validation verified; no writes; creation and consumer parity not checked")
            return 0
        stage = "retained-eso-parity"
        verify_consumers(template)
        stage = "runtime-discovery"
        inventory = run(["item", "list", "--vault", VAULT, "--include-archive", "--format=json"])
        matches = [item for item in inventory if item["title"] == TITLE]
        if len(matches) > 1:
            raise Blocked("runtime-title-ambiguous")
        if matches:
            item_id = matches[0]["id"]
            if not isinstance(item_id, str) or not re.fullmatch(r"[a-z2-7]{26}", item_id):
                raise Blocked("runtime-id-invalid")
            stage = "runtime-get"
            runtime = run(["item", "get", item_id, "--vault", VAULT, "--format=json"])
            if runtime["id"] != item_id:
                raise Blocked("runtime-id-mismatch")
        else:
            stage = "create" if args.apply else "create-preview"
            arguments = ["item", "create", "-", "--vault", VAULT, "--format=json", "--reveal"]
            if not args.apply:
                arguments.append("--dry-run")
            attempted = args.apply
            runtime = run(arguments, json.dumps(template, ensure_ascii=True).encode("utf-8"))
        stage = "runtime-validation"
        verify_runtime(runtime, template)
        if matches or args.apply:
            item_id = runtime["id"]
            if not isinstance(item_id, str) or not re.fullmatch(r"[a-z2-7]{26}", item_id):
                raise Blocked("runtime-id-invalid")
            stage = "runtime-readback"
            fresh = run(["item", "get", item_id, "--vault", VAULT, "--format=json"])
            if fresh["id"] != item_id:
                raise Blocked("runtime-id-mismatch")
            verify_runtime(fresh, template)
        stage = "source-readback"
        if run(["item", "get", SOURCE, "--vault", VAULT, "--format=json"]) != source:
            raise Blocked("original-source-changed")
        stage = "retained-eso-readback"
        verify_consumers(template)
        if not matches and not args.apply:
            print("INFO cloudflare-runtime: CLI creation preview and both retained ESO byte gates verified; no writes")
        else:
            print(f"PASS cloudflare-runtime: narrow item/source/both retained ESO bytes verified; item-id={item_id}")
        print("INFO cloudflare-runtime: no source edits or consumer cutover; Operator ownership/parity/auth/rotation remain required")
        return 0
    except (Blocked, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, AttributeError, KeyboardInterrupt) as error:
        category = failure_category(error)
        print(f"FAIL cloudflare-runtime: stage={stage} category={category} outcome={'unknown-create-inspect-before-retry' if attempted else 'no-writes'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
