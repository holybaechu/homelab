#!/usr/bin/env bash
# Synthetic fixture: run only in the isolated CI PostgreSQL container.
set -euo pipefail
export PGHOST=$1 PGUSER=postgres PGDATABASE=postgres
for attempt in {1..60}; do
  if pg_isready >/dev/null 2>&1; then break; fi
  sleep 1
done
psql -v ON_ERROR_STOP=1 -c 'CREATE ROLE fixture_owner' >/dev/null
createdb -O fixture_owner fixture_source
psql -d fixture_source -v ON_ERROR_STOP=1 -c "CREATE TABLE objects (id int, body bytea); INSERT INTO objects VALUES (1, decode('010203ff', 'hex')); ALTER TABLE objects OWNER TO fixture_owner" >/dev/null
mkdir -p /source /recovery/..data/app /tmp/restored
printf 'synthetic-media' > /source/media.txt
printf 'synthetic-original-key' > /recovery/..data/app/key
ln -s ..data/app /recovery/app
export PGDATABASE=fixture_source
sh /repo/kubernetes/backup/postgres.sh > /tmp/capture.tar
tar -xf /tmp/capture.tar -C /tmp/restored
cmp /source/media.txt /tmp/restored/media.txt
cmp /recovery/app/key /tmp/restored/recovery/app/key
test ! -L /tmp/restored/recovery/app/key
export PGDATABASE=postgres
dropdb fixture_source
psql -v ON_ERROR_STOP=1 -c 'DROP ROLE fixture_owner' >/dev/null
# The isolated target already has this one known bootstrap role.
sed '/^CREATE ROLE postgres;$/d' /tmp/restored/roles.sql | psql -v ON_ERROR_STOP=1 >/dev/null
createdb -O fixture_owner fixture_restored
pg_restore --exit-on-error -d fixture_restored /tmp/restored/database.dump
test "$(psql -d fixture_restored -Atc "SELECT id, encode(body, 'hex') FROM objects")" = '1|010203ff'
test "$(psql -d fixture_restored -Atc "SELECT tableowner FROM pg_tables WHERE tablename = 'objects'")" = 'fixture_owner'
printf 'Native PostgreSQL dump, roles, restored binary data, media and original key verified\n'
