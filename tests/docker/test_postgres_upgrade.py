"""Protect the PostgreSQL major upgrade and exercise it with disposable volumes."""
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest
import yaml

from tests.helpers import REPO_ROOT

PACKAGE = REPO_ROOT / "apps/compose/homelab"
OLD_IMAGE = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"


def test_postgres18_has_its_own_volume_and_mount_root():
    model = yaml.safe_load((PACKAGE / "compose.yml").read_text())
    database = model["services"]["authentik-db"]
    assert database["image"].startswith("postgres:18.")
    assert database["volumes"] == ["authentik_db:/var/lib/postgresql"]
    assert model["volumes"]["authentik_db"]["name"] == "platform_authentik_db_pg18"


def _model(major, volume):
    target = "/var/lib/postgresql/data" if major == 16 else "/var/lib/postgresql"
    return {"services": {"authentik-db": {
        "image": f"postgres:{major}.1-alpine@sha256:" + "a" * 64,
        "volumes": [{"type": "volume", "source": "database", "target": target}],
    }}, "volumes": {"database": {"name": volume}}}


@pytest.mark.parametrize("major,volume,message", (
    (18, "original", "separate empty volume"),
    (19, "new", "Only the tested"),
    (16, "original", None),
))
def test_major_upgrade_rejects_volume_reuse_and_unprepared_transitions(major, volume, message):
    from apps.compose.homelab import postgres_upgrade as upgrade
    if message:
        with pytest.raises(RuntimeError, match=message):
            upgrade.plan(_model(16, "original"), _model(major, volume))
    else:
        assert upgrade.plan(_model(16, "original"), _model(major, volume)) is None


def test_stale_target_is_rejected_before_writers_stop(tmp_path, monkeypatch):
    from apps.compose.homelab import postgres_upgrade as upgrade
    from types import SimpleNamespace
    monkeypatch.setattr(upgrade, "_compose", lambda *a, **k: b"source-id")
    monkeypatch.setattr(upgrade, "_run", lambda *a, **k:
                        json.dumps([{"Name": "original", "Destination": "/var/lib/postgresql/data"}]).encode())
    monkeypatch.setattr(upgrade, "_sql", lambda *a: b"160015")
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    backup = tmp_path / "private-backup"
    with pytest.raises(RuntimeError, match="already exists"):
        upgrade.upgrade(tmp_path / "old", tmp_path / "new", backup,
                        ("original", "/var/lib/postgresql/data", "new"))
    assert not backup.exists()


