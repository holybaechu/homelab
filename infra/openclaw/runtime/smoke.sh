#!/bin/sh
set -eu

: "${OPENCLAW_SECRET_ROOT:?OpenClaw release secret root is required}"

python3 - "$OPENCLAW_SECRET_ROOT/gateway_token" <<'PY'
from __future__ import annotations

import re
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SmokeFailure(RuntimeError):
    pass


def probe(url: str, token: str | None = None) -> int:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=10) as response:
            response.read(4096)
            return response.status
    except HTTPError as error:
        error.read(4096)
        return error.code
    except URLError as error:
        raise SmokeFailure(f"Gateway request failed: {error.reason}") from error


token_path = Path(sys.argv[1])
metadata = token_path.lstat()
if not stat.S_ISREG(metadata.st_mode) or token_path.is_symlink():
    raise SmokeFailure("Gateway token is not a regular file")
if stat.S_IMODE(metadata.st_mode) & 0o077:
    raise SmokeFailure("Gateway token permissions are broader than 0600")
token = token_path.read_text(encoding="utf-8").strip()
if re.fullmatch(r"[0-9a-f]{64}", token) is None:
    raise SmokeFailure("Gateway token is not exact lowercase 64-hex")

ready = probe("http://127.0.0.1:18789/readyz")
if not 200 <= ready < 300:
    raise SmokeFailure(f"Gateway readiness returned HTTP {ready}")
protected_url = "http://127.0.0.1:18789/control-ui-config.json"
unauthenticated = probe(protected_url)
if unauthenticated not in {401, 403}:
    raise SmokeFailure(
        f"unauthenticated Gateway control probe returned HTTP {unauthenticated}, expected 401 or 403"
    )
wrong_token = "0" * 64 if token != "0" * 64 else "1" * 64
wrongly_authenticated = probe(protected_url, wrong_token)
if wrongly_authenticated not in {401, 403}:
    raise SmokeFailure(
        f"wrong-token Gateway control probe returned HTTP {wrongly_authenticated}, expected 401 or 403"
    )
authenticated = probe(
    protected_url,
    token,
)
if not 200 <= authenticated < 300:
    raise SmokeFailure(f"authenticated Gateway smoke returned HTTP {authenticated}")
print("OpenClaw readiness, authentication rejection, and authenticated smoke passed")
PY
