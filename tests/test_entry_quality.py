import datetime as dt
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo
from market_radar import entry_quality as q
from market_radar.store import Store

class EntryQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'s.db')
        self.now=dt.datetime(2026,9,21,10,2,tzinfo=ZoneInfo('Asia/Taipei'))
        self.item=dict(plan=dict(entry_low=100,entry_high=103,stop=95,target=125,atr=10))
        self.row=dict(c='3042',t='10:01:00',z='101',a='101.1_101.2_',b='101_100.9_',v='1500')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def check(self):return q.inspect(self.s,self.item,self.row,self.now)
    def seed(self,days=5):
        for day in ['2026-09-14','2026-09-15','2026-09-16','2026-09-17','2026-09-18'][:days]:
            for minute in (600,601):
                self.s.ingest({('quote','entry-volume'):[dict(code='3042',period=f'{day}:{minute}',date=day,minute=minute,volume=1000)]})
    def test_wide_or_missing_book_is_not_eligible(self):
        self.row['b']='99_';self.assertIn('spread_above_0.5pct',self.check()['blockers'])
        self.row['a']='-';self.assertIn('missing_or_invalid_bid_ask',self.check()['blockers'])
    def test_ask_not_last_controls_entry_range(self):
        self.row.update(a='103.1_',b='103_')
        self.assertIn('ask_outside_entry_range',self.check()['blockers'])
    def test_distant_target_not_rewritten_to_pass(self):
        self.item['plan']['atr']=2
        self.assertIn('target_beyond_3atr',self.check()['blockers'])
        self.assertEqual(self.item['plan']['target'],125)
    def test_missing_history_is_unknown_not_failed_volume(self):
        result=self.check();self.assertTrue(result['hard_passed'])
        self.assertEqual(result['same_time_volume']['status'],'pending')
        self.assertIsNone(result['same_time_volume']['ratio'])
    def test_baseline_needs_distinct_days_and_excludes_today_future(self):
        self.seed(4)
        for day in ['2026-09-21','2026-09-22']:
            self.s.ingest({('quote','entry-volume'):[dict(code='3042',period=f'{day}:601',date=day,minute=601,volume=1)]})
        r=self.check()['same_time_volume'];self.assertEqual(r['sample_days'],4);self.assertIsNone(r['ratio'])
    def test_same_clock_median_uses_one_observation_per_day(self):
        self.seed();r=self.check()['same_time_volume']
        self.assertEqual(r['sample_days'],5);self.assertAlmostEqual(r['ratio'],1.5)
        self.assertEqual(r['status'],'supports')
    def test_weak_volume_is_comparison_only(self):
        self.seed();self.row['v']='500';r=self.check()
        self.assertEqual(r['same_time_volume']['status'],'weak');self.assertTrue(r['hard_passed'])
    def test_wrong_clock_does_not_substitute_full_day_volume(self):
        self.seed();self.row['t']='11:01:00'
        self.assertEqual(self.check()['same_time_volume']['sample_days'],0)
