"""Exercise data preservation and routine deployment without Docker or production."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

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
            package = yaml.safe_load((directory / 'compose.yml').read_text())
            database = dict(package['services']['authentik-db'])
            database['volumes'] = [
                {'type': 'volume', 'source': name, 'target': target}
                for name, target in (mount.split(':', 1) for mount in database['volumes'])
            ]
            return json.dumps({'services': {'adguard': {'image': 'unchanged'},
                                          'authentik-worker': {'image': 'unchanged'},
                                          'authentik-db': database},
                               'volumes': package['volumes']})
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


def test_retired_slot_installation_is_rejected_before_mutation(environment):
    source, bundle, root, _, calls = environment
    legacy = root.parent / 'compose-runtime/a/stack'
    deployment.sync(source, legacy)
    deployment.prepare(bundle, legacy, legacy / 'topology.json')
    state = root.parent / 'compose-control/release-state.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'active_slot': 'a', 'pending': None}))
    calls.clear()
    with pytest.raises(RuntimeError, match='Legacy'):
        apply(environment)
    assert calls == []
    assert not (root / '.revision').exists()


def test_database_volume_changes_are_rejected_before_activation(environment):
    apply(environment)
    source, _, root, installed, calls = environment
    before = (root / 'compose.yml').read_bytes(), installed.read_bytes()
    compose_file = source / 'compose.yml'
    compose_file.write_text(compose_file.read_text().replace('platform_authentik_db_pg18', 'unmigrated_database'))
    calls.clear()
    with pytest.raises(RuntimeError, match='PostgreSQL 18'):
        apply(environment)
    assert ((root / 'compose.yml').read_bytes(), installed.read_bytes()) == before
    assert not any(args[0] in ('stop', 'up', 'pull') for _, args in calls)


def test_retired_source_package_is_rejected_without_republishing_it(environment):
    apply(environment)
    source, _, root, _, calls = environment
    before = (root / 'deploy.py').read_bytes()
    (source / 'postgres_upgrade.py').write_text('# retired conversion helper')
    calls.clear()
    with pytest.raises(RuntimeError, match='Retired migration packages'):
        apply(environment)
    assert (root / 'deploy.py').read_bytes() == before
    assert calls == []


def test_deploy_removes_only_retired_code_and_keeps_private_backups(environment):
    apply(environment)
    _, _, root, _, calls = environment
    retired = root / 'postgres_upgrade.py'
    retired.write_text('# obsolete helper')
    bytecode = root / '__pycache__/postgres_upgrade.cpython-314.pyc'
    bytecode.parent.mkdir()
    bytecode.write_bytes(b'obsolete bytecode')
    retained = root.parent / 'backups/authentik.sql'
    retained.parent.mkdir()
    retained.write_text('private backup fixture')
    other = bytecode.parent / 'prepare_release.cpython-314.pyc'
    other.write_bytes(b'current preparation bytecode')
    calls.clear()
    apply(environment)
    assert not retired.exists()
    assert not bytecode.exists()
    assert other.exists()
    assert retained.read_text() == 'private backup fixture'
    assert not any('--force-recreate' in args for _, args in calls)
