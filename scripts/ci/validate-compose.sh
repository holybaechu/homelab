#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 \
  || ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required for Compose package validation" >&2
  exit 1
fi

target="${1:-all}"
case "$target" in
  all|apps|openclaw) ;;
  *) echo "usage: $0 [all|apps|openclaw]" >&2; exit 2 ;;
esac

temporary="$(mktemp -d "${TMPDIR:-/tmp}/homelab-compose-validate.XXXXXXXX")"
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT

if [ "$target" = all ] || [ "$target" = apps ]; then
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
  docker compose \
    --project-directory "$apps" \
    -f "$apps/compose.yml" \
    config --no-env-resolution --no-path-resolution >/dev/null
fi

if [ "$target" = all ] || [ "$target" = openclaw ]; then
  OPENCLAW_GATEWAY_REF="ghcr.io/holybaechu/homelab-openclaw-gateway@sha256:$(printf '1%.0s' {1..64})" \
  OPENCLAW_CTF_REF="ghcr.io/holybaechu/homelab-openclaw-ctf@sha256:$(printf '2%.0s' {1..64})" \
  OPENCLAW_CONFIG_COMMIT="$(printf '3%.0s' {1..40})" \
  OPENCLAW_RELEASE_ID="$(printf '4%.0s' {1..64})" \
  OPENCLAW_CONFIG_ROOT="$temporary/openclaw-config" \
  OPENCLAW_SECRET_ROOT="$temporary/openclaw-secrets" \
  OPENCLAW_DOCKER_GID=999 \
    docker compose \
      --project-directory infra/openclaw/runtime \
      -f infra/openclaw/runtime/compose.yml \
      config --no-env-resolution --no-path-resolution >/dev/null
fi

echo "$target Compose package validation passed"
