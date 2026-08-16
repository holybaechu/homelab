#!/usr/bin/env bash
set -euo pipefail

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  for compose_file in apps/compose/*/compose.yml; do
    project_dir="$(dirname "${compose_file}")"
    cleanup_env=0
    if [ ! -f "${project_dir}/.env" ] && [ -f "${project_dir}/.env.example" ]; then
      cp "${project_dir}/.env.example" "${project_dir}/.env"
      cleanup_env=1
    fi
    docker compose -f "${compose_file}" config >/dev/null
    if [ "${cleanup_env}" -eq 1 ]; then
      rm -f "${project_dir}/.env"
    fi
  done
else
  echo "docker compose is required for compose config validation" >&2
  exit 1
fi
