#!/bin/sh
set -eu
umask 077
capture=$(mktemp -d)
trap 'rm -rf "$capture"' EXIT HUP INT TERM
failed() {
  code=$?
  printf 'PostgreSQL %s failed (exit %s)\n' "$1" "$code" >&2
  exit "$code"
}
# Native tools keep credentials and database diagnostics off the stream.
pg_dump --format=custom > "$capture/database.dump" 2>/dev/null || failed 'database dump'
pg_dumpall --roles-only > "$capture/roles.sql" 2>/dev/null || failed 'roles dump'
mkdir "$capture/recovery"
# Dereference only the explicitly projected credential files.
for key in /recovery/*; do
  [ -e "$key" ] || continue
  cp -RL --parents "$key" "$capture" 2>/dev/null || failed 'recovery key copy'
done
# Database first, files second, as recommended by Immich's backup guide.
tar --xattrs --xattrs-include='*' --acls --numeric-owner -cf - \
  -C "$capture" database.dump roles.sql recovery -C /source . 2>/dev/null || failed 'file archive'
