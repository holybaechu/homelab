#!/bin/sh

failures=0

pass() {
  printf 'PASS %s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1"
  failures=$((failures + 1))
}

if docker compose exec -T qbittorrent test -f /vuetorrent/public/index.html \
  >/dev/null 2>&1; then
  pass 'asset: /vuetorrent/public/index.html exists'
else
  fail 'asset: /vuetorrent/public/index.html is missing or inaccessible'
fi

if docker compose exec -T qbittorrent grep -Fx -- \
  'WebUI\AlternativeUIEnabled=true' \
  /config/qBittorrent/qBittorrent.conf >/dev/null 2>&1; then
  pass 'config: exact WebUI\AlternativeUIEnabled=true'
else
  fail 'config: exact WebUI\AlternativeUIEnabled=true is missing'
fi

if docker compose exec -T qbittorrent grep -Fx -- \
  'WebUI\RootFolder=/vuetorrent/public' \
  /config/qBittorrent/qBittorrent.conf >/dev/null 2>&1; then
  pass 'config: exact WebUI\RootFolder=/vuetorrent/public'
else
  fail 'config: exact WebUI\RootFolder=/vuetorrent/public is missing'
fi

effective_mods="$(
  docker compose exec -T qbittorrent printenv DOCKER_MODS 2>/dev/null
)"
effective_mods_status=$?
effective_mods="$(printf '%s' "${effective_mods}" | tr -d '\r')"
effective_mods_lines="$(printf '%s\n' "${effective_mods}" | awk 'END { print NR }')"
effective_mods_matches="$(
  printf '%s\n' "${effective_mods}" \
    | grep -Ec '^ghcr\.io/vuetorrent/vuetorrent-lsio-mod:[0-9]+\.[0-9]+\.[0-9]+$'
)"

if test "${effective_mods_status}" -ne 0; then
  fail 'environment: effective DOCKER_MODS=<invalid or unavailable; value suppressed>; printenv command failed'
elif test "${effective_mods_lines}" -eq 1 \
  && test "${effective_mods_matches}" -eq 1; then
  pass "environment: effective DOCKER_MODS=${effective_mods}"
else
  fail 'environment: effective DOCKER_MODS=<invalid or unavailable; value suppressed>; expected one official VueTorrent mod with a semantic-version tag'
fi

if test "${failures}" -eq 0; then
  exit 0
fi

init_logs="$(docker compose logs --no-color --tail 80 qbittorrent 2>&1)"
init_logs_status=$?
if test "${init_logs_status}" -eq 0; then
  vuetorrent_mentioned=no
  docker_mod_mentioned=no
  error_failure_mentioned=no
  if printf '%s\n' "${init_logs}" | grep -Eiq 'vuetorrent' 2>/dev/null; then
    vuetorrent_mentioned=yes
  fi
  if printf '%s\n' "${init_logs}" \
    | grep -Eiq 'docker[ _-]?mods?|linuxserver[ _-]?mods?' 2>/dev/null; then
    docker_mod_mentioned=yes
  fi
  if printf '%s\n' "${init_logs}" \
    | grep -Eiq 'error|fail(ed|ure|ing)?' 2>/dev/null; then
    error_failure_mentioned=yes
  fi
  printf 'INFO qBittorrent init-log summary: VueTorrent mentioned=%s\n' \
    "${vuetorrent_mentioned}"
  printf 'INFO qBittorrent init-log summary: Docker mod mentioned=%s\n' \
    "${docker_mod_mentioned}"
  printf 'INFO qBittorrent init-log summary: error/failure mentioned=%s\n' \
    "${error_failure_mentioned}"
else
  printf '%s\n' \
    'INFO qBittorrent init-log summary unavailable (docker compose logs command failed)'
fi

exit 1
