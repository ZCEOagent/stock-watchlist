import copy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import requests
import config
from market_clock import session_dates, last_completed_session, completed_weeks, advance_session
from quality import assess_market, clean_bars
from indicators import compute_indicators
from radar import technical_plan, evaluate, net_rr, verified_catalyst
from radar_data import summarize_evidence
from notify import deliver, send_telegram, build_messages
from evaluation import replay
from tracking import append_highlights
from storage import read_json, write_json
from report import generate_html


def bars(end="2026-09-21", count=140):
    start = (datetime.fromisoformat(end) - timedelta(days=count * 3)).date().isoformat()
    dates = session_dates(start, end, "tw")[-count:]
    assert len(dates) == count, "Insufficient provider sessions for test fixture"
    result = []
    for i, day in enumerate(dates):
        close = 100 + i * 0.01
        result.append(dict(date=day, open=close-0.1, high=close+0.4,
                           low=close-0.4, close=close, volume=3_000_000))
    result[20]["high"] = 125.0  # Independently observed overhead resistance.
    return result


def catalyst():
    return {"reviewed": True, "source_url": "https://example.com/company/announcement",
            "published_at": "2026-09-18T10:00:00+08:00", "observed_on": "2026-09-18",
            "valid_until": "2026-09-24", "thesis": "fixture", "impact": "fixture",
            "priced_in_risk": "fixture", "cancel_if": "fixture"}


ITEM = {"stock_id": "2330", "stock_name": "測試股票", "sector": "半導體"}
CONTEXT = {"breadth": 0.6, "benchmark": 0.0, "sectors": {"半導體": {"count": 10, "return": 0.01}}}
FACTS = {"fundamental_ok": True, "chips_ok": True}
PLAN = {"kind": "breakout", "entry_low": 100, "entry_high": 102, "stop": 95, "target": 120,
        "atr": 2, "rr": 3, "close": 101, "return20": .02, "volume_ratio": 1.5,
        "holding_sessions": [10, 20], "max_holding_sessions": 40}


class QualityTests(unittest.TestCase):
    def test_stale_stock_is_not_current_signal(self):
        history = {"2330": bars(), "9999": bars()[:-1]}
        valid, q = assess_market([ITEM, {"stock_id": "9999"}], history, "2026-09-21", "stock_id")
        self.assertEqual(set(valid), {"2330"})
        self.assertEqual(q["rejected"]["9999"], "stale")
        self.assertFalse(q["passed"])

    def test_nan_zero_and_conflicting_duplicates(self):
        sample = bars()[-2:]
        sample += [{**sample[0], "close": math.nan}, {**sample[1], "close": 9}]
        _, errors = clean_bars(sample, "2026-09-21", True)
        self.assertTrue(errors)

    def test_today_volume_excluded_from_baseline(self):
        sample = bars()[-21:]
        sample[-1]["volume"] = 6_000_000
        self.assertEqual(compute_indicators(sample)["volume_ratio"], 2.0)

    def test_insufficient_history_does_not_screen(self):
        self.assertIsNone(compute_indicators(bars()[-2:]))

    def test_tracking_dedupes_actual_data_date(self):
        highlight = {"id": "2330", "name": "test", "latest_date": "2026-09-18", "close": 100,
                     "change_pct": 1, "tags": []}
        log = append_highlights([], [highlight], "tw", "2026-09-20")
        log = append_highlights(log + log, [highlight], "tw", "2026-09-21")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["date"], "2026-09-18")


class CalendarTests(unittest.TestCase):
    def test_provider_closure_overrides_static_calendar(self):
        from market_clock import sessions_after
        with patch("market_clock._tw_sessions", ["2026-01-01", "2026-07-09", "2026-07-13", "2026-12-31"]):
            self.assertEqual(sessions_after("2026-07-09", "2026-07-13"), ["2026-07-13"])
            self.assertEqual(last_completed_session("tw", datetime.fromisoformat("2026-07-10T16:00:00+08:00")), "2026-07-09")

    def test_monday_before_close_uses_friday(self):
        self.assertEqual(last_completed_session("tw", datetime.fromisoformat("2026-09-21T10:00:00+08:00")), "2026-09-18")

    def test_monday_after_buffer_uses_monday(self):
        self.assertEqual(last_completed_session("tw", datetime.fromisoformat("2026-09-21T14:01:00+08:00")), "2026-09-21")

    def test_us_holiday(self):
        self.assertEqual(last_completed_session("us", datetime.fromisoformat("2026-07-03T22:00:00-04:00")), "2026-07-02")

    def test_incomplete_week_not_used(self):
        weeks = completed_weeks(bars(), "2026-09-21")
        self.assertEqual(weeks[-1]["date"], "2026-09-18")

    def test_expiry_counts_sessions_not_calendar_days(self):
        self.assertEqual(advance_session("2026-09-18", 3), "2026-09-23")


