from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'kubernetes/backup' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


online = load('online')


class OnlineCapture(unittest.TestCase):
    def test_committed_wal_recovery_keys_links_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'; source.mkdir()
            volume = source / 'hermes-data'; volume.mkdir()
            recovery = root / 'recovery'; recovery.mkdir()
            (recovery / '..data').mkdir()
            (recovery / '..data' / 'app').mkdir()
            (recovery / '..data' / 'app' / 'key').write_text('nested-original-key')
            (recovery / 'app').symlink_to('..data/app')
            (recovery / '..data' / 'key').write_text('synthetic-original-key')
            (recovery / 'key').symlink_to('..data/key')
            (volume / 'workspace').mkdir()
            file = volume / 'workspace' / 'unpublished.txt'; file.write_text('unpublished work')
            file.chmod(0o640)
            os.setxattr(file, 'user.backup-fixture', b'metadata')
            os.link(file, volume / 'workspace' / 'hardlink')
            (volume / 'link').symlink_to('workspace/unpublished.txt')
            (volume / 'outside').symlink_to('/does-not-exist/private')
            cache = volume / 'workspace' / 'node_modules'; cache.mkdir(); (cache / 'cache').write_text('omit')
            with closing(sqlite3.connect(volume / 'state.db')) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('PRAGMA wal_autocheckpoint=0')
                writer.execute('CREATE TABLE messages (body TEXT)'); writer.commit()
                writer.execute("INSERT INTO messages VALUES ('committed in WAL')"); writer.commit()
                self.assertGreater((volume / 'state.db-wal').stat().st_size, 0)
                archive = root / 'backup.tar'
                with archive.open('wb') as output:
                    online.archive(source, recovery, output)
            restored = root / 'restored'; restored.mkdir()
            subprocess.run(['tar', '--xattrs', '--acls', '-xf', str(archive), '-C', str(restored)], check=True)
            db = restored / 'hermes-data' / 'state.db'
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                self.assertEqual(connection.execute('SELECT body FROM messages').fetchone(), ('committed in WAL',))
            self.assertFalse(db.with_name('state.db-wal').exists())
            self.assertEqual((restored / 'recovery' / 'key').read_text(), 'synthetic-original-key')
            self.assertFalse((restored / 'recovery' / 'key').is_symlink())
            self.assertEqual((restored / 'recovery' / 'app' / 'key').read_text(), 'nested-original-key')
            self.assertFalse((restored / 'hermes-data' / 'workspace' / 'node_modules').exists())
            restored_file = restored / 'hermes-data' / 'workspace' / 'unpublished.txt'
            self.assertEqual(restored_file.stat().st_mode & 0o777, 0o640)
            self.assertEqual(os.getxattr(restored_file, 'user.backup-fixture'), b'metadata')
            self.assertEqual(restored_file.stat().st_ino, (restored_file.parent / 'hardlink').stat().st_ino)
            self.assertEqual(os.readlink(restored / 'hermes-data' / 'outside'), '/does-not-exist/private')

    def test_corrupt_sqlite_fails_instead_of_streaming_raw_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source'; source.mkdir()
            (source / 'broken.db').write_bytes(b'SQLite format 3\0' + b'invalid' * 50)
            with (root / 'backup.tar').open('wb') as output:
                with self.assertRaises(sqlite3.DatabaseError):
                    online.archive(source, root / 'absent', output)


# The fixture exercises the real Bash recovery functions and resourceVersion writes.
KUBE_FIXTURE = r'''
import json,os,sys
from pathlib import Path
p=Path(os.environ['FIXTURE']); args=sys.argv[1:]
def read(): return json.loads((p/'object').read_text())
if 'get' in args:
 print(json.dumps(read()))
elif 'replace' in args:
 value=json.load(sys.stdin); old=read()
 if os.environ.get('CONFLICT') or value['metadata']['resourceVersion'] != old['metadata']['resourceVersion']:sys.exit(1)
 value['metadata']['resourceVersion']=str(int(old['metadata']['resourceVersion'])+1)
 (p/'object').write_text(json.dumps(value))
elif 'scale' in args or 'patch' in args or 'rollout' in args:
 with (p/'actions').open('a') as handle:handle.write(json.dumps(args)+'\n')
 if os.environ.get('API_FAILURE'):sys.exit(1)
 if 'rollout' in args and os.environ.get('ROLLOUT_TIME'):(p/'now').write_text(os.environ['ROLLOUT_TIME'])
else:sys.exit(2)
'''


