import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import main
import config
import radar_pipeline
from storage import read_json, write_json
from radar import evaluate
from test_radar import bars, ITEM, FACTS, CONTEXT, PLAN, catalyst


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.calendar_patch = patch("main.refresh_tw_sessions")
        self.calendar_patch.start()
        self.addCleanup(self.calendar_patch.stop)

    def test_quality_failure_leaves_last_good_cache_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "tw.json"
            write_json(cache, {"last_good": True})
            with patch.object(config, "TW_CACHE_PATH", str(cache)), patch.object(config, "RUNTIME_CACHE_DIR", tmp), \
                 patch("main.get_tw_universe", return_value=[ITEM]), \
                 patch("main.get_tw_history", return_value=({}, "test")), \
                 patch("main.last_completed_session", return_value="2026-09-21"), \
                 patch("main.build_radar") as radar:
                with self.assertRaises(RuntimeError):
                    main.run_tw()
                radar.assert_not_called()
            self.assertEqual(read_json(cache, {}), {"last_good": True})

    def test_limit_run_cannot_write_production_cache_tracking_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(config, "RUNTIME_CACHE_DIR", tmp), \
                 patch("main.get_tw_universe", return_value=[ITEM]), \
                 patch("main.get_tw_history", return_value=({"2330": bars()}, "test")), \
                 patch("main.last_completed_session", return_value="2026-09-21"), \
                 patch("main.get_tw_news", return_value=[]), \
                 patch("main.build_radar", return_value={}) as radar, \
                 patch("main.data_cache.save_market_cache") as save, \
                 patch("main.tracking.save_log") as log:
                main.run_tw(limit=1)
                save.assert_not_called()
                log.assert_not_called()
                self.assertFalse(radar.call_args.kwargs["persist"])
            self.assertTrue((Path(tmp) / "test_tw_cache.json").exists())

    def test_unknown_adjusted_data_preserves_previous_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {"RADAR_STATE_PATH": str(Path(tmp)/"state.json"),
                     "RADAR_EVENTS_PATH": str(Path(tmp)/"events.json"),
                     "RADAR_EVIDENCE_PATH": str(Path(tmp)/"evidence.json"),
                     "RADAR_WATCHLIST_PATH": str(Path(tmp)/"watchlist.json")}
            prior = {"status": "waiting", "plan": PLAN, "created_on": "2026-09-18", "expires_on": "2026-09-23"}
            write_json(paths["RADAR_STATE_PATH"], {"2330": prior})
            with patch.multiple(config, **paths), patch("radar_pipeline.adjusted_histories", return_value=({}, {"2330": "offline"})):
                result = radar_pipeline.build_radar([ITEM], {"2330": bars()}, "2026-09-21", {"passed": True})
                result2 = radar_pipeline.build_radar([ITEM], {"2330": bars()}, "2026-09-21", {"passed": True})
            saved = read_json(paths["RADAR_STATE_PATH"], {})["2330"]
            self.assertEqual(saved["plan"], PLAN)
            self.assertEqual(saved["expires_on"], "2026-09-23")
            self.assertEqual(saved["status"], "unconfirmed")
            self.assertEqual(len(read_json(paths["RADAR_EVENTS_PATH"], [])), 1)

    @patch("radar.technical_plan", return_value=(PLAN, None))
    def test_expired_same_structure_not_rearmed(self, _):
        prior = {"status": "expired", "plan": PLAN, "created_on": "2026-09-14", "expires_on": "2026-09-17"}
        result = evaluate(ITEM, bars(), CONTEXT, FACTS, catalyst(), "2026-09-21", prior)
        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["expires_on"], "2026-09-17")


if __name__ == "__main__":
    unittest.main()
