#!/usr/bin/env python3
"""Administrator-only Cloudflare field-label PATCH; never print item contents."""
import argparse
import copy
import json
import os
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
PATCH = [{"op": "replace", "path": f"/fields/{name}/label", "value": target}
         for name, (_, target, _) in FIELDS.items()]
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
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        body = None
        if method == "PATCH":
            headers["Content-Type"] = "application/json"
            body = json.dumps(PATCH).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/v1/vaults/{VAULT}/items/{ITEM}",
            data=body, headers=headers, method=method,
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply only the eight reviewed field-label patches")
    args = parser.parse_args(argv)
    stage, submitted = "connect-setup", False
    try:
        client = Connect()
        stage = "source-get"
        before = client.request("GET")
        stage = "source-inspection"
        already_target = inspect(before)
        if already_target:
            print("INFO cloudflare-label: already-target-label; no write; parity not checked")
            return 0
        if not args.apply:
            print("INFO cloudflare-label: eligible eight-field label-only PATCH; no write; parity not checked")
            return 0
        stage, submitted = "patch", True
        response = client.request("PATCH")
        stage = "patch-response-verification"
        verify(before, response)
        stage = "source-readback"
        verify(before, client.request("GET"))
    except Exception as error:
        # HTTP errors, JSON schemas and exceptions can carry secrets or entire item bodies.
        state = "unknown-after-patch-attempt" if submitted else "no-write-attempted"
        category = str(error) if isinstance(error, Refused) else "execution-or-response-error"
        if isinstance(error, urllib.error.HTTPError):
            category = f"http-status-{int(error.code)}"
            error.close()
        print(f"FAIL cloudflare-label: stage={stage} category={category} outcome={state}; inspect item history privately", file=sys.stderr)
        return 1
    print("PASS cloudflare-label: label-only PATCH response and GET verified; consumer parity not checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
