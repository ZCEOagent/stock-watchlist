"""Intraday session loop, slot ledger idempotency and scheduled-report guard."""
import argparse
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market_radar import session, ledger, cli, sources
from market_radar.store import Store

TZ = ZoneInfo('Asia/Taipei')
TRADING_DAY = dt.datetime(2026, 10, 19, tzinfo=TZ)     # Monday session in state/calendar.json
SUNDAY = dt.datetime(2026, 10, 18, tzinfo=TZ)


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=seconds)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.args = argparse.Namespace(state=str(root / 'state.sqlite'), output=str(root / 'reports'),
                                       price_seed=str(root / 'none.json'),
                                       maintenance_state=str(root / 'none.sqlite'), send=False)
        self.beats = []
        self.modes = []

    def test_start_refreshes_exceptional_closure_before_polling(self):
        import market_clock
        clock = FakeClock(TRADING_DAY.replace(hour=9))
        # Yesterday's restored calendar says open; a new provider calendar excludes it.
        with patch.object(market_clock, '_tw_sessions', ['2026-10-19', '2026-10-20']), \
                patch.object(session, 'refresh_tw_sessions') as refresh, \
                patch.object(session, 'default_step') as step:
            def updated():
                market_clock._tw_sessions = ['2026-10-18', '2026-10-20']
            refresh.side_effect = updated
            self.assertEqual(session.start(self.args, clock=clock, beat=self.beat), 0)
            refresh.assert_called_once_with()
            step.assert_not_called()
        self.assertEqual(self.beats[-1][0], 'closed')

    def test_calendar_refresh_failure_blocks_stale_calendar(self):
        clock = FakeClock(TRADING_DAY.replace(hour=9))
        with patch.object(session, 'refresh_tw_sessions', side_effect=RuntimeError('provider failed')), \
                patch.object(session, 'run') as run:
            self.assertEqual(session.start(self.args, clock=clock, beat=self.beat), 1)
            run.assert_not_called()
        self.assertEqual(self.beats[-1][0], 'failed')
        self.assertEqual(self.beats[-1][2]['detail'], 'calendar_refresh_failed')

    def test_start_continues_after_successful_refresh(self):
        clock = FakeClock(TRADING_DAY.replace(hour=9))
        calls = []
        with patch.object(session, 'refresh_tw_sessions', side_effect=lambda: calls.append('refresh')), \
                patch.object(session, 'run', side_effect=lambda *a, **kw: calls.append('run') or 0):
            self.assertEqual(session.start(self.args, clock=clock, beat=self.beat), 0)
        self.assertEqual(calls, ['refresh', 'run'])

    def tearDown(self):
        self.tmp.cleanup()

    def beat(self, slot, status, now=None, **fields):
        self.beats.append((status, now, fields))
        return True

    def stepper(self, clock, snapshot_ok=10, fail=False):
        def step(mode, args):
            self.modes.append((mode, clock()))
            store = Store(args.state)
            store.meta('watch-health', json.dumps(dict(checked_at=clock().isoformat(), requested=10,
                                                       snapshot_ok=snapshot_ok, valid=snapshot_ok)))
            store.close()
            if fail:
                raise ValueError('No valid watch quotes')
        return step

    def entry(self, day='2026-10-19'):
        store = Store(self.args.state)
        try:
            return ledger.get(store, day, 'intraday')
        finally:
            store.close()

    def run_session(self, clock, **kw):
        with patch.object(session.time, 'monotonic', side_effect=lambda: clock.now.timestamp()):
            return session.run(self.args, clock=clock, sleep=clock.sleep,
                               step=kw.pop('step', None) or self.stepper(clock), beat=self.beat)

    def test_full_session_polls_whole_window_and_records_completion(self):
        clock = FakeClock(TRADING_DAY.replace(hour=8, minute=47))
        self.assertEqual(self.run_session(clock), 0)
        first, last = self.modes[0][1], self.modes[-1][1]
        self.assertGreaterEqual(first.time(), session.OPEN)            # nothing before the open
        self.assertLess(first.time(), dt.time(9, 1, 30))
        self.assertGreaterEqual(last.time(), dt.time(13, 31))           # post-close review covered
        self.assertLess(last.time(), session.END)
        gaps = [(b[1] - a[1]).total_seconds() for a, b in zip(self.modes, self.modes[1:])]
        self.assertLessEqual(max(gaps), session.INTERVAL + 1)           # never a 5-hour hole
        events = [t for m, t in self.modes if m == 'events']
        self.assertEqual(len(events), 10)                               # every 30 min, 09:00-13:36
        entry = self.entry()
        self.assertEqual(entry['status'], 'completed')
        self.assertEqual(entry['iterations'], len(self.modes))
        statuses = [b[0] for b in self.beats]
        self.assertEqual((statuses[0], statuses[-1]), ('started', 'completed'))
        self.assertIn('alive', statuses)

    def test_duplicate_trigger_after_completion_does_no_work(self):
        clock = FakeClock(TRADING_DAY.replace(hour=8, minute=47))
        self.run_session(clock)
        self.modes.clear()
        self.beats.clear()
        clock2 = FakeClock(TRADING_DAY.replace(hour=12, minute=0))
        self.assertEqual(self.run_session(clock2), 0)
        self.assertEqual(self.modes, [])
        self.assertEqual(self.beats, [])

    def test_late_trigger_covers_only_remaining_session(self):
        clock = FakeClock(TRADING_DAY.replace(hour=12, minute=30))
        self.run_session(clock)
        self.assertGreaterEqual(self.modes[0][1].time(), dt.time(12, 30))
        self.assertTrue(self.beats[0][2]['late_start'])

    def test_trigger_after_session_end_is_recorded_not_polled(self):
        clock = FakeClock(TRADING_DAY.replace(hour=17, minute=0))
        self.run_session(clock)
        self.assertEqual(self.modes, [])
        self.assertEqual(self.beats[0][0], 'skipped')
        self.assertIn('late_trigger_at', self.entry())

    def test_non_trading_day_reports_closed(self):
        clock = FakeClock(SUNDAY.replace(hour=8, minute=47))
        self.run_session(clock)
        self.assertEqual(self.modes, [])
        self.assertEqual(self.beats[0][0], 'closed')
        self.assertEqual(self.entry('2026-10-18')['status'], 'closed')

    def test_crashed_run_is_taken_over_and_recorded(self):
        store = Store(self.args.state)
        ledger.claim(store, '2026-10-19', 'intraday', TRADING_DAY.replace(hour=8, minute=46))
        store.close()
        clock = FakeClock(TRADING_DAY.replace(hour=11, minute=0))
        self.run_session(clock)
        entry = self.entry()
        self.assertEqual(entry['status'], 'completed')
        self.assertEqual(entry['previous']['status'], 'started')

    def test_bad_data_raises_degraded_heartbeat_and_failed_status(self):
        clock = FakeClock(TRADING_DAY.replace(hour=13, minute=0))
        self.assertEqual(self.run_session(clock, step=self.stepper(clock, snapshot_ok=0, fail=True)), 1)
        statuses = [b[0] for b in self.beats]
        self.assertEqual(statuses.count('degraded') >= 1, True)
        self.assertEqual(statuses[-1], 'failed')
        self.assertEqual(self.entry()['status'], 'failed')

    def test_one_failed_iteration_does_not_end_session(self):
        clock = FakeClock(TRADING_DAY.replace(hour=13, minute=0))
        calls = {'n': 0}
        good = self.stepper(clock)

        def flaky(mode, args):
            calls['n'] += 1
            good(mode, args)
            if calls['n'] == 2:
                raise ConnectionError('transient')
        self.assertEqual(self.run_session(clock, step=flaky), 0)
        self.assertGreater(calls['n'], 10)
        self.assertEqual(self.entry()['quote_errors'], 1)


