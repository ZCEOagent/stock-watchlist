import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_radar import sources, engine, reports, notify
from market_radar.cli import ingest_seed, private_inputs
from market_radar.store import Store


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
        self.today = '2026-09-05'
        self.company = {'code': '2330', 'name': '測試公司', 'market': 'twse', 'sector': '24'}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def populate(self, eps=10):
        base = {'code': '2330', 'source': 'https://example.org/official', 'source_date': '2026-09-04'}
        self.store.ingest({('twse', 'revenue'): [dict(base, period='2026-07', yoy=40, ytd_yoy=20)],
                           ('twse', 'financial'): [dict(base, period='2026Q2', period_end='2026-06-30', basis='YTD',
                                                       eps=eps, revenue=100, operating=30, pretax=31, nonoperating=1, net=25),
                                                  dict(base, period='2025Q2', period_end='2025-06-30', basis='YTD', eps=5)],
                           ('twse', 'valuation'): [dict(base, pe=15, pb=2)]})
        prices = [dict(base, date=(dt.date(2026, 8, 6)+dt.timedelta(days=i)).isoformat(), close=100, volume=1000) for i in range(30)]
        self.store.ingest({('twse', 'price'): prices})

    def evaluate(self, **kwargs):
        return engine.evaluate(self.company, self.store, self.today, {'24': [20]*5}, set(), **kwargs)

    def test_dates_numbers_and_missing(self):
        self.assertEqual(sources.date('115/09/04'), '2026-09-04')
        self.assertEqual(sources.month('11507'), '2026-07')
        for bad in ('NaN', '--', '', None, 'inf'):
            self.assertIsNone(sources.number(bad))
        self.assertIsNone(sources.date('1151332'))
        r = self.evaluate()
        self.assertEqual(r['coverage'], 0)
        self.assertIsNone(r['score'])
        self.assertEqual(r['status'], 'WATCH')

    def test_cashflow_gate_and_buy(self):
        self.populate()
        r = self.evaluate()
        self.assertEqual(r['coverage'], 80)
        self.assertNotEqual(r['status'], 'BUY')
        extra = {'2330': {'period': '2026Q2', 'basis': 'YTD', 'source': 'https://example.org/filing',
                          'available_at': '2026-08-14', 'prior_year_eps': 5, 'operating_cash_flow': 30}}
        r = self.evaluate(supplements=extra)
        self.assertEqual(r['status'], 'BUY')
        self.assertEqual(r['coverage'], 100)
        extra['2330']['operating_cash_flow'] = -1
        self.assertEqual(self.evaluate(supplements=extra)['status'], 'REMOVE')

    def test_loss_and_bad_period(self):
        self.populate(eps=-1)
        self.assertEqual(self.evaluate()['status'], 'REMOVE')
        self.populate()
        extra = {'2330': {'period': '2026Q1', 'basis': 'YTD', 'source': 'https://example.org',
                          'available_at': '2026-08-14', 'operating_cash_flow': 30}}
        self.assertNotEqual(self.evaluate(supplements=extra)['status'], 'BUY')

    def test_source_failure_blocks_buy(self):
        self.populate()
        r = engine.evaluate(self.company, self.store, self.today, {'24': [20]*5}, {'price'})
        self.assertFalse(r['candidate'])
        self.assertNotEqual(r['status'], 'BUY')

    def test_same_day_and_baseline(self):
        self.populate()
        r = self.evaluate()
        s = {'day': self.today, 'version': engine.VERSION, 'stocks': [r]}
        self.store.snapshot(self.today, s)
        self.assertIsNone(self.store.previous(self.today))
        self.assertEqual(reports.select(s, None, {})[0], [])
        self.assertEqual(self.store.previous('2026-09-06')['day'], self.today)

    def test_change_requires_same_coverage(self):
        self.populate()
        r = self.evaluate()
        prev = dict(r, score=10)
        s = {'version': engine.VERSION, 'stocks': [r]}
        p = {'version': engine.VERSION, 'stocks': [prev]}
        self.assertEqual(len(reports.select(s, p, {})[1]), 1)
        prev['parts'] = {}
        self.assertEqual(reports.select(s, p, {})[1], [])

    def test_only_actual_holdings_get_hold(self):
        self.populate()
        r = self.evaluate()
        risks = reports.holding_risks([r], {'2330': {'stop': 101}, '6274': {}})
        self.assertEqual(risks[0]['status'], 'HOLD')
        self.assertIn('本輪找不到', risks[1]['risks'][0])
        self.assertEqual(r['status'], 'WATCH')

    def test_event_flag_does_not_assert_guilt(self):
        raw = {'公司代號': '2330', '出表日期': '1150904', '發言日期': '1150904', '主旨 ': '澄清並無重大訴訟'}
        e = sources.normalize('events', raw, 'twse', 'https://example.org', self.today)
        self.assertTrue(e['review_required'])
        self.assertEqual(e['title'], '澄清並無重大訴訟')

    def test_tpex_mapping(self):
        r = sources.normalize('price', {'SecuritiesCompanyCode': '6274', 'Date': '1150904', 'Close': '1,000', 'TradingShares': '2000'}, 'tpex', 'https://example.org', self.today)
        self.assertEqual((r['code'], r['date'], r['close']), ('6274', '2026-09-04', 1000))
        f = sources.normalize('financial', {'SecuritiesCompanyCode': '6274', 'Date': '1150904', 'Year': '115', 'Season': '2', '基本每股盈餘（元）': '12.4'}, 'tpex', 'https://example.org', self.today)
        self.assertEqual((f['period'], f['basis'], f['eps']), ('2026Q2', 'YTD', 12.4))

    def test_telegram_receipt_only_after_success(self):
        with patch('market_radar.notify.call', side_effect=notify.DeliveryRejected('failed')):
            with self.assertRaises(RuntimeError):
                notify.deliver(self.store, 'fake', 'fake', 'k', 'hello', self.today)
        self.assertFalse(self.store.sent('k:0'))
        with patch('market_radar.notify.call') as call:
            notify.deliver(self.store, 'fake', 'fake', 'k', 'hello', self.today)
            notify.deliver(self.store, 'fake', 'fake', 'k', 'hello', self.today)
            self.assertEqual(call.call_count, 1)

    def test_uncertain_delivery_not_retried(self):
        with patch('market_radar.notify.call', side_effect=notify.DeliveryUncertain('timeout')) as call:
            for _ in range(2):
                with self.assertRaises(notify.DeliveryUncertain):
                    notify.deliver(self.store, 'fake', 'fake', 'uncertain', 'hello', self.today)
            self.assertEqual(call.call_count, 1)

    def test_existing_shadow_strategy_cannot_be_bypassed(self):
        r = {'code': '2330', 'status': 'BUY', 'missing': [], 'price_date': self.today}
        snap = {'day': self.today, 'stocks': [r]}
        engine.apply_swing_gate(snap, {}, 'shadow')
        self.assertEqual(r['status'], 'WATCH')

    def test_recent_but_wrong_session_quote_rejected(self):
        self.populate()
        r = engine.evaluate(self.company, self.store, self.today, {'24': [20]*5}, set(), expected_date='2026-09-05')
        self.assertFalse(r['candidate'])

    def test_stale_or_expired_swing_plan_cannot_be_buy(self):
        for stale in (True, False):
            r = {'code': '2330', 'status': 'BUY', 'missing': [], 'price_date': self.today}
            snap = {'day': self.today, 'stocks': [r]}
            cache = {'as_of': self.today, 'quality': {'passed': True}, 'radar': {'items': [
                {'id': '2330', 'as_of': '2026-09-04' if stale else self.today,
                 'expires_on': self.today if stale else '2026-09-04', 'status': 'triggered', 'plan': {'stop': 90}}]}}
            engine.apply_swing_gate(snap, cache, 'live')
            self.assertEqual(r['status'], 'WATCH')

    def test_unicode_chunks(self):
        text = '台😀股' * 3000
        parts = notify.chunks(text)
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p.encode('utf-16-le'))//2 <= 3500 for p in parts))

    def test_seed_reuses_original_history(self):
        path = Path(self.temp.name) / 'seed.json'
        path.write_text(json.dumps({'2330': [{'date': '2026-09-04', 'close': 100, 'volume': 20}]}))
        self.assertEqual(ingest_seed(self.store, path), 1)
        self.assertEqual(ingest_seed(self.store, path), 1)
        self.assertEqual(len(self.store.history('price', '2330')), 1)

    def test_no_private_portfolio_in_snapshot(self):
        self.populate()
        r = self.evaluate()
        s = {'day': self.today, 'version': engine.VERSION, 'scanned': 1, 'health': [], 'price_dates': ['2026-09-04'], 'stocks': [r]}
        reports.render(s, None, {'2330': {'stop': 123.456789}})
        self.store.snapshot(self.today, s)
        saved = json.dumps(self.store.latest())
        self.assertNotIn('123.456789', saved)
        self.assertNotIn('holdings', saved)

    def test_financial_supplement_rejects_nan(self):
        with patch.dict('os.environ', {'RADAR_HOLDINGS_JSON': '{}', 'RADAR_FINANCIALS_JSON': '{"2330":{"operating_cash_flow":"NaN"}}'}):
            _, extra = private_inputs()
            self.assertIsNone(extra['2330']['operating_cash_flow'])


if __name__ == '__main__':
    unittest.main()
