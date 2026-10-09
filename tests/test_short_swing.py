import unittest
from unittest.mock import patch
import config
from evaluation import replay
from performance import evaluate_signals
from radar import evaluate
from test_radar import PLAN, ITEM, CONTEXT, FACTS, bars, catalyst

class ShortSwingTests(unittest.TestCase):
    def test_two_day_checkpoint_three_day_exit_even_with_old_extension(self):
        rows=[dict(date=d,open=101,high=102,low=100,close=c,volume=100)
              for d,c in [('2026-09-22',101),('2026-09-23',102),('2026-09-24',100)]]
        plan=dict(PLAN,simulation_holding_sessions=40)
        result=replay(plan,'2026-09-21',rows)
        self.assertEqual(result['sessions_held'],3)
        self.assertEqual(result['exit_reason'],'time_exit')
        self.assertEqual(result['exit'],100)
        self.assertEqual(set(result['horizons']),{'2','3'})
        self.assertLess(result['net_return_pct'],(100/101-1)*100)
    @patch('radar.technical_plan',return_value=(PLAN,None))
    def test_old_eight_week_flag_cannot_extend_frozen_plan(self,_):
        prior=dict(status='triggered',plan=PLAN,created_on='2026-09-16',triggered_on='2026-09-16',
                   expires_on='2026-09-21',as_of='2026-09-18')
        evidence=dict(catalyst(),extend_to_8_weeks=True)
        result=evaluate(ITEM,bars(),CONTEXT,FACTS,evidence,'2026-09-21',prior)
        self.assertEqual(result['status'],'expired')
        self.assertEqual(result['plan']['max_holding_sessions'],3)
        self.assertEqual(result['plan']['entry_low'],PLAN['entry_low'])
        self.assertEqual(result['triggered_on'],prior['triggered_on'])
    @patch('radar.technical_plan',return_value=(PLAN,None))
    def test_second_session_is_review_before_third_session_deadline(self,_):
        prior=dict(status='triggered',plan=PLAN,created_on='2026-09-17',triggered_on='2026-09-17',
                   expires_on='2026-09-22',as_of='2026-09-18')
        result=evaluate(ITEM,bars(),CONTEXT,FACTS,catalyst(),'2026-09-21',prior)
        self.assertEqual(result['status'],'review')
    def test_next_open_is_not_labeled_post_alert_performance(self):
        result=evaluate_signals([],{},'2026-09-21')
        self.assertEqual(result['role'],'next_open_benchmark_only')
        self.assertEqual(result['entry_alert_performance']['status'],'pending')
        self.assertEqual(config.RADAR_HOLD_SESSIONS,(2,3))
        self.assertEqual(config.RADAR_MAX_HOLD_SESSIONS,3)
