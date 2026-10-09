"""Read-only cloud observer. Never dispatch a scan or modify production state."""
from contextlib import closing
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from . import notify
from .store import Store

UTC = dt.timezone.utc
WORKFLOWS = {'market-radar.yml': 48, 'daily-tw.yml': 96, 'daily-us.yml': 96,
             'health.yml': 36, 'radar-maintenance.yml': 6}


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def restore_artifact(repo, workflow, branch, name, destination):
    """Only trust completed runs from this workflow on the default branch."""
    runs = api(f'repos/{repo}/actions/workflows/{workflow}/runs?branch={branch}&per_page=50')['workflow_runs']
    eligible = {r['id'] for r in runs if r['status'] == 'completed'}
    artifacts = api(f'repos/{repo}/actions/artifacts?name={name}&per_page=100')['artifacts']
    matches = [a for a in artifacts if not a['expired'] and
               a.get('workflow_run', {}).get('id') in eligible and
               a.get('workflow_run', {}).get('head_branch') == branch]
    if not matches:
        return None
    artifact = max(matches, key=lambda a: a['created_at'])
    Path(destination).mkdir(parents=True, exist_ok=True)
    subprocess.run(['gh', 'run', 'download', str(artifact['workflow_run']['id']),
                    '--repo', repo, '--name', name, '--dir', str(destination)], check=True)
    return artifact['workflow_run']['id']


def read_snapshot(path):
    # Read a downloaded copy; do not open/create a production Store.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT body FROM snapshots ORDER BY day DESC LIMIT 1').fetchone()
        if not row:
            raise ValueError('missing snapshot')
        snapshot = json.loads(row[0])
        health_row = db.execute("SELECT value FROM meta WHERE key='source-health:latest'").fetchone()
        if health_row:
            diagnostic = json.loads(health_row[0])
            snapshot['health'] = diagnostic['health']
        snapshot['report_retry'] = bool(db.execute("SELECT value FROM meta WHERE key='report-retry' AND value != ''").fetchone())
        receipt_count, last_receipt = db.execute('SELECT count(*), max(sent_at) FROM receipts').fetchone()
        uncertain = db.execute("SELECT count(*) FROM meta WHERE key LIKE 'delivery:%' AND value IN ('sending','uncertain')").fetchone()[0]
    return snapshot, {'count': receipt_count, 'latest': last_receipt, 'uncertain': uncertain}


