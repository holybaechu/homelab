"""Synthetic HTTP-provider + Chromium tests against the pinned, patched native broker."""
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.parse import parse_qs

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "kubernetes/apps/hermes"
SOURCE = Path(os.environ.get("HERMES_SOURCE", "/opt/hermes"))
TEMP = tempfile.TemporaryDirectory(prefix="connect-vault-test-")
ROOT = Path(TEMP.name)
for line in (APP / "browser-vault.sha256").read_text().splitlines():
    digest, path = line.split()
    if hashlib.sha256((SOURCE / path).read_bytes()).hexdigest() != digest:
        raise RuntimeError("Pinned native browser-vault boundary changed; review before upgrading")
shutil.copytree(SOURCE / "agent", ROOT / "agent", ignore=shutil.ignore_patterns("__pycache__"))
(ROOT / "tools").mkdir()
(ROOT / "tools/__init__.py").write_text((SOURCE / "tools/__init__.py").read_text() +
                                       "\n__path__.append(" + repr(str(SOURCE / "tools")) + ")\n")
shutil.copyfile(SOURCE / "tools/browser_vault_tool.py", ROOT / "tools/browser_vault_tool.py")
subprocess.run(["git", "apply", "--check", str(APP / "browser-vault-connect.patch")], cwd=ROOT, check=True)
subprocess.run(["git", "apply", str(APP / "browser-vault-connect.patch")], cwd=ROOT, check=True)
shutil.copyfile(APP / "connect_vault.py", ROOT / "agent/vault_backends/connect_vault.py")
sys.path[:0] = [str(ROOT), str(SOURCE)]
os.environ["HERMES_HOME"] = str(ROOT / "home")
(ROOT / "home").mkdir()

from agent.vault_backends.connect_vault import ConnectVault, ConnectError, origin
from agent.vault_store import normalize_otp_secret, totp_now
from tools import browser_vault_tool as tool

V1, V2, ITEM = "a" * 26, "b" * 26, "c" * 26
# Deliberately synthetic, never a real provider token/credential.
TOKEN, PASSWORD, SEED = "synthetic-connect-token", "synthetic-Password-987!", "JBSWY3DPEHPK3PXP"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.paths.append(self.path)
        if self.path.startswith("/v1/"):
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.end_headers()
                return
            status = self.server.status
            self.send_response(status)
            if status == 302:
                self.send_header("Location", self.server.redirect)
            self.end_headers()
            if status != 200:
                self.wfile.write((TOKEN + PASSWORD + SEED).encode())
                return
            if self.server.payload is not None:
                data = self.server.payload
            elif self.path == "/v1/vaults":
                data = [{"id": V1}, {"id": V2}]
            elif self.path.endswith("/items"):
                data = [{"id": ITEM, "category": "LOGIN"}] if V1 in self.path else []
            else:
                data = self.server.item
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if self.path == "/account" and self.headers.get("Cookie") == "synthetic_session=verified":
                self.wfile.write(b"<!doctype html><title>logged-in-synthetic</title>")
                return
            self.wfile.write(b'''<!doctype html><title>Synthetic login</title>
<form action=/session method=post>
<input name=username autocomplete=username><input name=password type=password autocomplete=current-password>
<input name=otp autocomplete=one-time-code><button>Sign in</button></form>''')

    def do_POST(self):
        fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        valid = (self.path == "/session" and fields.get("username") == ["synthetic@example.com"] and
                 fields.get("password") == [PASSWORD] and fields.get("otp") == [totp_now(SEED)])
        self.send_response(303 if valid else 403)
        if valid:
            self.send_header("Set-Cookie", "synthetic_session=verified; HttpOnly; SameSite=Strict")
            self.send_header("Location", "/account")
        self.end_headers()


