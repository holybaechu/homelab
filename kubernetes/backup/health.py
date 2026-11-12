#!/usr/bin/env python3
"""Check B2 recovery-point freshness and use Hermes's native direct delivery."""
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from http.client import HTTPConnection

STATE = Path('/stage/health.json')
TOKEN = Path('/notifications/hermes-token')
MESSAGES = {
    'backup-failed': 'Backblaze 백업 작업이 실패했습니다. backup-state와 Job 상태를 확인하고, 중지된 앱의 복구 상태를 먼저 확인하세요. 불완전한 스냅샷은 복원에 사용하지 마세요.',
    'maintenance-failed': 'Backblaze 백업 저장소 유지보수가 실패했습니다. backup-state와 backup-maintenance Job 상태를 확인하세요. 강제 잠금 해제나 저장소 재초기화는 하지 마세요.',
    'stale': 'Backblaze에 최근 24시간 이내의 complete 백업이 없습니다. backup-daily Job과 B2 연결 상태를 확인하세요.',
    'unavailable': 'Backblaze 백업 저장소를 확인하지 못했습니다. 최신 백업 여부를 확인할 수 없으므로 B2 인증·연결과 backup-health Job 상태를 확인하세요.',
    'test': 'Backblaze 백업 알림 연결 시험입니다. 실제 백업 실패를 뜻하지 않습니다.',
}


def notify(kind):
    connection = HTTPConnection('hermes.homelab.svc.cluster.local', 8644, timeout=10)
    try:
        connection.request('POST', '/webhooks/backup',
                           body=json.dumps({'event_type': 'backup.alert', 'message': MESSAGES[kind]}).encode(),
                           headers={'Content-Type': 'application/json', 'X-Gitlab-Token': TOKEN.read_text()})
        response = connection.getresponse()
        result = json.loads(response.read(4096))
        if response.status != 200 or result.get('status') != 'delivered' or result.get('target') != 'discord':
            raise RuntimeError('Backup notification was not delivered')
    finally:
        connection.close()


def freshness(snapshots, now):
    times = [datetime.fromisoformat(snapshot['time'])
             for snapshot in snapshots if 'complete' in snapshot.get('tags', [])]
    if any(value.tzinfo is None for value in times):
        raise ValueError('Backup snapshot time has no timezone')
    timestamps = [value.timestamp() for value in times]
    if not timestamps:
        return 'stale'
    age = now - max(timestamps)
    if age < -300:
        raise ValueError('Backup snapshot time is in the future')
    return 'fresh' if age < 86400 else 'stale'


def snapshots():
    from worker import restic
    return json.loads(restic('b2', ['--no-cache', '--no-lock', 'snapshots', '--tag', 'complete', '--json'], timeout=60))


def check():
    now = time.time()
    try:
        status = freshness(snapshots(), now)
    except Exception:
        status = 'unavailable'
    # Only the health CronJob owns this state; capture/maintenance use a different lock.
    with STATE.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = json.loads(STATE.read_text()) if STATE.exists() else {}
        if status == 'fresh':
            current = {'status': status}
        elif previous.get('status') == status and now - previous.get('notified', 0) < 86400:
            current = previous
        else:
            notify(status)
            current = {'status': status, 'notified': now}
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(current))
        temporary.replace(STATE)
    print('B2 backup freshness: ' + status)
    return 0 if status == 'fresh' else 1


if __name__ == '__main__':
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['test']:
            notify('test')
        elif not sys.argv[1:]:
            sys.exit(check())
        else:
            raise ValueError('Unknown health operation')
    except Exception:
        print('Backup health/notification check failed', file=sys.stderr)
        sys.exit(1)
