#!/usr/bin/env python3
"""Reconcile one additional PVE login realm without changing local authentication."""
import argparse
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.request

REALM = 'authentik'
ISSUER = 'https://auth.home.hchu.me/application/o/proxmox/'
SETTINGS = {'type': 'openid', 'issuer-url': ISSUER, 'client-id': 'homelab-proxmox',
            'username-claim': 'preferred_username', 'autocreate': 0, 'default': 0,
            'scopes': 'openid profile email'}

def command(argv):
    if '--client-key' in argv:
        # Invoke the native PVE CLI with arguments read from stdin so the client
        # credential is absent from the operating system's process arguments.
        perl = 'local $/; @ARGV = @{decode_json(<STDIN>)}; PVE::CLI::pveum->run_cli_handler();'
        result = subprocess.run(['perl', '-MPVE::CLI::pveum', '-MJSON::PP', '-e', perl],
                                input=json.dumps(argv[1:]), capture_output=True, text=True)
    else:
        result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        # Captured output and command arguments may contain the client credential.
        raise RuntimeError('PVE identity operation failed')
    return json.loads(result.stdout) if result.stdout.strip() else None

def reconcile(secret, administrators, refresh_secret, run=command, check_provider=True):
    if not re.fullmatch(r'[a-f0-9]{64}', secret):
        raise ValueError('Invalid PVE identity credential')
    if not administrators or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', user) for user in administrators):
        raise ValueError('Declare at least one supported administrator username')
    realms = run(['pveum', 'realm', 'list', '--output-format', 'json'])
    users = run(['pveum', 'user', 'list', '--output-format', 'json'])
    if not any(realm['realm'] == 'pam' and realm['type'] == 'pam' for realm in realms):
        raise RuntimeError('Linux PAM recovery realm is unavailable')
    if not any(user['userid'] == 'root@pam' and user.get('enable', 1) for user in users):
        raise RuntimeError('Local root recovery account is unavailable')
    if check_provider:
        with urllib.request.urlopen(ISSUER + '.well-known/openid-configuration', timeout=10) as response:
            discovery = json.load(response)
        if discovery.get('issuer') != ISSUER or not discovery.get('jwks_uri'):
            raise RuntimeError('Proxmox identity provider is unavailable')
    existing = next((realm for realm in realms if realm['realm'] == REALM), None)
    changed = False
    if existing is not None and existing['type'] != 'openid':
        raise RuntimeError('The authentik realm already belongs to another authentication type')
    configured = run(['pvesh', 'get', '/access/domains/' + REALM, '--output-format', 'json']) if existing else None
    if existing is None or refresh_secret or any(configured.get(key) != value for key, value in SETTINGS.items()):
        args = ['pveum', 'realm', 'add' if existing is None else 'modify', REALM]
        for key, value in {**SETTINGS, 'client-key': secret}.items():
            args.extend(['--' + key, str(value)])
        run(args)
        changed = True
    acls = run(['pveum', 'acl', 'list', '--output-format', 'json'])
    declared = {name + '@' + REALM for name in administrators}
    for acl in acls:
        userid = acl.get('ugid', '')
        if acl.get('type') == 'user' and userid.endswith('@' + REALM) and userid not in declared and acl.get('path') == '/' and acl.get('roleid') == 'PVEAdmin':
            run(['pveum', 'acl', 'delete', '/', '--users', userid, '--roles', 'PVEAdmin'])
            changed = True
    for name in administrators:
        userid = name + '@' + REALM
        existing_user = next((user for user in users if user['userid'] == userid), None)
        if existing_user is None:
            run(['pveum', 'user', 'add', userid, '--enable', '1'])
            changed = True
        elif not existing_user.get('enable', 1):
            run(['pveum', 'user', 'modify', userid, '--enable', '1'])
            changed = True
        if not any(acl.get('type') == 'user' and acl.get('ugid') == userid and acl.get('path') == '/' and acl.get('roleid') == 'PVEAdmin' and acl.get('propagate', 1) for acl in acls):
            run(['pveum', 'acl', 'modify', '/', '--users', userid, '--roles', 'PVEAdmin', '--propagate', '1'])
            changed = True
    return {'changed': changed, 'local_recovery_preserved': True}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--secret-bundle', type=Path, required=True)
    parser.add_argument('--administrators', required=True)
    parser.add_argument('--refresh-secret', action='store_true')
    parser.add_argument('--keep-secret', action='store_true')
    args = parser.parse_args()
    try:
        metadata = args.secret_bundle.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o077:
            raise ValueError('PVE identity requires a private root-owned regular bundle')
        bundle = json.loads(args.secret_bundle.read_text())
        if bundle.get('component') != 'pve' or type(bundle.get('version')) is not int or bundle['version'] != 2:
            raise ValueError('PVE identity requires its version-2 component bundle')
        if set(bundle.get('values', {})) != {'deploy_ssh_public_keys', 'proxmox_oidc_client_secret'}:
            raise ValueError('Invalid PVE identity component fields')
        result = reconcile(bundle['values']['proxmox_oidc_client_secret'], args.administrators.split(','), args.refresh_secret)
    except Exception:
        print('PVE identity reconciliation failed; private diagnostics were suppressed', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
