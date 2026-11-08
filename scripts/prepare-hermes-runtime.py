#!/usr/bin/env python3
"""Administrator-only narrow item creation; credentials stay in captured memory."""
import argparse
import base64
import json
import os
import re
import subprocess
import sys


VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"
SOURCE = "c2qpwaw6hsgretdwvbpx3sge5u"
TITLE = "Hermes Runtime"
FIELDS = {
    "op3m7hudiprbm63fk4wcxqglmi": ("discord-bot-token", "CONCEALED", "Discord \ubd07 \ud1a0\ud070"),
    "fn446i6ddovmn7n4jcmck5afg4": ("discord-allowed-users", "STRING", "Discord \ud5c8\uc6a9\ub41c \uc720\uc800"),
    "7ooupb36fhrns3ylsnros4fj6e": ("discord-home-channel", "STRING", "Discord \ud648 \ucc44\ub110"),
    "dhkzzpalgt33qdmvlm6knod72a": ("exa-api-key", "CONCEALED", "Exa API \ud0a4"),
    "52qljnwq55ysuf3hb2r4vxawni": ("browser-use-api-key", "CONCEALED", "Browser Use API \ud0a4"),
    "t4554lneydfcucvqsqjuck2zdi": ("perplexity-api-key", "CONCEALED", "Perplexity API \ud0a4"),
}
BROAD = "dxraaqrzo4qi6p3swaak5ebyue"


class Blocked(ValueError):
    pass


def run(arguments, payload=None):
    result = subprocess.run(
        ["op", *arguments], input=payload, capture_output=True, check=True, timeout=60,
        stdin=None if payload is not None else subprocess.DEVNULL,
    )
    return json.loads(result.stdout)


def source_template(source):
    if (source["id"] != SOURCE or source["vault"]["id"] != VAULT
            or source["title"] != "Hermes Agent" or source.get("files") or source.get("urls")):
        raise Blocked("source-identity-or-content")
    fields = {field["id"]: field for field in source["fields"]}
    if len(fields) != len(source["fields"]) or set(fields) != {*FIELDS, BROAD, "notesPlain"}:
        raise Blocked("source-field-ids")
    sections = source.get("sections") or []
    if len(sections) != 1 or sections[0]["id"] != "add more" or sections[0].get("label"):
        raise Blocked("source-sections")
    output = []
    for field_id, (label, kind, old_label) in FIELDS.items():
        field = fields[field_id]
        if (field.get("label") not in {old_label, label} or field.get("type") != kind
                or field.get("purpose") is not None
                or (field.get("section") or {}).get("id") != "add more"
                or not isinstance(field.get("value"), str) or not field["value"]):
            raise Blocked("source-runtime-field")
        output.append({"id": field_id, "label": label, "type": kind, "value": field["value"]})
    # Construct from an allowlist, not by copying or deleting from the broad item.
    output.append({"id": "notesPlain", "label": "notesPlain", "type": "STRING",
                   "purpose": "NOTES", "value": ""})
    return {"title": TITLE, "category": "SECURE_NOTE", "fields": output}


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
        if any(field.get(key) != wanted.get(key) for key in ("label", "type", "purpose")):
            raise Blocked("runtime-field-metadata")
        if field.get("section") or field.get("value", "") != wanted["value"]:
            raise Blocked("runtime-field-bytes")


def read_secret(name):
    result = subprocess.run(
        ["kubectl", "-n", "homelab", "get", "secret", name, "-o", "json"],
        stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60,
    )
    return {key: base64.b64decode(value, validate=True)
            for key, value in json.loads(result.stdout)["data"].items()}


