#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 \
  || ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required for Compose package validation" >&2
  exit 1
fi

[ "$#" -eq 0 ] || { echo "usage: $0" >&2; exit 2; }

temporary="$(mktemp -d "${TMPDIR:-/tmp}/homelab-compose-validate.XXXXXXXX")"
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT

apps="$temporary/apps"
cp -R apps/compose/homelab "$apps"
python3 - "$temporary/apps-secrets.json" <<'PY'
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path


payload = {
    "component": "apps",
    "version": 1,
    "cloudflare": {
        "traefik_dns_api_token": "validation-traefik-token",
        "ddns_api_token": "validation-ddns-token",
    },
    "adguard": {
        "username": "admin",
        "password_hash": "$2y$10$" + "a" * 53,
    },
    "qbittorrent": {
        "username": "admin",
        "password_hash": "@ByteArray(%s:%s)"
        % (
            base64.b64encode(bytes(16)).decode("ascii"),
            base64.b64encode(bytes(64)).decode("ascii"),
        ),
    },
    "copyparty_users": [{"name": "validator", "password": "validation-password"}],
}
path = Path(sys.argv[1])
path.write_text(json.dumps(payload), encoding="utf-8")
path.chmod(0o600)
PY
python3 "$apps/prepare_release.py" \
  --secret-bundle "$temporary/apps-secrets.json" \
  --release-root "$apps" \
  --topology infra/ansible/inventory/prod/topology.json
(
  cd "$apps"
  docker compose \
    --project-directory "$apps" \
    -f compose.yml \
    config --quiet
)

echo "apps Compose package validation passed"
