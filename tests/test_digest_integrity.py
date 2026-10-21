"""2026-10-20 regressions: digest prices for the expected session; no cross-midnight backup."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_radar import history
from market_radar.store import Store

COMPANIES = [{'code': '2330', 'market': 'twse'}, {'code': '6274', 'market': 'tpex'}]


def rows(market, day, code):
    return [dict(code=code, market=market, date=day, source_date=day, close=100.0, volume=1000.0, source='x')]


class EnsureCurrentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 's.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_fetches_only_the_expected_session_for_both_markets(self):
        calls = []
        def fake(market, day):
            calls.append((market, day))
            return rows(market, day, '2330' if market == 'twse' else '6274')
        with patch.object(history, 'fetch_day', side_effect=fake), patch.object(history.time, 'sleep'):
            result = history.ensure_current(self.store, COMPANIES, '2026-10-20')
        self.assertEqual(sorted(calls), [('tpex', '2026-10-20'), ('twse', '2026-10-20')])
        self.assertEqual(result['fetched_days'], 2)
        self.assertEqual(self.store.history('price', '2330')[-1]['date'], '2026-10-20')

    def test_already_stored_session_is_not_refetched(self):
        self.store.meta('history:twse:2026-10-20', 'ok')
        self.store.meta('history:tpex:2026-10-20', 'ok')
        with patch.object(history, 'fetch_day', side_effect=AssertionError('no request')):
            self.assertEqual(history.ensure_current(self.store, COMPANIES, '2026-10-20')['fetched_days'], 0)

    def test_unpublished_session_is_reported_and_nothing_is_relabeled(self):
        self.store.ingest({('twse', 'price'): rows('twse', '2026-10-19', '2330')})
        with patch.object(history, 'fetch_day', side_effect=RuntimeError('not yet')), patch.object(history.time, 'sleep'):
            result = history.ensure_current(self.store, COMPANIES, '2026-10-20')
        self.assertEqual(result['failed_days'], 2)
        self.assertEqual([p['date'] for p in self.store.history('price', '2330')], ['2026-10-19'])


class WorkflowScheduleTests(unittest.TestCase):
    def test_no_backup_cron_can_cross_midnight_into_the_next_digest(self):
        text = (Path(__file__).resolve().parent.parent / '.github/workflows/market-radar.yml').read_text(encoding='utf-8')
        crons = [line.split("'")[1] for line in text.splitlines() if line.strip().startswith('- cron:')]
        # GitHub delays were up to 8.5 h; any backup must start before 15:30 UTC (23:30 Taipei - 8 h).
        for cron in crons:
            minute, hour = cron.split()[:2]
            self.assertLess(int(hour) * 60 + int(minute), 8 * 60, cron)

    def test_close_reports_have_no_github_schedule(self):
        root = Path(__file__).resolve().parent.parent / '.github/workflows'
        for name in ('daily-tw.yml', 'daily-us.yml'):
            self.assertNotIn('- cron:', (root / name).read_text(encoding='utf-8'), name)


if __name__ == '__main__':
    unittest.main()
