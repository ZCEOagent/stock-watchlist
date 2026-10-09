import argparse
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from market_radar import validation, watch, cli
from market_radar.store import Store


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'state.db')
        self.now=dt.datetime(2026,9,29,10,2,tzinfo=ZoneInfo('Asia/Taipei'))
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def audit(self):
        return validation.audit(self.store,'2026-09-29',{})
    def test_no_observation_is_unknown_not_perfect_coverage(self):
        self.assertIsNone(self.audit()['coverage_ratio'])
        self.assertIsNone(self.audit()['valid_quote_ratio'])
    def test_budget_and_uncertain_delivery_remain_uncovered_until_confirmed(self):
        validation.observation(self.store,'3042',self.now,True,[dict(direction='up',level=5)])
        for reason in ('public_budget','delivery_not_confirmed'):
            validation.observation(self.store,'3042',self.now,result=reason)
        self.assertEqual(self.audit()['uncovered_directions'],1)
        validation.observation(self.store,'3042',self.now,result='delivered',direction='up',level=3)
        self.assertEqual(self.audit()['uncovered_directions'],1)
        validation.observation(self.store,'3042',self.now,result='delivered',direction='up',level=5)
        self.assertEqual(self.audit()['uncovered_directions'],0)
    def test_independent_directions_and_gap(self):
        validation.observation(self.store,'3042',self.now,True,[dict(direction='up',level=5)])
        validation.observation(self.store,'3042',self.now+dt.timedelta(minutes=15),True,[dict(direction='down',level=5)])
        result=self.audit()
        self.assertEqual(result['observed_eligible_directions'],2)
        self.assertEqual(result['max_observed_gap_seconds'],900)
    def test_dry_run_does_not_claim_missed_delivery(self):
        row=dict(c='3042',ex='tse',d='20260929',t='10:01:00',y='100',z='108',h='109',l='100',n='晶技')
        with patch.object(watch,'fetch',return_value=[row]):
            watch.check(self.store,self.now,send=False)
        self.assertEqual(self.audit()['observed_eligible_directions'],0)
    def test_failed_quote_check_still_exports_audit_and_raises(self):
        args=argparse.Namespace(mode='quotes',send=False,state=str(Path(self.tmp.name)/'q.db'),output=str(Path(self.tmp.name)/'out'))
        with patch.object(cli,'private_inputs',return_value=({},{})), patch.object(watch,'check',side_effect=ValueError('no quotes')):
            with self.assertRaises(ValueError): cli.run(args)
        self.assertIsNone(json.loads((Path(args.output)/'validation.json').read_text())['coverage_ratio'])
    def test_stale_swing_plan_is_withheld_and_never_auto_approved(self):
        cache=dict(as_of='2026-09-29',quality={'passed':True},radar={'items':[dict(id='3042',as_of='2026-09-28',gates={'catalyst':False},plan={'entry':100},facts={'source':'https://example.org'})]})
        result=validation.swing_reviews(cache)
        self.assertIsNone(result['items'][0]['reference_plan'])
        self.assertEqual(result['approved_by_this_process'],0)
        self.assertEqual(result['blocked_gate_counts'],{'catalyst':1})
    def test_candidate_bundle_preserves_sources_but_rejects_stale_gate(self):
        self.store.ingest({('twse','price'):[dict(code='3042',date='2026-09-29',source='https://example.org',fetched_at='2026-09-29T10:00:00+08:00')]})
        snapshot=dict(day='2026-09-29',freshness={'expected_date':'2026-09-29'},stocks=[dict(code='3042',candidate=True,price_date='2026-09-29')])
        cache=dict(as_of='2026-09-28',quality={'passed':True},radar={'items':[dict(id='3042',as_of='2026-09-28',plan={'entry':100})]})
        result=validation.reviews(snapshot,self.store,cache)
        self.assertEqual(result['items'][0]['sources'][0]['period'],'2026-09-29')
        self.assertIsNone(result['items'][0]['reference_plan'])
        self.assertEqual(result['approved_by_this_process'],0)