class PlanTests(unittest.TestCase):
    def test_target_from_history_and_costs_at_worst_entry(self):
        plan, reason = technical_plan(bars(), "2026-09-21")
        self.assertIsNone(reason)
        self.assertEqual(plan["target"], 125)
        self.assertGreaterEqual(net_rr(plan["entry_high"], plan["stop"], plan["target"]), 1.999)

    def test_no_target_is_not_fabricated(self):
        sample = bars()
        sample[20]["high"] = sample[20]["close"] + .4
        plan, reason = technical_plan(sample, "2026-09-21")
        self.assertIsNone(plan)
        self.assertIn("目標", reason)

    def test_gap_in_recent_sessions_blocks_plan(self):
        sample = bars()
        del sample[-5]
        self.assertIsNone(technical_plan(sample, "2026-09-21")[0])

    def test_risk_reward_after_costs_less_than_gross(self):
        self.assertLess(net_rr(100, 95, 110), 2)
        self.assertEqual(net_rr(100, 99, 100.1), 0)

    def test_future_or_unreviewed_news_is_not_catalyst(self):
        evidence = catalyst()
        self.assertTrue(verified_catalyst(evidence, "2026-09-21"))
        evidence["published_at"] = "2026-09-22T10:00:00+08:00"
        self.assertFalse(verified_catalyst(evidence, "2026-09-21"))
        self.assertFalse(verified_catalyst({"title": "new news"}, "2026-09-21"))

    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_missing_catalyst_never_triggers(self, _):
        value = evaluate(ITEM, bars(), CONTEXT, FACTS, {}, "2026-09-21")
        self.assertEqual(value["status"], "unconfirmed")
        self.assertNotIn("plan", value)

    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_frozen_plan_not_moved_when_price_changes(self, _):
        sample = bars()
        previous = {"status": "waiting", "plan": {**PLAN, "entry_low": 102, "entry_high": 104},
                    "created_on": "2026-09-18", "expires_on": "2026-09-23", "as_of": "2026-09-18"}
        value = evaluate(ITEM, sample, CONTEXT, FACTS, catalyst(), "2026-09-21", previous)
        self.assertEqual(value["plan"]["entry_low"], 102)
        self.assertEqual(value["expires_on"], "2026-09-23")
        self.assertEqual(value["status"], "waiting")

    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_stop_between_scans_cancels_even_after_rebound(self, _):
        sample = bars()
        sample[-2]["low"] = 94
        previous = {"status": "waiting", "plan": PLAN, "created_on": "2026-09-16",
                    "expires_on": "2026-09-23", "as_of": "2026-09-16"}
        value = evaluate(ITEM, sample, CONTEXT, FACTS, catalyst(), "2026-09-21", previous)
        self.assertEqual(value["status"], "invalid")

    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_no_new_signal_after_expiry(self, _):
        prior = {"status": "waiting", "plan": PLAN, "created_on": "2026-09-14",
                 "expires_on": "2026-09-17", "as_of": "2026-09-18"}
        value = evaluate(ITEM, bars(), CONTEXT, FACTS, catalyst(), "2026-09-21", prior)
        self.assertEqual(value["status"], "expired")


class EvidenceTests(unittest.TestCase):
    def test_financial_period_date_never_becomes_catalyst(self):
        bundle = {"revenue": {"observed_on": "2026-09-21", "rows": [
            {"date": "2026-09-01", "revenue_year": 2026, "revenue_month": 8, "revenue": 110},
            {"date": "2025-09-01", "revenue_year": 2025, "revenue_month": 8, "revenue": 100}]},
            "financials": {"rows": [{"date": "2026-06-30", "type": "IncomeAfterTaxes", "value": 100}]}}
        result = summarize_evidence(bundle, "2026-09-21")
        self.assertTrue(result["fundamental_ok"])
        self.assertAlmostEqual(result["revenue_yoy"], 10)
        self.assertFalse(verified_catalyst(result, "2026-09-21"))
        self.assertFalse(result["chips_ok"])


