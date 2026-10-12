"""Exercise data preservation and routine deployment without Docker or production."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.docker.test_prepare_release import stage_and_bundle

PACKAGE = Path(__file__).parents[2] / 'apps/compose/homelab'
sys.path.insert(0, str(PACKAGE))
spec = importlib.util.spec_from_file_location('homelab_deploy', PACKAGE / 'deploy.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source, bundle = stage_and_bundle(tmp_path / 'source')
    root = tmp_path / 'install/compose'
    installed = tmp_path / 'private/apps.json'
    calls = []

    def compose(directory, *args, capture=False):
        calls.append((directory, args))
        if args[0] == 'config':
            return json.dumps({'services': {'adguard': {'image': 'unchanged'},
                                          'authentik-worker': {'image': 'unchanged'}}})
        return ''

    monkeypatch.setattr(deployment, 'compose', compose)
    monkeypatch.setattr(deployment.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(deployment, 'data_backup', lambda *a, **k: calls.append(('backup', a)))
    return source, bundle, root, installed, calls


def apply(env):
    source, bundle, root, installed, _ = env
    deployment.deploy(source, bundle, 'test-revision', root=root, installed_bundle=installed)


def test_repeat_deploy_preserves_mounted_inodes_and_adguard_runtime(environment):
    apply(environment)
    _, _, root, installed, calls = environment
    directory_inode = (root / 'generated/copyparty').stat().st_ino
    config_inode = (root / 'generated/copyparty/copyparty.conf').stat().st_ino
    adguard = root / 'generated/adguard/AdGuardHome.yaml'
    adguard.write_text(adguard.read_text() + '\n# normalized by AdGuard\n')
    calls.clear()
    apply(environment)
    assert (root / 'generated/copyparty').stat().st_ino == directory_inode
    assert (root / 'generated/copyparty/copyparty.conf').stat().st_ino == config_inode
    assert 'normalized by AdGuard' in adguard.read_text()
    assert not any('--force-recreate' in args or args[0] == 'stop' for _, args in calls)
    assert installed.stat().st_mode & 0o077 == 0


def test_changed_copyparty_config_recreates_only_copyparty(environment):
    apply(environment)
    source, _, root, _, calls = environment
    template = source / 'config/copyparty.conf.tmpl'
    template.write_text(template.read_text() + '\n# declarative update\n')
    calls.clear()
    apply(environment)
    forced = [args for _, args in calls if '--force-recreate' in args]
    assert len(forced) == 1 and forced[0][-1] == 'copyparty'
    assert not any(args[0] == 'stop' for _, args in calls)
    assert (root.parent / 'compose-previous/generated/copyparty/copyparty.conf').exists()


def test_migration_backs_up_before_activation_and_restores_legacy_on_failure(environment, monkeypatch):
    source, bundle, root, installed, calls = environment
    legacy = root.parent / 'compose-runtime/a/stack'
    deployment.sync(source, legacy)
    deployment.prepare(bundle, legacy, legacy / 'topology.json')
    state = root.parent / 'compose-control/release-state.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'active_slot': 'a', 'pending': None}))
    original = deployment.compose

    def compose(directory, *args, **kwargs):
        if directory == root and args[0] == 'up':
            calls.append((directory, args))
            raise RuntimeError('activation failed')
        return original(directory, *args, **kwargs)

    monkeypatch.setattr(deployment, 'compose', compose)
    with pytest.raises(RuntimeError, match='activation failed'):
        apply(environment)
    backup_index = next(i for i, (directory, _) in enumerate(calls) if directory == 'backup')
    activation_index = next(i for i, (directory, args) in enumerate(calls) if directory == root and args[0] == 'up')
    assert backup_index < activation_index
    assert any(directory == legacy and args[0] == 'up' for directory, args in calls)
    assert not (root / '.revision').exists()
    assert (legacy / 'compose.yml').exists()


def test_invalid_credentials_leave_installed_configuration_untouched(environment):
    apply(environment)
    _, bundle, root, installed, calls = environment
    before = installed.read_bytes(), (root / 'compose.yml').read_bytes()
    bundle.write_text('{}')
    calls.clear()
    with pytest.raises(RuntimeError):
        apply(environment)
    assert (installed.read_bytes(), (root / 'compose.yml').read_bytes()) == before
    assert calls == []


def test_identity_credential_rotation_explicitly_refreshes_providers(environment):
    apply(environment)
    _, bundle, _, _, calls = environment
    payload = json.loads(bundle.read_text())
    payload['headscale']['oidc_client_secret'] = 'rotated-private-client-secret'
    bundle.write_text(json.dumps(payload))
    calls.clear()
    apply(environment)
    assert any(args[:4] == ('exec', '-T', 'authentik-worker', 'ak')
               and 'apply_blueprint' in args for _, args in calls)


def test_startup_failure_identifies_container_without_echoing_credentials(tmp_path, monkeypatch, capsys):
    secrets = tmp_path / '.secrets'
    secrets.mkdir()
    (secrets / 'authentik.env').write_text('PASSWORD=private-database-password\n')
    original = deployment.compose
    # Use the real wrapper, bypassing only Docker itself.
    monkeypatch.setattr(deployment.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=1, stdout='', stderr='container homelab-copyparty-1 unhealthy; private-database-password'))
    with pytest.raises(RuntimeError, match='up.*exit 1'):
        original(tmp_path, 'up', '-d')
    output = capsys.readouterr().out
    assert 'homelab-copyparty-1 unhealthy' in output
    assert 'private-database-password' not in output


def test_public_blueprint_is_readable_by_authentiks_non_root_user(environment):
    apply(environment)
    _, _, root, _, _ = environment
    blueprint = root / 'generated/authentik/authentik-blueprint.yaml'
    assert blueprint.parent.stat().st_mode & 0o005 == 0o005
    assert blueprint.stat().st_mode & 0o004
    # The readable blueprint references env vars; private values remain restricted.
    assert not (root / '.secrets/authentik.env').stat().st_mode & 0o077
    assert '!Env' in blueprint.read_text()


def _postgres_models(environment, monkeypatch, old_root=None):
    from tests.docker.test_postgres_upgrade import _model
    _, _, root, _, _ = environment
    original = deployment.compose
    old_root = old_root or root

    def compose(directory, *args, **kwargs):
        value = original(directory, *args, **kwargs)
        if args[0] == "config":
            model = _model(16 if directory == old_root else 18,
                           "original" if directory == old_root else "converted")
            model["services"].update(json.loads(value)["services"])
            return json.dumps(model)
        return value

    monkeypatch.setattr(deployment, "compose", compose)


def test_postgres_preflight_failure_preserves_installed_configuration(environment, monkeypatch):
    apply(environment)
    _, _, root, installed, calls = environment
    before = (root / "compose.yml").read_bytes(), installed.read_bytes()
    _postgres_models(environment, monkeypatch)
    monkeypatch.setattr(deployment.postgres_upgrade, "preflight",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("destination already exists")))
    calls.clear()
    with pytest.raises(RuntimeError, match="destination already exists"):
        apply(environment)
    assert ((root / "compose.yml").read_bytes(), installed.read_bytes()) == before
    assert not any(args[0] in ("stop", "up", "pull") for _, args in calls)


def test_postgres_conversion_finishes_before_configuration_is_published(environment, monkeypatch):
    apply(environment)
    _, _, root, _, calls = environment
    before = (root / "compose.yml").read_bytes()
    _postgres_models(environment, monkeypatch)
    monkeypatch.setattr(deployment.postgres_upgrade, "preflight", lambda *a, **k: None)
    phases = []

    def convert(old, staged, backup, transition, **kwargs):
        assert old == root
        assert (root / "compose.yml").read_bytes() == before
        assert (kwargs["recovery"] / "compose.yml").read_bytes() == before
        phases.append("convert")

    original_sync = deployment.sync

    def publish(source, destination, **kwargs):
        assert phases == ["convert"]
        phases.append("publish")
        return original_sync(source, destination, **kwargs)

    monkeypatch.setattr(deployment.postgres_upgrade, "upgrade", convert)
    monkeypatch.setattr(deployment, "sync", publish)
    calls.clear()
    apply(environment)
    assert phases == ["convert", "publish"]
    assert any(args[0] == "up" for _, args in calls)


def test_legacy_conversion_failure_does_not_restart_all_legacy_services(environment, monkeypatch):
    source, bundle, root, _, calls = environment
    legacy = root.parent / "compose-runtime/a/stack"
    deployment.sync(source, legacy)
    deployment.prepare(bundle, legacy, legacy / "topology.json")
    state = root.parent / "compose-control/release-state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"active_slot": "a", "pending": None}))
    _postgres_models(environment, monkeypatch, old_root=legacy)
    monkeypatch.setattr(deployment.postgres_upgrade, "preflight", lambda *a, **k: None)

    def failed_conversion(*args, **kwargs):
        raise RuntimeError("PostgreSQL migration failed; PostgreSQL 16 restored")

    monkeypatch.setattr(deployment.postgres_upgrade, "upgrade", failed_conversion)
    with pytest.raises(RuntimeError, match="PostgreSQL 16 restored"):
        apply(environment)
    assert not any(directory == legacy and args[0] == "up" for directory, args in calls)
    assert not (root / ".revision").exists()
