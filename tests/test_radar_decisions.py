import argparse
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from market_radar import cli, decision, reports, notify
from market_radar.store import Store


def row(**updates):
    value = dict(code='3042',name='晶技',status='WATCH',candidate=True,score=87.5,coverage=80,
                 parts={'eps_growth':[10,15],'trend':[10,10]},risks=[],
                 missing=['同交易日有效本益比／至少 5 家同業樣本待補'],
                 reasons=['2026-08 營收年增 23.5%'],financial_period='2026Q2',revenue_period='2026-08',
                 close=208.5,price_date='2026-09-29',ma20=183.43,pe=None,peer_median=None)
    value.update(updates)
    return value


class DecisionTests(unittest.TestCase):
    def test_data_gap_does_not_hide_negative_cash_flow(self):
        r=row(risks=['價格資料缺漏／過期／不符最近完成交易日，禁止新進決策','營業現金流為負'])
        parts=decision.issues(r)
        self.assertEqual(parts['risks'],['營業現金流為負'])
        self.assertTrue(any('價格資料' in x for x in parts['data']))
        text=reports.stock_card(r)
        self.assertIn('風險／待查事件：營運現金流出多於流入',text)
        self.assertIn('等資料：最新股價還沒補齊',text)

    def test_unchanged_watch_is_compact_but_keeps_risk_and_missing(self):
        r=row(risks=['營業現金流為負'])
        text=reports.stock_card(dict(r,close=210),r,compact=True)
        self.assertIn('條件無重大變化',text)
        self.assertIn('仍需留意',text)
        self.assertIn('仍等資料',text)
        self.assertNotIn('看點',text)

    def test_filled_data_not_described_as_earnings_improvement(self):
        old=row();new=row(coverage=100,score=95,missing=[],parts=dict(old['parts'],valuation=[20,20]))
        changes=decision.changes(new,old)
        self.assertTrue(any('資料完整性' in x for x in changes))
        self.assertFalse(any('同口徑評分' in x or '已核實營收' in x for x in changes))

    def test_verified_metric_and_gate_crossing_are_material(self):
        old=row(metrics={'eps_growth':20},parts={'valuation':[8,20]})
        new=row(metrics={'eps_growth':40},parts={'valuation':[15,20]})
        changes=decision.changes(new,old)
        self.assertTrue(any('獲利數字' in x for x in changes))
        self.assertIn('估值條件已通過。',changes)

    def test_price_reference_requires_complete_inputs_and_does_not_invent_interval(self):
        self.assertIsNone(decision.price_reference(row(condition_price_ceiling=190)))
        full=row(missing=[],coverage=100,condition_price_ceiling=190)
        self.assertIn('非正式買點',decision.price_reference(full))
        self.assertIn('沒有交集',decision.price_reference(dict(full,condition_price_ceiling=180)))

    def test_loss_comparator_is_not_claimed_to_be_missing(self):
        r=row(missing=['缺去年同期累計 EPS／去年為負，成長率不適用'],coverage=100,metrics={'prior_year_eps':-1})
        info=decision.issues(r)
        self.assertEqual(info['data'],[])
        self.assertTrue(any('年增率不適用' in x for x in info['conditions']))

    def test_only_successfully_delivered_summary_advances_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=argparse.Namespace(state=tmp+'/s.db',output=tmp,price_seed=tmp+'/none',maintenance_state=tmp+'/none',mode='daily',send=False,delivery_slot='manual')
            snapshot=dict(day='2026-09-29',stocks=[],scanned=0,health=[],price_dates=[],marker='first')
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','RADAR_HOLDINGS_JSON':'{}'}),patch.object(cli.sources,'collect',return_value=({},[])),patch.object(cli.engine,'scan',side_effect=lambda *a,**k:copy.deepcopy(snapshot)),patch.object(cli.engine,'apply_swing_gate'),patch.object(cli.reports,'render',return_value='ok') as render,patch.object(cli.reports,'full_report',return_value='ok'),patch.object(notify,'call',return_value={'message_id':1}) as call:
                cli.run(args)
                s=Store(args.state);self.assertIsNone(s.meta('report-baseline'));s.close()
                args.send=True;call.side_effect=notify.DeliveryRejected('rejected')
                with self.assertRaises(notify.DeliveryRejected):cli.run(args)
                s=Store(args.state);self.assertIsNone(s.meta('report-baseline'));s.close()
                call.side_effect=None;cli.run(args)
                snapshot['marker']='not-delivered';cli.run(args)
                s=Store(args.state);self.assertEqual(json.loads(s.meta('report-baseline'))['marker'],'first');s.close()
                self.assertEqual(render.call_args.args[1]['marker'],'first')
                self.assertEqual(call.call_count,2)

class ChipCalendarTests(unittest.TestCase):
    def test_positive_flows_with_a_missing_session_cannot_pass(self):
        from market_clock import session_dates
        from radar_data import summarize_evidence
        days=session_dates('2026-09-01','2026-09-29')[-6:]
        broken=days[:2]+days[3:]
        rows=[dict(date=d,name=n,buy=100,sell=0) for d in broken for n in ('Foreign_Investor','Investment_Trust')]
        self.assertFalse(summarize_evidence({'chips':{'rows':rows}},days[-1])['chips_ok'])

    def test_exact_five_sessions_can_pass(self):
        from market_clock import session_dates
        from radar_data import summarize_evidence
        days=session_dates('2026-09-01','2026-09-29')[-5:]
        rows=[dict(date=d,name=n,buy=100,sell=0) for d in days for n in ('Foreign_Investor','Investment_Trust')]
        self.assertTrue(summarize_evidence({'chips':{'rows':rows}},days[-1])['chips_ok'])
