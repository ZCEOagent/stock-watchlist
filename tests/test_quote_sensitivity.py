import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from market_radar import watch, notify
from market_radar.store import Store

class SensitivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'s.db')
        self.now = dt.datetime(2026,9,29,10,2,tzinfo=ZoneInfo('Asia/Taipei'))
        self.row = dict(c='3042',ex='tse',d='20260929',t='10:01:00',y='100',z='103.5',h='104',l='100',n='晶技')
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def test_two_fresh_distinct_samples_then_only_one_alert_and_audit(self):
        with patch.object(watch,'fetch',return_value=[self.row]), patch.object(notify,'call',return_value={'message_id':123}) as send:
            self.assertEqual(watch.check(self.store,self.now,'t','c',True),0)
            self.row['t']='10:06:00'
            self.assertEqual(watch.check(self.store,self.now+dt.timedelta(minutes=5),'t','c',True),1)
            self.row['t']='10:11:00'
            self.assertEqual(watch.check(self.store,self.now+dt.timedelta(minutes=10),'t','c',True),0)
            self.assertEqual(send.call_count,1)
        record=self.store.history('watch-signal','3042')[0]
        self.assertEqual(record['price'],103.5)
        self.assertEqual(record['level'],3)
        self.assertNotIn('holdings',record)
    def test_stale_quote_breaks_confirmation_and_cannot_emit_extreme(self):
        with patch.object(watch,'fetch',return_value=[self.row]):
            watch.check(self.store,self.now)
            self.row['h']='109'
            with self.assertRaises(ValueError):
                watch.check(self.store,self.now+dt.timedelta(minutes=5))
            self.row.update(h='104',t='10:11:00')
            self.assertEqual(watch.check(self.store,self.now+dt.timedelta(minutes=10)),0)
    def test_missing_one_stock_does_not_block_valid_risk_alert(self):
        self.row.update(z='108',h='109')
        with patch.object(watch,'fetch',return_value=[self.row]):
            self.assertEqual(watch.check(self.store,self.now),1)
    def test_resolve_exchange_from_roster_and_ignore_stale_candidates(self):
        self.store.ingest({('twse','universe'):[dict(code='6443',market='twse',source_date='2026-09-29'),dict(code='9999',market='tpex',source_date='2026-09-29')]})
        self.store.snapshot('2026-09-20',dict(day='2026-09-20',stocks=[dict(code='9999',candidate=True)]))
        selected=watch.universe(self.store,self.now,{})
        self.assertEqual(selected['6443'],'tse')
        self.assertNotIn('9999',selected)
        self.assertEqual(watch.universe(self.store,self.now,{'9999':{}})['9999'],'otc')
    def test_public_budget_does_not_suppress_holding_risk(self):
        self.store.meta('watch-public-count:2026-09-29',6)
        self.row.update(z='108',h='109')
        with patch.object(watch,'fetch',return_value=[self.row]):
            self.assertEqual(watch.check(self.store,self.now),0)
            self.assertEqual(watch.check(self.store,self.now,holdings={'3042':{}}),1)
    def test_close_review_rejects_midday_stale_last_trade(self):
        self.assertEqual(watch.signals(dict(self.row,h='109'),self.now.replace(hour=16)),[])
    def test_forward_results_require_exact_future_session_and_never_fabricate_fill(self):
        self.store.ingest({('watch','watch-signal'):[dict(code='3042',period='2026-09-29:up:3',date='2026-09-29',price=100,direction='up',level=3)]})
        self.store.ingest({('twse','price'):[dict(code='3042',date='2026-09-30',close=102),dict(code='3042',date='2026-10-01',close=900)]})
        result=watch.outcomes(self.store,'2026-09-30')
        self.assertEqual(result['horizons']['1']['samples'],1)
        self.assertAlmostEqual(result['horizons']['1']['mean_directional_pct'],2)
        self.assertEqual(result['horizons']['3']['samples'],0)
        self.assertIsNone(result['horizons']['3']['positive_ratio'])
    def test_quote_only_mode_skips_full_market_collection(self):
        import argparse
        from market_radar import cli
        args=argparse.Namespace(mode='quotes',send=False,state=str(Path(self.tmp.name)/'q.db'),output=str(Path(self.tmp.name)/'out'))
        with patch.object(cli,'private_inputs',return_value=({},{})), patch.object(watch,'check',return_value=0), patch.object(cli.sources,'collect') as collect, patch.object(cli,'ingest_seed') as seed:
            cli.run(args)
            collect.assert_not_called()
            seed.assert_not_called()
    def test_request_elapsed_time_does_not_reject_quote_arriving_during_fetch(self):
        self.row.update(t='10:02:05',z='108',h='109')
        with patch.object(watch,'fetch',return_value=[self.row]), patch.object(watch.time,'monotonic',side_effect=[100,110]):
            self.assertEqual(watch.check(self.store,self.now),1)
    def test_all_missing_quotes_are_a_failed_inspection_with_health_saved(self):
        import json
        with patch.object(watch,'fetch',return_value=[]):
            with self.assertRaises(ValueError):
                watch.check(self.store,self.now)
        health=json.loads(self.store.meta('watch-health'))
        self.assertEqual(health['valid'],0)
        self.assertEqual(health['missing'],health['requested'])
    def test_duplicate_quote_is_quarantined_without_blocking_other_codes(self):
        import json
        strong=dict(self.row,z='108',h='109')
        other=dict(strong,c='2330')
        with patch.object(watch,'fetch',return_value=[strong,dict(strong,z='101'),other]):
            self.assertEqual(watch.check(self.store,self.now),1)
        self.assertEqual(json.loads(self.store.meta('watch-health'))['duplicate_codes'],1)
        self.assertEqual(self.store.history('watch-quote','3042'),[])
    def test_one_network_batch_failure_still_returns_other_batch(self):
        import requests
        from unittest.mock import Mock
        codes={str(1000+i):'tse' for i in range(51)}
        with patch.object(watch.requests,'Session') as session:
            get=session.return_value.__enter__.return_value.get
            response=Mock()
            response.json.return_value=dict(rtcode='0000',msgArray=[dict(self.row,c='1050')])
            get.side_effect=[requests.Timeout('timeout'),response]
            self.assertEqual(watch.fetch(codes)[0]['c'],'1050')
            self.assertEqual(get.call_count,2)
    def test_uncertain_delivery_never_overwrites_original_evidence(self):
        import json
        from market_radar import reports
        self.row.update(z='108',h='109')
        key=reports.message_key('watch-price:2026-09-29:3042:up:',8)
        self.store.meta('delivery:'+key+':0','uncertain')
        self.store.meta('watch-evidence:'+key,json.dumps({'price':105}))
        with patch.object(watch,'fetch',return_value=[self.row]):
            with self.assertRaises(notify.DeliveryUncertain):
                watch.check(self.store,self.now,'t','c',True)
        self.assertEqual(json.loads(self.store.meta('watch-evidence:'+key))['price'],105)
        self.assertEqual(self.store.history('watch-signal','3042'),[])
    def test_small_sample_report_does_not_advertise_direction_ratio(self):
        from market_radar import reports
        snapshot=dict(day='2026-09-29',stocks=[],scanned=0,version='test',health=[],
            alert_outcomes=dict(records=1,horizons={'3':dict(samples=1,pending=0,positive_ratio=1,mean_directional_pct=5)}))
        text=reports.render(snapshot,None,{})
        self.assertIn('成熟樣本不足 20 筆',text)
        self.assertNotIn('方向相符比例',text)
