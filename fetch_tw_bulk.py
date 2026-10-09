"""Bounded official batch quotes with date-scoped, resumable public caches.

Reuse the same TWSE/TPEx parsers already used by market_radar.history. Never
fall back to an hours-long per-stock crawl or substitute an older trading day.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import time

import config
from market_clock import last_completed_session, session_dates
from market_radar.history import fetch_day, URLS
from quality import clean_bars
from storage import read_json, write_json


def get_bulk_history(universe, as_of=None, persist=True):
    as_of = as_of or last_completed_session('tw')
    days = session_dates((date.fromisoformat(as_of)-timedelta(days=65)).isoformat(), as_of)[-30:]
    if len(days) < 21 or days[-1] != as_of:
        raise RuntimeError('官方批次行情：交易日清單不足')
    markets = sorted({s['type'] for s in universe})
    if not set(markets) <= set(URLS):
        raise ValueError('Unknown TW market')
    root = Path(config.RUNTIME_CACHE_DIR) / 'official_daily_v1'
    batches, missing = {}, []
    for market in markets:
        for day in days:
            path = root / market / (day + '.json')
            try:
                saved = read_json(path, {}) if persist else {}
                rows = saved.get('rows')
                valid = (saved.get('market') == market and saved.get('date') == day
                         and saved.get('source') == URLS[market] and isinstance(rows, list)
                         and len(rows) >= (700 if market == 'twse' else 500)
                         and all(r.get('date') == day and r.get('market') == market for r in rows))
            except (ValueError, TypeError, AttributeError):
                valid = False
            if valid:
                batches[market, day] = rows
            else:
                missing.append((market, day))

    def fetch(job):
        market, day = job
        try:
            return job, fetch_day(market, day), None
        except Exception as exc:
            return job, None, type(exc).__name__
        finally:
            time.sleep(.6)

    failures = []
    # Two public requests at most; write each completed date before continuing.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index, ((market, day), rows, error) in enumerate(pool.map(fetch, missing), 1):
            if index % 10 == 0 or index == len(missing):
                print(f'官方批次下載進度：{index}/{len(missing)} 個市場日期', flush=True)
            if error:
                failures.append(f'{market}:{day}:{error}')
                continue
            batches[market, day] = rows
            if persist:
                write_json(root / market / (day + '.json'),
                           dict(market=market, date=day, source=URLS[market], rows=rows))
    print(f'官方批次行情：快取 {len(batches)-len(missing)+len(failures)} 天／市場；'
          f'下載 {len(missing)-len(failures)}；失敗 {len(failures)}', flush=True)
    if failures:
        # Missing entire market dates must not look like complete 21-bar histories.
        raise RuntimeError('官方批次行情缺日，保留前次報告；下輪只補缺日：' + ', '.join(failures))
    ids = {(s['type'], s['stock_id']) for s in universe}
    result = {}
    for (market, day), rows in sorted(batches.items()):
        for row in rows:
            if (market, row['code']) in ids:
                bar = {k: row[k] for k in ('date', 'open', 'high', 'low', 'close', 'volume')}
                result.setdefault(row['code'], []).append(bar)
    # Preserve the current quality gate; invalid or discontinuous recent series
    # cannot acquire a technical signal merely because batching is faster.
    required = days[-21:]
    complete = {}
    for code, rows in result.items():
        clean, errors = clean_bars(rows, as_of, ohlc=True)
        if not errors and [r['date'] for r in clean][-21:] == required:
            complete[code] = clean
    if persist:
        for market in markets:
            for path in (root / market).glob('*.json'):
                if path.stem < days[0]:
                    path.unlink()
    return complete, 'TWSE＋TPEx 官方批次行情（最近30交易日，逐日快取）'
