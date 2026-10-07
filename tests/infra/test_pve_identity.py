import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from tests.helpers import REPO_ROOT, task_enabled

HELPER = REPO_ROOT / 'infra/ansible/roles/pve_identity/files/reconcile_identity.py'
spec = importlib.util.spec_from_file_location('pve_identity_reconcile', HELPER)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)

class PVE:
    def __init__(self):
        self.realms = [{'realm': 'pam', 'type': 'pam', 'default': 1}, {'realm': 'pve', 'type': 'pve'}]
        self.users = [{'userid': 'root@pam', 'enable': 1}]
        self.acls = [{'type': 'user', 'ugid': 'root@pam', 'roleid': 'Administrator', 'path': '/', 'propagate': 1}]
        self.calls = []

    def run(self, argv):
        self.calls.append(argv)
        if argv[:3] == ['pveum', 'realm', 'list']:
            return copy.deepcopy(self.realms)
        if argv[:3] == ['pveum', 'user', 'list']:
            return copy.deepcopy(self.users)
        if argv[:3] == ['pveum', 'acl', 'list']:
            return copy.deepcopy(self.acls)
        if argv[:3] == ['pvesh', 'get', '/access/domains/authentik']:
            return copy.deepcopy(next(row for row in self.realms if row['realm'] == 'authentik'))
        values = dict(zip(argv[4::2], argv[5::2]))
        if argv[1] == 'realm':
            self.realms = [row for row in self.realms if row['realm'] != 'authentik'] + [
                {'realm': 'authentik', **{key.removeprefix('--'): int(value) if key in ('--autocreate', '--default') else value for key, value in values.items()}}
            ]
        elif argv[1] == 'user':
            self.users = [row for row in self.users if row['userid'] != argv[3]] + [{'userid': argv[3], 'enable': 1}]
        elif argv[2] == 'delete':
            self.acls = [row for row in self.acls if row['ugid'] != values['--users'] or row['roleid'] != values['--roles'] or row['path'] != argv[3]]
        else:
            self.acls.append({'type': 'user', 'ugid': values['--users'], 'roleid': values['--roles'], 'path': argv[3], 'propagate': 1})

def test_sso_reconcile_is_idempotent_and_preserves_local_recovery():
    pve = PVE()
    recovery = copy.deepcopy((pve.realms, pve.users, pve.acls))
    assert identity.reconcile('a' * 64, ['akadmin'], False, pve.run, False)['changed']
    assert not identity.reconcile('a' * 64, ['akadmin'], False, pve.run, False)['changed']
    assert pve.realms[:2] == recovery[0]
    assert pve.users[0] == recovery[1][0] and pve.acls[0] == recovery[2][0]
    assert 'a' * 64 not in json.dumps(identity.reconcile('a' * 64, ['akadmin'], False, pve.run, False))
    assert next(row for row in pve.realms if row['realm'] == 'authentik')['default'] == 0

def test_removing_a_declared_sso_admin_revokes_only_the_owned_admin_grant():
    pve = PVE()
    identity.reconcile('a' * 64, ['akadmin', 'retired'], False, pve.run, False)
    identity.reconcile('a' * 64, ['akadmin'], False, pve.run, False)
    assert all(row['ugid'] != 'retired@authentik' for row in pve.acls)
    assert any(row['ugid'] == 'root@pam' for row in pve.acls)

def test_unavailable_local_recovery_stops_before_any_identity_mutation():
    pve = PVE()
    pve.users[0]['enable'] = 0
    with pytest.raises(RuntimeError, match='recovery'):
        identity.reconcile('a' * 64, ['akadmin'], False, pve.run, False)
    assert all(argv[2] == 'list' for argv in pve.calls)

def test_identity_only_reconciliation_cannot_touch_storage_lxcs_or_ssh():
    plays = yaml.safe_load((REPO_ROOT / 'infra/ansible/playbooks/reconcile.yml').read_text())
    settings = {'homelab_unit': 'pve', 'pve_lxc_reconcile_mode': 'apply', 'homelab_pve_identity_only': True}
    roles = [task['ansible.builtin.include_role']['name'] for task in plays[1]['tasks']
             if 'ansible.builtin.include_role' in task and task_enabled(task, **settings)]
    assert roles == ['pve_identity']


def test_private_client_credential_is_not_exposed_in_process_arguments(monkeypatch):
    calls = []
    def process(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(identity.subprocess, 'run', process)
    identity.command(['pveum', 'realm', 'add', 'authentik', '--client-key', 'a' * 64])
    argv, options = calls[0]
    assert 'a' * 64 not in ' '.join(argv)
    assert json.loads(options['input'])[-1] == 'a' * 64