class OfflineRecovery(unittest.TestCase):
    def recover(self, state, now=1000, operation="recover", **env):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / 'object').write_text(json.dumps({'metadata': {'resourceVersion': '1'}, 'data': {'state': json.dumps(state)}}))
            (p / 'now').write_text(str(now)); (p / 'kube.py').write_text(KUBE_FIXTURE)
            command = '''source kubernetes/backup/offline.sh
kube() { python3 "$FIXTURE/kube.py" "$@"; }
date() { cat "$FIXTURE/now"; }
''' + "run=fixture; " + operation + "\n"
            result = subprocess.run(['bash', '-c', command], cwd=ROOT, env=dict(os.environ, FIXTURE=str(p), **env), capture_output=True)
            final = json.loads(json.loads((p / 'object').read_text())['data']['state'])
            actions = [json.loads(line) for line in (p / 'actions').read_text().splitlines()] if (p / 'actions').exists() else []
            return result.returncode, final, actions

    def test_projected_directory_keys_are_regular_files_without_hidden_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'credentials'; source.mkdir()
            (source / '..data').mkdir(); (source / '..data' / 'app').mkdir()
            (source / '..data' / 'app' / 'key').write_text('synthetic-original-key')
            (source / 'app').symlink_to('..data/app')
            target = root / 'recovery'
            subprocess.run(['bash', '-c', 'source kubernetes/backup/offline.sh; copy_keys "$1" "$2"',
                            'bash', str(source), str(target)], cwd=ROOT, check=True)
            self.assertEqual((target / 'app' / 'key').read_text(), 'synthetic-original-key')
            self.assertFalse((target / 'app').is_symlink()); self.assertFalse((target / '..data').exists())

    def test_pod_listing_failure_or_terminating_pod_never_passes_stop_boundary(self):
        for command, expected in [('return 1', 1), ('echo invalid-json', 1),
                                  ("echo '{\"items\":[{\"metadata\":{\"deletionTimestamp\":\"now\"}}]}'", 1),
                                  ("echo '{\"items\":[]}'", 0)]:
            with self.subTest(command=command):
                code = 'source kubernetes/backup/offline.sh; kube() { ' + command + '; }; wait_stopped n8n 1'
                result = subprocess.run(['bash', '-c', code], cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, expected)

    def state(self, **changes):
        return dict(phase='resume', run='fixture', deadline=1100, valid=True, suspended=True,
                    replicas=[['opencloud', 3], ['qbittorrent', 0]], **changes)

    def test_restores_exact_replicas_and_preexisting_suspension(self):
        code, state, actions = self.recover(self.state())
        self.assertEqual(code, 0); self.assertEqual(state['phase'], 'done'); self.assertTrue(state['valid'])
        scales = [a for a in actions if 'scale' in a]
        self.assertEqual([a[-1] for a in scales], ['--replicas=3', '--replicas=0'])
        self.assertEqual(json.loads(next(a[-1] for a in actions if 'patch' in a)), {'spec': {'suspend': True}})

    def test_dead_runner_expires_and_never_publishes(self):
        state = self.state(); state.update(phase='capture', deadline=900)
        code, state, actions = self.recover(state)
        self.assertEqual(code, 0); self.assertEqual(state['phase'], 'done'); self.assertFalse(state['valid'])
        self.assertEqual(len([a for a in actions if 'scale' in a]), 2)

    def test_live_capture_is_left_alone(self):
        state = self.state(); state['phase'] = 'capture'
        code, final, actions = self.recover(state)
        self.assertEqual(code, 0); self.assertEqual(final, state); self.assertFalse(actions)

    def test_failed_api_retains_recovery_intent_and_originals(self):
        code, state, _ = self.recover(self.state(), API_FAILURE='1')
        self.assertNotEqual(code, 0); self.assertEqual(state['phase'], 'recovering')
        self.assertEqual(state['replicas'], [['opencloud', 3], ['qbittorrent', 0]])

    def test_cas_conflict_prevents_any_restore_mutation(self):
        code, state, actions = self.recover(self.state(), CONFLICT='1')
        self.assertNotEqual(code, 0); self.assertEqual(state['phase'], 'resume'); self.assertFalse(actions)

    def test_slow_rollout_invalidates_capture(self):
        code, state, _ = self.recover(self.state(), ROLLOUT_TIME='1281')
        self.assertEqual(code, 0); self.assertFalse(state['valid'])

    def test_late_runner_cannot_mark_expired_capture_valid(self):
        state = self.state(); state.update(phase='capture', deadline=900)
        code, final, _ = self.recover(state, operation='request_resume true')
        self.assertEqual(code, 0); self.assertEqual(final['phase'], 'resume'); self.assertFalse(final['valid'])

    def test_old_runner_cannot_change_another_capture(self):
        state = self.state(); state.update(phase='capture', run='other-owner')
        code, final, actions = self.recover(state, operation='request_resume true')
        self.assertEqual(code, 0); self.assertEqual(final, state); self.assertFalse(actions)

    def test_abandoned_done_releases_after_acknowledgement_window(self):
        state = self.state(); state.update(phase='done', deadline=900)
        code, final, actions = self.recover(state)
        self.assertEqual(code, 0); self.assertEqual(final, {'phase': 'idle'}); self.assertFalse(actions)


