#!/usr/bin/env python3
"""Check K8up results and B2 restore points; deliver through the existing Hermes route."""
from datetime import datetime
import fcntl
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
from urllib.request import Request, urlopen

STATE = Path('/stage/health.json')
TOKEN = Path('/notifications/hermes-token')
ACCOUNT = Path('/var/run/secrets/kubernetes.io/serviceaccount')
APPS = {
    'homelab': ['opencloud', 'immich', 'n8n', 'zipline', 'hermes', 't3'],
    'identity': ['authentik', 'headscale'],
    'networking': ['headscale-gateway'],
}
OFFLINE = {'opencloud', 'n8n', 'zipline'}
MESSAGES = {
    'failed': 'K8up 백업 또는 저장소 검사가 실패했습니다. 해당 Backup/Check/Prune과 Job을 확인하세요. 정지 캡처 앱은 backup-capture-state의 복구 상태부터 확인하세요.',
    'stale': '최근 24시간 이내의 성공한 K8up 작업과 대응하는 Backblaze 복원 지점을 확인하지 못했습니다. 해당 Schedule, Job과 저장소를 확인하세요.',
    'unavailable': 'K8up 또는 Backblaze 상태 조회에 실패했습니다. 최신 백업 여부를 확인할 수 없습니다. backup-health Job과 인증·연결을 확인하세요.',
    'test': 'Backblaze 백업 알림 연결 시험입니다. 실제 백업 실패를 뜻하지 않습니다.',
}


def notify(kind, app=''):
    connection = HTTPConnection('hermes.homelab.svc.cluster.local', 8644, timeout=10)
    try:
        connection.request('POST', '/webhooks/backup',
                           body=json.dumps({'event_type': 'backup.alert', 'message': (app + ': ' if app else '') + MESSAGES[kind]}).encode(),
                           headers={'Content-Type': 'application/json', 'X-Gitlab-Token': TOKEN.read_text().strip()})
        response = connection.getresponse()
        result = json.loads(response.read(4096))
        if response.status != 200 or result.get('status') != 'delivered' or result.get('target') != 'discord':
            raise RuntimeError('Backup notification was not delivered')
    finally:
        connection.close()


def kube(group, namespace, resource):
    path = f'/apis/{group}/namespaces/{namespace}/{resource}'
    request = Request('https://kubernetes.default.svc' + path,
                      headers={'Authorization': 'Bearer ' + (ACCOUNT / 'token').read_text().strip()})
    with urlopen(request, context=ssl.create_default_context(cafile=ACCOUNT / 'ca.crt'), timeout=20) as response:
        return json.load(response)['items']


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Missing timezone')
    return parsed.timestamp()


def result(resource):
    conditions = resource.get('status', {}).get('conditions', [])
    if any(c['type'] == 'PreBackupPodReady' and c['status'] == 'False' and c.get('reason', '').endswith('Failed') for c in conditions):
        return 'failed'
    completed = next((c for c in conditions if c['type'] == 'Completed' and c['status'] == 'True'), None)
    return ('success' if completed['reason'] == 'Succeeded' else 'failed') if completed else 'active'


def owned_by(resource, kind, name):
    return any(owner['kind'] == kind and owner['name'] == name for owner in resource['metadata'].get('ownerReferences', []))


def successful_jobs(backups, jobs):
    uids = {backup['metadata']['uid'] for backup in backups if result(backup) == 'success'}
    return [job for job in jobs if any(owner['kind'] == 'Backup' and owner['uid'] in uids for owner in job['metadata'].get('ownerReferences', []))
            and any(c['type'] == 'Complete' and c['status'] == 'True' for c in job.get('status', {}).get('conditions', []))]


def freshness(snapshots, jobs, app, namespace, now):
    for snapshot in snapshots:
        if app not in snapshot.get('tags', []):
            continue
        if app in OFFLINE:
            if 'data' not in snapshot.get('tags', []):
                continue
        elif snapshot.get('paths') != [f'/{namespace}-capture.tar']:
            continue
        captured = timestamp(snapshot['time'])
        if captured > now + 300:
            raise ValueError('Future snapshot')
        if now - captured >= 86400:
            continue
        for job in jobs:
            status = job['status']
            if timestamp(status['startTime']) - 300 <= captured <= timestamp(status['completionTime']) + 300:
                return 'fresh'
    return 'stale'


def snapshots(namespace, app):
    env = dict(os.environ, AWS_DEFAULT_REGION='us-west-004',
               AWS_ACCESS_KEY_ID=Path('/repository/access-key-id').read_text().strip(),
               AWS_SECRET_ACCESS_KEY=Path('/repository/secret-access-key').read_text().strip(),
               RESTIC_PASSWORD_FILE='/repository/restic-password')
    repository = f's3:https://s3.us-west-004.backblazeb2.com/homelab-backup-b2/k8up/{namespace}/{app}'
    completed = subprocess.run(['restic', '-r', repository, '--no-cache', '--no-lock', 'snapshots', '--tag', app, '--json'],
                               env=env, check=True, timeout=90, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return json.loads(completed.stdout)


def collect(now):
    issues = {}
    for namespace, apps in APPS.items():
        try:
            schedules = kube('k8up.io/v1', namespace, 'schedules')
            resources = {kind: kube('k8up.io/v1', namespace, kind) for kind in ['backups', 'checks', 'prunes']}
            jobs = kube('batch/v1', namespace, 'jobs')
        except Exception:
            issues[namespace] = 'unavailable'
            continue
        for app in apps:
            try:
                name = 'b2-' + app
                if not any(s['metadata']['name'] == name for s in schedules):
                    issues[app] = 'stale'
                    continue
                selected = {kind: [r for r in values if owned_by(r, 'Schedule', name)] for kind, values in resources.items()}
                for kind, values in selected.items():
                    terminal = [r for r in values if result(r) != 'active']
                    latest = max(terminal, key=lambda r: timestamp(r['metadata']['creationTimestamp']), default=None)
                    stuck = any(result(r) == 'active' and now - timestamp(r['metadata']['creationTimestamp']) > 7200 for r in values)
                    if stuck or latest and result(latest) == 'failed':
                        issues[app + '/' + kind] = 'failed'
                successful = successful_jobs(selected['backups'], jobs)
                status = freshness(snapshots(namespace, app), successful, app, namespace, now)
                if status != 'fresh':
                    issues[app] = status
            except Exception:
                issues[app] = 'unavailable'
    return issues


def check():
    now = time.time()
    issues = collect(now)
    with STATE.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = json.loads(STATE.read_text()) if STATE.exists() else {}
        current = {}
        for app, kind in issues.items():
            old = previous.get(app, {})
            if old.get('kind') == kind and now - old.get('notified', 0) < 86400:
                current[app] = old
            else:
                notify(kind, app)
                current[app] = {'kind': kind, 'notified': now}
                # Persist only delivered warnings, even if a later delivery fails.
                temporary = STATE.with_suffix('.tmp')
                temporary.write_text(json.dumps({**previous, **current}))
                temporary.replace(STATE)
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(current))
        temporary.replace(STATE)
    print(f'K8up/B2 health: {len(issues)} issue(s)')
    return int(bool(issues))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['test']:
            notify('test')
        elif not sys.argv[1:]:
            sys.exit(check())
        else:
            raise ValueError('Unknown operation')
    except Exception:
        print('Backup health/notification check failed', file=sys.stderr)
        sys.exit(1)