def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.status, srv.payload, srv.paths = 200, None, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ConnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server()
        cls.other = server()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"
        cls.other_url = f"http://127.0.0.1:{cls.other.server_port}"
        cls.token_file = ROOT / "token"
        cls.token_file.write_text(TOKEN)
        cls.token_file.chmod(0o600)
        cls.cfg = {"connect_host": cls.url, "connect_token_file": str(cls.token_file), "enabled": True}
        (ROOT / "home/config.yaml").write_text(json.dumps({"vault": {"onepassword": cls.cfg}}))
        cls.item = {"id": ITEM, "vault": {"id": V1}, "category": "LOGIN", "title": "Synthetic Login",
                    "urls": [{"href": cls.url + "/login"}, {"href": "https://example.com:443/"}],
                    "fields": [{"purpose": "USERNAME", "value": "synthetic@example.com"},
                               {"purpose": "PASSWORD", "value": PASSWORD},
                               {"type": "OTP", "value": SEED}]}
        cls.handle = f"op:connect:{V1}:{ITEM}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.other.shutdown()
        cls.srv.server_close()
        cls.other.server_close()

    def setUp(self):
        self.srv.item = copy.deepcopy(self.item)
        self.srv.status, self.srv.payload, self.srv.paths = 200, None, []
        self.adapter = ConnectVault(self.url, str(self.token_file))

    def test_discovery_all_granted_vaults_and_metadata_only(self):
        result = json.loads(tool._handle_vault({"action": "list"}))
        self.assertTrue(result["success"])
        found = next(i for i in result["items"] if i["handle"] == self.handle)
        self.assertEqual(found["identifier"], "synthetic@example.com")
        self.assertEqual(found["allowed_origins"], [self.url, "https://example.com"])
        self.assertEqual(found["two_factor"], "automatic")
        self.assertIn(f"/v1/vaults/{V2}/items", self.srv.paths)
        self.assertEqual(result["connect_vaults"], [{"vault_id": V1, "active_logins": 1}, {"vault_id": V2, "active_logins": 0}])
        self.assert_safe(json.dumps(result))

    def assert_safe(self, result):
        for value in (TOKEN, PASSWORD, SEED):
            self.assertNotIn(value, result)

    def test_transport_refuses_redirects_and_sanitizes_errors(self):
        self.srv.redirect = self.other_url + "/v1/vaults"
        before = len(self.other.paths)
        for status, category in ((302, "http_failed"), (401, "authentication_failed"), (403, "access_denied"), (500, "http_failed")):
            self.srv.status = status
            with self.assertRaises(ConnectError) as caught:
                self.adapter.list_items()
            self.assertEqual(str(caught.exception), "1Password Connect: " + category)
            self.assertIsNone(caught.exception.__context__)
        self.assertEqual(len(self.other.paths), before)
        self.assert_safe(tool._handle_vault({"action": "list"}))
        refusal = tool._handle_vault({"action": "fill", "handle": self.handle})
        self.assertEqual(json.loads(refusal)["error_type"], "backend_unavailable")
        self.assert_safe(refusal)
        self.srv.status = 200
        with patch.dict(os.environ, {"HTTP_PROXY": self.other_url, "http_proxy": self.other_url,
                                     "HTTPS_PROXY": self.other_url, "https_proxy": self.other_url,
                                     "NO_PROXY": "", "no_proxy": ""}):
            self.assertEqual(len(self.adapter.list_items()), 1)
        self.assertEqual(len(self.other.paths), before)

    def test_missing_or_partial_bootstrap_never_falls_back(self):
        from agent.vault_backends.onepassword import OnePasswordLoginBackend
        for cfg in ({"connect_token_file": str(self.token_file)}, {"connect_host": self.url},
                    {"connect_host": "http://unapproved.invalid", "connect_token_file": str(self.token_file)}):
            backend = OnePasswordLoginBackend(cfg)
            with patch.object(backend, "_run", side_effect=AssertionError("no CLI fallback")) as run:
                with self.assertRaises(ConnectError):
                    backend.get_meta(self.handle)
                run.assert_not_called()

    def test_malformed_and_excessive_provider_responses_fail_closed(self):
        for payload in ({}, None, [{"id": "../bad"}], [{"id": V1}, {"id": V1}], [{"id": V1}] * 257):
            if payload is None:
                continue
            self.srv.payload = payload
            with self.assertRaises(ConnectError):
                self.adapter.list_items()
        self.srv.payload = "x" * (4 * 1024 * 1024)
        with self.assertRaisesRegex(ConnectError, "response_too_large"):
            self.adapter.list_items()

    def test_handle_identity_and_ambiguous_fields(self):
        for handle in ("bw:any", "op:../../etc", f"op:connect:{V1}:../bad"):
            before = len(self.srv.paths)
            with self.assertRaises(ConnectError):
                self.adapter.meta(handle)
            self.assertEqual(len(self.srv.paths), before)
        self.srv.item["vault"]["id"] = V2
        with self.assertRaisesRegex(ConnectError, "identity_mismatch"):
            self.adapter.meta(self.handle)
        self.srv.item = copy.deepcopy(self.item)
        self.srv.item["fields"].append({"purpose": "PASSWORD", "value": "ambiguous"})
        with self.assertRaisesRegex(ConnectError, "ambiguous_field"):
            self.adapter.password(self.handle)

    def test_snapshot_prevents_item_change_between_origin_check_and_resolution(self):
        with self.adapter.fill_context():
            self.assertIsNotNone(self.adapter.meta(self.handle))
            self.srv.item["fields"][1]["value"] = "changed-synthetic"
            self.assertEqual(self.adapter.password(self.handle), PASSWORD)
        self.assertEqual(self.adapter.password(self.handle), "changed-synthetic")

    def test_totp_rfc_vector_and_invalid_seeds(self):
        seed = normalize_otp_secret("otpauth://totp/synthetic?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&digits=8")
        self.assertEqual(totp_now(seed, at=59), "94287082")
        self.assertEqual(self.adapter.otp(self.handle), totp_now(SEED))
        self.srv.item["fields"][2]["value"] = "A"
        with self.assertRaisesRegex(ConnectError, "invalid_otp"):
            self.adapter.meta(self.handle)

    def test_url_canonicalization_rejects_parser_ambiguities(self):
        self.assertEqual(origin("https://EXAMPLE.com:443/path"), "https://example.com")
        for value in ("https://example.com\\@evil.com", "https://example.com%2fevil", "https://user@example.com", "https://example.com\n", "http://127.1", "http://2130706433", "https://example.com.", "https://example..com", "https://[::1]/"):
            with self.assertRaises(Exception):
                origin(value)

    def test_non_connect_backends_still_use_cli_contract(self):
        from agent.vault_backends.onepassword import OnePasswordLoginBackend
        backend = OnePasswordLoginBackend()
        backend._item_vaults = {ITEM: V1}
        with patch.object(backend, "_run", return_value="synthetic-cli-password\n") as run:
            self.assertEqual(backend.resolve_password("op:" + ITEM), "synthetic-cli-password")
            self.assertIn("--vault", run.call_args.args)

    def test_native_chromium_fill_otp_redaction_mismatch_and_navigation(self):
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        from tools.browser_cdp_tool import _redact_cdp_output
        from agent.vault_backends.onepassword import OnePasswordLoginBackend

        profile = ROOT / "chromium"
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        self.assertIsNotNone(chromium, "Chromium required; this test must not be skipped")
        process = subprocess.Popen([chromium, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                                    "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                                    "--user-data-dir=" + str(profile), self.url + "/login"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        task = "synthetic-connect-vault"
        try:
            port_file = profile / "DevToolsActivePort"
            for _ in range(100):
                if port_file.exists():
                    break
                time.sleep(0.1)
            port = port_file.read_text().splitlines()[0]
            with urlopen(f"http://127.0.0.1:{port}/json/version") as response:
                endpoint = json.load(response)["webSocketDebuggerUrl"]
            sup = SUPERVISOR_REGISTRY.get_or_start(task_id=task, cdp_url=endpoint)
            for _ in range(50):
                if sup.evaluate_runtime("!!document.querySelector('input[type=password]')").get("result"):
                    break
                time.sleep(0.1)
            def evaluate(expression):
                result = sup.evaluate_runtime(expression)
                self.assertTrue(result["ok"])
                return result["result"]
            result = tool._handle_vault({"action": "fill", "handle": self.handle}, task_id=task)
            self.assertTrue(json.loads(result)["success"], result)
            self.assert_safe(result)
            self.assertTrue(evaluate("document.querySelector('[name=password]').value === " + json.dumps(PASSWORD)))
            with patch.object(OnePasswordLoginBackend, "resolve_password", side_effect=AssertionError("must not resolve")) as resolve:
                unbound = json.loads(tool._handle_vault({"action": "fill", "handle": self.handle}, task_id="synthetic-other-session"))
                self.assertFalse(unbound["success"])
                resolve.assert_not_called()
            self.assert_safe(json.dumps(_redact_cdp_output({"result": {"value": PASSWORD}})))
            result = tool._handle_vault({"action": "enter_code", "handle": self.handle}, task_id=task)
            self.assertTrue(json.loads(result)["success"], result)
            code = evaluate("document.querySelector('[name=otp]').value")
            self.assertEqual(code, totp_now(SEED))
            self.assertNotIn(code, result)
            self.assertNotIn(code, json.dumps(_redact_cdp_output({"result": {"value": code}})))
            evaluate("document.querySelector('[name=username]').value = 'synthetic@example.com'; document.querySelector('button').click()")
            time.sleep(0.3)
            self.assertEqual(evaluate("document.title"), "logged-in-synthetic")
            # The provider changes its saved origins after precheck: the in-context
            # fresh snapshot must refuse before password resolution or any write.
            evaluate("location.href = " + json.dumps(self.url + "/login"))
            time.sleep(0.3)
            bound = tool._bound_page_origin
            def change_saved_origin(*args):
                result = bound(*args)
                self.srv.item["urls"] = [{"href": self.other_url}]
                return result
            with patch.object(tool, "_bound_page_origin", change_saved_origin), patch.object(
                    OnePasswordLoginBackend, "resolve_password", side_effect=AssertionError("must not resolve")) as resolve:
                result = json.loads(tool._handle_vault({"action": "fill", "handle": self.handle}, task_id=task))
                resolve.assert_not_called()
            self.assertEqual(result["error_type"], "origin_mismatch")
            self.assertEqual(evaluate("document.querySelector('[name=password]').value"), "")
            self.srv.item = copy.deepcopy(self.item)
            # Mismatched current origin: use actual browser navigation, no secret resolution.
            evaluate("location.href = " + json.dumps(self.other_url + "/login"))
            time.sleep(0.3)
            with patch.object(OnePasswordLoginBackend, "resolve_password", side_effect=AssertionError("must not resolve")) as resolve:
                result = json.loads(tool._handle_vault({"action": "fill", "handle": self.handle}, task_id=task))
                resolve.assert_not_called()
            self.assertEqual(result["error_type"], "origin_mismatch")
            self.assertEqual(evaluate("document.querySelector('[name=password]').value"), "")
            # Navigation race after the initial origin check: real JS recheck rejects the write.
            evaluate("location.href = " + json.dumps(self.url + "/login"))
            time.sleep(0.3)
            original = OnePasswordLoginBackend.resolve_password
            def navigate_before_resolution(backend, handle):
                evaluate("location.href = " + json.dumps(self.other_url + "/login"))
                time.sleep(0.3)
                return original(backend, handle)
            with patch.object(OnePasswordLoginBackend, "resolve_password", navigate_before_resolution):
                result = json.loads(tool._handle_vault({"action": "fill", "handle": self.handle}, task_id=task))
            self.assertEqual(result["error_type"], "origin_changed")
            self.assertEqual(evaluate("document.querySelector('[name=password]').value"), "")
        finally:
            SUPERVISOR_REGISTRY.stop_all()
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
