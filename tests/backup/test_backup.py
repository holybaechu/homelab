from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'kubernetes/backup' / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller, worker = load('controller'), load('worker')


class Journal:
    def __init__(self, state=None):
        self.state = state or {'phase': 'idle', 'replicas': []}

    def read(self):
        return json.loads(json.dumps(self.state))

    def write(self, state):
        self.state = json.loads(json.dumps(state))


class Recovery(unittest.TestCase):
    def test_failure_restores_exact_counts_and_controls(self):
        journal = Journal(dict(run='run', phase='preparing', replicas=[]))
        scales = []
        controls = [['flux-system', 'kustomization', 'apps', False]]
        def kube(*args, **kwargs):
            if 'deployment' in args:
                return {'spec': {'replicas': 3}}
            return {}
        with patch.object(controller, 'kube', side_effect=kube), \
             patch.object(controller, 'controls_for', return_value=controls), \
             patch.object(controller, 'suspend') as suspend, \
             patch.object(controller, 'scale', side_effect=lambda *args: scales.append(args)), \
             patch.object(controller, 'wait_stopped'), \
             patch.object(controller, 'worker', side_effect=RuntimeError('failed producer')):
            with self.assertRaises(RuntimeError):
                controller.capture(journal, 'run', 'opencloud', [('homelab', 'opencloud')])
        self.assertEqual(scales, [('homelab', 'opencloud', 0), ('homelab', 'opencloud', 3)])
        self.assertEqual(suspend.call_args_list[-1].args, (controls[0], False))
        self.assertEqual(journal.state['phase'], 'aborted')
        self.assertEqual(journal.state['replicas'], [])

    def test_expired_capture_cannot_commit(self):
        journal = Journal(dict(run='r', phase='capture', deadline=0, replicas=[]))
        with self.assertRaises(RuntimeError):
            controller.assert_capture(journal, 'r')
        journal.state.update(phase='recovering', deadline=time.time() + 10)
        with self.assertRaises(RuntimeError):
            controller.assert_capture(journal, 'r')

    def test_recovery_retries_failures_and_preserves_original_zero(self):
        journal = Journal(dict(run='r', phase='capture', deadline=0,
                               replicas=[['homelab', 'n8n', 0], ['homelab', 'zipline', 2]]))
        with patch.object(controller, 'scale', side_effect=[RuntimeError('API'), None]):
            with self.assertRaises(RuntimeError):
                controller.restore(journal, journal.read())
        self.assertEqual(journal.state['phase'], 'recovering')
        self.assertEqual(len(journal.state['replicas']), 2)
        with patch.object(controller, 'scale') as scale:
            controller.restore(journal, journal.read())
        self.assertEqual(scale.call_args_list[0].args, ('homelab', 'n8n', 0))
        self.assertEqual(journal.state['replicas'], [])

    def test_success_restores_before_next_phase(self):
        journal = Journal(dict(run='run', phase='preparing', replicas=[]))
        order = []
        with patch.object(controller, 'kube', return_value={'spec': {'replicas': 1}, 'metadata': {'generation': 1}, 'status': {'observedGeneration': 1, 'readyReplicas': 1}}), \
             patch.object(controller, 'controls_for', return_value=[]), \
             patch.object(controller, 'scale', side_effect=lambda ns, name, n: order.append(n)), \
             patch.object(controller, 'wait_stopped'), \
             patch.object(controller, 'worker', side_effect=lambda *a, **k: order.append('capture')):
            controller.capture(journal, 'run', 'n8n', [('homelab', 'n8n')])
        self.assertEqual(order, [0, 'capture', 1])
        self.assertEqual(journal.state['phase'], 'preparing')

    def test_resumption_excludes_competitors_and_preserves_watchdog_intent(self):
        for takeover in (False, True):
            with self.subTest(watchdog=takeover):
                journal = Journal(dict(run='run', phase='preparing', replicas=[]))
                def kube(*args, **kwargs):
                    if 'backup-watchdog' in args:
                        return {'status': {'readyReplicas': 1}}
                    if journal.state['phase'] in ('aborted', 'resuming'):
                        with patch.object(controller, 'Journal', return_value=journal):
                            with self.assertRaises(RuntimeError):
                                controller.maintenance()
                            with self.assertRaises(RuntimeError):
                                controller.backup()
                        if takeover:
                            journal.state['phase'] = 'recovering'
                    return {'spec': {'replicas': 1}, 'metadata': {'generation': 1},
                            'status': {'readyReplicas': 1, 'observedGeneration': 1}}
                with patch.object(controller, 'kube', side_effect=kube), \
                     patch.object(controller, 'controls_for', return_value=[]), \
                     patch.object(controller, 'scale'), patch.object(controller, 'wait_stopped'), \
                     patch.object(controller, 'worker'):
                    if takeover:
                        with self.assertRaises(RuntimeError):
                            controller.capture(journal, 'run', 'n8n', [('homelab', 'n8n')])
                        self.assertEqual(journal.state['phase'], 'recovering')
                        self.assertTrue(journal.state['replicas'])
                    else:
                        controller.capture(journal, 'run', 'n8n', [('homelab', 'n8n')])
                        self.assertEqual(journal.state['phase'], 'preparing')

    def test_backup_failure_leaves_watchdog_recovery_intent(self):
        journal = Journal()
        recovery = {}
        def interrupted(*args, **kwargs):
            journal.state.update(phase='recovering', deadline=0,
                                 replicas=[['homelab', 'n8n', 2]], controls=[])
            recovery.update(journal.read())
            raise RuntimeError('Watchdog took over')
        with patch.object(controller, 'Journal', return_value=journal), \
             patch.object(controller, 'kube', return_value={'status': {'readyReplicas': 1}}), \
             patch.object(controller, 'worker', side_effect=interrupted), \
             patch.object(controller, 'scale') as scale:
            with self.assertRaises(RuntimeError):
                controller.backup()
        self.assertEqual(journal.read(), recovery)
        scale.assert_not_called()

    def test_stale_owner_cannot_start_next_group(self):
        journal = Journal(dict(run='other', phase='preparing', replicas=[]))
        with patch.object(controller, 'kube', return_value={'spec': {'replicas': 1}}), \
             patch.object(controller, 'controls_for', return_value=[]), \
             patch.object(controller, 'scale') as scale:
            with self.assertRaises(RuntimeError):
                controller.capture(journal, 'old', 'n8n', [('homelab', 'n8n')])
        scale.assert_not_called()
        self.assertEqual(journal.state['run'], 'other')


