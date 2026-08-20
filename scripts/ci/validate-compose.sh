#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 \
  || ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required for compose config validation" >&2
  exit 1
fi

stack=apps/compose/homelab
temporary=$(mktemp -d "${TMPDIR:-/tmp}/homelab-compose-validate.XXXXXXXX")
cleanup() {
  rm -f -- "$temporary/traefik.env" "$temporary/cloudflare-ddns.env" \
    "$temporary/compose.yml"
  rmdir -- "$temporary"
}
trap cleanup EXIT
: >"$temporary/traefik.env"
: >"$temporary/cloudflare-ddns.env"
sed \
  -e 's|/etc/homelab/secrets/traefik.env|traefik.env|' \
  -e 's|/etc/homelab/secrets/cloudflare-ddns.env|cloudflare-ddns.env|' \
  "$stack/compose.yml" >"$temporary/compose.yml"
T3CODE_IMAGE_REF='ghcr.io/holybaechu/homelab-t3code@sha256:0000000000000000000000000000000000000000000000000000000000000000' \
  docker compose \
    --env-file "$stack/.env.example" \
    -f "$temporary/compose.yml" \
    config --no-env-resolution --no-path-resolution >/dev/null
