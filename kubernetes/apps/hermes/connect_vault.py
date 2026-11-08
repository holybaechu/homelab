"""Read-only Connect adapter for Hermes's native credential broker.

No CLI/SDK is needed for these three documented GET endpoints. Authentication
stays in a server-only Secret projection, never in child process environments.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from agent.vault_backends.plugin import PluginBackendError
from agent.vault_store import VaultItemMeta, normalize_otp_secret, normalize_origin, totp_now

_ID = re.compile(r"[a-z0-9]{26}")
_INTERNAL = "http://onepassword-connect.onepassword.svc.cluster.local:8080"
_MAX_BYTES = 4 * 1024 * 1024


class ConnectError(PluginBackendError):
    pass


def require(condition, category="invalid_response"):
    if not condition:
        raise ConnectError("1Password Connect: " + category)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "invalid_response")
        result[key] = value
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def origin(value):
    # Reject URL spellings on which Python and the browser URL parser disagree.
    require(isinstance(value, str) and len(value) <= 8192, "invalid_url")
    require(not any(ord(c) <= 32 or ord(c) >= 127 or c in "\\%" for c in value), "invalid_url")
    parts = urlsplit(value)
    require(parts.scheme in ("http", "https") and parts.hostname and
            not parts.username and not parts.password and "[" not in parts.netloc, "invalid_url")
    require(re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]+)?", parts.netloc) is not None, "invalid_url")
    host = parts.hostname or ""
    require(not host.endswith(".") and not host.startswith(".") and ".." not in host, "invalid_url")
    # Numeric/non-decimal IPv4 spellings are normalized differently by WHATWG.
    if host.split(".")[-1].isdigit():
        import ipaddress
        require(str(ipaddress.IPv4Address(host)) == host, "invalid_url")
    return normalize_origin(value)


class ConnectVault:
    def __init__(self, host, token_file):
        self.host, self.token_file = host, token_file
        self.coverage = []
        self.snapshot: ContextVar[dict | None] = ContextVar("connect_vault_snapshot", default=None)

    def get(self, path):
        category = "transport_failed"
        try:
            parsed = urlsplit(self.host)
            require(self.host == _INTERNAL or
                    (parsed.scheme == "https" and parsed.hostname and parsed.path in ("", "/") and
                     not parsed.username and not parsed.password and not parsed.query and not parsed.fragment) or
                    (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and
                     parsed.path == "" and not parsed.query and not parsed.fragment and
                     not parsed.username and not parsed.password), "invalid_endpoint")
            token = Path(self.token_file).read_text(encoding="utf-8")
            require(0 < len(token) <= 16384 and not any(c.isspace() for c in token), "invalid_token_file")
            request = Request(self.host.rstrip("/") + path, headers={
                "Authorization": "Bearer " + token, "Accept": "application/json"}, method="GET")
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
                require(response.status == 200)
                data = response.read(_MAX_BYTES + 1)
            require(len(data) <= _MAX_BYTES, "response_too_large")
            return json.loads(data, object_pairs_hook=unique_object)
        except ConnectError:
            raise
        except HTTPError as exc:
            category = {401: "authentication_failed", 403: "access_denied", 404: "not_found"}.get(exc.code, "http_failed")
            exc.close()
        except Exception:
            pass
        # Outside the except block: neither body nor secret-bearing request is chained.
        raise ConnectError("1Password Connect: " + category)

    @staticmethod
    def collection(value, limit):
        require(isinstance(value, list) and len(value) <= limit)
        require(all(isinstance(v, dict) and isinstance(v.get("id"), str) and
                    _ID.fullmatch(v["id"]) for v in value))
        require(len({v["id"] for v in value}) == len(value))
        return value

    @staticmethod
    def ids(handle):
        match = re.fullmatch(r"op:connect:([a-z0-9]{26}):([a-z0-9]{26})", handle)
        if match is None:
            raise ConnectError("1Password Connect: invalid_handle")
        return match.groups()

    def item(self, handle):
        vault, ident = self.ids(handle)
        snapshot = self.snapshot.get()
        if snapshot is not None and handle in snapshot:
            return snapshot[handle]
        item = self.get(f"/v1/vaults/{vault}/items/{ident}")
        require(isinstance(item, dict) and item.get("id") == ident and
                isinstance(item.get("vault"), dict) and item["vault"].get("id") == vault and
                item.get("category") == "LOGIN" and item.get("state", "ACTIVE") == "ACTIVE", "identity_mismatch")
        fields, urls = item.get("fields", []), item.get("urls", [])
        require(isinstance(fields, list) and len(fields) <= 256 and all(isinstance(f, dict) for f in fields))
        require(isinstance(urls, list) and len(urls) <= 64 and all(isinstance(u, dict) for u in urls))
        require(all(isinstance(f.get("value", ""), str) and len(f.get("value", "")) <= 65536 for f in fields))
        require(isinstance(item.get("title", ""), str) and len(item.get("title", "")) <= 1024)
        if snapshot is not None:
            snapshot[handle] = item
        return item

    @staticmethod
    def field(item, purpose):
        values = [f.get("value", "") for f in item.get("fields", []) if f.get("purpose") == purpose]
        require(len(values) <= 1, "ambiguous_field")
        return values[0] if values else ""

    @staticmethod
    def seed(item):
        values = [f.get("value", "") for f in item.get("fields", [])
                  if f.get("type") == "OTP" or f.get("purpose") == "ONE_TIME_PASSWORD"]
        require(len(values) <= 1, "ambiguous_otp")
        if not values or not values[0]:
            return None
        failed, seed = False, ""
        try:
            seed = normalize_otp_secret(values[0])
            totp_now(seed)
        except Exception:
            failed = True
        require(not failed, "invalid_otp")
        return seed

    def meta(self, handle):
        item = self.item(handle)
        origins = []
        failed = False
        try:
            for url in item.get("urls", []):
                href = url.get("href")
                if isinstance(href, str) and not href.startswith(("http://", "https://")):
                    continue  # Native app URLs do not authorize a browser origin.
                bound = origin(href)
                if bound not in origins:
                    origins.append(bound)
        except Exception:
            failed = True
        require(not failed, "invalid_url")
        if not origins:
            return None
        username = self.field(item, "USERNAME")
        return VaultItemMeta(id=handle, kind="login", label=item.get("title") or origins[0],
                             origin=origins[0], created_at="", identifier=username or None,
                             identifier_type="username" if username else None,
                             allowed_origins=tuple(origins), has_otp=self.seed(item) is not None)

    def list_items(self):
        out, total = [], 0
        coverage = []
        for vault in self.collection(self.get("/v1/vaults"), 256):
            vid = vault["id"]
            items = self.collection(self.get(f"/v1/vaults/{vid}/items"), 10000)
            total += len(items)
            require(total <= 10000, "too_many_items")
            logins = 0
            for item in items:
                if item.get("category") != "LOGIN" or item.get("state", "ACTIVE") != "ACTIVE":
                    continue
                require(len(out) < 10000, "too_many_logins")
                meta = self.meta(f"op:connect:{vid}:{item['id']}")
                logins += 1
                if meta:
                    out.append(meta)
            coverage.append({"vault_id": vid, "active_logins": logins})
        self.coverage = coverage
        return out

    @contextmanager
    def fill_context(self):
        token = self.snapshot.set({})
        try:
            yield
        finally:
            self.snapshot.reset(token)

    def password(self, handle):
        value = self.field(self.item(handle), "PASSWORD")
        require(bool(value), "missing_password")
        return value

    def otp(self, handle):
        seed = self.seed(self.item(handle))
        return totp_now(seed) if seed else None
