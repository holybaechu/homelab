#!/usr/bin/env python3
"""Deploy the homelab in a stable directory using native Docker Compose."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from prepare_release import prepare

ROOT = Path('/opt/homelab/compose')
BUNDLE = Path('/etc/homelab/secrets/apps.json')
CONFIG_SERVICES = {
    'config/traefik.yml': 'traefik',
    'config/init-qbittorrent.sh': 'qbittorrent',
    'config/authentik-blueprint.yaml': 'authentik-worker',
    'generated/authentik/': 'authentik-worker',
    'generated/copyparty/': 'copyparty',
    'generated/headscale/': 'headscale',
    'generated/qbittorrent/': 'qbittorrent',
    'generated/adguard/': 'adguard',
}


def compose(root, *args, capture=False):
    result = subprocess.run(
        ['docker', 'compose', '--project-name', 'homelab', '--project-directory', str(root),
         '-f', str(root / 'compose.yml'), *args],
        stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode:
        # Compose config and environment diagnostics can include credentials.
        # Report the failed operation, never its private output.
        raise RuntimeError('Docker Compose failed: ' + args[0] + ' (exit ' + str(result.returncode) + ')')
    return result.stdout if capture else ''


def sync(source: Path, destination: Path, *, skip_adguard=False) -> set[str]:
    """Keep mounted directory inodes stable; replace only changed files."""
    changed = set()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source).as_posix()
        if '__pycache__' in path.parts or path.is_dir():
            continue
        if path.is_symlink():
            raise RuntimeError('Symlinks are not supported in the Compose package')
        if skip_adguard and relative.startswith('generated/adguard/'):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists() and target.read_bytes() == path.read_bytes():
            continue
        temporary = target.with_name('.' + target.name + '.new')
        shutil.copyfile(path, temporary)
        temporary.chmod(0o600)
        temporary.replace(target)
        changed.add(relative)
    # Removed public source files are removed; runtime-generated AdGuard files stay.
    for path in destination.rglob('*'):
        relative = path.relative_to(destination).as_posix()
        if path.is_file() and not (source / relative).exists():
            if relative.startswith(('config/', '.secrets/')):
                path.unlink()
    return changed


def affected_configs(changed: set[str]) -> set[str]:
    return {service for prefix, service in CONFIG_SERVICES.items()
            if any(name == prefix or name.startswith(prefix.rstrip('/') + '/') for name in changed)}


def container_ids(root: Path) -> dict[str, str]:
    rows = compose(root, 'ps', '--all', '--format', 'json', capture=True)
    return {row['Service']: row['ID'] for line in rows.splitlines()
            if line.strip() for row in [json.loads(line)]}


def legacy_root(install: Path) -> Path | None:
    state_path = install / 'compose-control/release-state.json'
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text())
    if state.get('pending') is not None or state.get('active_slot') not in ('a', 'b'):
        raise RuntimeError('Finish or recover the previous release before migrating')
    return install / 'compose-runtime' / state['active_slot'] / 'stack'


def data_backup(root: Path, destination: Path, *, headscale=False):
    """Before identity upgrades, preserve a logical DB dump and offline VPN state."""
    destination.mkdir(parents=True, mode=0o700)
    dump = destination / 'authentik.sql'
    with dump.open('xb') as output:
        dump.chmod(0o600)
        result = subprocess.run(
            ['docker', 'compose', '--project-name', 'homelab', '-f', str(root / 'compose.yml'),
             'exec', '-T', 'authentik-db', 'pg_dump', '-U', 'authentik', 'authentik'],
            stdout=output, stderr=subprocess.PIPE,
        )
    if result.returncode or dump.stat().st_size == 0:
        raise RuntimeError('Authentik database backup failed; deployment stopped')
    if headscale:
        compose(root, 'stop', 'headscale')
        try:
            mount = subprocess.check_output(
                ['docker', 'volume', 'inspect', 'platform_headscale_data', '--format', '{{.Mountpoint}}'],
                text=True).strip()
            shutil.copytree(mount, destination / 'headscale', copy_function=shutil.copy2)
        finally:
            compose(root, 'start', 'headscale')


def deploy(source: Path, bundle: Path, revision: str, *, root=ROOT, installed_bundle=BUNDLE):
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = root.parent / 'compose-previous'
    old = root if (root / '.revision').exists() else legacy_root(root.parent)
    migration = old is not None and old != root
    with tempfile.TemporaryDirectory(prefix='homelab-') as temporary:
        staged = Path(temporary) / 'stack'
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns('__pycache__', 'generated', '.secrets'))
        prepare(bundle, staged, staged / 'topology.json')
        before = container_ids(old) if old else {}
        new_model = json.loads(compose(staged, 'config', '--format', 'json', capture=True))['services']
        old_model = json.loads(compose(old, 'config', '--format', 'json', capture=True))['services'] if old else {}
        # Absolute staged bind paths differ by design; native up compares the final paths.
        images_changed = {name for name, model in new_model.items()
                          if old_model.get(name, {}).get('image') != model.get('image')}
        compose(staged, 'pull', '--quiet')
        if old:
            if previous.exists():
                shutil.rmtree(previous)
            shutil.copytree(old, previous, ignore=shutil.ignore_patterns('__pycache__'))
            if installed_bundle.exists():
                shutil.copy2(installed_bundle, previous / 'apps.json')
            if migration or images_changed & {'authentik-db', 'authentik-server', 'authentik-worker', 'headscale'}:
                data_backup(old, root.parent / 'backups' / time.strftime('%Y%m%d-%H%M%S'),
                            headscale=migration or 'headscale' in images_changed)
        # AdGuard rewrites its own config. Compare its declarative inputs, not normalized YAML.
        old_bundle = json.loads(installed_bundle.read_text()) if installed_bundle.exists() else {}
        incoming = json.loads(bundle.read_text())
        keep_adguard = bool(old and not migration and
                            old_bundle.get('adguard') == incoming.get('adguard') and
                            (root / 'config/AdGuardHome.yaml.tmpl').read_bytes() ==
                            (staged / 'config/AdGuardHome.yaml.tmpl').read_bytes() and
                            (root / 'topology.json').read_bytes() == (staged / 'topology.json').read_bytes())
        try:
            if old and not keep_adguard:
                compose(old, 'stop', 'adguard')
            changed = sync(staged, root, skip_adguard=keep_adguard)
            installed_bundle.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            secret_temporary = installed_bundle.with_suffix('.new')
            shutil.copyfile(bundle, secret_temporary)
            secret_temporary.chmod(0o600)
            secret_temporary.replace(installed_bundle)
            forced = affected_configs(changed)
            # A single recreation attaches the new stable mounts during initial migration.
            if migration or not old:
                compose(root, 'up', '-d', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '300')
            else:
                if forced:
                    compose(root, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never',
                            '--force-recreate', *sorted(forced))
                compose(root, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '300')
            identity_changed = migration or not old or bool(forced & {'authentik-worker'}) or bool(images_changed & {'authentik-server', 'authentik-worker'})
            if identity_changed:
                compose(root, 'exec', '-T', 'authentik-worker', 'ak', 'apply_blueprint',
                        '/blueprints/homelab/authentik-blueprint.yaml', capture=True)
            for attempt in range(10):
                result = subprocess.run(['sh', 'smoke.sh'], cwd=root, capture_output=True, text=True)
                if result.returncode == 0:
                    print('DNS, public reads, protected redirects, OIDC, and Headscale verified.', flush=True)
                    break
                if attempt == 9:
                    raise RuntimeError('Access smoke check failed; run sh smoke.sh from the stable directory')
                time.sleep(2)
        except Exception:
            if migration:
                # One-time migration fallback, using the untouched old mounts and volumes.
                compose(old, 'up', '-d', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '300')
                if (previous / 'apps.json').exists():
                    shutil.copy2(previous / 'apps.json', installed_bundle)
                print('Migration failed; previous Compose installation restored.', flush=True)
            raise
        (root / '.revision').write_text(revision + '\n')
        if root == ROOT:
            Path('/usr/local/libexec/homelab-release').unlink(missing_ok=True)
        after = container_ids(root)
        recreated = sorted(name for name in after if after[name] != before.get(name))
        print('Deployment verified. Configuration backup: ' + str(previous), flush=True)
        print('Recreated services: ' + (', '.join(recreated) if recreated else 'none'), flush=True)
        print('Mounted-config updates: ' + (', '.join(sorted(forced)) if forced else 'none'), flush=True)
        compose(root, 'ps')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--secret-bundle', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    # Shared with manual invocations; workflows also serialize production jobs.
    with open('/run/lock/homelab-compose.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            deploy(args.source, args.secret_bundle, args.revision)
        except Exception as error:
            # Only controlled messages; no subprocess output or credential-bearing traceback.
            print('Deployment failed: ' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), flush=True)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
