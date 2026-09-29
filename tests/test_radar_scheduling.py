import argparse,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import requests
from market_radar import cli,financials
from market_radar.store import Store

class SchedulingTests(unittest.TestCase):
    def test_daily_never_fetches_filings_or_historical_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=argparse.Namespace(state=str(Path(tmp)/'s.db'), output=tmp,price_seed=tmp+'/missing',mode='daily',send=False,financial_limit=300)
            snapshot={'day':'2026-09-29','stocks':[],'scanned':0,'health':[],'price_dates':[]}
            with patch.object(cli.sources,'collect',return_value=({},[])),patch.object(cli.engine,'scan',return_value=snapshot),patch.object(cli.engine,'apply_swing_gate'),patch.object(cli.reports,'render',return_value='ok'),patch.object(cli.reports,'full_report',return_value='ok'),patch.object(cli.history,'backfill',return_value={}) as backfill,patch.object(cli.financials,'complete',return_value=({},{})) as complete:
                cli.run(args)
            backfill.assert_not_called()
            complete.assert_not_called()

    def test_transport_failure_retries_later_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'s.db')
            companies=[{'code':'6274','market':'tpex'}]
            s.ingest({('tpex','financial'):[{'code':'6274','period':'2026Q2','eps':12.4,'net':3601714}]})
            good=Mock(status_code=200,content=(Path(__file__).parent/'fixtures/6274-filing.xml').read_bytes())
            with patch.object(financials.requests,'get',side_effect=[requests.Timeout(),good]) as fetch,patch.object(financials.time,'sleep'):
                financials.complete(s,companies,'2026-09-29T08:00:00+08:00')
                rows,status=financials.complete(s,companies,'2026-09-29T10:00:00+08:00')
            s.close()
            self.assertEqual(status['verified'],1)
            self.assertEqual(fetch.call_count,2)

    def test_refresh_starts_before_cache_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'s.db'); companies=[{'code':'6274','market':'tpex'}]
            s.ingest({('tpex','financial'):[{'code':'6274','period':'2026Q2','eps':12.4,'net':3601714}]})
            good=Mock(status_code=200,content=(Path(__file__).parent/'fixtures/6274-filing.xml').read_bytes())
            with patch.object(financials.requests,'get',return_value=good) as fetch,patch.object(financials.time,'sleep'):
                financials.complete(s,companies,'2026-09-23T08:00:00+08:00')
                financials.complete(s,companies,'2026-09-27T08:00:00+08:00')
            s.close()
            self.assertEqual(fetch.call_count,2)

class MaintenanceStateTests(unittest.TestCase):
    def test_merge_keeps_newer_facts_and_never_imports_receipts(self):
        from market_radar.maintenance import import_public
        with tempfile.TemporaryDirectory() as tmp:
            a=Store(Path(tmp)/'a.db'); b=Store(Path(tmp)/'b.db')
            old={'code':'2330','period':'2026Q2','fetched_at':'2026-09-23','net':1}
            new=dict(old,fetched_at='2026-09-29',net=2)
            a.ingest({('twse','supplement'):[old]}); a.mark_sent('existing','today')
            b.ingest({('twse','supplement'):[new]}); b.mark_sent('foreign','today')
            b.meta('delivery:foreign','uncertain'); b.close()
            import_public(a,Path(tmp)/'b.db')
            row=a.history('supplement','2330')[0]; receipts=list(a.db.execute('select id from receipts'))
            self.assertEqual(row,new); self.assertEqual(receipts,[('existing',)])
            self.assertIsNone(a.meta('delivery:foreign'))
            b=Store(Path(tmp)/'b.db'); b.ingest({('twse','supplement'):[old]}); b.close()
            import_public(a,Path(tmp)/'b.db')
            row=a.history('supplement','2330')[0]; a.close()
            self.assertEqual(row,new)

    def test_expired_or_revised_cached_fact_is_not_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'s.db'); companies=[{'code':'6274','market':'tpex'}]
            f={'code':'6274','period':'2026Q2','eps':12.4,'net':3601714}
            row=financials.parse_filing((Path(__file__).parent/'fixtures/6274-filing.xml').read_bytes(),'6274','2026Q2','2026-09-23')
            s.ingest({('tpex','financial'):[f],('tpex','supplement'):[row]})
            self.assertEqual(financials.cached(s,companies,'2026-09-30')[1]['verified'],1)
            self.assertEqual(financials.cached(s,companies,'2026-10-01')[1]['verified'],0)
            s.ingest({('tpex','financial'):[dict(f,eps=13)]})
            actual=financials.cached(s,companies,'2026-09-29')[1]['verified']; count=len(s.history('supplement','6274')); s.close()
            self.assertEqual(actual,0); self.assertEqual(count,1)

    def test_history_backlog_rotates_failures_instead_of_starving_dates(self):
        from market_radar import history
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'s.db')
            companies=[{'code':'2330','market':'twse'}]
            with patch.object(history,'fetch_day',side_effect=RuntimeError('offline')) as fetch,patch.object(history.time,'sleep'):
                history.backfill(s,companies,['2026-09-21','2026-09-22'],limit=1)
                history.backfill(s,companies,['2026-09-21','2026-09-22'],limit=1)
            calls=[call.args[1] for call in fetch.call_args_list];s.close()
            self.assertEqual(calls,['2026-09-22','2026-09-21'])

    def test_manual_delivery_does_not_consume_scheduled_delivery(self):
        import os
        from market_radar import notify
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            args=argparse.Namespace(state=str(Path(tmp)/'s.db'),output=tmp,price_seed=tmp+'/missing',maintenance_state=tmp+'/missing',mode='daily',send=True,delivery_slot='manual')
            snapshot={'day':'2026-09-29','stocks':[],'scanned':0,'health':[],'price_dates':[]}
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test'}),patch.object(cli.sources,'collect',return_value=({},[])),patch.object(cli.engine,'scan',return_value=snapshot),patch.object(cli.engine,'apply_swing_gate'),patch.object(cli.reports,'render',return_value='ok'),patch.object(cli.reports,'full_report',return_value='ok'),patch.object(notify,'call',return_value={'message_id':123}) as send:
                cli.run(args)
                cli.run(args)
                args.delivery_slot='scheduled'
                cli.run(args)
            self.assertEqual(send.call_count,2)

    def test_failed_scheduled_report_recovers_and_idle_retry_does_no_work(self):
        import os
        from market_radar import notify
        with tempfile.TemporaryDirectory() as tmp:
            args=argparse.Namespace(state=str(Path(tmp)/'s.db'),output=tmp,price_seed=tmp+'/missing',maintenance_state=tmp+'/missing',mode='daily',send=True,delivery_slot='scheduled')
            snapshot={'day':'2026-09-29','stocks':[],'scanned':0,'health':[],'price_dates':[]}
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test'}),patch.object(cli.sources,'collect',return_value=({},[])) as collect,patch.object(cli.engine,'scan',side_effect=[RuntimeError('source down'),snapshot,snapshot]),patch.object(cli.engine,'apply_swing_gate'),patch.object(cli.reports,'render',return_value='ok'),patch.object(cli.reports,'full_report',return_value='ok'),patch.object(notify,'call',return_value={'message_id':123}) as send:
                with self.assertRaises(RuntimeError):cli.run(args)
                args.mode='retry';cli.run(args)
                args.mode='retry';cli.run(args)
            self.assertEqual(collect.call_count,2)
            self.assertEqual(send.call_count,2)