class ResticCopy(unittest.TestCase):
    def test_native_copy_with_stream_lock_preserves_files_and_retention_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source'; source.mkdir()
            file = source / 'file'; file.write_text('synthetic captured bytes'); file.chmod(0o640)
            os.setxattr(file, 'user.backup-fixture', b'preserved')
            os.link(file, source / 'hardlink'); (source / 'link').symlink_to('file')
            env = dict(os.environ, RESTIC_PASSWORD='synthetic-fixture-only', RESTIC_FROM_PASSWORD='synthetic-fixture-only', RESTIC_CACHE_DIR=str(root / 'cache'))
            def restic(repo, *args):
                return subprocess.run(['restic', '-r', str(repo), *args], env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            local, remote = root / 'local', root / 'remote'
            restic(local, 'init'); restic(remote, 'init')
            restic(local, 'backup', '--host', 'homelab', '--tag', 'data,opencloud', str(source))
            snapshot = json.loads(restic(local, 'snapshots', '--json'))[0]['id']
            # K8up's stdin backup and the helper's copy share the remote repository.
            with subprocess.Popen(['restic', '-r', str(remote), 'backup', '--stdin', '--stdin-filename', '/homelab-capture.json', '--host', 'homelab', '--tag', 'opencloud'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as stream:
                try:
                    restic(remote, 'copy', '--from-repo', str(local), snapshot)
                    _, errors = stream.communicate(b'{"fixture":true}\n', timeout=60)
                    self.assertEqual(stream.returncode, 0, errors.decode())
                finally:
                    if stream.poll() is None:stream.kill(); stream.wait()
            snapshots = json.loads(restic(remote, 'snapshots', '--tag', 'data,opencloud', '--json'))
            self.assertEqual(len(snapshots), 1); self.assertEqual(snapshots[0]['hostname'], 'homelab')
            restored = root / 'restore'
            restic(remote, 'restore', snapshots[0]['id'], '--target', str(restored))
            target = restored / str(source).lstrip('/') / 'file'
            self.assertEqual(target.read_text(), file.read_text()); self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual(os.getxattr(target, 'user.backup-fixture'), b'preserved')
            self.assertEqual(target.stat().st_ino, (target.parent / 'hardlink').stat().st_ino)
            self.assertEqual(os.readlink(target.parent / 'link'), 'file')
            restic(remote, 'forget', '--host', 'homelab', '--keep-daily', '7', '--keep-weekly', '4', '--keep-monthly', '6', '--prune')
            restic(remote, 'check', '--read-data')