class NotificationTests(unittest.TestCase):
    def response(self, status, body):
        from unittest.mock import Mock
        result = Mock(status_code=status)
        result.json.return_value = body
        return result

    def test_http_success_with_ok_false_is_failure(self):
        status, _ = send_telegram("hello", "dummy", "dummy", post=lambda *a, **k: self.response(200, {"ok": False}))
        self.assertEqual(status, "failed")

    def test_timeout_is_uncertain_not_retried(self):
        from unittest.mock import Mock
        post = Mock(side_effect=requests.Timeout())
        status, _ = send_telegram("hello", "dummy", "dummy", post=post)
        self.assertEqual(status, "uncertain")
        self.assertEqual(post.call_count, 1)

    def test_rate_limit_retry_then_success(self):
        from unittest.mock import Mock
        post = Mock(side_effect=[self.response(429, {"ok": False, "parameters": {"retry_after": 1}}), self.response(200, {"ok": True})])
        self.assertEqual(send_telegram("hello", "dummy", "dummy", post=post, sleep=lambda _: None)[0], "sent")

    def test_dry_run_does_not_write_delivery_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertTrue(deliver([("x", "hello")], state_path=path))
            self.assertFalse(path.exists())

    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "dummy", "TELEGRAM_CHAT_ID": "dummy"})
    @patch("notify.send_telegram", return_value=("sent", None))
    def test_dedup_and_checkpoint_before_transmission(self, post):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            checkpoints = []
            def checkpoint(path):
                checkpoints.append(list(read_json(path, {}).values())[0]["status"])
            self.assertTrue(deliver([("x", "hello")], True, path, checkpoint))
            self.assertTrue(deliver([("x", "hello")], True, path, checkpoint))
            self.assertEqual(post.call_count, 1)
            self.assertEqual(checkpoints, ["sending", "sent"])

    def test_missing_quality_blocks_report_notification(self):
        self.assertEqual(build_messages("tw", {"as_of": "2026-09-21"}), [])


class ReplayTests(unittest.TestCase):
    def test_next_open_outside_range_is_not_filled(self):
        sample = [dict(date="2026-09-22", open=110, high=115, low=109, close=112, volume=100)]
        self.assertEqual(replay(PLAN, "2026-09-21", sample)["status"], "not_filled")

    def test_same_bar_target_and_stop_uses_stop(self):
        sample = [dict(date="2026-09-22", open=101, high=125, low=94, close=110, volume=100)]
        result = replay(PLAN, "2026-09-21", sample)
        self.assertEqual(result["exit_reason"], "stop_first")
        self.assertLess(result["net_return_pct"], 0)

    def test_gap_stop_uses_worse_open(self):
        sample = [dict(date="2026-09-22", open=101, high=103, low=100, close=101, volume=100),
                  dict(date="2026-09-23", open=90, high=96, low=88, close=91, volume=100)]
        result = replay(PLAN, "2026-09-21", sample)
        self.assertEqual(result["exit"], 90)
        self.assertEqual(result["exit_reason"], "gap_stop")

    def test_signal_day_bar_not_used_for_fill(self):
        sample = [dict(date="2026-09-21", open=101, high=125, low=94, close=110, volume=100)]
        self.assertEqual(replay(PLAN, "2026-09-21", sample)["status"], "pending")


class ReportTests(unittest.TestCase):
    def test_separate_dates_and_html_escaping(self):
        meta = {"as_of": "2026-09-18", "generated_at": "2026-09-21T21:00+08:00",
                "radar": {"mode": "shadow", "items": [{"id": "1234", "name": "<script>",
                            "status": "unconfirmed", "reasons": ["missing"]}]}}
        text = generate_html([], [], [], [], [], [], [], 0, 0, 0, 0, "test", meta, {"as_of": "2026-09-17"})
        self.assertIn("台股行情日期：2026-09-18", text)
        self.assertIn("美股行情日期：2026-09-17", text)
        self.assertIn("&lt;script&gt;", text)


if __name__ == "__main__":
    unittest.main()
