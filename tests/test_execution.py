import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from market_radar import execution as ex, watch, notify
from market_radar.store import Store

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.s=Store(Path(self.tmp.name)/'s.db')
        self.now=dt.datetime(2026,9,21,10,2,tzinfo=ZoneInfo('Asia/Taipei'))
        self.p=dict(entry_low=100,entry_high=103,stop=95,target=125,atr=10)
        self.item=dict(id='3042',name='晶技',created_on='2026-09-18',expires_on='2026-09-23',as_of='2026-09-18',status='waiting',plan=self.p,
            gates={g:True for g in ex.GATES},catalyst=dict(reviewed=True,source_url='https://example.org/filing',published_at='2026-09-18T10:00:00+08:00',observed_on='2026-09-18',valid_until='2026-09-24',thesis='fixture',impact='fixture',priced_in_risk='fixture',cancel_if='fixture'))
        self.cache=dict(as_of='2026-09-18',quality={'passed':True},radar={'items':[self.item]})
        self.messages=[]
        def deliver(store,token,chat,key,text,now):
            self.messages.append((key,text))
            store.mark_sent(key+':0',now)
        self.mock=patch.object(notify,'deliver',side_effect=deliver);self.mock.start()
    def tearDown(self):
        self.mock.stop();self.s.close();self.tmp.cleanup()
    def row(self,now,price=101,**kw):
        return dict(c='3042',ex='tse',d=now.strftime('%Y%m%d'),t=now.strftime('%H:%M:%S'),z=str(price),a=str(price+.05)+'_',b=str(price-.05)+'_',v='1000',y='100',h=str(max(price,110)),l=str(min(price,90)),**kw)
    def run_at(self,now,price=101,rows=None,enabled=True):
        ex.process(self.s,[self.row(now,price)] if rows is None else rows,self.cache,now,'t','c',enabled)
    def trade(self):return ex.records(self.s)[0]
    def alert(self):
        self.run_at(self.now);self.run_at(self.now+dt.timedelta(minutes=5))
    def opened(self):
        self.alert();self.run_at(self.now+dt.timedelta(minutes=10));self.assertEqual(self.trade()['status'],'open')
    def test_delivered_alert_never_fills_from_same_or_pre_alert_quote(self):
        self.alert();self.assertEqual(self.trade()['status'],'awaiting_fill')
        self.run_at(self.now+dt.timedelta(minutes=6),rows=[self.row(self.now+dt.timedelta(minutes=5))])
        self.assertEqual(self.trade()['status'],'awaiting_fill')
        self.run_at(self.now+dt.timedelta(minutes=10))
        self.assertEqual(self.trade()['status'],'open')
        self.assertGreater(self.trade()['entry_at'],self.trade()['alerted_at'])
    def test_missing_gate_and_stale_cache_block_entry(self):
        self.item['gates']['chips']=False;self.alert();self.assertEqual(ex.records(self.s),[])
        self.item['gates']['chips']=True;self.cache['as_of']='2026-09-17';self.alert();self.assertEqual(ex.records(self.s),[])
    def test_dry_run_has_no_trade_or_message_side_effect(self):
        self.run_at(self.now,enabled=False);self.run_at(self.now+dt.timedelta(minutes=5),enabled=False)
        self.assertEqual(ex.records(self.s),[]);self.assertEqual(self.messages,[])
    def test_missing_quote_breaks_two_sample_confirmation(self):
        self.run_at(self.now);self.run_at(self.now+dt.timedelta(minutes=5),rows=[])
        self.run_at(self.now+dt.timedelta(minutes=10));self.assertEqual(ex.records(self.s),[])
    def test_expired_quote_cannot_fill(self):
        self.alert();self.run_at(self.now+dt.timedelta(minutes=16))
        self.assertEqual(self.trade()['status'],'expired')
    def test_gap_above_range_is_cancelled_not_chased(self):
        self.alert();self.run_at(self.now+dt.timedelta(minutes=10),104)
        self.assertEqual(self.trade()['status'],'cancelled')
    def test_slippage_cannot_push_entry_outside_range(self):
        self.alert();self.run_at(self.now+dt.timedelta(minutes=10),103)
        self.assertEqual(self.trade()['status'],'cancelled')
    def test_target_uses_observed_price_and_costs_not_pre_entry_high(self):
        self.opened();self.run_at(self.now+dt.timedelta(minutes=15),101)
        self.assertEqual(self.trade()['status'],'open')
        self.run_at(self.now+dt.timedelta(minutes=20),126)
        t=self.trade();self.assertEqual(t['exit_reason'],'target');self.assertAlmostEqual(t['exit'],125.95*.999)
        self.assertLess(t['net_return_pct'],(126/101-1)*100)
    def test_gap_stop_uses_observed_price_not_ideal_stop(self):
        self.opened();self.run_at(self.now+dt.timedelta(days=1),90)
        self.assertEqual(self.trade()['exit_reason'],'stop');self.assertAlmostEqual(self.trade()['exit'],89.95*.999)
    def test_day_two_review_day_three_time_exit_once(self):
        self.opened();self.run_at(self.now+dt.timedelta(days=1));self.assertTrue(self.trade()['review_sent'])
        self.run_at(self.now+dt.timedelta(days=1,minutes=5));self.assertEqual(len([m for m in self.messages if m[0].endswith(':review')]),1)
        self.run_at(self.now.replace(day=23,hour=13,minute=22),102)
        self.assertEqual(self.trade()['exit_reason'],'time_exit');self.assertFalse(self.trade()['delayed_exit'])
        self.run_at(self.now.replace(day=23,hour=13,minute=27),105)
        self.assertEqual(len([m for m in self.messages if m[0].endswith(':exit')]),1)
    def test_missing_deadline_quote_does_not_fabricate_close(self):
        self.opened();self.run_at(self.now.replace(day=23,hour=13,minute=22),rows=[])
        self.assertEqual(self.trade()['status'],'open')
        self.run_at(self.now.replace(day=24),99)
        self.assertTrue(self.trade()['delayed_exit']);self.assertAlmostEqual(self.trade()['exit'],98.95*.999)
    def test_close_receipt_failure_preserves_exit_price_for_retry(self):
        self.opened()
        with patch.object(notify,'deliver',side_effect=RuntimeError('rejected')):
            with self.assertRaises(RuntimeError):self.run_at(self.now.replace(day=23,hour=13,minute=22),102)
        self.assertEqual(self.trade()['status'],'closed');price=self.trade()['exit']
        self.run_at(self.now.replace(day=23,hour=13,minute=27),105)
        self.assertEqual(self.trade()['exit'],price);self.assertTrue(self.trade()['exit_sent'])
    def test_entry_delivery_failure_never_creates_fill(self):
        self.run_at(self.now)
        with patch.object(notify,'deliver',side_effect=RuntimeError('rejected')):
            with self.assertRaises(RuntimeError):self.run_at(self.now+dt.timedelta(minutes=5))
        self.assertEqual(self.trade()['status'],'alert_pending');self.assertNotIn('entry',self.trade())
    def test_receipt_recovery_does_not_realert_or_fill_earlier_quote(self):
        self.alert();t=self.trade();t['status']='alert_pending';t.pop('alerted_at');ex.save(self.s,t)
        self.run_at(self.now+dt.timedelta(minutes=10));self.assertEqual(len(self.messages),1)
        self.assertEqual(self.trade()['status'],'awaiting_fill')
    def test_watch_wires_validated_quotes_into_execution(self):
        with patch.object(watch,'fetch',side_effect=[[self.row(self.now)],[self.row(self.now+dt.timedelta(minutes=5))]]):
            watch.check(self.s,self.now,'t','c',True,cache=self.cache)
            watch.check(self.s,self.now+dt.timedelta(minutes=5),'t','c',True,cache=self.cache)
        self.assertEqual(self.trade()['status'],'awaiting_fill')
    def test_no_samples_has_no_fake_win_rate(self):
        self.assertIsNone(ex.summary(self.s)['win_rate'])
    def test_active_trade_keeps_exchange_when_roster_expires(self):
        self.opened();t=self.trade();t['code']='9999';ex.save(self.s,t)
        self.assertEqual(watch.universe(self.s,self.now+dt.timedelta(days=30),{})['9999'],'tse')
    def test_uncertain_entry_is_not_filled_or_automatically_reissued(self):
        self.alert();t=self.trade();key=f"paper:{t['period']}:entry"
        self.s.db.execute('DELETE FROM receipts WHERE id=?',(key+':0',));self.s.db.commit()
        self.s.meta('delivery:'+key+':0','uncertain');t['status']='alert_pending';t.pop('alerted_at');ex.save(self.s,t)
        # Real deliver checks the durable uncertain marker before contacting Telegram.
        self.mock.stop()
        with patch.object(notify,'call') as network:
            with self.assertRaises(RuntimeError):self.run_at(self.now+dt.timedelta(minutes=10))
            network.assert_not_called()
        self.mock.start()
        self.assertEqual(self.trade()['status'],'alert_pending')
    def test_execution_failure_does_not_skip_quote_health(self):
        import json
        with patch.object(ex,'process',side_effect=RuntimeError('delivery')),patch.object(watch,'fetch',return_value=[self.row(self.now)]):
            with self.assertRaises(RuntimeError):watch.check(self.s,self.now,'t','c',True,cache=self.cache)
        self.assertEqual(json.loads(self.s.meta('watch-health'))['valid'],1)
    def test_after_hours_quote_cannot_backfill_a_time_exit(self):
        self.opened()
        late=self.now.replace(day=23,hour=18,minute=0)
        self.run_at(late,rows=[self.row(late.replace(hour=13,minute=30))])
        self.assertEqual(self.trade()['status'],'open')
    def test_unfilled_alert_is_cancelled_and_excluded_from_win_rate(self):
        self.alert();self.run_at(self.now+dt.timedelta(minutes=10),104)
        self.run_at(self.now+dt.timedelta(minutes=15),101)
        self.assertTrue(self.trade()['cancel_sent'])
        self.assertEqual(ex.summary(self.s)['closed'],0)
        self.assertIsNone(ex.summary(self.s)['win_rate'])
    def test_delivery_wait_cannot_turn_old_quote_into_current_entry(self):
        self.run_at(self.now)
        with patch.object(ex.time,'perf_counter',side_effect=[0,240]):
            self.run_at(self.now+dt.timedelta(minutes=5))
        self.assertEqual(self.messages,[])
        self.assertNotEqual(self.trade()['status'],'open')
    def test_post_alert_fill_requires_valid_ask_and_target_distance(self):
        self.alert();row=self.row(self.now+dt.timedelta(minutes=10));row['a']='-'
        self.run_at(self.now+dt.timedelta(minutes=10),rows=[row])
        self.assertEqual(self.trade()['status'],'cancelled')
    def test_stop_without_bid_waits_then_uses_next_bid_even_after_rebound(self):
        self.opened();now=self.now+dt.timedelta(days=1);row=self.row(now,90);row['b']='-'
        self.run_at(now,rows=[row]);self.assertEqual(self.trade()['status'],'open')
        self.assertEqual(self.trade()['pending_exit_reason'],'stop')
        self.run_at(now+dt.timedelta(minutes=5),98)
        self.assertEqual(self.trade()['exit_reason'],'stop')
        self.assertAlmostEqual(self.trade()['exit'],97.95*.999)
