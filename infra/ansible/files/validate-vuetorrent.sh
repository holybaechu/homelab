#!/bin/sh

failures=0

pass() {
  printf 'PASS %s\n' "$1"
}

fail() {
  printf 'FAIL %s\n' "$1"
  failures=$((failures + 1))
}

if docker compose exec -T qbittorrent test -f /vuetorrent/index.html \
  >/dev/null 2>&1; then
  pass 'asset: /vuetorrent/index.html exists'
else
  fail 'asset: /vuetorrent/index.html is missing or inaccessible'
fi

if docker compose exec -T qbittorrent grep -Fx -- \
  'WebUI\AlternativeUIEnabled=true' \
  /config/qBittorrent/qBittorrent.conf >/dev/null 2>&1; then
  pass 'config: exact WebUI\AlternativeUIEnabled=true'
else
  fail 'config: exact WebUI\AlternativeUIEnabled=true is missing'
fi

if docker compose exec -T qbittorrent grep -Fx -- \
  'WebUI\RootFolder=/vuetorrent' \
  /config/qBittorrent/qBittorrent.conf >/dev/null 2>&1; then
  pass 'config: exact WebUI\RootFolder=/vuetorrent'
else
  fail 'config: exact WebUI\RootFolder=/vuetorrent is missing'
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

safe_effective_mods="$(
  printf '%s' "${effective_mods}" | tr '\n' ' ' | cut -c 1-200
)"
if test -z "${safe_effective_mods}"; then
  safe_effective_mods='<empty>'
elif printf '%s\n' "${safe_effective_mods}" \
  | grep -Eiq '(password|passwd|secret|token|authorization|cookie|private[ _-]?key|pbkdf2|hash)'; then
  safe_effective_mods='<redacted potentially sensitive value>'
fi

if test "${effective_mods_status}" -eq 0 \
  && test "${effective_mods_lines}" -eq 1 \
  && test "${effective_mods_matches}" -eq 1; then
  pass "environment: effective DOCKER_MODS=${safe_effective_mods}"
else
  fail "environment: effective DOCKER_MODS=${safe_effective_mods}; expected one official VueTorrent mod with a semantic-version tag"
fi

if test "${failures}" -eq 0; then
  exit 0
fi

printf '%s\n' \
  'INFO qBittorrent init logs: last 80 lines, max 400 characters per line; sensitive lines redacted'
init_logs="$(docker compose logs --no-color --tail 80 qbittorrent 2>&1)"
init_logs_status=$?
if test "${init_logs_status}" -eq 0; then
  printf '%s\n' "${init_logs}" | awk '
    {
      lower = tolower($0)
      if (lower ~ /(password|passwd|secret|token|authorization|cookie|private[ _-]?key|pbkdf2|hash)/) {
        print "[redacted potentially sensitive init-log line]"
      } else {
        print substr($0, 1, 400)
      }
    }
  '
else
  printf 'INFO qBittorrent init logs unavailable (docker compose rc=%s)\n' \
    "${init_logs_status}"
fi

exit 1
