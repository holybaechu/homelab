"""Synthetic native-client dump/restore test, isolated Docker network only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

major, host = sys.argv[1:]
env = dict(os.environ, PGHOST=host, PGUSER='postgres', PGDATABASE='postgres')
bin = Path('/usr/lib/postgresql') / major / 'bin'


def run(program, *args, **kwargs):
    return subprocess.run([str(bin / program), *args], env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout


for attempt in range(60):
    try:
        run('pg_isready')
        break
    except subprocess.CalledProcessError:
        time.sleep(1)
else:
    raise RuntimeError('Isolated PostgreSQL fixture did not start')
run('psql', '-v', 'ON_ERROR_STOP=1', '-c', 'CREATE ROLE fixture_owner')
run('createdb', '-O', 'fixture_owner', 'fixture_source')
run('psql', '-d', 'fixture_source', '-v', 'ON_ERROR_STOP=1', '-c',
    "CREATE TABLE objects (id int, body bytea); INSERT INTO objects VALUES (1, decode('010203ff', 'hex')); ALTER TABLE objects OWNER TO fixture_owner")
with tempfile.TemporaryDirectory() as directory:
    dump = Path(directory) / 'database.dump'
    roles = run('pg_dumpall', '--roles-only')
    assert b'CREATE ROLE fixture_owner;' in roles
    dump.write_bytes(run('pg_dump', '-d', 'fixture_source', '--format=custom'))
    run('dropdb', 'fixture_source')
    run('psql', '-v', 'ON_ERROR_STOP=1', '-c', 'DROP ROLE fixture_owner')
    # The isolated target already has the standard bootstrap superuser.
    roles = roles.replace(b'CREATE ROLE postgres;\n', b'')
    run('psql', '-v', 'ON_ERROR_STOP=1', input=roles)
    run('createdb', '-O', 'fixture_owner', 'fixture_restored')
    run('pg_restore', '--exit-on-error', '-d', 'fixture_restored', str(dump))
    result = run('psql', '-d', 'fixture_restored', '-Atc', "SELECT id, encode(body, 'hex') FROM objects")
    assert result.strip() == b'1|010203ff'
    owner = run('psql', '-d', 'fixture_restored', '-Atc', "SELECT tableowner FROM pg_tables WHERE tablename = 'objects'")
    assert owner.strip() == b'fixture_owner'
print(f'PostgreSQL {major}: native dump, roles, isolated restore and binary payload verified')
