import copy
import unittest
from portfolio import calculate, risk_state, size_order
from performance import evaluate_signals
from evaluation import replay
from notify import build_messages
from unittest.mock import patch
import config

POLICY = dict(max_loss=1000, drawdown_rate=.05, risk_rate=.01,
              aggregate_risk_rate=.02, max_trade_loss=500)


def event(key, kind, **kwargs):
    return dict(event_id=key, kind=kind, at='2026-09-21T14:00:00+08:00', **kwargs)


class LedgerTests(unittest.TestCase):
    def test_private_storage_rejects_any_git_checkout(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from private_account import private_path
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            with self.assertRaises(ValueError):
                private_path(root / 'docs' / 'account.json')

    def test_private_failed_import_leaves_account_unchanged(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from private_account import update_account
        from storage import write_json, read_json
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'account.json'
            account = {'policy': POLICY, 'events': [event('d', 'deposit', amount=5000)]}
            write_json(path, account)
            with self.assertRaises(ValueError):
                update_account(path, account, {}, '2026-09-21', [event('d', 'deposit', amount=5000)])
            self.assertEqual(read_json(path, {}), account)

    def test_partial_sale_costs_and_cash_reconcile(self):
        events = [event('d', 'deposit', amount=10000),
                  event('b', 'buy', stock_id='1234', shares=50, price=100, fee=10, tax=0),
                  event('s', 'sell', stock_id='1234', shares=20, price=110, fee=10, tax=7)]
        a = calculate(events, {'1234': {'date': '2026-09-21', 'price': 105}}, '2026-09-21')
        self.assertEqual(a['cash'], 7173)
        self.assertEqual(a['realized_pnl'], 179)
        self.assertEqual(a['positions']['1234']['unrealized_pnl'], 144)
        self.assertEqual(a['net_pnl'], 323)
        self.assertEqual(a['net_pnl'], a['realized_pnl'] + 144)

    def test_duplicate_and_oversell_rejected(self):
        deposit = event('d', 'deposit', amount=5000)
        with self.assertRaises(ValueError):
            calculate([deposit, deposit], {}, '2026-09-21')
        with self.assertRaises(ValueError):
            calculate([deposit, event('s', 'sell', stock_id='1234', shares=1, price=100, fee=0, tax=0)], {}, '2026-09-21')

    def test_stale_marks_cannot_issue_position_size(self):
        a = calculate([event('d', 'deposit', amount=5000), event('b', 'buy', stock_id='1234', shares=1, price=100, fee=0, tax=0)], {}, '2026-09-21')
        r = risk_state(a, POLICY)
        self.assertIsNone(a['net_pnl'])
        self.assertTrue(r['blocked'])
        self.assertEqual(size_order(a, r, 100, 95, POLICY)['shares'], 0)

    def test_deposit_does_not_reset_drawdown_or_pause(self):
        a = calculate([event('d', 'deposit', amount=5000)], {}, '2026-09-21')
        initial = risk_state(a, POLICY)
        a.update(equity=4700, net_pnl=-300)
        stopped = risk_state(a, POLICY, initial)
        self.assertTrue(stopped['paused'])
        a.update(equity=9700, deposits=10000)
        later = risk_state(a, POLICY, stopped)
        self.assertTrue(later['paused'])
        self.assertEqual(later['drawdown_allowance'], 250)

    def test_costs_and_cash_constrain_small_account(self):
        a = calculate([event('d', 'deposit', amount=5000)], {}, '2026-09-21')
        order = size_order(a, risk_state(a, POLICY), 100, 95, POLICY)
        self.assertLessEqual(order['planned_loss'], 50)
        self.assertLessEqual(order['estimated_cost'], 5000)
        self.assertEqual(order['shares'], 1)  # minimum fees consume most of small risk budget
        self.assertEqual(size_order(a, risk_state(a, POLICY), 10000, 9500, POLICY)['shares'], 0)

    def test_future_and_negative_inputs_rejected(self):
        with self.assertRaises(ValueError):
            calculate([event('d', 'deposit', amount=-1)], {}, '2026-09-21')
        with self.assertRaises(ValueError):
            calculate([event('d', 'deposit', amount=100)], {}, '2026-09-20')


class PerformanceTests(unittest.TestCase):
    @patch('radar.technical_plan')
    def test_missing_catalyst_cannot_keep_old_trigger_alive(self, technical):
        from radar import evaluate
        from test_radar import PLAN, ITEM, FACTS, CONTEXT, bars
        technical.return_value = (PLAN, None)
        old = dict(status='triggered', plan=PLAN, created_on='2026-08-10',
                   triggered_on='2026-08-10', expires_on='2026-08-13', as_of='2026-09-18')
        result = evaluate(ITEM, bars(), CONTEXT, FACTS, None, '2026-09-21', old)
        self.assertEqual(result['status'], 'expired')

    def test_missing_next_session_is_not_fabricated_fill(self):
        p = dict(entry_low=100, entry_high=102, stop=95, target=120)
        bars = [dict(date='2026-09-23', open=101, high=102, low=100, close=101, volume=100)]
        self.assertEqual(replay(p, '2026-09-21', bars)['status'], 'invalid_data')

    def test_default_simulation_exits_at_twenty_not_forty(self):
        from market_clock import session_dates
        dates = session_dates('2026-09-22', '2026-11-30')[:21]
        bars = [dict(date=d, open=101, high=102, low=100, close=101, volume=100) for d in dates]
        p = dict(entry_low=100, entry_high=102, stop=95, target=120, max_holding_sessions=40)
        self.assertEqual(replay(p, '2026-09-21', bars)['sessions_held'], 20)

    def test_no_signal_no_invented_performance(self):
        p = evaluate_signals([], {}, '2026-09-21')
        self.assertIsNone(p['summary']['win_rate'])
        self.assertEqual(p['summary']['closed'], 0)

    def test_rebased_prices_block_simulation(self):
        snap = dict(id='1234', created_on='2026-09-18', triggered_on='2026-09-21',
                    as_of='2026-09-21', observed_close=100, status='triggered', plan={'entry_low': 100})
        p = evaluate_signals([{'snapshot': snap}], {'1234': [{'date': '2026-09-21', 'close': 90}]}, '2026-09-21')
        self.assertEqual(next(iter(p['records'].values()))['outcome']['status'], 'unconfirmed')

    @patch.object(config, 'RADAR_MODE', 'live')
    @patch.object(config, 'NOTIFY_REPORT_UPDATES', False)
    def test_at_most_one_new_idea_same_key_for_evening(self):
        p = dict(entry_low=100, entry_high=102, stop=95, target=120, rr=3)
        item = dict(id='1234', name='fixture', status='triggered', plan=p, created_on='2026-09-18',
                    triggered_on='2026-09-21', expires_on='2026-09-24', current_rr=3)
        cache = dict(as_of='2026-09-21', quality={'passed': True}, radar={'items': [item, {**item, 'id': '2345', 'current_rr': 4}]})
        msgs = build_messages('tw', cache)
        self.assertEqual(len(msgs), 1)
        self.assertIn('2345', msgs[0][1])
        cache['radar']['items'] = [item]
        self.assertEqual(build_messages('tw', cache)[0][0], msgs[0][0])

    @patch.object(config, 'RADAR_MODE', 'shadow')
    @patch.object(config, 'NOTIFY_REPORT_UPDATES', False)
    def test_shadow_silent(self):
        self.assertEqual(build_messages('tw', {'as_of': '2026-09-21', 'quality': {'passed': True}}), [])
