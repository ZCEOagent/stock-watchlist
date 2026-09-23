import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_radar import history, financials
from market_radar.store import Store


class HistoricalCompletionTests(unittest.TestCase):
    def test_tpex_date_and_fields_are_checked(self):
        payload = {'stat': 'ok', 'date': '20260922', 'tables': [{'title': '上櫃股票行情',
            'fields': ['代號', '名稱', '收盤', '開盤', '最高', '最低', '成交股數'],
            'data': [['6274', '台燿', '1,000', '990', '1010', '980', '1,234']]}]}
        rows = history.parse_day('tpex', '2026-09-22', payload)
        self.assertEqual(rows[0]['volume'], 1234)
        self.assertEqual(rows[0]['close'], 1000)
        with self.assertRaises(ValueError):
            history.parse_day('tpex', '2026-09-21', payload)

    def test_backfill_idempotent_and_failed_dates_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'s.db')
            days = ['2026-09-21', '2026-09-22']
            company = [{'code': '6274', 'market': 'tpex'}]
            def fetch(market, day):
                if day.endswith('21'):
                    raise RuntimeError('source down')
                return [{'code': '6274', 'date': day, 'source_date': day, 'close': 10, 'volume': 20}]
            with patch.object(history, 'fetch_day', side_effect=fetch) as call:
                health = history.backfill(store, company, days)
                self.assertEqual(health['failed_days'], 1)
                history.backfill(store, company, days)
                self.assertEqual(call.call_count, 3)
            self.assertEqual(len(store.history('price', '6274')), 1)
            store.close()

    def test_official_history_cannot_invent_weekend_bars(self):
        with self.assertRaises(ValueError):
            history.parse_day('twse', '2026-09-20', {'stat': '很抱歉，沒有符合條件的資料!'})


class FilingTests(unittest.TestCase):
    def fixture(self, code='6274'):
        return (Path(__file__).parent/'fixtures'/f'{code}-filing.xml').read_bytes()

    def parse(self, content=None, code='6274'):
        return financials.parse_filing(content or self.fixture(code), code, '2026Q2', '2026-09-23T18:00:00+08:00')

    def test_real_filing_scale_sign_and_same_period(self):
        row = self.parse()
        self.assertEqual(row['current_eps'], 12.4)
        self.assertEqual(row['prior_year_eps'], 4.79)
        self.assertEqual(row['operating_cash_flow'], -2282493)
        self.assertEqual(row['net'], 3601714)
        self.assertEqual(row['available_at'], '2026-09-23')
        row = self.parse(code='2330')
        self.assertEqual(row['prior_year_eps'], 29.31)
        self.assertEqual(row['operating_cash_flow'], 1482341242)

    def test_mismatched_company_or_period_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(self.fixture(), code='2330')
        with self.assertRaises(ValueError):
            financials.parse_filing(self.fixture(), '6274', '2026Q3', '2026-09-23')

    def test_units_nil_and_nonfinite_rejected(self):
        for content in [self.fixture().replace(b'iso4217:TWD', b'iso4217:USD'),
                        self.fixture().replace(b'>12.40<', b'>NaN<')]:
            with self.assertRaises(ValueError):
                self.parse(content)

    def test_quarter_context_cannot_replace_ytd(self):
        with self.assertRaises(ValueError):
            self.parse(self.fixture().replace(b'2026-01-01', b'2026-04-01'))

    def test_dimension_context_rejected(self):
        with self.assertRaises(ValueError):
            self.parse(self.fixture().replace(b'</xbrli:context>', b'<xbrli:scenario/></xbrli:context>'))

    def test_bulk_disagreement_rejected(self):
        row = self.parse()
        fin = {'period': '2026Q2', 'eps': 12.4, 'net': 3601714}
        self.assertTrue(financials.matches(row, fin))
        self.assertFalse(financials.matches(row, dict(fin, eps=13)))
        self.assertFalse(financials.matches(row, dict(fin, net=3601714000)))

    def test_completion_caches_success_and_retries_failure_next_day(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'s.db')
            companies = [{'code': '6274', 'market': 'tpex'}]
            store.ingest({('tpex', 'financial'): [{'code': '6274', 'period': '2026Q2', 'eps': 12.4, 'net': 3601714}]})
            response = Mock(status_code=200, content=self.fixture())
            with patch.object(financials.requests, 'get', return_value=response) as fetch, patch.object(financials.time, 'sleep'):
                rows, health = financials.complete(store, companies, '2026-09-23')
                financials.complete(store, companies, '2026-09-24')
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(health['verified'], 1)
                # A revised bulk fact invalidates cache; disagreement stays missing.
                store.ingest({('tpex', 'financial'): [{'code': '6274', 'period': '2026Q2', 'eps': 15, 'net': 3601714}]})
                rows, health = financials.complete(store, companies, '2026-09-24')
                self.assertEqual(rows, {})
                self.assertEqual(health['failed'], 1)
                financials.complete(store, companies, '2026-09-24')
                self.assertEqual(fetch.call_count, 2)
                financials.complete(store, companies, '2026-09-25')
                self.assertEqual(fetch.call_count, 3)
            store.close()
