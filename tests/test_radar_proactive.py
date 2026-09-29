import unittest
from unittest.mock import patch
from market_radar import reports, engine, sources

class ProactiveTests(unittest.TestCase):
    def row(self, code):
        return dict(code=code,name=code,status='WATCH',score=90,coverage=80,candidate=True,risks=[],missing=['valuation pending'],reasons=['growth'],parts={},price_date='2026-09-29')

    def test_shadow_baseline_shows_research_and_requested_watch(self):
        s=dict(day='2026-09-29',fetched_at='2026-09-29T14:27:41+08:00',version=engine.VERSION,strategy_mode='shadow',scanned=6,price_dates=['2026-09-29'],health=[],stocks=[self.row(c) for c in ('2330','6274','3042','6691','2368','6196')])
        text=reports.render(s,None,{})
        self.assertIn('6691',text)
        self.assertIn('3042',text)
        self.assertIn(s['fetched_at'],text)
        self.assertEqual(text.count('①'),1)
        self.assertIn('3042',reports.full_report(s,None,{}))

    def test_filtered_sources_do_not_fetch_other_feeds(self):
        with patch.object(sources,'fetch_json',return_value=[]):
            feeds,health=sources.collect('2026-09-29',kinds={'valuation'})
        self.assertEqual(set(feeds),{('twse','valuation'),('tpex','valuation')})

class WatchTests(unittest.TestCase):
    def setUp(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        self.now=dt.datetime(2026,9,29,13,20,tzinfo=ZoneInfo('Asia/Taipei'))
        self.row=dict(c='3042',ex='tse',d='20260929',t='13:18:00',y='203',z='208.5',h='222.5',l='205',n='晶技')

    def test_high_near_limit_is_not_claimed_as_last_price(self):
        from market_radar import watch
        signal=watch.signals(self.row,self.now)[0]
        self.assertEqual(signal['level'],8)
        self.assertAlmostEqual(signal['change'],9.6059,places=3)
        self.assertEqual(signal['last'],208.5)

    def test_reject_old_future_stale_invalid_and_wrong_exchange_quotes(self):
        from market_radar import watch
        for override in ({'d':'20260924'},{'d':'20260930'},{'t':'13:21:00'},{'t':'09:10:00'},{'z':'-'},{'y':'0'},{'ex':'otc'}):
            self.assertEqual(watch.signals(dict(self.row,**override),self.now),[])

    def test_bounded_escalation_dedup_and_delivery_audit(self):
        import tempfile,json
        from pathlib import Path
        from market_radar import watch,notify
        from market_radar.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'s.db')
            rows=[dict(self.row,h='214'),dict(self.row,c='2330',z='203',h='203',l='203'),dict(self.row,c='6274',ex='otc',z='203',h='203',l='203')]
            with patch.object(watch,'fetch',return_value=rows),patch.object(notify,'call',return_value={'message_id':789}) as send:
                self.assertEqual(watch.check(s,self.now,'test','test',True),1)
                self.assertEqual(watch.check(s,self.now,'test','test',True),0)
                rows[0]['h']='222.5'
                self.assertEqual(watch.check(s,self.now,'test','test',True),1)
                self.assertEqual(watch.check(s,self.now,'test','test',True),0)
                self.assertEqual(send.call_count,2)
                payload=json.loads(send.call_args.args[2])['text']
                self.assertIn('非 BUY',payload)
                self.assertIn('208.5',payload)
            audits=[json.loads(r[0]) for r in s.db.execute("select value from meta where key like 'delivery-audit:%'")]
            self.assertEqual([a['message_id'] for a in audits],[789,789])
            self.assertTrue(all(len(a['payload_sha256'])==64 for a in audits))
            s.close()

    def test_holiday_and_outside_window_do_not_fetch(self):
        import datetime as dt
        from market_radar import watch
        with patch.object(watch,'fetch') as fetch:
            self.assertEqual(watch.check(None,self.now.replace(hour=23,minute=30)),0)
            self.assertEqual(watch.check(None,self.now.replace(day=27)),0)
            fetch.assert_not_called()


    def test_cloud_fetch_uses_public_api_without_unneeded_homepage(self):
        from market_radar import watch
        with patch.object(watch.requests,'Session') as session:
            get=session.return_value.__enter__.return_value.get
            get.return_value.json.return_value={'rtcode':'0000','msgArray':[self.row]}
            self.assertEqual(watch.fetch(),[self.row])
            self.assertEqual(get.call_count,1)
            self.assertEqual(get.call_args.args[0],watch.URL)

    def test_late_recovery_reports_high_once_then_stops_close_polling(self):
        import tempfile
        from pathlib import Path
        from market_radar import watch,notify
        from market_radar.store import Store
        rows=[dict(self.row,t='13:30:00'),dict(self.row,c='2330',z='203',h='203',l='203'),dict(self.row,c='6274',ex='otc',z='203',h='203',l='203')]
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'s.db')
            with patch.object(watch,'fetch',return_value=rows) as fetch,patch.object(notify,'call',return_value={'message_id':790}):
                self.assertEqual(watch.check(s,self.now.replace(hour=16),'test','test',True),1)
                self.assertEqual(watch.check(s,self.now.replace(hour=17),'test','test',True),0)
                self.assertEqual(fetch.call_count,1)
            s.close()
