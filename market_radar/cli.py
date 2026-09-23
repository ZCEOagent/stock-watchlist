"""Run with python -m radar.cli daily|weekly|events. Default is dry-run."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
import config
from market_clock import last_completed_session, session_dates
from storage import read_json

from . import engine, notify, reports, sources, history, financials
from .store import Store


def private_inputs():
    holdings = json.loads(os.environ.get('RADAR_HOLDINGS_JSON', '{}'))
    if not isinstance(holdings, dict) or len(holdings) > 100:
        raise ValueError('RADAR_HOLDINGS_JSON must be an object with at most 100 codes')
    for code, item in holdings.items():
        if not isinstance(code, str) or not code.isdigit() or len(code) != 4 or not isinstance(item, dict):
            raise ValueError('invalid holdings format')
        if 'stop' in item:
            stop = sources.number(item['stop'])
            if stop is None or stop <= 0:
                raise ValueError('holding stop must be positive')
            item['stop'] = stop
    supplements = json.loads(os.environ.get('RADAR_FINANCIALS_JSON', '{}'))
    if not isinstance(supplements, dict):
        raise ValueError('financial supplements must be an object')
    for code, item in supplements.items():
        if not isinstance(item, dict):
            raise ValueError('invalid financial supplement')
        for field in ('prior_year_eps', 'operating_cash_flow'):
            if field in item:
                item[field] = sources.number(item[field])
    return holdings, supplements


def ingest_seed(store, path):
    if not Path(path).exists():
        return 0
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    # The current Bot already exports quality-checked history to its runtime cache.
    if 'history' in data:
        data = data['history']
    records = []
    for code, rows in data.items():
        if not code.isdigit() or len(code) != 4:
            continue
        for r in rows:
            day, close, volume = sources.date(r.get('date')), sources.number(r.get('close')), sources.number(r.get('volume'))
            if day and close is not None and close > 0 and volume is not None and volume > 0:
                records.append({'code': code, 'date': day, 'source_date': day, 'close': close, 'volume': volume,
                                'source': 'https://github.com/ZCEOagent/stock-watchlist', 'fetched_at': None})
    # Ingest seed first; the current official quote overwrites the matching day.
    store.ingest({('seed', 'price'): records})
    return len(records)


def run(args):
    now = dt.datetime.now(ZoneInfo('Asia/Taipei'))
    today, stamp = now.date().isoformat(), now.isoformat()
    holdings, supplements = private_inputs()
    token, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
    if args.send and (not token or not chat):
        raise RuntimeError('Telegram configuration missing; no notification sent')
    store = Store(args.state)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        previous = store.previous(today)
        ingest_seed(store, args.price_seed)
        feeds, health = sources.collect(stamp, events_only=args.mode == 'events')
        # An out-of-date/future row must never become current evidence.
        for key, rows in feeds.items():
            feeds[key] = [r for r in rows if r.get('source_date') and r['source_date'] <= today]
            if rows and not feeds[key]:
                for status in health:
                    if (status['market'], status['kind']) == key:
                        status.update(ok=False, count=0)
        store.ingest(feeds)
        if args.mode == 'events':
            snapshot = store.latest()
            candidates = {r['code'] for r in [s for s in (snapshot or {}).get('stocks', []) if s['candidate']][:5]}
            relevant = set(engine.PRIORITY) | set(holdings) | candidates
            since = (now.date() - dt.timedelta(days=3)).isoformat()
            events = [e for e in store.events_since(since) if e['code'] in relevant and e['review_required']]
            pending = [e for e in events if not store.sent('event:' + e['id'])]
            # Non-holding events are capped per day, not just per polling batch.
            public_sent = int(store.meta('public-events:' + today) or '0')
            held = [e for e in pending if e['code'] in holdings]
            public = [e for e in pending if e['code'] not in holdings][:max(0, 3-public_sent)]
            pending = held + public
            # Do not emit an initialization avalanche. Bound first/backlog batch to 5; retain the rest.
            if pending:
                batch = pending[:5]
                text = '台股雷達｜重大事件排程提醒（非即時行情）\n' + '\n\n'.join(
                    ('③ 持股風險警報' if e['code'] in holdings else '② 評分大幅變化｜公告待核實，尚未改分') +
                    f"\n{e['code']}｜公告 {e['published']}\n{e['title'][:350]}\n{e['source']}" for e in batch)
                text += '\n\n關鍵字只提示人工查閱原文，不表示已證實違約、舞弊或應賣出。'
                (output / 'events.txt').write_text(text, encoding='utf-8')
                if args.send:
                    key = reports.message_key('events', *sorted(e['id'] for e in batch))
                    notify.deliver(store, token, chat, key, text, stamp)
                    for e in batch:
                        store.mark_sent('event:' + e['id'], stamp)
                    store.meta('public-events:' + today, public_sent + sum(e['code'] not in holdings for e in batch))
            print(f"Event check: {len(pending)} pending; {sum(not h['ok'] for h in health)} source failures")
            if any(not h['ok'] for h in health):
                raise RuntimeError('重大事件來源不完整；已保留資料及送達紀錄，等待下次重試')
            return
        companies = [r for (_, kind), rows in feeds.items() if kind == 'universe' for r in rows]
        as_of = last_completed_session('tw')
        days = session_dates((dt.date.fromisoformat(as_of)-dt.timedelta(days=65)).isoformat(), as_of)[-30:]
        history_health = history.backfill(store, companies, days)
        automatic, financial_health = financials.complete(store, companies, stamp, args.financial_limit)
        # Verified same-filing facts take precedence over optional manual supplements.
        supplements.update(automatic)
        try:
            snapshot = engine.scan(feeds, health, store, today, supplements,
                                   expected_date=as_of)
        except RuntimeError:
            text = (f'台股雷達 {today}｜全市場資料不完整，保留上次排名。\n\n'
                    '① 今日新進雷達\n無法確認。\n\n② 評分大幅變化\n暫停比較。\n\n'
                    '③ 持股風險警報\n本輪無法完成風險核實。\n\n④ 本週真正值得考慮交易的標的\n本輪不產生 BUY 決策；請等待資料恢復。')
            (output / 'summary.txt').write_text(text, encoding='utf-8')
            if args.send:
                notify.deliver(store, token, chat, reports.message_key('outage', today), text, stamp)
            raise
        snapshot['fetched_at'] = stamp
        snapshot['completion'] = {'history': history_health, 'financials': financial_health}
        engine.apply_swing_gate(snapshot, read_json(config.TW_CACHE_PATH, {}), config.RADAR_MODE)
        # Public research snapshots contain no position sizes, stops or ownership flags.
        store.snapshot(today, snapshot)
        summary = reports.render(snapshot, previous, holdings, weekly=args.mode == 'weekly')
        full = reports.full_report(snapshot, previous, holdings)
        (output / 'summary.txt').write_text(summary, encoding='utf-8')
        (output / 'decision-report.txt').write_text(full, encoding='utf-8')
        (output / 'ranking.json').write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        if args.send:
            key = reports.message_key(args.mode, today, engine.VERSION)
            notify.deliver(store, token, chat, key, summary, stamp)
            risks = reports.holding_risks(snapshot['stocks'], holdings)
            if args.mode == 'weekly' or len(risks) > 3:
                notify.deliver(store, token, chat, key + ':full', full, stamp, document=True)
        print(f"Radar {args.mode}: scanned={snapshot['scanned']}; candidates={sum(s['candidate'] for s in snapshot['stocks'])}; "
              f"source_failures={sum(not h['ok'] for h in health)}; delivery={'enabled' if args.send else 'dry-run'}")
        store.prune(today)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('daily', 'weekly', 'events'))
    parser.add_argument('--state', default='.radar/state.sqlite')
    parser.add_argument('--output', default='.radar/reports')
    parser.add_argument('--price-seed', default='.runtime/tw_history.json')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--financial-limit', type=int, choices=range(0, 2201), default=300, metavar='0..2200')
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        # Never print JSON inputs, paths containing tokens, or underlying HTTP exception URLs.
        print(f'Radar failed ({type(exc).__name__}); state preserved; inspect source health and configuration.')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
