#!/usr/bin/env python3
"""Coordinate local captures; persist recovery intent before changing replicas."""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from typing import Any

NAMESPACES = ('homelab', 'identity', 'networking')
GROUPS = (
    ('opencloud', [('homelab', 'opencloud'), ('homelab', 'qbittorrent')]),
    ('immich', [('homelab', 'immich')]),
    ('n8n', [('homelab', 'n8n')]),
    ('zipline', [('homelab', 'zipline')]),
    ('authentik', [('identity', 'authentik-server'), ('identity', 'authentik-worker')]),
    ('headscale', [('identity', 'headscale'), ('networking', 'headscale-gateway')]),
)


def kube(*args, output=False, timeout=20) -> Any:
    result = subprocess.run(['kubectl', '--request-timeout=10s', *args],
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Kubernetes operation failed')
    return json.loads(result.stdout) if output else None


class Journal:
    def read(self):
        self.obj = kube('-n', 'homelab', 'get', 'configmap', 'backup-state', '-o', 'json', output=True)
        return json.loads(self.obj['data']['state'])

    def write(self, state):
        obj = dict(self.obj)
        obj['data'] = {'state': json.dumps(state)}
        result = subprocess.run(['kubectl', '--request-timeout=10s', 'replace', '-f', '-'],
                                input=json.dumps(obj).encode(), capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError('Backup journal conflict or API unavailable')
        self.obj = json.loads(result.stdout) if result.stdout.startswith(b'{') else self.obj
        self.read()


def worker(namespace, *args, timeout: float = 3600):
    result = subprocess.run(['kubectl', '--request-timeout=0', '-n', namespace, 'exec',
                             'backup-worker-0', '-c', 'worker', '--', 'python3',
                             '/backup/worker.py', *map(str, args)],
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{namespace} backup worker failed ({args[0]})')
    return result.stdout.decode().strip()


def collect(namespace):
    from pathlib import Path
    import shutil
    target = Path('/stage/capture') / namespace
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(mode=0o700)
    producer = subprocess.Popen(['kubectl', '--request-timeout=0', '-n', namespace, 'exec',
                                 'backup-worker-0', '-c', 'worker', '--', 'tar',
                                 '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner', '-cf', '-',
                                 '-C', '/stage/capture', '.'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        consumer = subprocess.run(['tar', '--xattrs', '--xattrs-include=*', '--acls', '--numeric-owner', '-xf', '-',
                                   '-C', str(target)], stdin=producer.stdout,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
        assert producer.stdout is not None
        producer.stdout.close()
        if producer.wait(timeout=20) or consumer.returncode:
            raise RuntimeError('Namespace staging transfer failed; refusing publication')
    finally:
        if producer.poll() is None:
            producer.kill()
            producer.wait()


def scale(namespace, name, replicas):
    kube('-n', namespace, 'scale', 'deployment', name, f'--replicas={replicas}')


def controls_for(targets):
    layers = {'homelab': 'apps', 'identity': 'identity', 'networking': 'access'}
    controls = [('flux-system', 'kustomization.kustomize.toolkit.fluxcd.io', layer)
                for layer in sorted({layers[ns] for ns, _ in targets})]
    if ('identity', 'authentik-server') in targets:
        controls.append(('identity', 'helmrelease.helm.toolkit.fluxcd.io', 'authentik'))
    result = []
    for ns, kind, name in controls:
        obj = kube('-n', ns, 'get', kind, name, '-o', 'json', output=True)
        result.append([ns, kind, name, obj['spec'].get('suspend', False)])
    return result


def suspend(control, value):
    ns, kind, name, _ = control
    kube('-n', ns, 'patch', kind, name, '--type=merge', '-p', json.dumps({'spec': {'suspend': value}}))


def transition(journal, run, phase, state):
    current = journal.read()
    if current.get('run') != run or current['phase'] != phase:
        raise RuntimeError('Backup ownership or phase changed')
    journal.write(state)


def restore(journal, state, *, resume=False):
    # Recovery invalidates capture; normal resumption retains the global owner.
    if journal.read() != state:
        raise RuntimeError('Backup recovery intent changed')
    state = dict(state, phase='resuming' if resume else 'recovering')
    journal.write(state)
    failures = []
    for namespace, name, replicas in state['replicas']:
        try:
            scale(namespace, name, replicas)
        except Exception:
            failures.append(name)
    if failures:
        raise RuntimeError('Replica restoration pending; watchdog will retry')
    for control in state.get('controls', []):
        suspend(control, control[3])
    if not resume:
        transition(journal, state['run'], 'recovering',
                   dict(state, phase='aborted', replicas=[], controls=[]))


def assert_capture(journal, run):
    state = journal.read()
    if state.get('run') != run or state['phase'] != 'capture' or time.time() >= state['deadline']:
        raise RuntimeError('Capture expired or recovery started')
    return state


def wait_stopped(targets, deadline, journal, run):
    while time.time() < deadline:
        assert_capture(journal, run)
        stopped = True
        for namespace, name in targets:
            d = kube('-n', namespace, 'get', 'deployment', name, '-o', 'json', output=True)
            # status.replicas may go to zero before terminating pods have exited.
            selector = ','.join(f'{k}={v}' for k, v in d['spec']['selector']['matchLabels'].items())
            pods = kube('-n', namespace, 'get', 'pods', '-l', selector, '-o', 'json', output=True)
            stopped &= d['spec']['replicas'] == 0 and not pods['items']
        if stopped:
            return
        time.sleep(2)
    raise TimeoutError('Writers did not stop within capture budget')


def capture(journal, run, group, targets):
    replicas = []
    for namespace, name in targets:
        d = kube('-n', namespace, 'get', 'deployment', name, '-o', 'json', output=True)
        replicas.append([namespace, name, d['spec']['replicas']])
    state: dict[str, Any] = dict(run=run, phase='capture', deadline=time.time() + 420, replicas=replicas,
                 controls=controls_for(targets))
    transition(journal, run, 'preparing', state)
    try:
        for control in state['controls']:
            suspend(control, True)
            ns, kind, name, _ = control
            obj = kube('-n', ns, 'get', kind, name, '-o', 'json', output=True)
            if any(c['type'] == 'Reconciling' and c['status'] == 'True'
                   for c in obj.get('status', {}).get('conditions', [])):
                raise RuntimeError('GitOps reconciliation in progress; capture refused')
        for namespace, name in targets:
            assert_capture(journal, run)
            if time.time() >= state['deadline'] - 60:
                raise TimeoutError('Insufficient budget to issue another stop safely')
            scale(namespace, name, 0)
        wait_stopped(targets, state['deadline'] - 30, journal, run)
        namespaces = sorted({ns for ns, _ in targets})
        for namespace in namespaces:
            assert_capture(journal, run)
            worker(namespace, 'capture', run, group, state['deadline'] - 30,
                   timeout=max(1, state['deadline'] - time.time() - 20))
        assert_capture(journal, run)
    except BaseException:
        current = journal.read()
        if current.get('run') == run and current['phase'] == 'capture' and current.get('replicas'):
            restore(journal, current)
        raise
    # Restore the original counts before any upload, even if originally zero.
    restore(journal, assert_capture(journal, run), resume=True)
    ready_deadline = min(time.time() + 90, state['deadline'] + 180)
    while True:
        current = journal.read()
        if current.get('run') != run or current['phase'] != 'resuming':
            raise RuntimeError('Capture recovery started during readiness')
        ready = True
        for namespace, name, count in replicas:
            if not count:
                continue
            obj = kube('-n', namespace, 'get', 'deployment', name, '-o', 'json', output=True)
            status = obj.get('status', {})
            ready &= status.get('observedGeneration', 0) >= obj['metadata']['generation'] \
                and status.get('readyReplicas', 0) >= count
        if ready:
            break
        if time.time() >= ready_deadline:
            raise TimeoutError('Apps did not become Ready within resumption budget')
        time.sleep(2)
    transition(journal, run, 'resuming', dict(run=run, phase='preparing', replicas=[]))


def backup():
    watchdog = kube('-n', 'homelab', 'get', 'deployment', 'backup-watchdog', '-o', 'json', output=True)
    if watchdog.get('status', {}).get('readyReplicas', 0) != 1:
        raise RuntimeError('Recovery watchdog is not Ready')
    journal = Journal()
    state = journal.read()
    if state.get('replicas') or state['phase'] not in ('idle', 'aborted', 'done', 'failed'):
        raise RuntimeError('Previous backup is active or requires recovery; refusing overlap')
    run = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    journal.write(dict(run=run, phase='preparing', replicas=[]))
    try:
        for ns in NAMESPACES:
            worker(ns, 'prepare', run)
        for group, targets in GROUPS:
            capture(journal, run, group, targets)
        worker('homelab', 'capture', run, 'hermes', time.time() + 3500)
        for ns in NAMESPACES:
            worker(ns, 'seal', run)
        transition(journal, run, 'preparing', dict(run=run, phase='upload', replicas=[]))
        failures = []
        for ns in ('identity', 'networking'):
            collect(ns)
        for destination in ('b2', 'ovh'):
            try:
                snapshot = worker('homelab', 'upload', run, destination, timeout=43200)
                worker('homelab', 'publish', run, destination, snapshot)
            except Exception:
                failures.append(destination)
        if failures:
            raise RuntimeError('One or more repositories did not complete')
        transition(journal, run, 'upload', dict(run=run, phase='done', replicas=[]))
    except BaseException:
        state = journal.read()
        if state.get('run') == run and state['phase'] in ('capture', 'resuming'):
            restore(journal, state)
        elif state.get('run') == run and state['phase'] in ('preparing', 'upload'):
            transition(journal, run, state['phase'], dict(state, phase='failed'))
        raise


def watchdog():
    while True:
        try:
            journal = Journal()
            state = journal.read()
            if state.get('replicas') and (state['phase'] == 'recovering' or time.time() >= state['deadline']):
                restore(journal, state)
                print('Expired capture aborted; original replicas restored', flush=True)
        except Exception:
            print('Backup recovery/API error; retrying', flush=True)
        time.sleep(2)


def maintenance():
    journal = Journal()
    state = journal.read()
    if state['phase'] not in ('idle', 'aborted', 'done', 'failed') or state.get('replicas'):
        raise RuntimeError('Backup/recovery active; maintenance refused')
    run = uuid.uuid4().hex
    journal.write(dict(run=run, phase='maintenance', replicas=[]))
    try:
        # All snapshots reside in each single shared repository, independent of namespace.
        for destination in ('b2', 'ovh'):
            worker('homelab', 'maintain', destination, timeout=43200)
    finally:
        transition(journal, run, 'maintenance', dict(phase='idle', replicas=[]))


if __name__ == '__main__':
    def interrupted(*_):
        raise InterruptedError('Backup interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        {'backup': backup, 'watchdog': watchdog, 'maintenance': maintenance}[sys.argv[1]]()
    except BaseException as error:
        print(f'Backup failed: {type(error).__name__}; inspect backup-state and Job status', file=sys.stderr)
        sys.exit(1)
