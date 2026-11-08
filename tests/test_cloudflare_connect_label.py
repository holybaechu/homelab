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


class CloudflareInspectionTests(unittest.TestCase):
    def run_main(self, responses, argv=()):
        client = Mock()
        client.request.side_effect = responses
        with patch.object(helper, "Connect", return_value=client), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = helper.main(list(argv))
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("PASS", text)
        return status, text, client

    def test_apply_is_disabled_before_setup_or_any_request(self):
        with patch.object(helper, "Connect") as setup, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(helper.main(["--apply"]), 1)
        setup.assert_not_called()
        self.assertIn("mutation-disabled-date-preservation-incident", err.getvalue())
        self.assertIn("outcome=no-write-attempted", err.getvalue())
        with patch.dict(os.environ, {"OP_CONNECT_HOST": "http://127.0.0.1:8080", "OP_CONNECT_TOKEN": "DUMMY"}):
            client = helper.Connect()
        with patch.object(client.opener, "open") as transport:
            with self.assertRaisesRegex(ValueError, "mutation-disabled"):
                client.request("PATCH")
        transport.assert_not_called()

    def test_default_is_read_only_and_does_not_treat_target_labels_as_parity(self):
        for item in (fixture(), changed(fixture())):
            status, output, client = self.run_main([item])
            self.assertEqual(status, 0)
            self.assertIn("no write; parity not checked", output)
            self.assertEqual([c.args for c in client.request.call_args_list], [("GET",)])

    def test_credential_or_date_change_is_not_normalized_away(self):
        before, after = fixture(), changed(fixture())
        helper.verify(before, after)
        for field_id in ("credential", "validFrom", "expires"):
            for value in ("", "PRIVATE_CHANGED", None, "0", 0):
                mutation = copy.deepcopy(after)
                next(f for f in mutation["fields"] if f["id"] == field_id)["value"] = value
                with self.assertRaises(ValueError):
                    helper.verify(before, mutation)

    def test_unset_dates_and_source_identity_remain_strict(self):
        for mutate in (
            lambda x: x.update(id="wrong"),
            lambda x: x["fields"].append(copy.deepcopy(x["fields"][0])),
            lambda x: next(f for f in x["fields"] if f["id"] == "validFrom").update(value="PRIVATE_DATE"),
            lambda x: next(f for f in x["fields"] if f["id"] == "credential").update(label="PRIVATE_LABEL"),
        ):
            before = fixture()
            mutate(before)
            status, output, client = self.run_main([before])
            self.assertEqual(status, 1)
            self.assertIn("no-write-attempted", output)
            self.assertEqual(client.request.call_count, 1)

    def test_date_metadata_is_value_redacted_and_does_not_weaken_preservation(self):
        for value, shape in ((None, "null"), ("", "empty-string"), ("PRIVATE_DATE", "nonempty-string"),
                             (0, "number-zero"), (123, "number-nonzero"), ({"PRIVATE": "PRIVATE"}, "other")):
            item = changed(fixture())
            next(f for f in item["fields"] if f["id"] == "validFrom")["value"] = value
            status, output, client = self.run_main([item], ["--source-metadata"])
            self.assertEqual(status, 0)
            self.assertIn("field=validFrom label-state=target type-match=true value-shape=" + shape, output)
            self.assertIn("field=expires label-state=target type-match=true value-shape=absent", output)
            self.assertIn("preservation and parity not verified", output)
            self.assertEqual(client.request.call_count, 1)

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
                status, output, client = self.run_main([error])
                self.assertEqual(status, 1)
                self.assertIn("stage=source-get category=" + category, output)
                self.assertIn("outcome=no-write-attempted", output)
                self.assertNotIn("inspect item history", output)
                self.assertEqual([c.args for c in client.request.call_args_list], [("GET",)])

    def test_real_http_transport_is_get_only(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.command, self.path))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(fixture()).encode())
            def log_message(self, format, *args):
                pass
        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch.dict(os.environ, {"OP_CONNECT_HOST": "http://127.0.0.1:8080", "OP_CONNECT_TOKEN": "DUMMY_TEST_TOKEN"}):
                    client = helper.Connect()
                client.host = f'http://127.0.0.1:{server.server_port}'
                helper.inspect(client.request("GET"))
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(requests, [("GET", f"/v1/vaults/{helper.VAULT}/items/{helper.ITEM}")])

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
