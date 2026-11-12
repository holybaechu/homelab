from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('health', Path(__file__).resolve().parents[2] / 'kubernetes/backup/health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


def snapshot(timestamp, tags=('complete',)):
    return {'time': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), 'tags': tags}


class Health(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = 1800000000
        self.state = self.root / 'health.json'

    def test_freshness_requires_a_recent_complete_snapshot(self):
        for values, expected in [([], 'stale'), ([snapshot(self.now, ('pending',))], 'stale'),
                                 ([snapshot(self.now - 86400)], 'stale'),
                                 ([snapshot(self.now - 86399)], 'fresh'),
                                 ([snapshot(self.now - 90000), snapshot(self.now - 1)], 'fresh')]:
            with self.subTest(values=values):
                self.assertEqual(health.freshness(values, self.now), expected)
        for value in [snapshot(self.now + 301), {'time': 'bad', 'tags': ['complete']},
                      {'time': '2026-11-13T04:00:00', 'tags': ['complete']}]:
            with self.assertRaises(ValueError):
                health.freshness([value], self.now)

    def test_notification_requires_actual_direct_discord_delivery(self):
        token = self.root / 'token'
        token.write_text('synthetic-token')
        for status, result, delivered in [
                (200, {'status': 'delivered', 'target': 'discord'}, True),
                (202, {'status': 'accepted', 'target': 'discord'}, False),
                (200, {'status': 'duplicate', 'target': 'discord'}, False),
                (200, {'status': 'delivered', 'target': 'log'}, False),
                (302, {'status': 'delivered', 'target': 'discord'}, False),
                (502, {'status': 'error'}, False)]:
            with self.subTest(status=status, result=result), patch.object(health, 'TOKEN', token), \
                 patch.object(health, 'HTTPConnection') as connect:
                client = connect.return_value
                client.getresponse.return_value.status = status
                client.getresponse.return_value.read.return_value = json.dumps(result).encode()
                if delivered:
                    health.notify('test')
                else:
                    with self.assertRaises(RuntimeError):
                        health.notify('test')
                client.request.assert_called_once()
                self.assertEqual(client.request.call_args.args, ('POST', '/webhooks/backup'))
                self.assertEqual(client.request.call_args.kwargs['headers']['X-Gitlab-Token'], 'synthetic-token')
                client.close.assert_called_once()

    def test_alert_is_deduplicated_until_recovery_or_one_day(self):
        with patch.object(health, 'STATE', self.state), patch.object(health.time, 'time', return_value=self.now), \
             patch.object(health, 'snapshots', return_value=[]) as snapshots, \
             patch.object(health, 'notify') as notify, redirect_stdout(StringIO()):
            self.assertEqual(health.check(), 1)
            self.assertEqual(health.check(), 1)
            notify.assert_called_once_with('stale')
            with patch.object(health.time, 'time', return_value=self.now + 86400):
                self.assertEqual(health.check(), 1)
            self.assertEqual(notify.call_count, 2)
            snapshots.return_value = [snapshot(self.now)]
            self.assertEqual(health.check(), 0)
            snapshots.return_value = []
            self.assertEqual(health.check(), 1)
            self.assertEqual(notify.call_count, 3)

    def test_failed_delivery_does_not_suppress_the_next_attempt(self):
        with patch.object(health, 'STATE', self.state), patch.object(health.time, 'time', return_value=self.now), \
             patch.object(health, 'snapshots', return_value=[]), \
             patch.object(health, 'notify', side_effect=RuntimeError('synthetic delivery failure')):
            with self.assertRaises(RuntimeError):
                health.check()
        self.assertFalse(self.state.exists())

    def test_unreadable_repository_is_an_alert_not_freshness_success(self):
        output = StringIO()
        with patch.object(health, 'STATE', self.state), patch.object(health.time, 'time', return_value=self.now), \
             patch.object(health, 'snapshots', side_effect=RuntimeError('synthetic-private-native-error')), \
             patch.object(health, 'notify') as notify, redirect_stdout(output):
            self.assertEqual(health.check(), 1)
        notify.assert_called_once_with('unavailable')
        self.assertNotIn('synthetic-private-native-error', output.getvalue())
