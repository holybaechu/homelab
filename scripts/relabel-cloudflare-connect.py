#!/usr/bin/env python3
"""Read-only Cloudflare incident inspection; all mutations are disabled."""
import argparse
import copy
import http.client
import json
import os
import socket
import sys
import urllib.error
import urllib.request


VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"
ITEM = "x62vzi4s2sdnerrjev6pp5ndnu"
FIELDS = {
    "notesPlain": ("notesPlain", "notes-plain", "STRING"),
    "username": ("\uc0ac\uc6a9\uc790\uba85", "username", "STRING"),
    "credential": ("\uc790\uaca9 \uc99d\uba85", "credential", "CONCEALED"),
    "type": ("\uc720\ud615", "type", "MENU"),
    "filename": ("\ud30c\uc77c \uc774\ub984", "filename", "STRING"),
    "validFrom": ("\ucde8\ub4dd\uc77c", "valid-from", "DATE"),
    "expires": ("\ub9cc\ub8cc\uc77c", "expires", "DATE"),
    "hostname": ("\ud638\uc2a4\ud2b8 \uc774\ub984", "hostname", "STRING"),
}

HOSTS = {"http://127.0.0.1:8080", "http://localhost:8080",
         "http://onepassword-connect.onepassword.svc.cluster.local:8080"}


class Refused(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("redirect-refused")


class Connect:
    def __init__(self):
        self.host = os.environ.get("OP_CONNECT_HOST", "")
        self.token = os.environ.get("OP_CONNECT_TOKEN", "")
        if self.host not in HOSTS or not self.token or "\r" in self.token or "\n" in self.token:
            raise Refused("explicit-local-connect-credentials-required")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method):
        if method != "GET":
            raise Refused("mutation-disabled-date-preservation-incident")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        request = urllib.request.Request(
            f"{self.host}/v1/vaults/{VAULT}/items/{ITEM}",
            headers=headers, method="GET",
        )
        with self.opener.open(request, timeout=20) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise Refused("response-size-invalid")
        item = json.loads(data)
        if not isinstance(item, dict):
            raise Refused("response-schema-invalid")
        return item


def inspect(item):
    if item.get("id") != ITEM or item.get("vault", {}).get("id") != VAULT or item.get("title") != "Cloudflare DNS":
        raise Refused("source-identity")
    fields = item.get("fields")
    if not isinstance(fields, list) or len(fields) != len(FIELDS):
        raise Refused("source-field-ids")
    indexed = {f["id"]: f for f in fields}
    if set(indexed) != set(FIELDS):
        raise Refused("source-field-ids")
    for name, (old, target, field_type) in FIELDS.items():
        if indexed[name].get("label") not in {old, target} or indexed[name].get("type") != field_type:
            raise Refused("source-field-metadata")
    field = indexed["credential"]
    if field.get("type") != "CONCEALED" or not isinstance(field.get("value"), str) or not field["value"]:
        raise Refused("source-credential-shape")

    for name in ("validFrom", "expires"):
        if indexed[name].get("type") != "DATE" or indexed[name].get("value") not in (None, ""):
            raise Refused("source-dates-not-unset")
    return all(indexed[name]["label"] == target for name, (_, target, _) in FIELDS.items())


def verify(before, after):
    inspect(after)
    expected = copy.deepcopy(before)
    for field in expected["fields"]:
        field["label"] = FIELDS[field["id"]][1]
    actual = copy.deepcopy(after)
    # Only Connect's documented item revision/timestamp are server-owned changes.
    for item in (expected, actual):
        item.pop("version", None)
        item.pop("updatedAt", None)
    if expected != actual:
        raise Refused("non-label-change-or-stale-readback")


def metadata(item):
    if item.get("id") != ITEM or item.get("vault", {}).get("id") != VAULT or item.get("title") != "Cloudflare DNS":
        raise Refused("source-identity")
    fields = item.get("fields")
    if not isinstance(fields, list):
        raise Refused("source-field-ids")
    lines = []
    for name, (old, target, kind) in FIELDS.items():
        matches = [field for field in fields if field.get("id") == name]
        lines.append(f"field={name} occurrences={len(matches)}")
        if len(matches) != 1:
            continue
        field = matches[0]
        label = "target" if field.get("label") == target else "old" if field.get("label") == old else "unreviewed"
        line = f"field={name} label-state={label} type-match={str(field.get('type') == kind).lower()}"
        if kind == "DATE":
            value = field.get("value")
            shape = ("absent" if "value" not in field else "null" if value is None else
                     "empty-string" if value == "" else "nonempty-string" if isinstance(value, str) else
                     "number-zero" if type(value) in (int, float) and value == 0 else
                     "number-nonzero" if type(value) in (int, float) else "other")
            line += f" value-shape={shape}"
        lines.append(line)
    lines.append(f"unreviewed-fields-count={sum(field.get('id') not in FIELDS for field in fields)}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="disabled after the real DATE preservation failure")
    mode.add_argument("--source-metadata", action="store_true", help="GET-only redacted incident structure; never a parity PASS")
    args = parser.parse_args(argv)
    stage = "connect-setup"
    try:
        if args.apply:
            raise Refused("mutation-disabled-date-preservation-incident")
        client = Connect()
        stage = "source-get"
        before = client.request("GET")
        if args.source_metadata:
            stage = "source-metadata"
            for line in metadata(before):
                print("INFO cloudflare-label: " + line)
            print("INFO cloudflare-label: GET-only incident inspection; no writes; preservation and parity not verified")
            return 0
        stage = "source-inspection"
        already_target = inspect(before)
        if already_target:
            print("INFO cloudflare-label: already-target-label; no write; parity not checked")
            return 0
        print("INFO cloudflare-label: current source structure inspected; mutations disabled; no write; parity not checked")
        return 0
    except Exception as error:
        # HTTP errors, JSON schemas and exceptions can carry secrets or entire item bodies.
        state = "no-write-attempted"
        category = str(error) if isinstance(error, Refused) else "execution-or-response-error"
        if isinstance(error, urllib.error.HTTPError):
            category = f"http-status-{int(error.code)}"
            error.close()
        elif isinstance(error, json.JSONDecodeError):
            category = "response-invalid-json"
        elif isinstance(error, UnicodeError):
            category = "response-or-header-encoding-invalid"
        elif isinstance(error, http.client.IncompleteRead):
            category = "response-incomplete"
        elif isinstance(error, http.client.HTTPException):
            category = "http-protocol-error"
        else:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            for kind, name in ((TimeoutError, "transport-timeout"),
                               (ConnectionRefusedError, "transport-connection-refused"),
                               (ConnectionResetError, "transport-connection-reset"),
                               (socket.gaierror, "transport-name-resolution")):
                if isinstance(reason, kind):
                    category = name
                    break
            else:
                if isinstance(error, urllib.error.URLError):
                    category = "transport-url-error"
                elif isinstance(error, OSError):
                    category = "transport-os-error"
        guidance = "; GET-only diagnosis; no PATCH sent by this run"
        print(f"FAIL cloudflare-label: stage={stage} category={category} outcome={state}{guidance}", file=sys.stderr)
        return 1



if __name__ == "__main__":
    sys.exit(main())
