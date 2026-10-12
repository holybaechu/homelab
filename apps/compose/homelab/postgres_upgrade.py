"""Convert the Authentik PostgreSQL 16 cluster into a separate PostgreSQL 18 volume."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

WRITERS = ("authentik-server", "authentik-worker")
COUNTS_SQL = r"""
SELECT format('SELECT json_build_array(%L, count(*)) FROM %I.%I',
              schemaname || '.' || tablename, schemaname, tablename)
FROM pg_catalog.pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
  AND schemaname NOT LIKE 'pg_toast%'
ORDER BY schemaname, tablename
\gexec
"""


def _major(image):
    match = re.match(r"^postgres:(\d+)\.", image)
    if match is None:
        raise RuntimeError("Authentik database requires a pinned official PostgreSQL image")
    return int(match[1])


def _volume(model, major):
    target = "/var/lib/postgresql/data" if major == 16 else "/var/lib/postgresql"
    mounts = [mount for mount in model["services"]["authentik-db"]["volumes"]
              if mount["type"] == "volume" and mount["target"] == target]
    if len(mounts) != 1:
        raise RuntimeError("PostgreSQL major upgrade requires its matching data mount")
    return model["volumes"][mounts[0]["source"]]["name"], target


def plan(old, new):
    """Reject unprepared major transitions before installed configuration changes."""
    if "authentik-db" not in old.get("services", {}):
        return None
    old_major = _major(old["services"]["authentik-db"]["image"])
    new_major = _major(new["services"]["authentik-db"]["image"])
    if old_major == new_major:
        return None
    if (old_major, new_major) != (16, 18):
        raise RuntimeError("Only the tested PostgreSQL 16-to-18 migration is supported")
    source, source_mount = _volume(old, 16)
    destination, _ = _volume(new, 18)
    if source == destination:
        raise RuntimeError("PostgreSQL 18 must use a separate empty volume")
    return source, source_mount, destination


def _run(argv, label, *, stdin=None, stdout=subprocess.PIPE, input=None):
    result = subprocess.run(argv, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, input=input)
    if result.returncode:
        # Database diagnostics and dumps can contain identity data or credentials.
        raise RuntimeError("PostgreSQL migration failed during " + label)
    return result.stdout or b""


def _prefix(root, project):
    return ["docker", "compose", "--project-name", project, "--project-directory", str(root),
            "-f", str(root / "compose.yml")]


def _compose(root, project, *args, **kwargs):
    return _run(_prefix(root, project) + list(args), args[0], **kwargs)


def _sql(root, project, sql):
    return _compose(root, project, "exec", "-T", "authentik-db", "psql",
                    "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "authentik",
                    "-d", "authentik", "-c", sql)


def preflight(old, transition, *, project="homelab"):
    """Check live source identity and refuse a stale destination before stopping writers."""
    source, source_mount, destination = transition
    identifier = _compose(old, project, "ps", "-q", "authentik-db").decode().strip()
    if not identifier:
        raise RuntimeError("PostgreSQL migration requires a running source database")
    mounts = json.loads(_run(["docker", "inspect", identifier, "--format", "{{json .Mounts}}"],
                             "source mount verification"))
    if not any(mount.get("Name") == source and mount["Destination"] == source_mount for mount in mounts):
        raise RuntimeError("Running PostgreSQL source volume does not match installed Compose")
    if int(_sql(old, project, "SHOW server_version_num").strip()) // 10000 != 16:
        raise RuntimeError("Running PostgreSQL source must be version 16")
    exists = subprocess.run(["docker", "volume", "inspect", destination],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if exists.returncode == 0:
        raise RuntimeError("PostgreSQL 18 destination volume already exists; follow migration recovery")


def _counts(root, project):
    result = _compose(root, project, "exec", "-T", "authentik-db", "psql",
                      "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "authentik", "-d", "authentik",
                      input=COUNTS_SQL.encode())
    return dict(json.loads(line) for line in result.splitlines() if line.strip())


def upgrade(old: Path, staged: Path, backup: Path, transition, *, project="homelab", recovery=None):
    """Freeze writers, restore atomically, verify, and recover 16 on pre-activation failure."""
    preflight(old, transition, project=project)
    rows = _compose(old, project, "ps", "--all", "--format", "json", *WRITERS)
    writers = [row["Service"] for line in rows.splitlines() if line.strip()
               for row in [json.loads(line)] if row["State"] == "running"]
    backup.mkdir(parents=True, mode=0o700)
    dump = backup / "authentik-pg16.dump"
    try:
        if writers:
            _compose(old, project, "stop", *writers)
        expected = _counts(old, project)
        with dump.open("xb") as output:
            dump.chmod(0o600)
            _compose(old, project, "exec", "-T", "authentik-db", "pg_dump",
                     "--format=custom", "--no-owner", "--no-acl", "-U", "authentik", "authentik",
                     stdout=output)
        if dump.stat().st_size == 0:
            raise RuntimeError("PostgreSQL migration produced an empty archive")
        _compose(staged, project, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                 "--wait", "--wait-timeout", "300", "authentik-db")
        if int(_sql(staged, project, "SHOW server_version_num").strip()) // 10000 != 18:
            raise RuntimeError("PostgreSQL migration target must be version 18")
        with dump.open("rb") as archive:
            _compose(staged, project, "exec", "-T", "authentik-db", "pg_restore",
                     "--single-transaction", "--no-owner", "--no-acl", "-U", "authentik",
                     "-d", "authentik", stdin=archive)
        if _counts(staged, project) != expected:
            raise RuntimeError("PostgreSQL migration table counts differ")
        _sql(staged, project, "ANALYZE")
    except Exception:
        try:
            _compose(old, project, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                     "--force-recreate", "--wait", "--wait-timeout", "300", "authentik-db")
            if writers:
                _compose(old, project, "start", "--wait", "--wait-timeout", "300", *writers)
        except Exception:
            raise RuntimeError("PostgreSQL migration and recovery failed; recover with "
                               + str(recovery or old)) from None
        raise RuntimeError("PostgreSQL migration failed; PostgreSQL 16 restored. Private backup: "
                           + str(backup)) from None
