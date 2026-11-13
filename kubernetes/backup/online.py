#!/usr/bin/env python3
"""Stream selected files with SQLite's committed online snapshots overlaid."""
from contextlib import closing
import fnmatch
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


def archive(source, recovery, output):
    patterns = EXCLUDES + HERMES_EXCLUDES + T3_EXCLUDES
    deadline = time.monotonic() + 3600
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
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
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
                if producer.wait(timeout=max(1, deadline - time.monotonic())):
                    raise RuntimeError('File capture changed or failed')
            finally:
                if producer.poll() is None:
                    os.killpg(producer.pid, signal.SIGKILL)
                    producer.wait()


if __name__ == '__main__':
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    try:
        archive(Path('/source'), Path('/recovery'), sys.stdout.buffer)
    except BaseException as error:
        print('Online backup failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
