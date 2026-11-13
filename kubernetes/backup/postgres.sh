#!/bin/sh
set -eu
umask 077
capture=$(mktemp -d)
trap 'rm -rf "$capture"' EXIT HUP INT TERM
# Native tools keep credentials and database diagnostics off the stream.
pg_dump --format=custom > "$capture/database.dump" 2>/dev/null
pg_dumpall --roles-only > "$capture/roles.sql" 2>/dev/null
mkdir "$capture/recovery"
# Dereference only the explicitly projected credential files.
find /recovery -type l ! -name '..*' -exec cp -RL --parents '{}' "$capture" ';'
# Database first, files second, as recommended by Immich's backup guide.
tar --xattrs --xattrs-include='*' --acls --numeric-owner -cf - \
  -C "$capture" database.dump roles.sql recovery -C /source . 2>/dev/null
