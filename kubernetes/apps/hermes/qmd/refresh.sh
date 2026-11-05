#!/bin/sh
set -u

# Call the pinned CLI directly so termination reaches the active Node process.
cli=/opt/qmd/node_modules/@tobilu/qmd/dist/cli/qmd.js
job=
stop() {
  trap - TERM INT
  if [ -n "$job" ]; then
    kill "$job" 2>/dev/null || true
    wait "$job" 2>/dev/null || true
  fi
  exit 0
}
trap stop TERM INT

run() {
  "$@" &
  job=$!
  wait "$job"
  result=$?
  job=
  return "$result"
}

mkdir -p "$XDG_CACHE_HOME/qmd"
while :; do
  if run node "$cli" --index index update && run node "$cli" --index index embed; then
    date -u +'%Y-%m-%dT%H:%M:%SZ' > "$XDG_CACHE_HOME/qmd/last-refresh"
  else
    echo 'QMD refresh failed; existing index remains available, retrying in 300s' >&2
  fi
  run sleep 300
done
