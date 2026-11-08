import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, HTTPServer


spec = importlib.util.spec_from_file_location(
    "cloudflare_patch", Path(__file__).parents[1] / "scripts/relabel-cloudflare-connect.py")
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def fixture():
    return {"id": helper.ITEM, "vault": {"id": helper.VAULT}, "title": "Cloudflare DNS",
            "version": 1, "updatedAt": "before", "fields": [
                {"id": name, "label": helper.FIELDS[name][0],
                 "type": helper.FIELDS[name][2],
                 **({"value": "PRIVATE_VALUE"} if name == "credential" else {})}
                for name in sorted(helper.FIELDS)]}


def changed(source):
    result = copy.deepcopy(source)
    for field in result["fields"]:
        field["label"] = helper.FIELDS[field["id"]][1]
    result.update(version=2, updatedAt="after")
    return result


class CloudflarePatchTests(unittest.TestCase):
    def run_main(self, responses, argv=()):
        client = Mock()
        client.request.side_effect = responses
        with patch.object(helper, "Connect", return_value=client), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = helper.main(list(argv))
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("PRIVATE", text)
        return status, text, client

    def test_default_is_read_only_and_already_target_is_idempotent(self):
        status, output, client = self.run_main([fixture()])
        self.assertEqual(status, 0)
        self.assertIn("no write; parity not checked", output)
        self.assertEqual([c.args for c in client.request.call_args_list], [("GET",)])
        status, output, client = self.run_main([changed(fixture())], ["--apply"])
        self.assertEqual(status, 0)
        self.assertIn("already-target-label", output)
        self.assertEqual(client.request.call_count, 1)

    def test_apply_requires_patch_response_and_exact_get_readback(self):
        before = fixture()
        after = changed(before)
        status, output, client = self.run_main([before, after, after], ["--apply"])
        self.assertEqual(status, 0)
        self.assertIn("consumer parity not checked", output)
        self.assertEqual([c.args for c in client.request.call_args_list], [("GET",), ("PATCH",), ("GET",)])
        for response in (before, {"PRIVATE": "PRIVATE_VALUE"}, OSError("PRIVATE_VALUE")):
            status, output, client = self.run_main([before, after, response], ["--apply"])
            self.assertEqual(status, 1)
            self.assertIn("unknown-after-patch-attempt", output)
            self.assertNotIn("PASS", output)
            self.assertEqual(client.request.call_count, 3)

    def test_credential_or_date_change_is_not_normalized_away(self):
        before, after = fixture(), changed(fixture())
        helper.verify(before, after)
        for field_id in ("credential", "validFrom", "expires"):
            for value in ("", "PRIVATE_CHANGED", None):
                mutation = copy.deepcopy(after)
                next(f for f in mutation["fields"] if f["id"] == field_id)["value"] = value
                with self.assertRaises(ValueError):
                    helper.verify(before, mutation)

    def test_unset_dates_and_source_identity_are_preconditions(self):
        for mutate in (
            lambda x: x.update(id="wrong"),
            lambda x: x["fields"].append(copy.deepcopy(x["fields"][0])),
            lambda x: next(f for f in x["fields"] if f["id"] == "validFrom").update(value="PRIVATE_DATE"),
            lambda x: next(f for f in x["fields"] if f["id"] == "credential").update(label="PRIVATE_LABEL"),
        ):
            before = fixture()
            mutate(before)
            status, output, client = self.run_main([before], ["--apply"])
            self.assertEqual(status, 1)
            self.assertIn("no-write-attempted", output)
            self.assertEqual(client.request.call_count, 1)

    def test_patch_failure_is_unknown_and_never_retried(self):
        errors = (OSError("PRIVATE_ERROR"), helper.urllib.error.HTTPError(
            "PRIVATE_URL", 403, "PRIVATE_ERROR", {}, io.BytesIO(b"PRIVATE_BODY")))
        for error in errors:
            status, output, client = self.run_main([fixture(), error], ["--apply"])
            self.assertEqual(status, 1)
            self.assertIn("stage=patch", output)
            self.assertIn("outcome=unknown-after-patch-attempt", output)
            self.assertEqual(client.request.call_count, 2)

    def test_get_transport_and_response_diagnostics_are_safe_and_never_patch(self):
        cases = (
            (helper.urllib.error.URLError(ConnectionRefusedError("PRIVATE_ENDPOINT")), "transport-connection-refused"),
            (helper.urllib.error.URLError(TimeoutError("PRIVATE_ENDPOINT")), "transport-timeout"),
            (ConnectionResetError("PRIVATE_ENDPOINT"), "transport-connection-reset"),
            (helper.urllib.error.URLError(helper.socket.gaierror("PRIVATE_HOST")), "transport-name-resolution"),
            (helper.urllib.error.URLError("PRIVATE_REASON"), "transport-url-error"),
            (json.JSONDecodeError("PRIVATE_ERROR", "PRIVATE_BODY", 0), "response-invalid-json"),
            (helper.http.client.IncompleteRead(b"PRIVATE_BODY"), "response-incomplete"),
            (helper.http.client.BadStatusLine("PRIVATE_STATUS"), "http-protocol-error"),
            (helper.urllib.error.HTTPError("PRIVATE_URL", 403, "PRIVATE_STATUS", {}, io.BytesIO(b"PRIVATE_BODY")), "http-status-403"),
        )
        for error, category in cases:
            with self.subTest(category=category):
                status, output, client = self.run_main([error], ["--apply"])
                self.assertEqual(status, 1)
                self.assertIn("stage=source-get category=" + category, output)
                self.assertIn("outcome=no-write-attempted", output)
                self.assertNotIn("inspect item history", output)
                self.assertEqual([c.args for c in client.request.call_args_list], [("GET",)])

    def test_real_http_transport_sends_only_reviewed_label_payload(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.command, self.path, None))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(fixture()).encode())
            def do_PATCH(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                requests.append((self.command, self.path, json.loads(body)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(changed(fixture())).encode())
            def log_message(self, format, *args):
                pass
        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch.dict(os.environ, {"OP_CONNECT_HOST": "http://127.0.0.1:8080",
                                             "OP_CONNECT_TOKEN": "DUMMY_TEST_TOKEN"}):
                    client = helper.Connect()
                client.host = f'http://127.0.0.1:{server.server_port}'
                before = client.request("GET")
                helper.verify(before, client.request("PATCH"))
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(requests, [
            ("GET", f"/v1/vaults/{helper.VAULT}/items/{helper.ITEM}", None),
            ("PATCH", f"/v1/vaults/{helper.VAULT}/items/{helper.ITEM}",
             [{"op": "replace", "path": f"/fields/{name}/label", "value": target}
              for name, (_, target, _) in helper.FIELDS.items()]),
        ])

    def test_external_hosts_missing_tokens_and_redirects_are_refused(self):
        for host, token in (("https://example.com", "dummy"), ("http://localhost:8080/", "dummy"),
                            ("http://127.0.0.1:8080", ""), ("http://127.0.0.1:8080", "dummy\n")):
            with patch.dict(os.environ, {"OP_CONNECT_HOST": host, "OP_CONNECT_TOKEN": token}):
                with self.assertRaises(ValueError):
                    helper.Connect()
        with self.assertRaisesRegex(ValueError, "redirect-refused"):
            helper.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


if __name__ == "__main__":
    unittest.main()
