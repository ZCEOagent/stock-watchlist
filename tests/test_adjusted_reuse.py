"""Same-as_of reuse of Yahoo adjusted history; never a different trading day."""
import tempfile
import unittest
from unittest.mock import patch

import config
import radar_data

AS_OF = '2026-10-16'


def bars(day, close=100.0):
    return [{'date': day, 'open': close, 'high': close, 'low': close, 'close': close, 'volume': 1000.0}]


class AdjustedReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = patch.object(config, 'RUNTIME_CACHE_DIR', self.tmp.name)
        self.dir.start()
        self.raw = {'2330': [{'date': AS_OF, 'close': 100.0}], '2317': [{'date': AS_OF, 'close': 50.0}]}
        self.universe = [{'stock_id': '2330', 'type': 'twse'}, {'stock_id': '2317', 'type': 'twse'}]

    def tearDown(self):
        self.dir.stop()
        self.tmp.cleanup()

    def run_with(self, fetched, issues, as_of=AS_OF, raw=None, persist=True):
        with patch.object(radar_data, '_fetch_adjusted', return_value=(dict(fetched), dict(issues))):
            return radar_data.adjusted_histories(self.universe, raw or self.raw, as_of, persist=persist)

    def test_evening_rerun_in_yahoo_window_reuses_earlier_same_day_fetch(self):
        self.run_with({'2330': bars(AS_OF), '2317': bars(AS_OF, 50.0)}, {})            # 19:30 run
        result, issues = self.run_with({}, {'2330': '還原行情過期', '2317': '還原行情過期'})  # 01:30 run
        self.assertEqual(issues, {})
        self.assertEqual(result['2330'][-1]['date'], AS_OF)

    def test_never_reuses_a_different_trading_day(self):
        self.run_with({'2330': bars('2026-10-15')}, {}, as_of='2026-10-15',
                      raw={'2330': [{'date': '2026-10-15', 'close': 100.0}]})
        result, issues = self.run_with({}, {'2330': '還原行情過期', '2317': '還原行情過期'})
        self.assertEqual(result, {})
        self.assertEqual(issues['2330'], '還原行情過期')

    def test_close_mismatch_with_official_price_is_not_reused(self):
        self.run_with({'2330': bars(AS_OF, 100.0)}, {})
        raw = {'2330': [{'date': AS_OF, 'close': 103.0}], '2317': self.raw['2317']}
        result, issues = self.run_with({}, {'2330': '還原行情過期'}, raw=raw)
        self.assertNotIn('2330', result)
        self.assertIn('2330', issues)

    def test_fresh_fetch_wins_and_partial_failure_reuses_only_missing(self):
        self.run_with({'2330': bars(AS_OF, 100.0), '2317': bars(AS_OF, 50.0)}, {})
        fresh = bars(AS_OF, 100.2)
        result, issues = self.run_with({'2330': fresh}, {'2317': '還原行情過期'})
        self.assertIs(result['2330'], fresh)
        self.assertEqual(result['2317'][-1]['close'], 50.0)
        self.assertEqual(issues, {})

    def test_test_runs_do_not_write_the_shared_cache(self):
        self.run_with({'2330': bars(AS_OF)}, {}, persist=False)
        result, issues = self.run_with({}, {'2330': '還原行情過期'})
        self.assertIn('2330', issues)


if __name__ == '__main__':
    unittest.main()
