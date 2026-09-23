import copy
import unittest
from telegram_account import process_update
from test_portfolio import POLICY


def update(uid, text, chat=123, sender=123, kind='private'):
    return {'update_id': uid, 'message': {'date': 1790049600,
            'chat': {'id': chat, 'type': kind}, 'from': {'id': sender, 'is_bot': False}, 'text': text}}


class TelegramAccountTests(unittest.TestCase):
    def setUp(self):
        self.account = {'policy': POLICY, 'events': []}

    def test_unauthorized_or_group_cannot_read_or_write(self):
        for message in (update(1, '/deposit 5000', chat=456, sender=456),
                        update(1, '/status', sender=456), update(1, '/status', kind='group')):
            result, reply = process_update(self.account, message, '123', {}, '2026-09-22')
            self.assertIsNone(reply)
            self.assertEqual(result['events'], [])

    def test_replayed_update_never_duplicates_deposit(self):
        result, reply = process_update(self.account, update(1, '/deposit 5000'), '123', {}, '2026-09-22')
        self.assertEqual(result['snapshot']['cash'], 5000)
        again, reply = process_update(result, update(1, '/deposit 5000'), '123', {}, '2026-09-22')
        self.assertIsNone(reply)
        self.assertEqual(again['snapshot']['cash'], 5000)

    def test_explicit_fill_time_and_partial_sale(self):
        a, _ = process_update(self.account, update(1, '/deposit 5000'), '123', {}, '2026-09-22')
        a, text = process_update(a, update(2, '/buy 1234 10 100 1 0 2026-09-22T13:30:00+08:00'), '123', {}, '2026-09-22')
        self.assertEqual(len(a['events']), 2)
        self.assertIsNone(a['snapshot']['net_pnl'])
        marks = {'1234': {'date': '2026-09-22', 'price': 110}}
        a, _ = process_update(a, update(3, '/sell 1234 5 110 1 2 2026-09-22T13:31:00+08:00'), '123', marks, '2026-09-22')
        self.assertEqual(a['snapshot']['positions']['1234']['shares'], 5)
        self.assertEqual(a['snapshot']['realized_pnl'], 46.5)

    def test_bad_input_never_modifies_events(self):
        for command in ('/deposit NaN', '/deposit -5', '/buy 1234 1 100', '/buy 1234 1 100 0 0 2027-01-01T12:00:00+08:00'):
            result, reply = process_update(self.account, update(1, command), '123', {}, '2026-09-22')
            self.assertEqual(result['events'], [])
            self.assertIn('未記帳', reply)
