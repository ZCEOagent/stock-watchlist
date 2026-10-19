"""P1 state integrity: restore fails closed; same-day reruns cannot erase valid evaluations."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import config
import radar_pipeline
from market_radar import restore
from performance import VERSION, evaluate_signals
from storage import read_json, write_json
from test_radar import bars, ITEM, PLAN

BRANCH = 'master'


def artifact(i, created, branch=BRANCH, expired=False, name='market-radar-state'):
    return {'id': i, 'name': name, 'expired': expired, 'created_at': created,
            'workflow_run': {'id': 1000 + i, 'head_branch': branch}}


class FakeGh:
    """Serves paged artifact listings and records downloads."""
    def __init__(self, artifacts, per_page=restore.PER_PAGE, fail_list=False, fail_download=False):
        self.artifacts, self.per_page = artifacts, per_page
        self.fail_list, self.fail_download = fail_list, fail_download
        self.downloads, self.pages = [], []

    def check_output(self, cmd, text=True):
        if self.fail_list:
            raise subprocess.CalledProcessError(1, cmd)
        page = int(cmd[2].split('page=')[-1])
        self.pages.append(page)
        chunk = self.artifacts[(page - 1) * self.per_page: page * self.per_page]
        return json.dumps({'total_count': len(self.artifacts), 'artifacts': chunk})

    def run(self, cmd, check=True):
        if self.fail_download:
            raise subprocess.CalledProcessError(1, cmd)
        self.downloads.append(cmd[3])
        dest = Path(cmd[cmd.index('--dir') + 1])
        (dest / 'state.sqlite').write_bytes(b'x')


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = str(Path(self.tmp.name) / '.radar')
        env = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'o/r', 'RADAR_DEFAULT_BRANCH': BRANCH})
        env.start()
        self.addCleanup(env.stop)

    def run_restore(self, gh, *args):
        with patch.object(restore.subprocess, 'check_output', gh.check_output), \
             patch.object(restore.subprocess, 'run', gh.run):
            return restore.main(['--destination', self.dest, *args])

    def test_missing_artifact_fails_closed_without_bootstrap(self):
        gh = FakeGh([artifact(1, '2026-10-01T00:00:00Z', branch='feature')])
        self.assertEqual(self.run_restore(gh), 1)
        self.assertEqual(gh.downloads, [])

    def test_bootstrap_allows_empty_baseline_only_when_nothing_retained(self):
        self.assertEqual(self.run_restore(FakeGh([]), '--bootstrap'), 0)
        self.assertFalse(Path(self.dest).exists())

    def test_listing_failure_fails_even_with_bootstrap(self):
        self.assertEqual(self.run_restore(FakeGh([], fail_list=True), '--bootstrap'), 1)

    def test_download_failure_fails(self):
        gh = FakeGh([artifact(1, '2026-10-01T00:00:00Z')], fail_download=True)
        self.assertEqual(self.run_restore(gh, '--bootstrap'), 1)

    def test_newest_artifact_found_beyond_first_page(self):
        old = [artifact(i, f'2026-09-01T00:{i % 60:02d}:00Z') for i in range(restore.PER_PAGE)]
        newest = artifact(999, '2026-10-19T06:00:00Z')
        gh = FakeGh(old + [artifact(998, '2026-10-19T07:00:00Z', expired=True), newest])
        self.assertEqual(self.run_restore(gh), 0)
        self.assertEqual(gh.pages, [1, 2])
        self.assertEqual(gh.downloads, ['1999'])

    def test_feature_branch_artifact_never_restored(self):
        gh = FakeGh([artifact(1, '2026-10-01T00:00:00Z'), artifact(2, '2026-10-02T00:00:00Z', branch='feature')])
        self.assertEqual(self.run_restore(gh), 0)
        self.assertEqual(gh.downloads, ['1001'])


def valid_eval(item, adjusted, context, facts, reviewed, as_of, prior):
    return {'id': item['stock_id'], 'name': item['stock_name'], 'as_of': as_of,
            'status': 'waiting', 'plan': PLAN, 'created_on': as_of, 'expires_on': '2026-09-24',
            'reasons': ['fixture'], 'facts': {}}


class SameDayRerunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = {'RADAR_STATE_PATH': str(root / 'state.json'), 'RADAR_EVENTS_PATH': str(root / 'events.json'),
                      'RADAR_EVIDENCE_PATH': str(root / 'evidence.json'),
                      'RADAR_WATCHLIST_PATH': str(root / 'watchlist.json')}
        for target in (patch.multiple(config, **self.paths), patch('radar_pipeline.EvidenceClient'),
                       patch('radar_pipeline.summarize_evidence', return_value={}),
                       patch('radar_pipeline.evaluate', side_effect=valid_eval)):
            target.start()
            self.addCleanup(target.stop)

    def run_radar(self, as_of, adjusted):
        history = {'2330': bars(as_of)}
        result = ({'2330': history['2330']}, {}) if adjusted else ({}, {'2330': '還原行情過期'})
        with patch('radar_pipeline.adjusted_histories', return_value=result):
            return radar_pipeline.build_radar([ITEM], history, as_of, {'passed': True})

    def state(self):
        return read_json(self.paths['RADAR_STATE_PATH'], {})['2330']

    def test_failed_rerun_keeps_earlier_valid_same_day_evaluation(self):
        self.run_radar('2026-09-21', adjusted=True)
        before = self.state()
        result = self.run_radar('2026-09-21', adjusted=False)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.state()['status'], 'waiting')
        self.assertEqual(result['coverage']['preserved_same_day'], [{'id': '2330', 'reason': '還原行情過期'}])
        item = result['items'][0]
        self.assertEqual(item['status'], 'waiting')
        self.assertIn('保留本日先前有效評估', item['reasons'][-1])
        events = read_json(self.paths['RADAR_EVENTS_PATH'], [])
        self.assertEqual([e['status'] for e in events], ['waiting'])

    def test_older_day_evaluation_never_stands_in_for_today(self):
        self.run_radar('2026-09-18', adjusted=True)
        result = self.run_radar('2026-09-21', adjusted=False)
        saved = self.state()
        self.assertEqual(saved['as_of'], '2026-09-21')
        self.assertEqual(saved['status'], 'unconfirmed')
        self.assertEqual(saved['reasons'], ['還原行情過期'])
        self.assertNotIn('preserved_same_day', result['coverage'])
        self.assertEqual(saved['plan'], PLAN)  # Frozen plan still survives unknown data.

    def test_same_day_failure_without_prior_valid_evaluation_stays_unconfirmed(self):
        write_json(self.paths['RADAR_STATE_PATH'],
                   {'2330': {'id': '2330', 'as_of': '2026-09-21', 'status': 'unconfirmed', 'reasons': ['offline']}})
        self.run_radar('2026-09-21', adjusted=False)
        self.assertEqual(self.state()['status'], 'unconfirmed')
        self.assertEqual(self.state()['reasons'], ['還原行情過期'])

    def test_successful_rerun_still_reevaluates(self):
        write_json(self.paths['RADAR_STATE_PATH'],
                   {'2330': {'id': '2330', 'as_of': '2026-09-21', 'status': 'review', 'observed_close': 1.0}})
        self.run_radar('2026-09-21', adjusted=True)
        self.assertEqual(self.state()['status'], 'waiting')


class SameDayPerformanceTests(unittest.TestCase):
    EVENTS = [{'snapshot': {'id': '2330', 'status': 'triggered', 'plan': PLAN, 'as_of': '2026-09-18',
                            'created_on': '2026-09-17', 'triggered_on': '2026-09-18', 'observed_close': 101.0}}]
    KEY = '2330:2026-09-17:2026-09-18'

    def old(self, evaluated_on, status='open'):
        return {self.KEY: {'signal_id': 'x', 'id': '2330', 'signal_date': '2026-09-18', 'evaluated_on': evaluated_on,
                           'method': VERSION, 'outcome': {'status': status}}}

    def test_same_day_stale_prices_keep_earlier_record(self):
        previous = self.old('2026-09-21')
        result = evaluate_signals(self.EVENTS, {}, '2026-09-21', previous)
        self.assertEqual(result['records'][self.KEY], previous[self.KEY])

    def test_previous_day_record_degrades_to_unconfirmed(self):
        result = evaluate_signals(self.EVENTS, {}, '2026-09-21', self.old('2026-09-18'))
        self.assertEqual(result['records'][self.KEY]['outcome'],
                         {'status': 'unconfirmed', 'reason': 'missing_or_stale_prices'})


if __name__ == '__main__':
    unittest.main()
