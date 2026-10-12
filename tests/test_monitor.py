import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_radar import monitor, notify
from market_radar.store import Store


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 23, 12, tzinfo=dt.timezone.utc)
        self.snapshot = {'scanned': 1900, 'fetched_at': self.now.isoformat(),
                         'price_dates': ['2026-09-23'], 'health': [{'ok': True}],
                         'completion': {'financials': {'eligible': 1800, 'verified': 450, 'pending': 1350}}}
        self.runs = {name: [{'id': 1, 'created_at': self.now.isoformat(),
                            'status': 'completed', 'conclusion': 'success'}] for name in monitor.WORKFLOWS}

    def current(self):
        return monitor.assess(self.snapshot, {'count': 4, 'uncertain': 0}, self.runs, self.now)

    def test_success_does_not_mean_backfill_complete(self):
        current = self.current()
        self.assertEqual(current['financials']['pending'], 1350)
        self.assertEqual(current['issues'], [])
        text = monitor.render(current, ['handoff'], 'owner/repo')
        self.assertIn('尚未列入可核實財報範圍：100', text)

    def test_no_repeated_normal_notification(self):
        previous = self.current()
        self.assertEqual(monitor.transition(previous, None, self.now), ['雲端追蹤已接手'])
        self.assertEqual(monitor.transition(self.current(), previous, self.now), [])

    def test_complete_only_with_positive_eligible_and_zero_pending(self):
        previous = self.current()
        self.snapshot['completion']['financials'] = {'eligible': 1800, 'verified': 1800, 'pending': 0}
        self.assertTrue(any('補齊' in r for r in monitor.transition(self.current(), previous, self.now)))
        self.snapshot['completion']['financials'] = {'eligible': 0, 'verified': 0, 'pending': 0}
        self.assertFalse(any('補齊' in r for r in monitor.transition(self.current(), previous, self.now)))

    def test_running_does_not_report_success_or_restart_scan(self):
        self.runs['daily-tw.yml'].insert(0, {'id': 2, 'status': 'in_progress',
                                            'created_at': self.now.isoformat()})
        current = self.current()
        self.assertEqual(current['issues'], [])
        self.assertEqual(current['active'][0]['id'], 2)

    def test_running_retry_does_not_hide_failure(self):
        self.runs['daily-tw.yml'][0]['conclusion'] = 'failure'
        self.runs['daily-tw.yml'].insert(0, {'id': 2, 'status': 'queued', 'created_at': self.now.isoformat()})
        self.assertTrue(any('未成功' in x for x in self.current()['issues']))

    def test_cancelled_manual_validation_does_not_replace_last_production_result(self):
        self.runs['daily-tw.yml'].insert(0,{'id':2,'status':'completed','event':'workflow_dispatch',
                                           'conclusion':'cancelled','created_at':self.now.isoformat()})
        self.assertEqual(self.current()['issues'],[])
        self.runs['daily-tw.yml'][0]['event']='schedule'
        self.assertTrue(any('未成功' in x for x in self.current()['issues']))

    def test_stale_missing_and_uncertain_are_not_healthy(self):
        self.snapshot['fetched_at'] = (self.now-dt.timedelta(days=3)).isoformat()
        current = monitor.assess(self.snapshot, {'uncertain': 1}, {}, self.now)
        self.assertEqual(len(current['issues']), len(monitor.WORKFLOWS)+2)
        missing = monitor.assess(None, {}, self.runs, self.now)
        self.assertIn('找不到可讀取的市場快照', missing['issues'])

    def test_stalled_notification_only_once(self):
        previous = self.current()
        previous['progress_since'] = (self.now-dt.timedelta(hours=49)).isoformat()
        current = self.current()
        self.assertTrue(any('未進展' in x for x in monitor.transition(current, previous, self.now)))
        self.assertEqual(monitor.transition(self.current(), current, self.now), [])

    def test_maintenance_completion_is_reported_without_waiting_for_daily(self):
        previous=self.current()
        current=self.current()
        maintenance={'fetched_at':(self.now+dt.timedelta(minutes=1)).isoformat(),
                     'financials':{'eligible':1800,'verified':1800,'pending':0}}
        monitor.include_maintenance(current,maintenance,self.now+dt.timedelta(minutes=2))
        self.assertTrue(any('補齊' in x for x in monitor.transition(current,previous,self.now)))
        older=dict(maintenance,fetched_at=(self.now-dt.timedelta(hours=7)).isoformat())
        current=self.current()
        monitor.include_maintenance(current,older,self.now)
        self.assertEqual(current['financials']['pending'],1350)
        self.assertTrue(any('6 小時' in x for x in current['issues']))

    def test_unknown_delivery_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'monitor.sqlite')
            with patch.object(notify, 'call', side_effect=notify.DeliveryUncertain('timeout')) as call:
                with self.assertRaises(notify.DeliveryUncertain):
                    notify.deliver(store, 'token', 'chat', 'key', 'text', self.now.isoformat())
                with self.assertRaises(notify.DeliveryUncertain):
                    notify.deliver(store, 'token', 'chat', 'key', 'text', self.now.isoformat())
                self.assertEqual(call.call_count, 1)
            store.close()

    def test_read_snapshot_never_creates_missing_database(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'missing.sqlite'
            with self.assertRaises(Exception):
                monitor.read_snapshot(target)
            self.assertFalse(target.exists())


class QuoteCoverageTests(unittest.TestCase):
    def test_after_close_success_does_not_prove_intraday_coverage(self):
        now=dt.datetime.fromisoformat('2026-10-12T19:00:00+08:00')
        health={'checked_at':'2026-10-12T14:50:24+08:00','valid':39,'requested':39}
        self.assertTrue(monitor.quote_coverage_issues(health,now))
    def test_holiday_or_previous_day_does_not_create_false_intraday_alarm(self):
        now=dt.datetime.fromisoformat('2026-10-11T19:00:00+08:00')
        self.assertEqual(monitor.quote_coverage_issues({'checked_at':'2026-10-08T13:27:00+08:00'},now),[])
    def test_long_intraday_gap_remains_visible_after_close(self):
        now=dt.datetime.fromisoformat('2026-10-12T19:00:00+08:00')
        health={'checked_at':'2026-10-12T14:50:00+08:00','intraday':{'checks':3,'last_at':'2026-10-12T13:27:00+08:00','max_gap_seconds':1800}}
        self.assertTrue(monitor.quote_coverage_issues(health,now))
        health['intraday']['max_gap_seconds']=300
        self.assertEqual(monitor.quote_coverage_issues(health,now),[])


if __name__ == '__main__':
    unittest.main()
