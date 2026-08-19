#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 \
  || ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required for compose config validation" >&2
  exit 1
fi

stack=apps/compose/homelab
T3CODE_IMAGE_REF='ghcr.io/holybaechu/homelab-t3code@sha256:0000000000000000000000000000000000000000000000000000000000000000' \
  docker compose \
    --env-file "$stack/.env.example" \
    -f "$stack/compose.yml" \
    config --no-env-resolution >/dev/null
