#!/usr/bin/env bash
set -euo pipefail

PYTEST_TARGETS=(tests/docker tests/infra tests/repo tests/tailnet)
python3 -m pytest "${PYTEST_TARGETS[@]}" -q

ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --syntax-check

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
  echo "docker compose is not available; skipping compose config validation" >&2
fi