class Capture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_sqlite_online_backup_includes_wal_without_mutating_source(self):
        source, target = self.root / 'state.db', self.root / 'copy.db'
        with closing(sqlite3.connect(source)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE sessions (value TEXT)')
            db.execute("INSERT INTO sessions VALUES ('committed')")
            db.commit()
            db.execute("INSERT INTO sessions VALUES ('inflight')")
            original = source.read_bytes()
            worker.sqlite_backup(source, target, time.time() + 10)
            self.assertEqual(source.read_bytes(), original)
            with closing(sqlite3.connect(target)) as restored:
                self.assertEqual(restored.execute('SELECT value FROM sessions').fetchall(), [('committed',)])

    @unittest.skipUnless(shutil.which('rsync'), 'rsync required')
    def test_hermes_final_inventory_captures_new_wal_database(self):
        source = self.root / 'source/hermes-data'
        source.mkdir(parents=True)
        (source / 'notes-wal').write_text('legitimate non-SQLite data')
        connections = []
        self.addCleanup(lambda: [db.close() for db in connections])
        sync = worker.sync
        calls = 0
        def introduce(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                db = sqlite3.connect(source / 'new.sqlite')
                connections.append(db)
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE sessions (value TEXT)')
                db.execute("INSERT INTO sessions VALUES ('new committed WAL')")
                db.commit()
            return sync(*args, **kwargs)
        (self.root / 'capture').mkdir()
        with patch.object(worker, 'SOURCES', source.parent), patch.object(worker, 'STAGE', self.root), \
             patch.object(worker, 'sync', side_effect=introduce):
            worker.copy_data('hermes-data', deadline=time.time() + 10)
        target = self.root / 'capture/hermes-data'
        self.assertFalse((target / 'new.sqlite-wal').exists())
        self.assertFalse((target / 'new.sqlite-shm').exists())
        with closing(sqlite3.connect(target / 'new.sqlite')) as db:
            self.assertEqual(db.execute('SELECT value FROM sessions').fetchall(), [('new committed WAL',)])
        self.assertEqual((target / 'notes-wal').read_text(), 'legitimate non-SQLite data')

    @unittest.skipUnless(shutil.which('rsync'), 'rsync required')
    def test_hermes_rejects_disappeared_or_retyped_final_database(self):
        for mutation in ('delete', 'replace'):
            with self.subTest(mutation=mutation):
                source = self.root / mutation / 'hermes-data'
                source.mkdir(parents=True)
                with closing(sqlite3.connect(source / 'state')) as db:
                    db.execute('CREATE TABLE sessions (value TEXT)')
                    db.commit()
                stage = self.root / ('stage-' + mutation)
                (stage / 'capture').mkdir(parents=True)
                sync = worker.sync
                calls = 0
                def mutate(*args, **kwargs):
                    nonlocal calls
                    result = sync(*args, **kwargs)
                    calls += 1
                    if calls == 2:
                        (source / 'state').unlink()
                        if mutation == 'replace':
                            (source / 'state').write_text('not a database')
                    return result
                with patch.object(worker, 'SOURCES', source.parent), patch.object(worker, 'STAGE', stage), \
                     patch.object(worker, 'sync', side_effect=mutate):
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        worker.copy_data('hermes-data', deadline=time.time() + 10)
                self.assertFalse((stage / 'valid').exists())

    def test_sqlite_deadline_does_not_replace_valid_copy(self):
        source, target = self.root / 'state.db', self.root / 'copy.db'
        with closing(sqlite3.connect(source)) as db:
            db.execute('CREATE TABLE sessions (value TEXT)')
        target.write_bytes(b'previous-valid')
        with self.assertRaises(TimeoutError):
            worker.sqlite_backup(source, target, 0)
        self.assertEqual(target.read_bytes(), b'previous-valid')

    def test_capture_seal_rejects_missing_group(self):
        run = '20261108T000000Z-123456ab'
        (self.root / 'run').write_text(run)
        with patch.object(worker, 'STAGE', self.root), patch.dict(os.environ, NAMESPACE='identity'):
            with self.assertRaises(FileNotFoundError):
                worker.seal(run)
        self.assertFalse((self.root / 'valid').exists())

    def test_gitops_omits_only_reconstructable_files(self):
        source = self.root / 'source'
        source.mkdir()
        repo = source / 'workspace'
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=fixture',
                                            '-c', 'user.email=fixture@example.invalid',
                                            '-c', 'commit.gpgsign=false', *args], stderr=subprocess.PIPE)
        git('init')
        git('remote', 'add', 'origin', 'https://github.com/holybaechu/homelab.git')
        (repo / 'clean').write_text('authoritative')
        (repo / 'a*').write_text('authoritative literal wildcard')
        (repo / 'changed').write_text('original')
        git('add', '.')
        git('commit', '-m', 'fixture')
        git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        (repo / 'changed').write_text('uncommitted recovery')
        (repo / 'untracked').write_text('private workspace')
        (repo / 'abc').write_text('must not be excluded as a wildcard match')
        selected = self.root / 'capture/hermes-data'
        shutil.copytree(source, selected)
        with patch.object(worker, 'STAGE', self.root):
            excludes = worker.gitops_excludes(source, selected)
        self.assertIn('/workspace/clean', excludes)
        self.assertNotIn('/workspace/changed', excludes)
        self.assertNotIn('/workspace/untracked', excludes)
        self.assertNotIn('/workspace/.git', excludes)
        if shutil.which('rsync'):
            filtered = self.root / 'filtered'
            worker.sync(source, filtered, excludes)
            self.assertTrue((filtered / 'workspace/abc').exists())
            self.assertFalse((filtered / 'workspace/a*').exists())
        git('add', '.')
        git('commit', '-m', 'unpublished fixture')
        with patch.object(worker, 'STAGE', self.root):
            self.assertEqual(worker.gitops_excludes(source, selected), [])

    @unittest.skipUnless(shutil.which('rsync'), 'rsync required')
    def test_git_deletion_tombstones_restore_staged_and_unstaged_deletions(self):
        source = self.root / 'source/hermes-data'
        repo = source / 'workspace'
        repo.mkdir(parents=True)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=fixture',
                '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', *args],
                stderr=subprocess.PIPE)
        git('init')
        git('remote', 'add', 'origin', 'https://github.com/holybaechu/homelab.git')
        for name in ('staged', 'unstaged', 'keep'):
            (repo / name).write_text(name)
        git('add', '.')
        git('commit', '-m', 'fixture')
        git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        git('rm', 'staged')
        (repo / 'unstaged').unlink()
        (self.root / 'capture').mkdir()
        with patch.object(worker, 'STAGE', self.root), patch.object(worker, 'SOURCES', source.parent):
            worker.copy_data('hermes-data', deadline=time.time() + 10)
        manifest = json.loads((self.root / 'capture/hermes-repositories.json').read_text())
        self.assertEqual(set(manifest[0]['deleted']), {'staged', 'unstaged'})
        restored = self.root / 'restore/drill'
        restored.mkdir(parents=True)
        checkout = restored / 'hermes-data/workspace'
        subprocess.check_call(['git', 'clone', '-q', str(repo), str(checkout)])
        subprocess.check_call(['git', '-C', str(checkout), 'checkout', '-q', manifest[0]['commit']])
        worker.command(['rsync', '-a', str(self.root / 'capture') + '/', str(restored) + '/'])
        self.assertTrue((checkout / 'staged').exists())
        self.assertTrue((checkout / 'unstaged').exists())
        with patch.object(worker, 'STAGE', self.root):
            worker.restore_git_deletions(restored)
        self.assertFalse((checkout / 'staged').exists())
        self.assertFalse((checkout / 'unstaged').exists())
        self.assertEqual((checkout / 'keep').read_text(), 'keep')
        status = subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain']).decode()
        self.assertIn('D  staged', status)
        self.assertIn(' D unstaged', status)
        protected = self.root / 'protected'
        protected.write_text('untouched')
        for malicious in ('../protected', '/absolute', '.git/config', 'link/protected', 'untracked'):
            with self.subTest(malicious=malicious):
                (checkout / 'link').symlink_to(self.root, target_is_directory=True)
                manifest[0]['deleted'] = [malicious]
                (restored / 'hermes-repositories.json').write_text(json.dumps(manifest))
                with patch.object(worker, 'STAGE', self.root):
                    with self.assertRaises(ValueError):
                        worker.restore_git_deletions(restored)
                (checkout / 'link').unlink()
                self.assertEqual(protected.read_text(), 'untouched')
        with patch.object(worker, 'STAGE', self.root):
            with self.assertRaises(ValueError):
                worker.restore_git_deletions(source)

    def test_incomplete_namespace_transfer_cannot_upload(self):
        run = '20261108T000000Z-123456ab'
        (self.root / 'run').write_text(run)
        (self.root / 'valid').write_text(run)
        (self.root / 'capture/identity').mkdir(parents=True)
        (self.root / 'capture/identity/capture.json').write_text(json.dumps({'run': 'stale'}))
        with patch.object(worker, 'STAGE', self.root), patch.object(worker, 'restic') as restic:
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                worker.upload(run, 'b2')
        restic.assert_not_called()

    def test_scaleway_endpoint_validation_precedes_credentials(self):
        root = self.root / 'scaleway'
        root.mkdir()
        (root / 'bucket').write_text('fixture-backup')
        for url, region in [
                ('s3:https://s3.sgp.io.cloud.ovh.net/fixture-backup', 'fr-par'),
                ('s3:https://s3.fr-par.scw.cloud/fixture-backup', 'nl-ams'),
                ('s3:https://s3.fr-par.scw.cloud/another-bucket', 'fr-par'),
                ('s3:https://s3.fr-par.scw.cloud/fixture-backup/prefix', 'fr-par'),
                ('s3:http://s3.fr-par.scw.cloud/fixture-backup', 'fr-par'),
                ('REQUIRED_VERIFIED_SCALEWAY_S3_REPOSITORY', 'REQUIRED_REGION')]:
            with self.subTest(url=url, region=region):
                (root / 'repository').write_text(url)
                (root / 'region').write_text(region)
                with patch.object(worker, 'REPOSITORIES', self.root):
                    with self.assertRaisesRegex(RuntimeError, 'approved target'):
                        worker.repository('scaleway')

    def test_scaleway_approved_regional_repository(self):
        root = self.root / 'scaleway'
        root.mkdir()
        for name, value in [('bucket', 'fixture-backup'), ('access-key-id', 'fixture-id'),
                            ('secret-access-key', 'fixture-secret')]:
            (root / name).write_text(value)
        for region in ('fr-par', 'nl-ams', 'pl-waw'):
            with self.subTest(region=region):
                url = f's3:https://s3.{region}.scw.cloud/fixture-backup'
                (root / 'repository').write_text(url)
                (root / 'region').write_text(region)
                with patch.object(worker, 'REPOSITORIES', self.root):
                    env = worker.repository('scaleway')
                self.assertEqual(env['RESTIC_REPOSITORY'], url)
                self.assertEqual(env['AWS_DEFAULT_REGION'], region)
                self.assertEqual(env['RESTIC_PASSWORD_FILE'], str(root / 'restic-password'))
        (root / 'bucket').write_text('REQUIRED_APPROVED_BUCKET')
        with patch.object(worker, 'REPOSITORIES', self.root):
            with self.assertRaisesRegex(RuntimeError, 'approved target'):
                worker.repository('scaleway')

    def test_scaleway_storage_class_applies_to_every_command_not_b2(self):
        for destination in ('scaleway', 'b2'):
            for operation in ('init', 'backup', 'tag', 'forget', 'prune', 'check', 'restore'):
                with self.subTest(destination=destination, operation=operation):
                    with patch.object(worker, 'repository', return_value={}) as repository, \
                         patch.object(worker, 'command') as command:
                        worker.restic(destination, [operation], timeout=123)
                    expected = ['restic']
                    if destination == 'scaleway':
                        expected += ['-o', 's3.storage-class=ONEZONE_IA']
                    command.assert_called_once_with([*expected, operation], env={}, timeout=123)
                    repository.assert_called_once_with(destination)

    @unittest.skipUnless(shutil.which('rsync'), 'rsync required')
    def test_projected_original_keys_are_materialized_not_dangling_links(self):
        source, target = self.root / 'projected', self.root / 'recovery'
        (source / '..version/n8n').mkdir(parents=True)
        (source / '..version/n8n/encryption_key').write_text('synthetic-original-key')
        (source / '..data').symlink_to('..version')
        (source / 'n8n').mkdir()
        (source / 'n8n/encryption_key').symlink_to('../..data/n8n/encryption_key')
        worker.sync(source, target, ['/..*'], dereference=True)
        key = target / 'n8n/encryption_key'
        self.assertFalse(key.is_symlink())
        self.assertEqual(key.read_text(), 'synthetic-original-key')
        self.assertFalse((target / '..data').exists())

    def test_native_producer_timeout_is_failure(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.command(['sleep', '60'], timeout=0.05)

    @unittest.skipUnless(shutil.which('rsync'), 'rsync required')
    def test_rsync_metadata_and_excluded_transient_uploads(self):
        source, target = self.root / 'source', self.root / 'target'
        source.mkdir()
        (source / 'body').write_bytes(b'completed torrent')
        (source / 'body').chmod(0o640)
        os.setxattr(source / 'body', 'user.backup-test', b'sidecar')
        (source / '.tmp').mkdir()
        (source / '.tmp/partial').write_bytes(b'incomplete')
        worker.sync(source, target, ['/.tmp/'])
        self.assertEqual((target / 'body').read_bytes(), b'completed torrent')
        self.assertEqual(os.getxattr(target / 'body', 'user.backup-test'), b'sidecar')
        self.assertEqual((target / 'body').stat().st_mode & 0o777, 0o640)
        self.assertFalse((target / '.tmp').exists())

    @unittest.skipUnless(shutil.which('restic'), 'restic required')
    def test_two_independent_repositories_and_isolated_sqlite_restore(self):
        source = self.root / 'capture'
        source.mkdir()
        dbfile = source / 'sessions.sqlite'
        with closing(sqlite3.connect(dbfile)) as db:
            db.execute('CREATE TABLE sessions (value TEXT)')
            db.execute("INSERT INTO sessions VALUES ('recovery-fixture')")
            db.commit()
        (source / 'n8n-data').mkdir()
        shutil.copyfile(dbfile, source / 'n8n-data/database.sqlite')
        (source / 'recovery/n8n').mkdir(parents=True)
        (source / 'recovery/n8n/encryption_key').write_text('synthetic-fixture-key')
        for namespace in ('identity', 'networking'):
            (source / namespace).mkdir()
            (source / namespace / 'capture.json').write_text(json.dumps({'run': '20261108T000000Z-123456ab'}))
        (source / 'uploads').mkdir()
        (source / 'uploads/body').write_bytes(b'fixture-upload')
        repo_ids = []
        for destination in ('b2', 'scaleway'):
            env = dict(os.environ, RESTIC_REPOSITORY=str(self.root / destination), RESTIC_PASSWORD='fixture-' + destination,
                       RESTIC_CACHE_DIR=str(self.root / ('cache-' + destination)))
            def restic(*args):
                return subprocess.check_output(['restic', *args], env=env, stderr=subprocess.PIPE)
            restic('init')
            repo_ids.append(json.loads(restic('cat', 'config'))['id'])
            run = '20261108T000000Z-123456ab'
            (self.root / 'run').write_text(run)
            (self.root / 'valid').write_text(run)
            from contextlib import redirect_stdout
            from io import StringIO
            with patch.object(worker, 'STAGE', self.root), patch.object(worker, 'repository', return_value=env), \
                 patch.dict(os.environ, NAMESPACE='homelab'), redirect_stdout(StringIO()) as output:
                worker.upload(run, destination)
                worker.publish(run, destination, output.getvalue().strip())
                worker.restore_test(destination)
            snapshots = json.loads(restic('snapshots', '--json', '--tag', 'complete'))
            self.assertEqual(len(snapshots), 1)
            self.assertNotIn('pending', snapshots[0]['tags'])
            restored = self.root / ('restore-' + destination)
            restic('restore', 'latest', '--tag', 'complete', '--target', str(restored))
            files = list(restored.rglob('sessions.sqlite'))
            self.assertEqual(len(files), 1)
            with closing(sqlite3.connect(files[0])) as db:
                self.assertEqual(db.execute('SELECT value FROM sessions').fetchall(), [('recovery-fixture',)])
            self.assertEqual(next(restored.rglob('body')).read_bytes(), b'fixture-upload')
            restic('check', '--read-data')
        self.assertNotEqual(repo_ids[0], repo_ids[1])


if __name__ == '__main__':
    unittest.main()