@pytest.mark.parametrize("failure", ("restore", "counts", "recovery", "writer"))
def test_failed_conversion_restores_original_writers_and_keeps_private_archive(tmp_path, monkeypatch, failure):
    from apps.compose.homelab import postgres_upgrade as upgrade
    old, new = tmp_path / "old", tmp_path / "new"
    calls = []
    monkeypatch.setattr(upgrade, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_sql", lambda *a: b"180006")
    monkeypatch.setattr(upgrade, "_counts", lambda root, project:
                        {"public.accounts": 2 if root == old else 1})

    def compose(root, project, *args, **kwargs):
        calls.append((root, args))
        if args[0] == "ps":
            return b'{"Service":"authentik-server","State":"running"}\n{"Service":"authentik-worker","State":"exited"}'
        if "pg_dump" in args:
            kwargs["stdout"].write(b"PGDMP-private-fixture")
        if "pg_restore" in args:
            assert "--single-transaction" in args
            if failure != "counts":
                raise RuntimeError("private-database-password")
        if failure == "recovery" and root == old and args[0] == "up":
            raise RuntimeError("private-database-password")
        if failure == "writer" and args[0] == "start":
            raise RuntimeError("private-database-password")
        return b""

    monkeypatch.setattr(upgrade, "_compose", compose)
    backup = tmp_path / "backup"
    recovery = tmp_path / "retained-pg16-configuration"
    with pytest.raises(RuntimeError) as error:
        upgrade.upgrade(old, new, backup, ("original", "/var/lib/postgresql/data", "new"),
                        recovery=recovery)
    assert "private-database-password" not in str(error.value)
    archive = backup / "authentik-pg16.dump"
    assert archive.read_bytes().startswith(b"PGDMP")
    if os.name != "nt":
        assert archive.stat().st_mode & 0o077 == 0
    assert (old, ("stop", "authentik-server")) in calls
    if failure in ("recovery", "writer"):
        assert "migration and recovery failed" in str(error.value)
        assert str(recovery) in str(error.value)
    else:
        assert "PostgreSQL 16 restored" in str(error.value)
        assert calls[-1] == (old, ("start", "--wait", "--wait-timeout", "300", "authentik-server"))
    assert not any(args[0] == "down" or "rm" in args for _, args in calls)


@pytest.fixture
def live_databases(tmp_path):
    if os.environ.get("HOMELAB_TEST_POSTGRES_UPGRADE") != "1":
        pytest.skip("set HOMELAB_TEST_POSTGRES_UPGRADE=1 for disposable Docker migration tests")
    from apps.compose.homelab import postgres_upgrade as upgrade

    project = "homelab-pg-upgrade-test-" + uuid.uuid4().hex[:12]
    volumes = (project + "-16", project + "-18")
    old, new = tmp_path / "old", tmp_path / "new"
    database = yaml.safe_load((PACKAGE / "compose.yml").read_text())["services"]["authentik-db"]
    image = database["image"]

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return result.stdout

    for root, version, database_image, volume in (
        (old, 16, OLD_IMAGE, volumes[0]), (new, 18, image, volumes[1]),
    ):
        root.mkdir()
        secret = root / "database.env"
        secret.write_text("POSTGRES_PASSWORD=disposable-fixture-password\n")
        secret.chmod(0o600)
        mount = "/var/lib/postgresql/data" if version == 16 else "/var/lib/postgresql"
        model = {"services": {
            "authentik-db": {
                "image": database_image,
                "env_file": [str(secret)],
                "environment": {"POSTGRES_USER": "authentik", "POSTGRES_DB": "authentik"},
                "volumes": [f"database:{mount}"],
                "healthcheck": {"test": database["healthcheck"]["test"],
                                "interval": "1s", "timeout": "5s", "retries": 30},
            },
            **{writer: {"image": OLD_IMAGE, "entrypoint": ["/bin/sh", "-c", "sleep 3600"]}
               for writer in upgrade.WRITERS},
        }, "volumes": {"database": {"name": volume}}}
        (root / "compose.yml").write_text(json.dumps(model))

    try:
        for root in (old, new):
            docker(*upgrade._prefix(root, project)[1:], "pull", "--quiet")
        upgrade._compose(old, project, "up", "-d", "--wait", "--wait-timeout", "120",
                         "authentik-db", "authentik-server")
        upgrade._sql(old, project, """
            CREATE EXTENSION pgcrypto;
            CREATE TABLE accounts (id serial PRIMARY KEY, name text NOT NULL, payload bytea);
            CREATE TABLE bindings (id serial PRIMARY KEY, account_id integer REFERENCES accounts(id));
            INSERT INTO accounts (name, payload) VALUES ('관리자', decode('00ff10', 'hex')), ('fixture', NULL);
            INSERT INTO bindings (account_id) VALUES (1), (2);
        """)
        old_model = json.loads(upgrade._compose(old, project, "config", "--format", "json"))
        new_model = json.loads(upgrade._compose(new, project, "config", "--format", "json"))
        yield upgrade, old, new, tmp_path / "backup", project, volumes, old_model, new_model, docker
    finally:
        # Only this fixture's randomly named project and volumes may be removed.
        assert all(volume.startswith(project + "-") for volume in volumes)
        subprocess.run(upgrade._prefix(new, project) + ["down"], capture_output=True)
        subprocess.run(upgrade._prefix(old, project) + ["down"], capture_output=True)
        for volume in volumes:
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


@pytest.mark.parametrize("corrupt_archive", (False, True))
def test_real_postgres16_to_18_preserves_data_or_recovers16(live_databases, monkeypatch, corrupt_archive):
    upgrade, old, new, backup, project, volumes, old_model, new_model, docker = live_databases
    transition = upgrade.plan(old_model, new_model)
    original_sql = upgrade._sql
    if corrupt_archive:
        def sql(root, selected_project, query):
            result = original_sql(root, selected_project, query)
            if root == new and query == "SHOW server_version_num":
                (backup / "authentik-pg16.dump").write_bytes(b"broken archive")
            return result
        monkeypatch.setattr(upgrade, "_sql", sql)
        with pytest.raises(RuntimeError, match="PostgreSQL 16 restored"):
            upgrade.upgrade(old, new, backup, transition, project=project)
        active, major = old, 16
        writers = upgrade._compose(old, project, "ps", "--format", "json", *upgrade.WRITERS)
        assert json.loads(writers)["Service"] == "authentik-server"
    else:
        upgrade.upgrade(old, new, backup, transition, project=project)
        active, major = new, 18
    assert int(original_sql(active, project, "SHOW server_version_num").strip()) // 10000 == major
    values = original_sql(active, project, "SELECT name, encode(payload, 'hex') FROM accounts ORDER BY id")
    assert values.decode().splitlines() == ["관리자|00ff10", "fixture|"]
    assert original_sql(active, project, "SELECT count(*) FROM bindings").strip() == b"2"
    with pytest.raises(RuntimeError):
        original_sql(active, project, "INSERT INTO bindings(account_id) VALUES (999999)")
    assert original_sql(active, project, "INSERT INTO accounts(name) VALUES ('next') RETURNING id").strip() == b"3"
    assert original_sql(active, project, "SELECT length(digest('fixture', 'sha256'))").strip() == b"32"
    old_version = docker("run", "--rm", "--mount",
                         f"type=volume,source={volumes[0]},target=/source,readonly",
                         "--entrypoint", "cat", OLD_IMAGE, "/source/PG_VERSION")
    assert old_version.strip() == b"16"


def test_real_stale_destination_leaves_source_and_writers_running(live_databases):
    upgrade, old, new, backup, project, volumes, old_model, new_model, docker = live_databases
    docker("volume", "create", volumes[1])
    with pytest.raises(RuntimeError, match="already exists"):
        upgrade.upgrade(old, new, backup, upgrade.plan(old_model, new_model), project=project)
    assert not backup.exists()
    assert upgrade._sql(old, project, "SELECT count(*) FROM accounts").strip() == b"2"
    rows = upgrade._compose(old, project, "ps", "--format", "json", *upgrade.WRITERS)
    assert json.loads(rows)["Service"] == "authentik-server"


def test_database_readiness_waits_for_the_final_tcp_server():
    model = yaml.safe_load((PACKAGE / "compose.yml").read_text())
    command = model["services"]["authentik-db"]["healthcheck"]["test"]
    assert command[command.index("-h") + 1] == "127.0.0.1"
