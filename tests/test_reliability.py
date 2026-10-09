import datetime as dt
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import config
import fetch_tw_bulk as bulk
from market_radar import monitor
from publish_state import push_with_retry


class BatchHistoryTests(unittest.TestCase):
    def setUp(self):
        self.days = [(dt.date(2026, 9, 1)+dt.timedelta(days=i)).isoformat() for i in range(30)]
        self.universe = [{'stock_id': '2330', 'type': 'twse'}, {'stock_id': '6488', 'type': 'tpex'}]

    def rows(self, market, day):
        codes = ['2330']+[str(3000+i) for i in range(699)] if market == 'twse' else ['6488']+[str(7000+i) for i in range(499)]
        return [dict(code=c, market=market, date=day, open=10, high=12, low=9, close=11, volume=1000) for c in codes]

    def test_cold_warm_and_next_day_request_budgets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config,'RUNTIME_CACHE_DIR',tmp), patch.object(bulk,'session_dates',return_value=self.days), patch.object(bulk.time,'sleep'), patch.object(bulk,'fetch_day',side_effect=self.rows) as fetch:
            result, _ = bulk.get_bulk_history(self.universe, self.days[-1])
            self.assertEqual(fetch.call_count,60)
            self.assertEqual(len(result['6488']),30)
            fetch.reset_mock()
            self.assertEqual(result,bulk.get_bulk_history(self.universe,self.days[-1])[0])
            fetch.assert_not_called()
            next_days=self.days[1:]+['2026-10-01']
            with patch.object(bulk,'session_dates',return_value=next_days):
                bulk.get_bulk_history(self.universe,next_days[-1])
            self.assertEqual(fetch.call_count,2)

    def test_partial_failure_is_saved_and_only_missing_day_retried(self):
        def fail(m,d):
            if m=='tpex' and d==self.days[-1]:raise RuntimeError('down')
            return self.rows(m,d)
        with tempfile.TemporaryDirectory() as tmp, patch.object(config,'RUNTIME_CACHE_DIR',tmp), patch.object(bulk,'session_dates',return_value=self.days), patch.object(bulk.time,'sleep'):
            with patch.object(bulk,'fetch_day',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'缺日'):bulk.get_bulk_history(self.universe,self.days[-1])
            self.assertEqual(len(list(Path(tmp).rglob('*.json'))),59)
            with patch.object(bulk,'fetch_day',side_effect=self.rows) as fetch:
                bulk.get_bulk_history(self.universe,self.days[-1])
                fetch.assert_called_once_with('tpex',self.days[-1])

    def test_missing_stock_bar_and_invalid_ohlc_cannot_create_signal(self):
        def gap(m,d):
            rows=self.rows(m,d)
            if m=='tpex' and d==self.days[-2]:rows=rows[1:]
            if m=='twse' and d==self.days[-1]:rows[0]['high']=1
            return rows
        with patch.object(bulk,'session_dates',return_value=self.days), patch.object(bulk.time,'sleep'), patch.object(bulk,'fetch_day',side_effect=gap):
            result,_=bulk.get_bulk_history(self.universe,self.days[-1],persist=False)
            self.assertNotIn('6488',result)
            self.assertNotIn('2330',result)

    def test_small_run_never_writes_production_cache(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config,'RUNTIME_CACHE_DIR',tmp), patch.object(bulk,'session_dates',return_value=self.days), patch.object(bulk.time,'sleep'), patch.object(bulk,'fetch_day',side_effect=self.rows):
            bulk.get_bulk_history(self.universe,self.days[-1],persist=False)
            self.assertEqual(list(Path(tmp).iterdir()),[])


