"""P2 strategy: setup-aware shortlist, 整理承接 signal, three-stage labels.

These verify definitions and that no gate was relaxed. They say nothing about
profitability; that needs forward samples (see RADAR.md).
"""
import copy
import unittest
from unittest.mock import patch

import config
import radar
from radar import technical_plan, evaluate, shortlist, stage, confirmation, net_rr
from tests.test_radar import bars, catalyst, ITEM, CONTEXT, FACTS, PLAN

AS_OF = "2026-09-21"


def consolidation_bars(box_volume=1_500_000, last_close_offset=0.85):
    sample = bars()
    level = sample[-21]["close"]
    for b in sample[-20:-10]:                               # base before the step up
        b.update(open=level - 0.1, high=level + 0.4, low=level - 0.4, close=level)
    for b in sample[-10:]:                                  # tight box on top of MA20
        b.update(open=level + 0.95, high=level + 1.4, low=level + 0.6, close=level + 1.0, volume=box_volume)
    sample[-1].update(open=level + 0.75, close=level + last_close_offset)
    return sample, level


class ConsolidationTests(unittest.TestCase):
    def test_box_on_ma20_with_quiet_volume_is_consolidation_entry_near_floor(self):
        sample, level = consolidation_bars()
        plan, reason = technical_plan(sample, AS_OF)
        self.assertIsNone(reason)
        self.assertEqual(plan["kind"], "consolidation")
        box_low = level + 0.6
        self.assertLess(plan["stop"], box_low)                       # invalid below the box
        self.assertGreater(plan["entry_low"], box_low)
        self.assertLessEqual(plan["entry_low"], sample[-1]["close"])
        self.assertEqual(plan["target"], 125)                         # historical pivot, not invented
        self.assertGreaterEqual(net_rr(plan["entry_high"], plan["stop"], plan["target"]), config.RADAR_MIN_RR - 1e-9)

    def test_expanding_volume_is_not_consolidation(self):
        sample, _ = consolidation_bars(box_volume=6_000_000)
        plan, _ = technical_plan(sample, AS_OF)
        self.assertNotEqual((plan or {}).get("kind"), "consolidation")

    def test_close_in_upper_half_is_breakout_not_consolidation(self):
        sample, _ = consolidation_bars(last_close_offset=1.3)
        plan, _ = technical_plan(sample, AS_OF)
        self.assertEqual(plan["kind"], "breakout")

    def test_box_below_ma20_is_rejected(self):
        sample, level = consolidation_bars()
        for b in sample[-20:-10]:
            b.update(open=level + 3, high=level + 3.4, low=level + 2.6, close=level + 3)
        plan, reason = technical_plan(sample, AS_OF)
        self.assertIsNone(plan)

    def test_consolidation_confirms_on_buyers_not_volume(self):
        rows = [{"volume": 3_000_000}] * 21
        plan = dict(PLAN, kind="consolidation")
        self.assertTrue(confirmation(plan, {"open": 100, "close": 100.5, "volume": 1}, rows))
        self.assertFalse(confirmation(plan, {"open": 100.5, "close": 100.4, "volume": 9e9}, rows))
        self.assertFalse(confirmation(dict(PLAN), {"open": 100, "close": 101, "volume": 1}, rows))


class ShortlistTests(unittest.TestCase):
    def series(self, closes):
        return [dict(date=f"d{i}", open=c, high=c + 0.2, low=c - 0.2, close=c, volume=1_000_000) for i, c in enumerate(closes)]

    def test_prefers_stock_at_setup_over_stock_hugging_ma20_from_below(self):
        below = self.series([100.0] * 20 + [99.95])              # 0.05% under MA20: old ranking's favourite
        at_high = self.series([100 + i * 0.1 for i in range(21)]) # above MA20, at its 20-day high
        universe = [{"stock_id": "1111"}, {"stock_id": "2222"}]
        with patch.object(config, "RADAR_CANDIDATE_LIMIT", 1):
            chosen = shortlist(universe, {"1111": below, "2222": at_high})
        self.assertEqual([c["stock_id"] for c in chosen], ["2222"])

    def test_forced_ids_still_included(self):
        below = self.series([100.0] * 20 + [99.95])
        at_high = self.series([100 + i * 0.1 for i in range(21)])
        with patch.object(config, "RADAR_CANDIDATE_LIMIT", 1):
            chosen = shortlist([{"stock_id": "1111"}, {"stock_id": "2222"}], {"1111": below, "2222": at_high}, {"1111"})
        self.assertEqual([c["stock_id"] for c in chosen], ["1111"])


class StageTests(unittest.TestCase):
    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_missing_catalyst_is_waiting_never_ready(self, _):
        out = evaluate(ITEM, bars(), CONTEXT, FACTS, None, AS_OF)
        self.assertEqual(out["status"], "unconfirmed")
        self.assertEqual(out["stage"], "waiting")
        self.assertNotIn("plan", out)                              # no plan without every gate

    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_all_gates_and_entry_zone_is_ready(self, _):
        out = evaluate(ITEM, bars(), CONTEXT, FACTS, catalyst(), AS_OF)
        self.assertIn(out["status"], ("waiting", "triggered"))
        self.assertEqual(out["stage"], "ready")

    @patch("radar.technical_plan", return_value=(None, "尚未接近突破、回測或整理承接觸發區"))
    def test_trend_aligned_but_not_at_entry_is_early(self, _):
        self.assertEqual(evaluate(ITEM, bars(), CONTEXT, FACTS, catalyst(), AS_OF)["stage"], "early")

    @patch("radar.technical_plan", return_value=(None, "日週K趨勢尚未配合"))
    def test_trend_not_aligned_has_no_stage(self, _):
        self.assertIsNone(evaluate(ITEM, bars(), CONTEXT, FACTS, catalyst(), AS_OF)["stage"])

    def test_finished_plans_have_no_stage(self):
        for status in ("review", "expired", "invalid", "target"):
            self.assertIsNone(stage({"status": status, "plan": PLAN, "gates": {"a": True}}))


if __name__ == "__main__":
    unittest.main()
