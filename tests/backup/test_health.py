from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from test_backup import load
health = load('health')


def at(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class Health(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'health.json'

    def job(self):
        return {'status': {'startTime': at(self.now - 200), 'completionTime': at(self.now - 10)}}

    def snapshot(self, **changes):
        return dict(time=at(self.now - 100), tags=['hermes'], paths=['/homelab-capture.tar'], **changes)

    def check(self, issues, notify):
        with patch.object(health, 'STATE', self.state), patch.object(health.time, 'time', return_value=self.now), \
             patch.object(health, 'collect', return_value=issues), patch.object(health, 'notify', notify), redirect_stdout(StringIO()):
            return health.check()

    def test_stream_requires_matching_successful_job(self):
        snapshot = self.snapshot()
        self.assertEqual(health.freshness([snapshot], [self.job()], 'hermes', 'homelab', self.now), 'fresh')
        self.assertEqual(health.freshness([snapshot], [], 'hermes', 'homelab', self.now), 'stale')
        snapshot['time'] = at(self.now - 1000)
        self.assertEqual(health.freshness([snapshot], [self.job()], 'hermes', 'homelab', self.now), 'stale')

    def test_receipt_alone_is_never_a_data_restore_point(self):
        snapshot = self.snapshot(); snapshot.update(tags=['opencloud'], paths=['/homelab-capture.json'])
        self.assertEqual(health.freshness([snapshot], [self.job()], 'opencloud', 'homelab', self.now), 'stale')
        snapshot['tags'].append('data')
        self.assertEqual(health.freshness([snapshot], [self.job()], 'opencloud', 'homelab', self.now), 'fresh')

    def test_old_missing_or_future_snapshots_are_not_healthy(self):
        snapshot = self.snapshot(); snapshot['time'] = at(self.now - 86401)
        self.assertEqual(health.freshness([snapshot], [self.job()], 'hermes', 'homelab', self.now), 'stale')
        self.assertEqual(health.freshness([], [self.job()], 'hermes', 'homelab', self.now), 'stale')
        snapshot['time'] = at(self.now + 301)
        with self.assertRaises(ValueError):health.freshness([snapshot], [self.job()], 'hermes', 'homelab', self.now)

    def test_success_requires_both_cr_and_native_job_owned_by_its_uid(self):
        backup = {'metadata': {'uid': 'backup1'}, 'status': {'conditions': [{'type': 'Completed', 'status': 'True', 'reason': 'Succeeded'}]}}
        job = self.job(); job['metadata'] = {'ownerReferences': [{'kind': 'Backup', 'uid': 'backup1'}]}
        job['status']['conditions'] = [{'type': 'Complete', 'status': 'True'}]
        self.assertEqual(health.successful_jobs([backup], [job]), [job])
        backup['status']['conditions'][0]['reason'] = 'Failed'
        self.assertEqual(health.successful_jobs([backup], [job]), [])

    def test_prebackup_failure_is_a_failure_without_native_job(self):
        self.assertEqual(health.result({'status': {'conditions': [{'type': 'PreBackupPodReady', 'status': 'False', 'reason': 'CreationFailed'}]}}), 'failed')

    def test_only_delivered_warnings_are_deduplicated_and_recovery_resets(self):
        from unittest.mock import Mock
        notify = Mock()
        self.assertEqual(self.check({'hermes': 'stale'}, notify), 1)
        self.assertEqual(self.check({'hermes': 'stale'}, notify), 1)
        notify.assert_called_once_with('stale', 'hermes')
        self.assertEqual(self.check({}, notify), 0)
        self.check({'hermes': 'stale'}, notify)
        self.assertEqual(notify.call_count, 2)

    def test_delivery_failure_does_not_suppress_retry(self):
        from unittest.mock import Mock
        with self.assertRaises(RuntimeError):self.check({'hermes': 'stale'}, Mock(side_effect=RuntimeError('synthetic failure')))
        self.assertFalse(self.state.exists())

    def test_actual_discord_delivery_response_is_required(self):
        for code, payload, delivered in [(200, {'status': 'delivered', 'target': 'discord'}, True), (202, {'status': 'accepted'}, False), (200, {'status': 'duplicate'}, False), (200, {'status': 'delivered', 'target': 'other'}, False)]:
            with self.subTest(payload=payload), patch.object(health, 'HTTPConnection') as connection, patch.object(health, 'TOKEN') as token:
                token.read_text.return_value = 'synthetic-authentication'
                response = connection.return_value.getresponse.return_value
                response.status = code; response.read.return_value = json.dumps(payload).encode()
                if delivered:health.notify('test')
                else:
                    with self.assertRaises(RuntimeError):health.notify('test')

    def test_unreadable_api_alerts_each_namespace_without_false_freshness(self):
        with patch.object(health, 'kube', side_effect=RuntimeError('private diagnostic')):
            issues = health.collect(self.now)
        self.assertEqual(issues, {namespace: 'unavailable' for namespace in health.APPS})

    def test_missing_schedule_alerts_expected_app_coverage(self):
        with patch.object(health, 'kube', return_value=[]):issues = health.collect(self.now)
        self.assertEqual(issues, {app: 'stale' for apps in health.APPS.values() for app in apps})
