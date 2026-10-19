import datetime as dt
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import requests

from market_radar import heartbeat

NOW = dt.datetime(2026, 10, 20, 8, 47, tzinfo=ZoneInfo('Asia/Taipei'))
ENV = {'HEARTBEAT_URL': 'https://w.example/', 'HEARTBEAT_TOKEN': 'secret', 'RADAR_DISPATCH_ID': '2026-10-20:intraday:1',
       'GITHUB_REPOSITORY': 'o/r', 'GITHUB_RUN_ID': '42', 'RADAR_TRIGGER': 'scheduler'}


class HeartbeatTests(unittest.TestCase):
    def test_posts_authenticated_public_payload(self):
        post = Mock(return_value=Mock(status_code=200))
        with patch.dict('os.environ', ENV, clear=True):
            self.assertTrue(heartbeat.send('intraday', 'started', NOW, post=post, valid_ratio=0.9))
        url, = post.call_args.args
        kwargs = post.call_args.kwargs
        self.assertEqual(url, 'https://w.example/heartbeat')
        self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer secret'})
        body = kwargs['json']
        self.assertEqual((body['slot'], body['status'], body['day'], body['dispatch_id']),
                         ('intraday', 'started', '2026-10-20', '2026-10-20:intraday:1'))
        self.assertEqual(body['run_url'], 'https://github.com/o/r/actions/runs/42')
        self.assertNotIn('secret', str(body))

    def test_missing_configuration_is_reported_not_fatal(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(heartbeat.send('intraday', 'started', NOW, post=Mock()))

    def test_retries_then_gives_up_without_raising(self):
        post = Mock(side_effect=requests.ConnectionError('down'))
        sleeps = []
        with patch.dict('os.environ', ENV, clear=True):
            self.assertFalse(heartbeat.send('tw-close', 'failed', NOW, post=post, sleep=sleeps.append))
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleeps, [1, 3])

    def test_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            heartbeat.send('intraday', 'ok', NOW, post=Mock())


if __name__ == '__main__':
    unittest.main()