def verify_consumers(template):
    fields = {field["label"]: field["value"].encode("utf-8") for field in template["fields"]}
    projections = {
        "hermes-discord": {"DISCORD_BOT_TOKEN": "discord-bot-token",
                           "DISCORD_ALLOWED_USERS": "discord-allowed-users",
                           "DISCORD_HOME_CHANNEL": "discord-home-channel"},
        "hermes-web": {"EXA_API_KEY": "exa-api-key"},
        "hermes-browser": {"BROWSER_USE_API_KEY": "browser-use-api-key"},
    }
    for name, mapping in projections.items():
        if read_secret(name) != {key: fields[label] for key, label in mapping.items()}:
            raise Blocked("eso-runtime-bytes")
    webhook = read_secret("hermes-webhook")
    if set(webhook) != {"WEBHOOK_SECRET"} or not webhook["WEBHOOK_SECRET"]:
        raise Blocked("eso-webhook-shape")
    source_webhook = subprocess.run(
        ["op", "read", "--no-newline", "op://Homelab/6esl52kqp2arngw72jrteqdbge/hermes_webhook_secret"],
        stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=60,
    ).stdout
    if source_webhook != webhook["WEBHOOK_SECRET"]:
        raise Blocked("source-webhook-bytes")
    original = read_secret("n8n-hermes-credentials")
    expected = [
        {"id": "hermesWebhookTok", "name": "Hermes webhook", "type": "httpHeaderAuth",
         "data": {"name": "X-Gitlab-Token", "value": source_webhook.decode("utf-8")}},
        {"id": "perplexityDecider", "name": "Perplexity Decider", "type": "perplexityApi",
         "data": {"apiKey": fields["perplexity-api-key"].decode("utf-8")}},
    ]
    if set(original) != {"credentials.json"} or json.loads(original["credentials.json"]) != expected:
        raise Blocked("eso-n8n-credentials-content")
    print("PASS hermes-runtime-consumers: four retained ESO targets and source webhook match; complete n8n import JSON verified")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="create only the six allowlisted runtime fields")
    mode.add_argument("--verify-consumers", action="store_true", help="read-only comparison with all four retained ESO targets")
    args = parser.parse_args(argv)
    stage, attempted = "environment", False
    try:
        if any(os.environ.get(name) for name in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN")):
            raise Blocked("personal-session-required")
        stage = "source-get"
        source = run(["item", "get", SOURCE, "--vault", VAULT, "--format=json"])
        stage = "source-validation"
        template = source_template(source)
        stage = "runtime-discovery"
        inventory = run(["item", "list", "--vault", VAULT, "--format=json"])
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
            if args.verify_consumers:
                raise Blocked("runtime-item-missing")
            stage = "create" if args.apply else "create-preview"
            arguments = ["item", "create", "-", "--vault", VAULT, "--format=json", "--reveal"]
            if not args.apply:
                arguments.append("--dry-run")
            attempted = args.apply
            runtime = run(arguments, json.dumps(template, ensure_ascii=True).encode("utf-8"))
        stage = "runtime-validation"
        verify_runtime(runtime, template)
        if not matches and not args.apply:
            print("INFO hermes-runtime: CLI creation preview verified; no writes; broad token excluded")
            return 0
        item_id = runtime["id"]
        if not isinstance(item_id, str) or not re.fullmatch(r"[a-z2-7]{26}", item_id):
            raise Blocked("runtime-id-invalid")
        stage = "runtime-readback"
        fresh = run(["item", "get", item_id, "--vault", VAULT, "--format=json"])
        if fresh["id"] != item_id:
            raise Blocked("runtime-id-mismatch")
        verify_runtime(fresh, template)
        stage = "source-readback"
        if source_template(run(["item", "get", SOURCE, "--vault", VAULT, "--format=json"])) != template:
            raise Blocked("source-runtime-bytes-changed")
        if args.verify_consumers:
            stage = "retained-eso-parity"
            verify_consumers(template)
        print(f"PASS hermes-runtime: six-field source bytes verified; item-id={item_id}; broad token excluded")
        print("INFO hermes-runtime: no consumer cutover or token revocation; broad-token trust gate remains")
        return 0
    except (Blocked, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, AttributeError, KeyboardInterrupt):
        print(f"FAIL hermes-runtime: stage={stage} outcome={'unknown-create-inspect-before-retry' if attempted else 'no-writes'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
