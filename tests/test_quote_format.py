"""MIS intraday format regression: last trade lives in the nested trade object."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market_radar import quote, watch, execution
from market_radar.store import Store

TZ = ZoneInfo('Asia/Taipei')
FIXTURE = Path(__file__).parent / 'fixtures' / 'mis-intraday-20261019.json'


def live_rows():
    return {r['c']: r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['msgArray']}


class QuoteFormatTests(unittest.TestCase):
    now = dt.datetime(2026, 10, 19, 12, 53, 24, tzinfo=TZ)

    def test_live_capture_top_level_z_dash_uses_nested_trade(self):
        row = quote.normalize(live_rows()['2330'])
        self.assertEqual(row['z'], '2410.0000')
        self.assertEqual(row['t'], '12:51:40')          # trade time, not snapshot
        self.assertEqual(row['snapshot_t'], '12:52:25')
        self.assertEqual(row['quote_reasons'], [])
        self.assertEqual(quote.classify(row, self.now), 'fresh')

    def test_live_capture_old_trade_is_stale_not_fresh(self):
        row = quote.normalize(live_rows()['6477'])   # last trade 12:42:01, 683 s old
        self.assertEqual(quote.classify(row, self.now), 'stale_trade')
        watched = {'6477': 'tse'}
        self.assertFalse(watch.valid_quote(row, self.now, watched))

    def test_old_parser_rejected_live_rows_new_parser_accepts_recent_trades(self):
        rows = live_rows()
        watched = {c: r['ex'] for c, r in rows.items()}
        raw_valid = [c for c, r in rows.items() if watch.valid_quote(r, self.now, watched)]
        fixed = [c for c, r in rows.items() if watch.valid_quote(quote.normalize(r), self.now, watched)]
        self.assertEqual(raw_valid, ['2444'])          # only the row whose snapshot was a trade
        self.assertEqual(sorted(fixed), ['2330', '2444', '6274'])

    def test_conflicting_trade_sources_are_quarantined(self):
        row = dict(live_rows()['2444'])
        row['trade'] = dict(row['trade'], z='15.0000')     # same time, different price
        normalized = quote.normalize(row)
        self.assertIn('trade_source_conflict', normalized['quote_reasons'])
        self.assertFalse(watch.valid_quote(normalized, self.now, {'2444': 'tse'}))
        self.assertEqual(quote.classify(normalized, self.now), 'invalid')

    def test_trade_after_snapshot_is_invalid(self):
        row = dict(live_rows()['2330'])
        row['trade'] = dict(row['trade'], t='12:59:00')
        self.assertIn('trade_after_snapshot', quote.normalize(row)['quote_reasons'])

    def test_stale_snapshot_rejected_even_with_recent_looking_trade(self):
        row = quote.normalize(live_rows()['2330'])
        later = self.now + dt.timedelta(minutes=2)       # snapshot 12:52:25 now 179 s old
        self.assertFalse(watch.valid_quote(row, later, {'2330': 'tse'}))

    def test_no_trade_at_all_is_not_a_price(self):
        row = dict(live_rows()['2330'], z='-')
        row['trade'] = {'t': '-', 'z': '-'}
        normalized = quote.normalize(row)
        self.assertIn('no_last_trade', normalized['quote_reasons'])
        self.assertFalse(watch.valid_quote(normalized, self.now, {'2330': 'tse'}))

    def test_legacy_rows_without_trade_object_keep_old_meaning(self):
        row = dict(c='3042', ex='tse', d='20261019', t='12:53:00', y='100', z='101', h='102', l='99')
        normalized = quote.normalize(row)
        self.assertEqual((normalized['z'], normalized['t']), ('101', '12:53:00'))
        self.assertTrue(watch.valid_quote(normalized, self.now, {'3042': 'tse'}))

    def test_execution_freshness_uses_trade_time(self):
        row = quote.normalize(live_rows()['2330'])
        self.assertTrue(execution.fresh(row, self.now))
        self.assertFalse(execution.fresh(row, self.now + dt.timedelta(seconds=120)))


class WatchHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 's.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_check_counts_live_capture_by_class_and_does_not_fail_on_illiquid(self):
        rows = list(live_rows().values())
        now = dt.datetime(2026, 10, 19, 12, 53, 24, tzinfo=TZ)
        watched = {r['c']: r['ex'] for r in rows}
        with patch.object(watch, 'universe', return_value=watched), \
             patch.object(watch, 'fetch', return_value=rows), \
             patch.object(watch.time, 'monotonic', side_effect=[0, 0]):
            watch.check(self.store, now)
        health = json.loads(self.store.meta('watch-health'))
        self.assertEqual(health['valid'], 3)
        self.assertEqual(health['quote_classes'], {'fresh': 3, 'stale_trade': 1})
        self.assertEqual(health['snapshot_ok'], 4)

    def test_only_illiquid_names_is_not_reported_as_outage(self):
        rows = [live_rows()['6477']]
        now = dt.datetime(2026, 10, 19, 12, 53, 24, tzinfo=TZ)
        with patch.object(watch, 'universe', return_value={'6477': 'tse'}), \
             patch.object(watch, 'fetch', return_value=rows), \
             patch.object(watch.time, 'monotonic', side_effect=[0, 0]):
            self.assertEqual(watch.check(self.store, now), 0)

    def test_stale_snapshots_only_is_an_outage(self):
        rows = [live_rows()['6477']]
        now = dt.datetime(2026, 10, 19, 13, 5, 0, tzinfo=TZ)   # snapshot 12:52:54 is 12 min old
        with patch.object(watch, 'universe', return_value={'6477': 'tse'}), \
             patch.object(watch, 'fetch', return_value=rows), \
             patch.object(watch.time, 'monotonic', side_effect=[0, 0]):
            with self.assertRaises(ValueError):
                watch.check(self.store, now)


if __name__ == '__main__':
    unittest.main()


class StaleQuoteNeverAlertsTests(unittest.TestCase):
    """End to end through watch.check: a stale last trade must never create an entry alert."""
    def setUp(self):
        from tests.test_execution import ExecutionTests
        self.fx = ExecutionTests('test_dry_run_has_no_trade_or_message_side_effect')
        self.fx.setUp()

    def tearDown(self):
        self.fx.tearDown()

    def mis_row(self, now, trade_at, price=101):
        return dict(c='3042', ex='tse', d=now.strftime('%Y%m%d'), t=now.strftime('%H:%M:%S'), z='-',
                    trade=dict(t=trade_at.strftime('%H:%M:%S'), z=str(price), v=1),
                    a=f'{price + .05}_', b=f'{price - .05}_', v='1000', y='100', h='110', l='90', n='晶技')

    def check(self, now, trade_at):
        with patch.object(watch, 'universe', return_value={'3042': 'tse'}), \
             patch.object(watch, 'fetch', return_value=[self.mis_row(now, trade_at)]), \
             patch.object(watch.time, 'monotonic', side_effect=[0, 0]):
            try:
                watch.check(self.fx.s, now, 't', 'c', True, cache=self.fx.cache)
            except ValueError:
                pass

    def test_stale_trade_with_fresh_book_creates_no_alert(self):
        now = self.fx.now
        self.check(now, now - dt.timedelta(minutes=12))
        self.check(now + dt.timedelta(minutes=5), now - dt.timedelta(minutes=12))
        self.assertEqual(execution.records(self.fx.s), [])
        self.assertEqual([m for m in self.fx.messages if m[0].startswith('paper:')], [])

    def test_fresh_trades_create_one_alert_after_two_confirmations(self):
        now = self.fx.now
        self.check(now, now - dt.timedelta(seconds=30))
        self.check(now + dt.timedelta(minutes=5), now + dt.timedelta(minutes=4, seconds=40))
        self.assertEqual([t['status'] for t in execution.records(self.fx.s)], ['awaiting_fill'])
        entry_messages = [m for m in self.fx.messages if m[0].startswith('paper:') and m[0].endswith(':entry')]
        self.assertEqual(len(entry_messages), 1)
        # The alert quote is the trade time, never the snapshot clock.
        self.assertTrue(execution.records(self.fx.s)[0]['alert_quote_at'].endswith('10:06:40+08:00'))
