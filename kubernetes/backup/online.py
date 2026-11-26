#!/usr/bin/env python3
"""Stream selected files with SQLite's committed online snapshots overlaid."""
from contextlib import closing, contextmanager
import fnmatch
import hashlib
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

EXCLUDES = ['*/node_modules', '*/__pycache__', '*/.venv', '*/.cache']
HERMES_EXCLUDES = [
    'hermes-data/coder-tools', 'hermes-data/.local', 'hermes-data/installs',
    'hermes-data/qmd', 'hermes-data/cache', 'hermes-data/image_cache',
    'hermes-data/audio_cache', 'hermes-data/logs', 'hermes-data/runtime',
    'hermes-data/telemetry', 'hermes-data/.npm', 'hermes-data/gateway.sock',
    'hermes-data/gateway.pid', 'hermes-data/gateway.lock',
    'hermes-data/profiles/*/cache', 'hermes-data/profiles/*/logs',
    'hermes-data/profiles/*/runtime', 'hermes-data/profiles/*/lsp',
    'hermes-data/config.yaml', 'hermes-data/SOUL.md',
    'hermes-data/profiles/coder/config.yaml', 'hermes-data/profiles/coder/SOUL.md',
    'hermes-data/workspace/.hermes.md', 'hermes-data/skills/writing-for-agents',
    'hermes-data/profiles/coder/skills/writing-for-agents', 'hermes-data/skills/homelab-services',
]
T3_EXCLUDES = ['t3-data/.npm', 't3-data/.local/share/mise/installs', 't3-data/server/logs']


def excluded(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def file_state(path):
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@contextmanager
def open_sqlite(path, temporary):
    source = None
    try:
        source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        source.execute('SELECT count(*) FROM sqlite_schema').fetchone()
    except sqlite3.Error as error:
        if source is not None:
            source.close()
        if error.sqlite_errorcode not in (sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY_DIRECTORY):
            raise
    else:
        with closing(source):
            yield source
        return

    # A dormant WAL database on a read-only mount cannot create its shared memory.
    wal = path.with_name(path.name + '-wal')
    shm = path.with_name(path.name + '-shm')
    watched = (path, path.parent, wal, shm)
    before = tuple(map(file_state, watched))
    if before[0] is None or before[3] is not None or (before[2] is not None and before[2][2] != 0):
        raise RuntimeError('SQLite fallback requires a dormant WAL database')
    with tempfile.TemporaryDirectory(dir=temporary) as isolated:
        copied = Path(isolated) / 'database.sqlite'
        shutil.copyfile(path, copied)
        with copied.open('rb') as handle:
            header = handle.read(100)
            handle.seek(0)
            expected = hashlib.file_digest(handle, 'sha256').digest()
        with path.open('rb') as handle:
            actual = hashlib.file_digest(handle, 'sha256').digest()
        if header[18:20] != b'\x02\x02' or expected != actual or tuple(map(file_state, watched)) != before:
            raise RuntimeError('SQLite source changed or was not in WAL mode')
        with closing(sqlite3.connect(copied, timeout=5)) as source:
            yield source


class ArchiveSourceChanged(RuntimeError):
    pass


def archive(source, recovery, output, deadline):
    patterns = EXCLUDES + HERMES_EXCLUDES + T3_EXCLUDES
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        overlay = temporary / 'overlay'
        overlay.mkdir()
        for directory, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs if not excluded(str((Path(directory) / name).relative_to(source)), patterns)]
            for name in files:
                path = Path(directory) / name
                relative = path.relative_to(source)
                if path.is_symlink() or excluded(str(relative), patterns) or not path.is_file():
                    continue
                with path.open('rb') as handle:
                    sqlite = handle.read(16) == b'SQLite format 3\x00'
                if not sqlite:
                    continue
                target = overlay / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                def progress(*_):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('SQLite capture deadline exceeded')
                with open_sqlite(path, temporary) as db:
                    with closing(sqlite3.connect(target)) as copy:
                        db.backup(copy, pages=256, progress=progress, sleep=0.05)
                        if copy.execute('PRAGMA quick_check').fetchone() != ('ok',):
                            raise RuntimeError('SQLite integrity check failed')
                shutil.copystat(path, target)
                os.chown(target, path.stat().st_uid, path.stat().st_gid)
                patterns.extend([str(relative), str(relative) + '-wal', str(relative) + '-shm'])
        if recovery.exists():
            shutil.copytree(recovery, temporary / 'recovery', ignore=lambda _, names: [n for n in names if n.startswith('.')])
        args = ['tar', '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner',
                '--anchored', '--transform=s,^overlay/,,', '-cf', '-',
                *['--exclude=' + p for p in patterns], '-C', str(source),
                *sorted(p.name for p in source.iterdir()), '-C', str(temporary), 'overlay']
        if (temporary / 'recovery').exists():
            args.append('recovery')
        with subprocess.Popen(args, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True) as producer:
            try:
                status = producer.wait(timeout=max(1, deadline - time.monotonic()))
                if status == 1:
                    raise ArchiveSourceChanged('Archive source changed during capture')
                if status:
                    print(f'File archive failed (exit {status})', file=sys.stderr)
                    raise RuntimeError('File capture changed or failed')
            finally:
                if producer.poll() is None:
                    os.killpg(producer.pid, signal.SIGKILL)
                    producer.wait()


def capture(source, recovery, output):
    deadline = time.monotonic() + 3600
    if not (source / 'hermes-data').is_dir():
        archive(source, recovery, output, deadline)
        return

    # Keep failed attempts out of the upload stream while Hermes changes files.
    for attempt in range(5):
        with tempfile.TemporaryFile() as staged:
            try:
                archive(source, recovery, staged, deadline)
            except ArchiveSourceChanged:
                if attempt == 4 or time.monotonic() + 2 >= deadline:
                    raise
                time.sleep(2)
                continue
            staged.seek(0)
            shutil.copyfileobj(staged, output, length=1024 * 1024)
            return


if __name__ == '__main__':
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    try:
        capture(Path('/source'), Path('/recovery'), sys.stdout.buffer)
    except BaseException as error:
        detail = ''
        if isinstance(error, sqlite3.Error):
            detail = ' (' + getattr(error, 'sqlite_errorname', 'SQLITE_UNKNOWN') + ')'
        print('Online backup failed: ' + type(error).__name__ + detail, file=sys.stderr)
        sys.exit(1)