def maintenance_status(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row = db.execute("SELECT value FROM meta WHERE key='maintenance:status'").fetchone()
    return json.loads(row[0]) if row else None


def include_maintenance(current, maintenance, now):
    if not maintenance:
        current['issues'].append('缺少背景補件進度')
        return
    stamp = maintenance['fetched_at']
    if (now-timestamp(stamp)).total_seconds() > 6*3600:
        current['issues'].append('背景補件進度超過 6 小時未更新')
        return
    if not current.get('snapshot_at') or timestamp(stamp) >= timestamp(current['snapshot_at']):
        current['financials'] = maintenance['financials']
    current['maintenance_at'] = stamp
    current['maintenance_history'] = maintenance.get('history', {})


def assess(snapshot, receipts, workflows, now):
    issues = []
    active = []
    for name, max_age in WORKFLOWS.items():
        runs = workflows.get(name, [])
        if not runs:
            issues.append(name + ': 找不到執行紀錄')
            continue
        latest = runs[0]
        # New attempts don't erase a failed result until a successful run completes.
        completed = next((r for r in runs if r['status'] == 'completed' and
                          not (r.get('event') == 'workflow_dispatch' and r.get('conclusion') == 'cancelled')), None)
        if completed and completed['conclusion'] != 'success':
            issues.append(name + ': 最近完成的執行未成功')
        if (now - timestamp(latest['created_at'])).total_seconds() > max_age * 3600:
            issues.append(name + ': 排程可能漏跑')
        for run in runs:
            if run['status'] != 'completed':
                active.append({'workflow': name, 'id': run['id'], 'status': run['status']})
                if (now - timestamp(run['created_at'])).total_seconds() > 8 * 3600:
                    issues.append(name + ': 排隊或執行超過 8 小時')
                    break
    if snapshot is None:
        return {'issues': sorted(set(issues + ['找不到可讀取的市場快照'])), 'active': active,
                'scanned': None, 'financials': {}, 'price_dates': [], 'receipts': receipts}
    if not snapshot.get('fetched_at') or (now - timestamp(snapshot['fetched_at'])).total_seconds() > 48 * 3600:
        issues.append('市場快照超過 48 小時未更新')
    if any(not h.get('ok') for h in snapshot.get('health', [])):
        issues.append('市場資料來源有失敗項目')
    if snapshot.get('freshness', {}).get('current_price_ratio', 1) < .9:
        issues.append('超過一成股票缺少最近完成交易日行情，已禁止受影響股票 BUY')
    if snapshot.get('report_retry'):
        issues.append('例行報告尚未完成送達，等待自動重試或通知核對')
    if receipts.get('uncertain'):
        issues.append('有 Telegram 送達狀態不明的紀錄，需人工核對，未自動重送')
    financials = snapshot.get('completion', {}).get('financials', {})
    if not financials or not financials.get('eligible'):
        issues.append('缺少有效的財報核實進度')
    return {'issues': sorted(set(issues)), 'active': active, 'scanned': snapshot.get('scanned'),
            'financials': financials, 'price_dates': snapshot.get('price_dates', []),
            'snapshot_at': snapshot.get('fetched_at'), 'receipts': receipts}


def transition(current, previous, now):
    """Keep routine progress quiet; notify first handoff, changed faults, completion."""
    reasons = []
    if previous is None:
        reasons.append('雲端追蹤已接手')
    # Consolidate alarms here instead of every producer notifying separately.
    # A source flapping between healthy/failed must not flood Telegram.
    alerts = dict((previous or {}).get('issue_alert_times', {}))
    added = set(current['issues']) - set((previous or {}).get('issues', []))
    fresh = [issue for issue in sorted(added) if issue not in alerts or
             (now-timestamp(alerts[issue])).total_seconds() >= 6*3600]
    if previous and fresh:
        reasons.append('新增異常：' + '；'.join(fresh))
    for issue in fresh:
        alerts[issue] = now.isoformat()
    current['issue_alert_times'] = alerts
    current['unresolved_alert'] = bool(current['issues'] or (previous or {}).get('unresolved_alert')
                                       or (previous or {}).get('issues'))
    if not current['issues'] and current['unresolved_alert']:
        since = (previous or {}).get('recovery_since') or now.isoformat()
        current['recovery_since'] = since
        if (now-timestamp(since)).total_seconds() >= 30*60:
            reasons.append('先前異常已恢復（持續確認至少30分鐘）')
            current['unresolved_alert'] = False
    else:
        current['recovery_since'] = None
    fin = current['financials']
    old = (previous or {}).get('financials', {})
    complete = fin.get('eligible', 0) > 0 and fin.get('pending') == 0 and fin.get('verified') == fin.get('eligible')
    if previous and complete and not (old.get('eligible', 0) > 0 and old.get('pending') == 0):
        reasons.append('本輪可核實財報範圍已補齊（不代表所有股票全部資料完整）')
    progress = (fin.get('eligible'), fin.get('verified'))
    old_progress = (old.get('eligible'), old.get('verified'))
    since = (previous or {}).get('progress_since', now.isoformat())
    if previous is None or progress != old_progress:
        since = now.isoformat()
    current['progress_since'] = since
    stalled = bool(fin.get('pending', 0) > 0 and (now - timestamp(since)).total_seconds() > 12 * 3600)
    current['stalled'] = stalled
    if stalled and not (previous or {}).get('stalled'):
        reasons.append('財報補件 12 小時未進展，需檢查來源或解析問題')
    return reasons


def render(current, reasons, repo):
    f = current['financials']
    scanned = current['scanned']
    lines = ['股票雷達｜系統狀態', '；'.join(reasons),
             f"掃描股票：{scanned if scanned is not None else '未知'}",
             f"財報核實：{f.get('verified', '未知')} / 可核實 {f.get('eligible', '未知')}；待補 {f.get('pending', '未知')}",
             '資料中的行情日期：' + (', '.join(current['price_dates']) or '未知')]
    if isinstance(scanned, int) and isinstance(f.get('eligible'), int):
        lines.append(f"尚未列入可核實財報範圍：{max(0, scanned-f['eligible'])} 檔")
    labels = {'daily-tw.yml': '台股收盤報告', 'daily-us.yml': '美股收盤報告',
              'health.yml': '行情時效檢查', 'market-radar.yml': '台股摘要',
              'radar-maintenance.yml': '背景資料補件'}
    for issue in current['issues']:
        for name, label in labels.items():
            issue = issue.replace(name, label)
        lines.append('• ' + issue)
    lines.append('目前仍有雲端掃描執行或排隊；沒有重啟掃描。' if current['active'] else '目前無掃描執行或排隊。')
    if current['issues']:
        lines.append('受影響的資料暫不作買進依據；同一異常不重複洗版，新問題仍會通知。')
    lines += ['詳細原因與執行紀錄：', f'https://github.com/{repo}/actions']
    return '\n'.join(lines)


def run(args):
    repo = os.environ['GITHUB_REPOSITORY']
    branch = os.environ.get('RADAR_DEFAULT_BRANCH', 'master')
    root = Path(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    restored = restore_artifact(repo, 'radar-monitor.yml', branch, 'radar-monitor-state', root / 'state')
    # Losing dedup history must not silently start resending alerts.
    if restored is not None and not (root / 'state/monitor.sqlite').exists():
        raise RuntimeError('monitor checkpoint missing')
    store = Store(root / 'state/monitor.sqlite')
    try:
        snapshot_run = restore_artifact(repo, 'market-radar.yml', branch, 'market-radar-state', root / 'source')
        snapshot, receipts = (None, {}) if snapshot_run is None else read_snapshot(root / 'source/state.sqlite')
        workflows = {name: api(f'repos/{repo}/actions/workflows/{name}/runs?branch={branch}&per_page=30')['workflow_runs'] for name in WORKFLOWS}
        now = dt.datetime.now(UTC)
        current = assess(snapshot, receipts, workflows, now)
        maintenance_run = restore_artifact(repo, 'radar-maintenance.yml', branch,
                                          'market-radar-maintenance', root / 'maintenance')
        maintenance = maintenance_status(root / 'maintenance/state.sqlite') if maintenance_run else None
        include_maintenance(current, maintenance, now)
        current['maintenance_run'] = maintenance_run
        previous_raw = store.meta('observation')
        previous = json.loads(previous_raw) if previous_raw else None
        reasons = transition(current, previous, now)
        current['source_run'] = snapshot_run
        summary = json.dumps(current, ensure_ascii=False, indent=2)
        (root / 'summary.json').write_text(summary, encoding='utf-8')
        print(summary)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as file:
                file.write('### Radar cloud monitor\n```json\n' + summary + '\n```\n')
        if reasons or store.meta('pending_notification'):
            text = render(current, reasons, repo)
            if args.send:
                token, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
                if not token or not chat:
                    raise RuntimeError('Telegram configuration missing')
                # Persist the same key through uncertain delivery; never resend blindly.
                pending = store.meta('pending_notification')
                if pending:
                    payload = json.loads(pending)
                else:
                    key = hashlib.sha256((now.isoformat() + text).encode()).hexdigest()
                    payload = {'key': 'monitor:' + key, 'text': text, 'observation': current}
                    store.meta('pending_notification', json.dumps(payload, ensure_ascii=False))
                notify.deliver(store, token, chat, payload['key'], payload['text'], now.isoformat())
                store.meta('observation', json.dumps(payload['observation'], ensure_ascii=False))
                store.meta('pending_notification', '')
            else:
                print('DRY RUN: ' + text)
        elif args.send:
            store.meta('observation', json.dumps(current, ensure_ascii=False))
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='.monitor')
    parser.add_argument('--send', action='store_true')
    try:
        run(parser.parse_args())
    except Exception as exc:
        print(f'Monitor failed ({type(exc).__name__}); inspect Actions; no automatic scan restart or uncertain resend.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
