import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from market_radar.store import Store
from market_radar.handoff import candidate_ids
from radar import shortlist


class HandoffTests(unittest.TestCase):
    def setUp(self):
        # Historical fixtures must not change when live provider holidays change.
        dates = ['2026-10-21', '2026-10-22', '2026-10-23']
        calendar = patch('market_radar.handoff.session_dates',
                         side_effect=lambda start, end: [d for d in dates if start <= d <= end])
        calendar.start()
        self.addCleanup(calendar.stop)

    def test_fresh_candidates_reach_deep_selection_without_approving_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.db'
            store = Store(path)
            store.snapshot('2026-10-21', dict(day='2026-10-21',
                freshness={'expected_date': '2026-10-21'}, stocks=[
                    dict(code='8415', candidate=True, price_date='2026-10-21'),
                    dict(code='2330', candidate=True, price_date='2026-10-20'),
                    dict(code='3042', candidate=False, price_date='2026-10-21')]))
            store.close()
            codes = candidate_ids(path, '2026-10-22')
            self.assertEqual(codes, ['8415'])
            universe = [dict(stock_id=str(i), stock_name=str(i)) for i in range(8400, 8430)]
            history = {i['stock_id']: [dict(close=100, volume=1000000)] * 21 for i in universe}
            selected = shortlist(universe, history, codes)
            self.assertIn('8415', [i['stock_id'] for i in selected])
            self.assertTrue(all('plan' not in i and 'catalyst' not in i for i in selected))
            self.assertEqual(candidate_ids(path, '2026-10-23'), [])
            # A closure does not age a candidate by another trading session.
            with patch('market_radar.handoff.session_dates', return_value=['2026-10-22']):
                self.assertEqual(candidate_ids(path, '2026-10-23'), ['8415'])
            self.assertEqual(candidate_ids(path, '2026-10-20'), [])

    def test_missing_artifact_and_stale_snapshot_cannot_supply_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.db'
            self.assertEqual(candidate_ids(path, '2026-10-22'), [])
            store = Store(path)
            store.snapshot('2026-10-22', dict(day='2026-10-22',
                freshness={'expected_date': '2026-10-21'}, stocks=[
                    dict(code='8415', candidate=True, price_date='2026-10-22')]))
            store.close()
            self.assertEqual(candidate_ids(path, '2026-10-22'), [])
