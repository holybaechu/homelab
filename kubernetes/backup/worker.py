#!/usr/bin/env python3
"""Read-only sources, local staging, then independent restic repositories."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import time

STAGE = Path('/stage')
SOURCES = Path('/source')
SECRETS = Path('/recovery')
REPOSITORIES = Path('/repositories')
DATA = {
    'opencloud': ['opencloud-config', 'opencloud-data'],
    'immich': ['immich-media'],
    'n8n': ['n8n-data'],
    'zipline': ['zipline-data', 'zipline-uploads'],
    'authentik': ['authentik-media'],
    'headscale': ['headscale', 'headscale-gateway'],
    'hermes': ['hermes-data'],
}
# Never exclude workspaces, vaults, profiles, recovery files or application DBs.
HERMES_EXCLUDES = [
    '/coder-tools/', '/.local/', '/installs/', '/qmd/', '/cache/', '/.cache/',
    '/image_cache/', '/audio_cache/', '/logs/', '/runtime/', '/telemetry/',
    '/profiles/*/cache/', '/profiles/*/image_cache/', '/profiles/*/audio_cache/',
    '/profiles/*/logs/', '/profiles/*/runtime/', '/profiles/*/lsp/',
    'node_modules/', '__pycache__/', '.venv/', '.cache/',
    '/config.yaml', '/SOUL.md', '/profiles/coder/config.yaml', '/profiles/coder/SOUL.md',
    '/workspace/.hermes.md', '/skills/writing-for-agents/',
    '/profiles/coder/skills/writing-for-agents/', '/skills/homelab-services/',
    '/.npm/', '/gateway.sock', '/gateway.pid', '/gateway.lock',
]


def command(args, *, env=None, timeout=3600, stdout=subprocess.PIPE, allowed=(0,)):
    with subprocess.Popen(args, stdout=stdout, stderr=subprocess.PIPE, env=env, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        result = process
    if result.returncode not in allowed:
        # Native tools may echo credential-bearing filenames/connection strings.
        raise RuntimeError(f'{Path(args[0]).name} exited {result.returncode}')
    return output


def sync(source, target, excludes=(), presync=False, dereference=False):
    target.mkdir(parents=True, exist_ok=True)
    command(['rsync', '-aHAX', '--numeric-ids', '--modify-window=-1', '--delete', '--delete-excluded',
             *(['--copy-links'] if dereference else []),
             *[f'--exclude={p}' for p in excludes], str(source) + '/', str(target) + '/'],
            allowed=(0, 24) if presync else (0,))


def sqlite_backup(source, target, deadline):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.backup-partial')
    temporary.unlink(missing_ok=True)
    def progress(*_):
        if time.time() >= deadline:
            raise TimeoutError('SQLite backup deadline exceeded')
    # Never immutable=1: active WAL must participate in SQLite's snapshot.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        with closing(sqlite3.connect(temporary)) as copy:
            db.backup(copy, pages=256, progress=progress, sleep=0.05)
            if copy.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise RuntimeError('SQLite integrity check failed')
    shutil.copystat(source, temporary)
    os.chown(temporary, source.stat().st_uid, source.stat().st_gid)
    temporary.replace(target)


def sqlite_files(root):
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not Path(directory, d).is_symlink()]
        for name in files:
            path = Path(directory, name)
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as handle:
                if handle.read(16) == b'SQLite format 3\x00':
                    found.append(path.relative_to(root))
    return found


def postgres(group, target):
    major, host, user = {'immich': ('14', 'immich-postgresql', 'immich'),
                         'authentik': ('18', 'authentik-postgresql', 'authentik')}[group]
    env = dict(os.environ, PGHOST=host, PGUSER=user, PGDATABASE=user,
               PGPASSWORD=(SECRETS / group / 'database-password').read_text(), PGCONNECT_TIMEOUT='10')
    for program, flags, name in [('pg_dump', ['--format=custom'], 'database.dump'),
                                 ('pg_dumpall', ['--roles-only'], 'roles.sql')]:
        with (target / name).open('wb') as output:
            command([f'/usr/lib/postgresql/{major}/bin/{program}', *flags], env=env, stdout=output)


def literal_exclude(path):
    return '/' + ''.join('\\' + c if c in '\\*?[' else c for c in str(path))


def safe_path(root, name):
    path = Path(name)
    if not name or path.is_absolute() or '..' in path.parts or '.git' in path.parts:
        raise ValueError('Unsafe recovery path')
    target = root / path
    if any(p.is_symlink() for p in [target, *target.parents]):
        raise ValueError('Symlink in recovery path')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Recovery path escapes root')
    return target


def restore_git_deletions(directory):
    # Only explicitly isolated restores under staging; never source/workspace trees.
    root = Path(directory)
    isolated = STAGE / 'restore'
    if root == isolated or not root.resolve().is_relative_to(isolated.resolve()):
        raise ValueError('Expected an isolated restore below /stage/restore')
    root = safe_path(isolated, str(root.relative_to(isolated)))
    entries = json.loads((root / 'hermes-repositories.json').read_text())
    removals = []
    for entry in entries:
        if entry['origin'] != 'https://github.com/holybaechu/homelab.git':
            raise ValueError('Unapproved recovery origin')
        repository = safe_path(root / 'hermes-data', entry['path'])
        if not re.fullmatch(r'[0-9a-f]{40,64}', entry['commit']):
            raise ValueError('Invalid recovery commit')
        for name in entry.get('deleted', []):
            if Path(name) == Path('.'):
                raise ValueError('Invalid tombstone')
            target = safe_path(repository, name)
            tracked = command(['git', '--literal-pathspecs', '-c', 'core.hooksPath=/dev/null', '-C', str(repository),
                               'ls-tree', '-z', entry['commit'], '--', name])
            if not tracked or tracked.split(b'\t', 1)[-1].rstrip(b'\0') != os.fsencode(name):
                raise ValueError('Tombstone is not tracked at the recorded commit')
            if target.is_dir():
                raise ValueError('Refusing recursive tombstone deletion')
            removals.append(target)
    for target in removals:
        target.unlink(missing_ok=True)


def gitops_excludes(root, selected):
    excluded, recovery = [], []
    for directory, dirs, files in os.walk(selected, followlinks=False):
        if '.git' not in dirs and '.git' not in files:
            continue
        if '.git' in dirs:
            dirs.remove('.git')
        relative = Path(directory).relative_to(selected)
        git = root / relative / '.git'
        if git.is_file():
            text = git.read_text().strip()
            if not text.startswith('gitdir: /opt/data/'):
                continue
            git = root / text.removeprefix('gitdir: /opt/data/')
        base = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                '-c', 'safe.directory=*', '--git-dir=' + str(git), '--work-tree=' + str(root / relative)]
        def read(*args):
            return command([*base, *args])
        repository_excludes = []
        try:
            if read('config', '--get', 'remote.origin.url').strip() != b'https://github.com/holybaechu/homelab.git':
                continue
            # Exclude only a commit already in the authoritative main branch.
            read('merge-base', '--is-ancestor', 'HEAD', 'refs/remotes/origin/main')
            dirty = set(read('diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--name-only', '-z').split(b'\0'))
            tracked = read('ls-files', '-z').split(b'\0')
            for name in tracked:
                if name and name not in dirty:
                    repository_excludes.append(literal_exclude(relative / os.fsdecode(name)))
            status = read('status', '--porcelain=v1', '-z')
            # Preserve index, staged blobs and stash as recovery state when dirty.
            has_stash = subprocess.run([*base, 'rev-parse', '--verify', 'refs/stash'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if not status and not has_stash:
                repository_excludes.append(literal_exclude(relative / '.git'))
            deleted = [os.fsdecode(p) for p in read(
                'diff', '--no-ext-diff', '--no-textconv', '--no-renames', 'HEAD', '--diff-filter=D',
                '--name-only', '-z').split(b'\0') if p]
            for name in deleted:
                path = safe_path(root / relative, name)
                if path.exists():
                    raise RuntimeError('Tracked deletion changed during inventory')
            recovery.append({'path': str(relative), 'commit': read('rev-parse', 'HEAD').decode().strip(),
                             'origin': 'https://github.com/holybaechu/homelab.git', 'deleted': deleted})
        except RuntimeError:
            continue
        excluded.extend(repository_excludes)
        if '.git' in dirs:
            dirs.remove('.git')
    (STAGE / 'capture/hermes-repositories.json').write_text(json.dumps(recovery))
    return excluded


def copy_data(name, *, presync=False, deadline=None):
    source, target = SOURCES / name, STAGE / 'capture' / name
    excludes = ['*-wal', '*-shm', '*.backup-partial'] if name in ('n8n-data', 'headscale') else []
    databases = []
    if name == 'hermes-data':
        excludes += HERMES_EXCLUDES
        # Inventory the staged selection, NOT excluded model/tool caches.
        sync(source, target, excludes, presync=True)
        databases = sqlite_files(target)
        excludes += gitops_excludes(source, target)
    elif name == 'n8n-data':
        databases = [Path('database.sqlite')]
        excludes += ['/database.sqlite']
    elif name == 'headscale':
        databases = [Path('db.sqlite')]
        excludes += ['/db.sqlite']
    if name == 'zipline-uploads':
        excludes += ['/.tmp/']
    sync(source, target, excludes, presync=presync)
    if not presync:
        if name == 'hermes-data':
            entries = json.loads((STAGE / 'capture/hermes-repositories.json').read_text())
            for entry in entries:
                repository = safe_path(target, entry['path'])
                if any(safe_path(repository, p).exists() for p in entry['deleted']):
                    raise RuntimeError('Tracked deletion changed during final sync')
            final_databases = sqlite_files(target)
            if any((target / p).exists() and p not in final_databases for p in databases):
                raise RuntimeError('SQLite inventory changed incompatibly')
            databases = final_databases
        for path in databases:
            original = safe_path(source, str(path))
            identity = original.stat()
            with original.open('rb') as handle:
                if handle.read(16) != b'SQLite format 3\x00':
                    raise RuntimeError('SQLite source changed during capture')
            sqlite_backup(original, target / path, deadline)
            after = original.stat()
            if (identity.st_dev, identity.st_ino) != (after.st_dev, after.st_ino):
                raise RuntimeError('SQLite source replaced during capture')
            for suffix in ('-wal', '-shm'):
                (target / (str(path) + suffix)).unlink(missing_ok=True)
    return databases


def prepare(run):
    STAGE.mkdir(exist_ok=True)
    (STAGE / 'run').write_text(run)
    for marker in ['valid', 'uploaded-b2', 'uploaded-scaleway']:
        (STAGE / marker).unlink(missing_ok=True)
    for group, names in DATA.items():
        if group == 'hermes':
            continue
        for name in names:
            if (SOURCES / name).exists():
                copy_data(name, presync=True)


def ensure_run(run):
    if not re.fullmatch(r'[0-9TZ-]+[0-9a-f]{8}', run) or (STAGE / 'run').read_text() != run:
        raise RuntimeError('Stale or invalid capture ID')


def capture(run, group, deadline):
    ensure_run(run)
    deadline = float(deadline)
    signal.alarm(max(1, int(deadline - time.time())))
    names = [n for n in DATA[group] if (SOURCES / n).exists()]
    if not names:
        raise RuntimeError('No selected sources mounted')
    target = STAGE / 'capture'
    target.mkdir(exist_ok=True)
    if group in ('immich', 'authentik'):
        dump = target / (group + '-postgresql')
        dump.mkdir(exist_ok=True)
        postgres(group, dump)
    for name in names:
        copy_data(name, deadline=deadline)
    if SECRETS.exists():
        sync(SECRETS, target / 'recovery', ['/..*'], dereference=True)
    (STAGE / ('captured-' + group)).write_text(run)
    signal.alarm(0)


def seal(run):
    ensure_run(run)
    groups = {'homelab': ['opencloud', 'immich', 'n8n', 'zipline', 'hermes'],
              'identity': ['authentik', 'headscale'], 'networking': ['headscale']}[os.environ['NAMESPACE']]
    if any((STAGE / ('captured-' + group)).read_text() != run for group in groups):
        raise RuntimeError('Incomplete local capture')
    (STAGE / 'capture' / 'capture.json').write_text(json.dumps({'run': run, 'groups': groups}))
    (STAGE / 'valid').write_text(run)


def repository(destination):
    if destination not in ('b2', 'scaleway'):
        raise ValueError('Unknown destination')
    root = REPOSITORIES / destination
    url, region = (root / 'repository').read_text().strip(), (root / 'region').read_text().strip()
    expected = ('s3:https://s3.us-west-004.backblazeb2.com/homelab-backup-b2', 'us-west-004')
    if destination == 'scaleway':
        bucket = (root / 'bucket').read_text().strip()
        if (region not in ('fr-par', 'nl-ams', 'pl-waw') or
                not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket)):
            raise RuntimeError('Repository endpoint/region differs from approved target')
        expected = (f's3:https://s3.{region}.scw.cloud/{bucket}', region)
    if (url, region) != expected:
        raise RuntimeError('Repository endpoint/region differs from approved target')
    return dict(os.environ,
                RESTIC_REPOSITORY=url,
                RESTIC_PASSWORD_FILE=str(root / 'restic-password'),
                AWS_ACCESS_KEY_ID=(root / 'access-key-id').read_text().strip(),
                AWS_SECRET_ACCESS_KEY=(root / 'secret-access-key').read_text().strip(),
                AWS_DEFAULT_REGION=region,
                RESTIC_CACHE_DIR='/stage/restic-cache/' + destination)


def restic(destination, args, timeout=3600):
    # ONEZONE_IA is Scaleway Standard One Zone, including repository metadata.
    options = ['-o', 's3.storage-class=ONEZONE_IA'] if destination == 'scaleway' else []
    return command(['restic', *options, *args], env=repository(destination), timeout=timeout)


def initialize(destination):
    restic(destination, ['init'])
    print(json.loads(restic(destination, ['cat', 'config']))['id'])


def upload(run, destination):
    ensure_run(run)
    if (STAGE / 'valid').read_text() != run:
        raise RuntimeError('Unsealed capture')
    for namespace in ('identity', 'networking'):
        manifest = json.loads((STAGE / 'capture' / namespace / 'capture.json').read_text())
        if manifest['run'] != run:
            raise RuntimeError('Namespace transfer is missing or stale')
    # Initialization is an explicit operator action, never an error fallback.
    output = restic(destination, ['backup', '--json', '--host', 'homelab-' + os.environ['NAMESPACE'],
                                  '--tag', 'pending', '--tag', run, str(STAGE / 'capture')], timeout=43200)
    summaries = [json.loads(line) for line in output.splitlines()]
    snapshot = next(s['snapshot_id'] for s in reversed(summaries) if s.get('message_type') == 'summary')
    (STAGE / ('uploaded-' + destination)).write_text(snapshot)
    print(snapshot)


def publish(run, destination, snapshot):
    ensure_run(run)
    if not re.fullmatch('[0-9a-f]{64}', snapshot):
        raise ValueError('Invalid snapshot')
    if (STAGE / ('uploaded-' + destination)).read_text() != snapshot:
        raise RuntimeError('Snapshot does not belong to completed upload')
    restic(destination, ['tag', '--add', 'complete', '--remove', 'pending', snapshot])
    forget(destination)


def restore_test(destination):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='restore-test-', dir=STAGE) as directory:
        restic(destination, ['restore', 'latest', '--tag', 'complete', '--verify',
                 '--include', str(STAGE / 'capture/n8n-data/database.sqlite'),
                 '--include', str(STAGE / 'capture/recovery/n8n/encryption_key'),
                 '--target', directory])
        root = Path(directory) / (STAGE / 'capture').relative_to('/')
        with closing(sqlite3.connect((root / 'n8n-data/database.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise RuntimeError('Restored n8n database failed integrity check')
            if not db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                raise RuntimeError('Restored application database has no schema')
        if not (root / 'recovery/n8n/encryption_key').read_bytes():
            raise RuntimeError('Original n8n encryption key absent')
    print('Isolated n8n SQLite restore and original-key presence verified')


def forget(destination):
    restic(destination, ['forget', '--tag', 'complete', '--group-by', 'host,paths',
                         '--keep-daily', '7', '--keep-weekly', '4', '--keep-monthly', '6'])


def maintain(destination):
    forget(destination)
    restic(destination, ['prune', '--max-repack-size', '0'], timeout=43200)
    restic(destination, ['check'], timeout=43200)


if __name__ == '__main__':
    os.umask(0o077)
    def expired(*_):
        raise TimeoutError('Local capture deadline exceeded')
    signal.signal(signal.SIGALRM, expired)
    try:
        import fcntl
        with (STAGE / 'operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            {'prepare': prepare, 'capture': capture, 'upload': upload, 'publish': publish,
             'maintain': maintain, 'seal': seal, 'restore-test': restore_test,
             'restore-git-deletions': restore_git_deletions, 'init': initialize}[sys.argv[1]](*sys.argv[2:])
    except BaseException as error:
        print(f'Backup worker failed: {type(error).__name__}', file=sys.stderr)
        sys.exit(1)