class PostCloseTests(SessionTests):
    def test_closing_review_does_not_raise_degraded_alarm(self):
        # After 13:30 snapshots freeze; a correct closing quote must not look like an outage.
        clock = FakeClock(TRADING_DAY.replace(hour=13, minute=20))
        def step(mode, args):
            self.modes.append((mode, clock()))
            ok = 10 if clock().time() < dt.time(13, 30) else 0     # snapshot_ok collapses post-close
            store = Store(args.state)
            store.meta('watch-health', json.dumps(dict(checked_at=clock().isoformat(), requested=10,
                                                       snapshot_ok=ok, valid=10)))
            store.close()
        self.assertEqual(self.run_session(clock, step=step), 0)
        self.assertNotIn('degraded', [b[0] for b in self.beats])
        self.assertEqual(self.entry()['status'], 'completed')


class ScheduledReportGuardTests(unittest.TestCase):
    def test_completed_daily_slot_exits_before_any_source_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.sqlite'
            today = dt.datetime.now(TZ).date().isoformat()
            store = Store(state)
            ledger.finish(store, today, 'radar-daily', dt.datetime.now(TZ))
            store.close()
            args = argparse.Namespace(mode='daily', state=str(state), output=str(Path(tmp) / 'out'),
                                      price_seed='none', send=True, delivery_slot='scheduled',
                                      maintenance_state='none', financial_limit=0)
            with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'x', 'TELEGRAM_CHAT_ID': '1'}), \
                 patch.object(sources, 'collect', side_effect=AssertionError('must not fetch')):
                cli.run(args)


if __name__ == '__main__':
    unittest.main()
