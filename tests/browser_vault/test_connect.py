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
for name in ("browser_vault_tool.py", "browser_use_cli.py", "browser_session.py"):
    shutil.copyfile(SOURCE / "tools" / name, ROOT / "tools" / name)
shutil.copytree(SOURCE / "plugins", ROOT / "plugins", ignore=shutil.ignore_patterns("__pycache__"))
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
                ids = self.server.items if self.server.items is not None else [ITEM]
                data = [{"id": ident, "category": "LOGIN"} for ident in ids] if V1 in self.path else []
            else:
                data = (self.server.items[self.path.rsplit("/", 1)[1]]
                        if self.server.items is not None else self.server.item)
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
        if self.path == "/browsers":
            if self.headers.get("X-Browser-Use-API-Key") != TOKEN:
                self.send_error(401)
                return
            self.server.cloud_creates.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(201)
            self.end_headers()
            payload = getattr(self.server, "cloud_response", None)
            self.wfile.write(payload if payload is not None else json.dumps(
                {"id": "synthetic-cloud-id", "cdpUrl": self.server.cdp_endpoint}).encode())
            return
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
        (ROOT / "home/config.yaml").write_text(json.dumps({"vault": {"onepassword": cls.cfg},
            "browser": {"cloud_provider": "browser-use", "allow_private_urls": True,
                        "auto_local_for_private_urls": False}}))
        cls.item = {"id": ITEM, "vault": {"id": V1}, "category": "LOGIN", "title": "Synthetic Login",
                    "urls": [{"href": cls.url + "/login"}, {"href": "HTTPS://EXAMPLE.com:443/login?next=%2Faccount"}],
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
        self.srv.items = None
        self.srv.status, self.srv.payload, self.srv.paths = 200, None, []
        self.srv.cloud_response = None
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

    def test_invalid_login_metadata_does_not_hide_valid_logins(self):
        bad_urls = ("https://example.com\n", "https://example.com/a b", "https://example.com\\evil",
                    "https://user@example.com", "https://example.com:99999", "https://[broken",
                    "http://127.1", "https://example..com", "https://example.com.", None)
        cases = []
        for href in bad_urls:
            bad = copy.deepcopy(self.item)
            bad["urls"].append({"href": href})
            cases.append((bad, "invalid_url"))
        for extra, category in (({"purpose": "USERNAME", "value": "duplicate"}, "ambiguous_field"),
                                ({"type": "OTP", "value": SEED}, "ambiguous_otp")):
            bad = copy.deepcopy(self.item)
            bad["fields"].append(extra)
            cases.append((bad, category))
        bad = copy.deepcopy(self.item)
        bad["fields"][2]["value"] = "A"
        cases.append((bad, "invalid_otp"))
        valid = copy.deepcopy(self.item)
        valid["id"] = "d" * 26
        valid_handle = f"op:connect:{V1}:{valid['id']}"
        for bad, category in cases:
            with self.subTest(category=category, urls=bad["urls"]):
                self.srv.items = {ITEM: bad, valid["id"]: valid}
                result = json.loads(tool._handle_vault({"action": "list"}))
                self.assertTrue(result["success"])
                self.assertEqual([i["handle"] for i in result["items"]], [valid_handle])
                self.assertEqual(result["errors"], [{"backend": "onepassword",
                    "error": "1Password Connect: " + category, "skipped_items": 1}])
                self.assertEqual(result["connect_vaults"], [
                    {"vault_id": V1, "active_logins": 2}, {"vault_id": V2, "active_logins": 0}])
                with self.assertRaisesRegex(ConnectError, category):
                    self.adapter.meta(self.handle)
                self.assert_safe(json.dumps(result))
        second_bad = copy.deepcopy(self.item)
        second_bad["id"] = "e" * 26
        second_bad["urls"] = [{"href": "https://example.com\n"}]
        self.srv.items = {ITEM: cases[0][0], second_bad["id"]: second_bad}
        self.assertEqual(self.adapter.list_items(), [])
        self.assertEqual(self.adapter.discovery_errors,
                         [{"error": "1Password Connect: invalid_url", "skipped_items": 2}])
        self.srv.items = {valid["id"]: valid}
        self.assertEqual([i.id for i in self.adapter.list_items()], [valid_handle])
        self.assertEqual(self.adapter.discovery_errors, [])

    def test_discovery_does_not_suppress_unexpected_or_provider_errors(self):
        for category in ("invalid_response", "identity_mismatch", "access_denied", "not_found",
                         "authentication_failed", "transport_failed", "http_failed"):
            with self.subTest(category=category), patch.object(self.adapter, "meta",
                    side_effect=ConnectError("1Password Connect: " + category)):
                with self.assertRaisesRegex(ConnectError, category):
                    self.adapter.list_items()
        with patch("agent.vault_backends.connect_vault.origin", side_effect=RuntimeError("synthetic bug")):
            with self.assertRaisesRegex(RuntimeError, "synthetic bug"):
                self.adapter.list_items()

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
        self.assertEqual(origin("https://EXAMPLE.com:443/login?next=%2Faccount"), "https://example.com")
        self.assertEqual(origin("https://example.com/\uB85C\uADF8\uC778"), "https://example.com")
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

    def test_byop_native_provider_sends_top_level_proxy_for_each_session(self):
        from agent.secret_scope import set_secret_scope, reset_secret_scope
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        proxy = {"host": "proxy.example.com", "port": 443, "protocol": "https",
                 "username": "synthetic-proxy", "password": PASSWORD}
        provider = BrowserUseBrowserProvider()
        self.srv.cloud_creates, self.srv.cdp_endpoint = [], "ws://synthetic.example/devtools/browser"
        scope = set_secret_scope({"BROWSER_USE_CUSTOM_PROXY_JSON": json.dumps(proxy)})
        try:
            with patch.object(provider, "_get_config", return_value={
                    "base_url": self.url, "api_key": TOKEN, "managed_mode": False}):
                first = provider.create_session("synthetic-byop")
                second = provider.create_session("synthetic-byop-named")
            self.assertEqual(self.srv.cloud_creates, [{"customProxy": proxy}, {"customProxy": proxy}])
            self.assertNotEqual(first["session_name"], second["session_name"])
            self.assertEqual(first["cdp_url"], self.srv.cdp_endpoint)
            self.assertNotIn(PASSWORD, json.dumps(first))
            self.assertEqual(provider._release_body({}), {"action": "stop"})
        finally:
            reset_secret_scope(scope)

    def test_byop_invalid_or_unsupported_config_never_creates_a_browser(self):
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        proxy = {"host": "proxy.example.com", "port": 443, "protocol": "https",
                 "username": "synthetic-proxy", "password": PASSWORD}
        invalid = ["", "not-json-" + PASSWORD, "null", "[]", "{}", "x" * 16385]
        for key, value in (("protocol", "http"), ("port", 80), ("port", True),
                           ("password", ""), ("password", "\n" + PASSWORD), ("username", None),
                           ("username", "x" * 2049), ("password", "x" * 8193),
                           ("host", "https://proxy.example.com"), ("host", "user@proxy.example.com"),
                           ("host", "127.0.0.1"), ("host", "proxy.example.com/path"),
                           ("ignoreCertErrors", True), ("caBundle", "synthetic")):
            invalid.append(json.dumps({**proxy, key: value}))
        provider = BrowserUseBrowserProvider()
        with patch.object(provider, "_get_config", return_value={"managed_mode": False}), patch.object(
                provider, "_post_create", side_effect=AssertionError("must not create")) as create:
            for case, raw in enumerate(invalid):
                with self.subTest(case=case), patch(
                        "agent.secret_scope.current_secret_scope", return_value={"BROWSER_USE_CUSTOM_PROXY_JSON": raw}):
                    with self.assertRaises(ValueError) as caught:
                        provider.create_session("synthetic-byop-invalid")
                    self.assertNotIn(PASSWORD, str(caught.exception))
            create.assert_not_called()
        with patch.object(provider, "_get_config", return_value={"managed_mode": True}), patch(
                "agent.secret_scope.current_secret_scope", return_value={"BROWSER_USE_CUSTOM_PROXY_JSON": json.dumps(proxy)}), patch.object(
                provider, "_post_create") as create:
            with self.assertRaisesRegex(ValueError, "requires the direct API provider"):
                provider.create_session("synthetic-managed-byop")
            create.assert_not_called()

    def test_byop_provider_refusal_and_outage_are_redacted_without_retry(self):
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        from types import SimpleNamespace
        provider = BrowserUseBrowserProvider()
        proxy = {"host": "proxy.example.com", "port": 443, "protocol": "https",
                 "username": "synthetic-proxy", "password": PASSWORD}
        with patch.object(provider, "_get_config", return_value={
                "base_url": self.url, "api_key": TOKEN, "managed_mode": False}), patch(
                "agent.secret_scope.current_secret_scope", return_value={"BROWSER_USE_CUSTOM_PROXY_JSON": json.dumps(proxy)}):
            for failure in (RuntimeError(PASSWORD), SimpleNamespace(ok=False, text=PASSWORD)):
                with self.subTest(outage=isinstance(failure, Exception)), patch.object(
                        provider, "_post_create") as create:
                    if isinstance(failure, Exception):
                        create.side_effect = failure
                    else:
                        create.return_value = failure
                    with self.assertRaisesRegex(RuntimeError, "Browser Use custom proxy session creation") as caught:
                        provider.create_session("synthetic-byop-unavailable")
                    self.assertNotIn(PASSWORD, str(caught.exception))
                    self.assertTrue(caught.exception.__suppress_context__)
                    self.assertEqual(create.call_count, 1)
                    self.assertEqual(create.call_args.args[2], {"customProxy": proxy})

    def test_byop_malformed_success_never_reaches_native_discovery_or_logs(self):
        import contextlib
        import io
        import logging
        import traceback
        from urllib.parse import quote
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        from tools import browser_session
        provider = BrowserUseBrowserProvider()
        proxy = dict(host="proxy.example.com", port=443, protocol="https",
                     username="synthetic-proxy-user", password=PASSWORD)
        valid = dict(id="synthetic-owned", cdpUrl="wss://cdp.example/session")
        invalid = [None, [], {}, dict(cdpUrl=valid["cdpUrl"]), dict(id=valid["id"])]
        for key, values in {
            "id": [None, 7, {}, "", " ", PASSWORD, "owned-" + PASSWORD],
            "cdpUrl": [None, 7, {}, "", PASSWORD, "not-a-url", "ftp://cdp.example/session",
                       "ws:///session", "wss://cdp.example:99999/session", "wss://[broken",
                       "wss://cdp.example/space here", "wss://cdp.example/\ncanary",
                       "https://cdp.example/" + PASSWORD,
                       "wss://cdp.example/" + quote(PASSWORD, safe=""),
                       "wss://cdp.example/" + proxy["username"]],
            "timeoutAt": [{}, [], True, PASSWORD],
        }.items():
            invalid.extend({**valid, key: value} for value in values)
        for key in ("id", "cdpUrl", "timeoutAt"):
            invalid.append({**valid, key: quote(PASSWORD, safe="")})
        for endpoint in ("wss://synthetic-proxy-user@cdp.example/session",
                         "wss://cdp.example/session?username=synthetic-proxy-user",
                         "wss://cdp.example/%73ynthetic-proxy-user",
                         "wss://cdp.example/session?username=%73ynthetic-proxy-user"):
            invalid.append({**valid, "cdpUrl": endpoint})
        invalid.extend({**valid, key: proxy["username"]} for key in ("id", "timeoutAt"))
        bodies = [b'{"bad":' + PASSWORD.encode(), *(json.dumps(value).encode() for value in invalid)]
        for key in ("id", "cdpUrl", "timeoutAt"):
            bodies.append((json.dumps({**valid, "timeoutAt": "2030-01-01T00:00:00Z"})[:-1] +
                           "," + json.dumps(key) + ":" + json.dumps(valid.get(key, 0)) + "}").encode())
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("tools.browser_session_cdp")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        with patch.object(provider, "_get_config", return_value={
                "base_url": self.url, "api_key": TOKEN, "managed_mode": False}), patch(
                "agent.secret_scope.current_secret_scope", return_value={"BROWSER_USE_CUSTOM_PROXY_JSON": json.dumps(proxy)}), patch(
                "requests.get", side_effect=AssertionError("must not discover")) as discovery, patch.object(
                browser_session, "_create_local_session", side_effect=AssertionError("no fallback")) as local:
            for case, body in enumerate(bodies):
                with self.subTest(case=case):
                    self.srv.cloud_creates, self.srv.cloud_response = [], body
                    # Test the provider diagnostic itself and the real native consumer boundary.
                    for create in (lambda: provider.create_session("synthetic-malformed"),
                                   lambda: browser_session._create_cloud_session_or_fallback("synthetic-malformed", provider)):
                        with contextlib.redirect_stderr(output), self.assertRaises(RuntimeError) as caught:
                            create()
                        rendered = "".join(traceback.format_exception(caught.exception))
                        self.assertTrue(caught.exception.__suppress_context__)
                        self.assertNotIn(PASSWORD, rendered)
                        self.assertNotIn(proxy["username"], rendered)
                        self.assertIn(str(caught.exception), (
                            "Invalid Browser Use custom proxy session response",
                            "Browser Use session creation failed; no local fallback"))
                    self.assertEqual(len(self.srv.cloud_creates), 2)  # exactly one POST per explicit call
            discovery.assert_not_called()
            local.assert_not_called()
        self.assertNotIn(PASSWORD, output.getvalue())
        self.assertNotIn(proxy["username"], output.getvalue())

    def test_byop_valid_success_preserves_native_endpoints_and_consumed_fields(self):
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        from tools import browser_session
        provider = BrowserUseBrowserProvider()
        proxy = dict(host="proxy.example.com", port=443, protocol="https",
                     username="synthetic-proxy-user", password=PASSWORD)
        with patch.object(provider, "_get_config", return_value={
                "base_url": self.url, "api_key": TOKEN, "managed_mode": False}), patch(
                "agent.secret_scope.current_secret_scope", return_value={"BROWSER_USE_CUSTOM_PROXY_JSON": json.dumps(proxy)}), patch(
                "requests.get") as discovery:
            discovery.return_value.json.return_value = {"webSocketDebuggerUrl": "ws://[::1]:9222/devtools/browser/synthetic"}
            for field, endpoint in (("cdpUrl", "wss://cdp.example/session?token=synthetic-cdp-token"),
                                    ("connectUrl", "ws://127.0.0.1:9222/devtools/browser/synthetic"),
                                    ("cdpUrl", "http://localhost:9222"), ("cdpUrl", "https://cdp.example")):
                with self.subTest(field=field, endpoint=endpoint):
                    data = dict(id="synthetic-owned", timeoutAt="2030-01-01T00:00:00Z", **{field: endpoint},
                                liveUrl=PASSWORD, metadata={"ignored": PASSWORD})
                    self.srv.cloud_creates, self.srv.cloud_response = [], json.dumps(data).encode()
                    result = browser_session._create_cloud_session_or_fallback("synthetic-valid", provider)
                    self.assertEqual(result["bb_session_id"], data["id"])
                    self.assertEqual(result["expires_at"], data["timeoutAt"])
                    self.assertEqual(result["cdp_url"], discovery.return_value.json.return_value["webSocketDebuggerUrl"]
                                     if endpoint.startswith("http") else endpoint)
                    self.assertNotIn(PASSWORD, json.dumps(result))
                    self.assertEqual(self.srv.cloud_creates, [{"customProxy": proxy}])

    def test_byop_username_coincidences_preserve_native_session_creation(self):
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        from tools import browser_session
        provider = BrowserUseBrowserProvider()
        session_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        for username, endpoint in (("browser-use", "wss://connect.browser-use.com/session"),
                                   ("a", "wss://cdp.example/session"),
                                   ("ordinary-user", "wss://cdp.example/session"),
                                   ("synthetic-long-user-" * 40, "https://cdp.example")):
            with self.subTest(username=username):
                proxy = dict(host="proxy.example.com", port=443, protocol="https",
                             username=username, password=PASSWORD)
                data = dict(id=session_id, cdpUrl=endpoint, timeoutAt="2030-01-01T00:00:00Z")
                self.srv.cloud_creates, self.srv.cloud_response = [], json.dumps(data).encode()
                with patch.object(provider, "_get_config", return_value={
                        "base_url": self.url, "api_key": TOKEN, "managed_mode": False}), patch(
                        "agent.secret_scope.current_secret_scope", return_value={"BROWSER_USE_CUSTOM_PROXY_JSON": json.dumps(proxy)}), patch(
                        "requests.get") as discovery, patch.object(browser_session, "_create_local_session") as local:
                    discovery.return_value.json.return_value = {"webSocketDebuggerUrl": "wss://cdp.example/session"}
                    result = browser_session._create_cloud_session_or_fallback("synthetic-username", provider)
                    self.assertEqual(result["bb_session_id"], session_id)
                    self.assertEqual(result["expires_at"], data["timeoutAt"])
                    self.assertEqual(result["cdp_url"], "wss://cdp.example/session" if endpoint.startswith("https") else endpoint)
                    self.assertEqual(discovery.call_count, int(endpoint.startswith("https")))
                    local.assert_not_called()
                self.assertEqual(self.srv.cloud_creates, [{"customProxy": proxy}])

    def test_unconfigured_native_provider_retains_direct_and_managed_creation(self):
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider, _pending_create_keys
        provider = BrowserUseBrowserProvider()
        self.srv.cdp_endpoint = "wss://cdp.example/session"
        with patch("agent.secret_scope.current_secret_scope", return_value={}):
            for managed in (False, True):
                with self.subTest(managed=managed), patch.object(provider, "_get_config", return_value={
                        "base_url": self.url, "api_key": TOKEN, "managed_mode": managed}):
                    self.srv.cloud_creates = []
                    result = provider.create_session("synthetic-unconfigured")
                    self.assertEqual(result["cdp_url"], self.srv.cdp_endpoint)
                    self.assertEqual(result["bb_session_id"], "synthetic-cloud-id")
                    self.assertEqual(self.srv.cloud_creates, [{"timeout": 5, "proxyCountryCode": "us"}
                                                            if managed else {"proxyCountryCode": "kr"}])
                    self.assertNotIn("synthetic-unconfigured", _pending_create_keys)

    def test_byop_duplicate_config_is_rejected_in_scope_and_profile_dotenv(self):
        from agent.secret_scope import set_secret_scope, reset_secret_scope
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        provider = BrowserUseBrowserProvider()
        proxy = dict(host="proxy.example.com", port=443, protocol="https",
                     username="synthetic-proxy-user", password=PASSWORD)
        raw = json.dumps(proxy).replace('"port": 443', '"port": 80, "port": 443')
        with tempfile.TemporaryDirectory() as temp, patch.object(provider, "_get_config", return_value={"managed_mode": False}), patch.object(
                provider, "_post_create") as create, patch("hermes_constants.get_hermes_home", return_value=Path(temp)):
            (Path(temp) / ".env").write_text("BROWSER_USE_CUSTOM_PROXY_JSON='" + raw + "'\n")
            for scope in ({"BROWSER_USE_CUSTOM_PROXY_JSON": raw}, None):
                token = set_secret_scope(scope)
                try:
                    with self.assertRaises(ValueError) as caught:
                        provider.create_session("synthetic-duplicate")
                    self.assertNotIn(PASSWORD, str(caught.exception))
                    self.assertTrue(caught.exception.__suppress_context__)
                finally:
                    reset_secret_scope(token)
            create.assert_not_called()

    def test_byop_secret_scope_does_not_inherit_another_profiles_proxy(self):
        from agent.secret_scope import (set_secret_scope, reset_secret_scope,
                                       set_multiplex_context, reset_multiplex_context)
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        scope, context = set_secret_scope({}), set_multiplex_context(True)
        try:
            with patch.dict(os.environ, {"BROWSER_USE_CUSTOM_PROXY_JSON": PASSWORD}):
                self.assertIsNone(BrowserUseBrowserProvider._custom_proxy())
        finally:
            reset_multiplex_context(context)
            reset_secret_scope(scope)

    def test_browser_use_creation_failure_never_uses_another_browser(self):
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        from tools import browser_session, browser_use_cli
        provider = BrowserUseBrowserProvider()
        with patch.object(provider, "create_session", side_effect=RuntimeError(TOKEN + PASSWORD)), patch.object(
                browser_session, "_create_local_session", side_effect=AssertionError("no local fallback")) as local:
            with self.assertRaisesRegex(RuntimeError, "^Browser Use session creation failed; no local fallback$") as caught:
                browser_session._create_cloud_session_or_fallback("synthetic-cloud-failure", provider)
            self.assertTrue(caught.exception.__suppress_context__)
            self.assert_safe(str(caught.exception))
            local.assert_not_called()
        with patch("tools.browser_session_cloud.get_cloud_provider", return_value=provider), patch.object(
                browser_session, "get_session_info", side_effect=RuntimeError("synthetic unavailable")), patch.object(
                browser_use_cli, "_resolve_local_cdp", side_effect=AssertionError("no alternate browser")) as local:
            error = browser_use_cli._resolve_backend_cdp({}, "synthetic-cloud-failure")
            self.assertIn("failed to provide a session", error)
            local.assert_not_called()

    def test_native_origin_failure_is_not_a_missing_code_field(self):
        from agent.vault_backends.onepassword import OnePasswordLoginBackend
        task = "synthetic-no-browser-context"
        with patch.object(OnePasswordLoginBackend, "resolve_password", side_effect=AssertionError("must not resolve")), patch.object(
                OnePasswordLoginBackend, "resolve_otp", side_effect=AssertionError("must not resolve")), patch.object(
                tool, "_eval_js_secret", side_effect=AssertionError("must not write")), patch(
                "agent.vault_backends.unlock.get_code_prompt_callback", side_effect=AssertionError("must not prompt")):
            for args in ({"action": "enter_code"}, {"action": "enter_code", "handle": self.handle},
                         {"action": "fill", "handle": self.handle}, {"action": "save_login"}):
                with self.subTest(action=args["action"], saved="handle" in args):
                    result = json.loads(tool._handle_vault(args, task_id=task))
                    self.assertFalse(result["success"])
                    self.assertEqual(result.get("error_type"), "origin_unavailable", result)
                    self.assertEqual(result["origin_status"], "no_supervisor")
                    connection = result["connection"]
                    self.assertFalse(connection["session_present"])
                    self.assertFalse(connection["supervisor_present"])
                    self.assertIsNone(connection["active"])
                    self.assertNotIn(task, json.dumps(result))

    def test_plugin_backend_presence_is_unknown_until_eval_observes_it(self):
        from types import SimpleNamespace
        from tools import browser_use_cli
        task = "synthetic-plugin-observation"
        plugin = SimpleNamespace(evaluate=lambda *args, **kwargs: "https://example.com")
        with patch.object(tool, "_page_backend", return_value=plugin) as lookup:
            attached = browser_use_cli._attach_vault_supervisor({}, task)
            self.assertIsNone(attached["plugin_backend_present"])
            lookup.assert_not_called()
            evaluated = tool._eval_js(task, "window.location.href")
            self.assertTrue(evaluated["success"])
            self.assertIs(evaluated["connection"]["plugin_backend_present"], True)
            self.assertEqual(attached["task_fingerprint"], evaluated["connection"]["task_fingerprint"])
        with patch.object(tool, "_page_backend", return_value=None), patch.object(
                tool, "_ensure_supervisor", return_value=None):
            evaluated = tool._eval_js(task, "window.location.href")
            self.assertIs(evaluated["connection"]["plugin_backend_present"], False)
        with patch.object(tool, "_page_backend", side_effect=RuntimeError("synthetic lookup failure")):
            evaluated = tool._eval_js(task, "window.location.href")
            self.assertIsNone(evaluated["connection"]["plugin_backend_present"])

    def test_native_origin_failures_are_typed_and_do_not_echo_transport(self):
        from types import SimpleNamespace
        from tools.browser_supervisor import CDPSupervisor, SUPERVISOR_REGISTRY
        from tools import browser_session, browser_use_cli
        task = "synthetic-origin-failure"
        sentinel = "synthetic-private-endpoint-query-header-credential-input"
        sup = CDPSupervisor(task, "ws://127.0.0.1/" + sentinel)
        # Exercise evaluate_runtime's real inactive and missing-page refusals.
        cases = [("inactive", None, False, None),
                 ("no_page_session", SimpleNamespace(is_running=lambda: True), True, None),
                 ("transport_failed", SimpleNamespace(is_running=lambda: True), True, sentinel)]
        with patch.object(tool, "_focus_bound_origin", return_value=None), patch.object(
                SUPERVISOR_REGISTRY, "get", return_value=sup):
            for category, loop, active, page in cases:
                sup._loop, sup._active, sup._page_session_id = loop, active, page
                with self.subTest(category=category):
                    # For the transport case use the bridge's returned-error contract, which may quote URLs.
                    if category == "transport_failed":
                        with patch.object(sup, "evaluate_runtime", return_value={"ok": False, "error": sentinel}):
                            result = tool._handle_vault({"action": "enter_code"}, task_id=task)
                    else:
                        result = tool._handle_vault({"action": "enter_code"}, task_id=task)
                    self.assertTrue(sentinel not in result, "unsafe native failure output")
                    parsed = json.loads(result)
                    self.assertEqual(parsed["origin_status"], category)
                    self.assertEqual(parsed["connection"]["page_session_present"], bool(page))
            with patch.object(sup, "evaluate_runtime", side_effect=RuntimeError(sentinel)):
                result = tool._handle_vault({"action": "enter_code"}, task_id=task)
                self.assertEqual(json.loads(result)["origin_status"], "transport_failed")
                self.assertTrue(sentinel not in result, "unsafe raised-error output")
        plugin = SimpleNamespace(name=sentinel, evaluate=lambda *args, **kw: (_ for _ in ()).throw(RuntimeError(sentinel)))
        with patch.object(tool, "_page_backend", return_value=plugin), patch.object(tool, "_focus_bound_origin", return_value=None):
            result = tool._handle_vault({"action": "enter_code"}, task_id=task)
            self.assertTrue(sentinel not in result, "unsafe plugin failure output")
            self.assertTrue(json.loads(result)["connection"]["plugin_backend_present"])
            self.assertEqual(json.loads(result)["origin_status"], "transport_failed")
        with patch.object(tool, "_page_backend", return_value=SimpleNamespace(evaluate=lambda *args, **kw: "about:blank")), patch.object(
                tool, "_focus_bound_origin", return_value=None):
            result = json.loads(tool._handle_vault({"action": "enter_code"}, task_id=task))
            self.assertEqual(result["origin_status"], "invalid_origin")
        # An attach failure with a known endpoint differs from no task session, without raw logging.
        endpoint = "ws://127.0.0.1/devtools/browser/synthetic?token=" + sentinel
        with patch.object(browser_session, "session_for_task", return_value={"cdp_url": endpoint}), patch.object(
                SUPERVISOR_REGISTRY, "get_or_start", side_effect=RuntimeError(sentinel)):
            with self.assertLogs(tool.logger, level="DEBUG") as logs:
                result = tool._handle_vault({"action": "enter_code"}, task_id=task)
            self.assertEqual(json.loads(result)["origin_status"], "transport_failed")
            self.assertTrue(sentinel not in result + str(logs.output), "unsafe attach failure output")
            attached = browser_use_cli._attach_vault_supervisor({"BU_CDP_WS": endpoint}, task)
            self.assertEqual(attached["attach_status"], "attach_failed")
            self.assertTrue(sentinel not in json.dumps(attached), "unsafe browser_exec attach output")
        unknown = tool._connection_metadata(task, supervisor=object())
        self.assertIsNone(unknown["active"])
        self.assertIsNone(unknown["page_session_present"])
        self.assertEqual(browser_use_cli._attach_vault_supervisor({}, task)["attach_status"], "no_endpoint")

    def test_native_chromium_fill_otp_redaction_mismatch_and_navigation(self):
        from tools.browser_supervisor import SUPERVISOR_REGISTRY
        from tools.browser_cdp_tool import _redact_cdp_output
        from agent.vault_backends.onepassword import OnePasswordLoginBackend
        from tools import browser_session, browser_use_cli

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
            from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
            from tools import browser_session, browser_use_cli
            self.srv.cdp_endpoint, self.srv.cloud_creates = endpoint, []
            provider = BrowserUseBrowserProvider()
            env = {}
            with patch("tools.browser_session_cloud.get_cloud_provider", return_value=provider), patch.object(
                    provider, "_get_config", return_value={"base_url": self.url, "api_key": TOKEN, "managed_mode": False}), patch.dict(
                    os.environ, {"BROWSER_USE_API_KEY": TOKEN, "BH_HOME": str(ROOT / "harness")}):
                self.assertTrue(browser_use_cli._browser_use_api_key_set())
                self.assertIsNone(browser_use_cli._resolve_backend_cdp(env, task))
                self.assertIsNone(browser_use_cli._resolve_backend_cdp({}, task))
                named = {}
                self.assertIsNone(browser_use_cli._resolve_backend_cdp(named, task, session_name="synthetic-named"))
                driven = browser_use_cli.browser_exec("new_tab(" + repr(self.url + "/login") +
                    "); wait_for_load(); print(page_info())\n" +
                    "for target in cdp('Target.getTargets')['targetInfos']:\n" +
                    "    if target['type'] == 'page' and target['targetId'] != current_tab()['targetId']:\n" +
                    "        cdp('Target.closeTarget', targetId=target['targetId'])\n",
                    session="synthetic-named", task_id=task)
                driven = json.loads(driven) if isinstance(driven, str) else driven
                self.assertTrue(driven["success"], driven)
                self.assertIn("Synthetic login", driven["output"])
                self.assert_safe(json.dumps(driven))
            self.assertEqual(self.srv.cloud_creates, [{"proxyCountryCode": "kr"}, {"proxyCountryCode": "kr"}])
            self.assertEqual(env.get("BU_CDP_WS") or env.get("BU_CDP_URL"), endpoint)
            self.assertNotEqual(browser_session._active_sessions[task]["session_key"],
                                browser_session._active_sessions["bu-named-synthetic-named"]["session_key"])
            self.assertEqual(driven["browser_vault_connection"]["attach_status"], "attached")
            sup = SUPERVISOR_REGISTRY.get(task)
            self.assertIsNotNone(sup)
            self.assertEqual(tool._eval_js(task, "window.location.href")["connection"]["task_fingerprint"],
                             driven["browser_vault_connection"]["task_fingerprint"])
            self.assertNotEqual(tool._connection_metadata("synthetic-other-session")["task_fingerprint"],
                                driven["browser_vault_connection"]["task_fingerprint"])
            with patch.object(sup, "evaluate_runtime", return_value={"ok": False, "error": TOKEN + PASSWORD}):
                refused = json.loads(tool._handle_vault({"action": "enter_code"}, task_id=task))
            self.assertEqual(refused["origin_status"], "transport_failed")
            self.assertEqual(refused["connection"]["task_fingerprint"],
                             driven["browser_vault_connection"]["task_fingerprint"])
            self.assert_safe(json.dumps(refused))
            self.assertEqual(sup.cdp_url, endpoint)
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
            self.assertTrue(evaluate("document.title").endswith("logged-in-synthetic"))
            no_field = json.loads(tool._handle_vault({"action": "enter_code"}, task_id=task))
            self.assertEqual(no_field["error_type"], "no_code_field")
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
            from tools import browser_session
            browser_use_cli.stop_harness_daemons()
            with browser_session._cleanup_lock:
                browser_session._active_sessions.clear()
            SUPERVISOR_REGISTRY.stop_all()
            process.terminate()
            process.wait(timeout=15)


class DefaultProxyBindingTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("configure_browser_proxy", APP / "configure_browser_proxy.py")
        assert spec is not None and spec.loader is not None
        self.binding = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.binding)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source, self.env = self.home / "projection", self.home / ".env"
        self.proxy = dict(host="proxy.hchu.me", port=443, protocol="https",
                          username="synthetic-user", password="synthetic \\ ' \" # ${HOME} 한글")
        self.source.write_text(json.dumps(self.proxy))

    def test_dotenv_preserves_unrelated_bytes_mode_and_native_roundtrip(self):
        from agent.secret_scope import load_env_file
        from hermes_cli.env_loader import load_hermes_dotenv
        key = self.binding.KEY
        prefix = b'\xef\xbb\xbf# keep comment\r\n  OTHER = "keep\\\\path # value"\r\nMULTI=\'first\nsecond\'\r\n'
        suffix = b'# trailing comment\r\nAUTH=untouched'
        self.env.write_bytes(prefix + (key + '=old\nexport ' + key + '=stale\n').encode() + suffix)
        self.env.chmod(0o640)
        coder = self.home / "profiles/coder/.env"
        coder.parent.mkdir(parents=True)
        coder.write_bytes(b'CODER=unchanged\n')
        with patch.dict(os.environ, {key: "inherited-default"}):
            self.binding.reconcile(self.source, self.env)
            self.assertEqual(os.environ[key], "inherited-default")
            result = self.env.read_bytes()
            self.binding.reconcile(self.source, self.env)
            self.assertEqual(self.env.read_bytes(), result)
            self.assertEqual(self.env.stat().st_mode & 0o777, 0o640)
            with patch("hermes_cli.env_loader._apply_external_secret_sources"):
                load_hermes_dotenv(hermes_home=self.home, load_external_secrets=False)
            self.assertEqual(json.loads(os.environ[key]), self.proxy)
        self.assertTrue(result.startswith(prefix))
        self.assertIn(suffix, result)
        self.assertEqual(result.count((key + "=").encode()), 1)
        self.assertEqual(json.loads(load_env_file(self.env)[key]), self.proxy)
        self.assertEqual(coder.read_bytes(), b'CODER=unchanged\n')

    def test_input_failures_leave_env_untouched_and_main_sanitized(self):
        import contextlib
        import io
        original = b'# protected\nMODEL=unchanged\n'
        invalid = [b'', b' ', b'null', b'[]', b'{}', b'---\n{}', b'{}\n{}', b'\xff',
                   json.dumps(self.proxy).replace('443', '443.0').encode(),
                   json.dumps(self.proxy).replace('proxy.hchu.me', 'unexpected.example').encode(),
                   json.dumps(self.proxy).replace('"port": 443', '"port": 443, "port": 443').encode()]
        for key, value in [('username', None), ('password', ''), ('password', ' '), ('port', True),
                           ('protocol', 'http'), ('password', 'synthetic\ncanary'), ('extra', 'synthetic-canary')]:
            invalid.append(json.dumps(dict(self.proxy, **{key: value})).encode())
        for raw in [None, *invalid]:
            with self.subTest(case=invalid.index(raw) if raw in invalid else -1):
                self.env.write_bytes(original)
                self.source.unlink(missing_ok=True)
                if raw is not None:
                    self.source.write_bytes(raw)
                out = io.StringIO()
                with patch.object(self.binding, 'SOURCE', self.source), patch.object(self.binding, 'ENV', self.env), contextlib.redirect_stderr(out):
                    self.assertEqual(self.binding.main(), 1)
                self.assertEqual(out.getvalue(), 'Browser proxy binding refused; gateway startup blocked\n')
                self.assertEqual(self.env.read_bytes(), original)

    def test_unexpected_env_and_failed_writer_are_safe(self):
        original = b'# keep\nAUTH=untouched\n'
        target = self.home / 'target'
        target.write_bytes(original)
        self.env.symlink_to(target)
        with self.assertRaises(ValueError):
            self.binding.reconcile(self.source, self.env)
        self.assertEqual(target.read_bytes(), original)
        self.env.unlink()
        self.env.write_bytes(b'BROKEN="unterminated\n')
        with self.assertRaises(ValueError):
            self.binding.reconcile(self.source, self.env)
        self.assertEqual(self.env.read_bytes(), b'BROKEN="unterminated\n')
        self.env.write_bytes(original)
        with patch.object(self.binding, '_write_env_lines', side_effect=OSError('synthetic-canary')):
            with self.assertRaises(OSError):
                self.binding.reconcile(self.source, self.env)
        self.assertEqual(self.env.read_bytes(), original)

    def test_unscoped_profiles_ignore_inherited_selector(self):
        from agent.secret_scope import set_secret_scope, reset_secret_scope
        from plugins.browser.browser_use.provider import BrowserUseBrowserProvider
        token = set_secret_scope(None)
        try:
            with patch.dict(os.environ, {self.binding.KEY: json.dumps(self.proxy)}), patch('hermes_constants.get_hermes_home', return_value=self.home):
                self.assertIsNone(BrowserUseBrowserProvider._custom_proxy())
                self.binding.reconcile(self.source, self.env)
                self.assertEqual(BrowserUseBrowserProvider._custom_proxy(), self.proxy)
                coder = self.home / 'profiles/coder'
                coder.mkdir(parents=True)
                (coder / '.env').write_text('UNRELATED=keep\n')
                with patch('hermes_constants.get_hermes_home', return_value=coder):
                    self.assertIsNone(BrowserUseBrowserProvider._custom_proxy())
        finally:
            reset_secret_scope(token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