class PublicationTests(unittest.TestCase):
    def test_real_git_rebases_unrelated_remote_update_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(where,*args):
                return subprocess.check_output(['git','-C',str(where),*args],stderr=subprocess.DEVNULL,text=True).strip()
            remote=root/'origin.git'
            subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
            for name in ('a','b'):
                subprocess.run(['git','clone',str(remote),str(root/name)],check=True,capture_output=True)
                git(root/name,'config','user.email','test@example.invalid')
                git(root/name,'config','user.name','test')
            (root/'a'/'base.txt').write_text('base')
            git(root/'a','add','.')
            git(root/'a','commit','-m','base')
            git(root/'a','push','origin','HEAD:master')
            git(root/'b','pull','origin','master')
            (root/'b'/'other.txt').write_text('other')
            git(root/'b','add','.')
            git(root/'b','commit','-m','other')
            git(root/'b','push','origin','HEAD:master')
            (root/'a'/'report.txt').write_text('new report')
            git(root/'a','add','.')
            git(root/'a','commit','-m','report')
            import os
            old=os.getcwd()
            try:
                os.chdir(root/'a')
                push_with_retry(sleep=lambda _:None)
            finally:os.chdir(old)
            self.assertEqual((root/'a'/'other.txt').read_text(),'other')
            self.assertEqual(git(root/'a','rev-parse','HEAD'),git(root/'a','rev-parse','origin/master'))

    def test_push_response_lost_does_not_replay_remote_commit(self):
        from types import SimpleNamespace
        with patch('publish_state.subprocess.check_output',side_effect=['master\n','abc\n']), patch('publish_state.subprocess.run',side_effect=[SimpleNamespace(returncode=n) for n in [1,0,0]]) as run:
            push_with_retry(sleep=lambda _:None)
            self.assertEqual(sum(c.args[0][1]=='push' for c in run.call_args_list),1)
            self.assertFalse(any(c.args[0][1]=='rebase' for c in run.call_args_list))

    def test_server_failure_retries_and_conflict_never_forces(self):
        from types import SimpleNamespace
        # First push fails, fetch succeeds, remote doesn't contain HEAD, rebase conflicts.
        with patch('publish_state.subprocess.check_output',side_effect=['master\n','abc\n']), patch('publish_state.subprocess.run',side_effect=[SimpleNamespace(returncode=n) for n in [1,0,1,1,0]]) as run:
            with self.assertRaisesRegex(RuntimeError,'版本衝突'):push_with_retry(sleep=lambda _:None)
            self.assertFalse(any('--force' in c.args[0] for c in run.call_args_list))
            self.assertEqual(run.call_args_list[-1].args[0],['git','rebase','--abort'])
        with patch('publish_state.subprocess.check_output',side_effect=['master\n','abc\n']), patch('publish_state.subprocess.run',side_effect=[SimpleNamespace(returncode=n) for n in [1,0,1,0,0]]) as run:
            push_with_retry(sleep=lambda _:None)
            self.assertEqual(sum(c.args[0][1]=='push' for c in run.call_args_list),2)


class AlertNoiseTests(unittest.TestCase):
    def state(self,issues):return dict(issues=issues,financials={},active=[],receipts={},scanned=0,price_dates=[])
    def test_flapping_same_issue_is_quiet_but_new_issue_alerts(self):
        now=dt.datetime(2026,10,8,tzinfo=dt.timezone.utc)
        first=self.state(['source failure'])
        monitor.transition(first,None,now)
        recovered=self.state([])
        self.assertEqual(monitor.transition(recovered,first,now+dt.timedelta(minutes=5)),[])
        flap=self.state(['source failure'])
        self.assertEqual(monitor.transition(flap,recovered,now+dt.timedelta(minutes=10)),[])
        new=self.state(['source failure','delivery uncertain'])
        self.assertTrue(monitor.transition(new,flap,now+dt.timedelta(minutes=11)))
    def test_recovery_needs_stable_half_hour(self):
        now=dt.datetime(2026,10,8,tzinfo=dt.timezone.utc)
        bad=self.state(['fault']);monitor.transition(bad,None,now)
        good=self.state([]);self.assertEqual(monitor.transition(good,bad,now),[])
        stable=self.state([])
        self.assertTrue(any('恢復' in x for x in monitor.transition(stable,good,now+dt.timedelta(minutes=31))))
        self.assertEqual(monitor.transition(self.state([]),stable,now+dt.timedelta(minutes=40)),[])


if __name__=='__main__':unittest.main()
