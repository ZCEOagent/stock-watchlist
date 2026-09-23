"""Bulk official historical quotes; exact date validation, persistent retries."""
import concurrent.futures
import time
import requests

from .sources import number, date

URLS = {'twse': 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        'tpex': 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes'}


def parse_day(market, day, payload):
    if str(payload.get('stat', '')).lower() != 'ok' or date(payload.get('date')) != day:
        raise ValueError('Historical quote date/status mismatch')
    names = ({'code': '證券代號', 'close': '收盤價', 'volume': '成交股數',
              'open': '開盤價', 'high': '最高價', 'low': '最低價'} if market == 'twse' else
             {'code': '代號', 'close': '收盤', 'volume': '成交股數', 'open': '開盤', 'high': '最高', 'low': '最低'})
    table = next((t for t in payload.get('tables', []) if all(v in t.get('fields', []) for v in names.values())), None)
    if table is None:
        raise ValueError('Historical quote schema mismatch')
    indices = {k: table['fields'].index(v) for k, v in names.items()}
    result = []
    for row in table.get('data', []):
        if len(row) <= max(indices.values()):
            continue
        code = str(row[indices['code']]).strip()
        if len(code) != 4 or not code.isdigit() or code[0] == '0':
            continue
        values = {k: number(row[i]) for k, i in indices.items() if k != 'code'}
        if any(values[k] is None or values[k] <= 0 for k in ('close', 'volume')):
            continue
        result.append(dict(code=code, market=market, date=day, source_date=day,
                           source=URLS[market], **values))
    return result


def fetch_day(market, day):
    params = {'date': day.replace('-', '') if market == 'twse' else day.replace('-', '/'), 'response': 'json'}
    if market == 'twse':
        params['type'] = 'ALLBUT0999'
    for attempt in range(2):
        try:
            response = requests.get(URLS[market], params=params, timeout=25)
            response.raise_for_status()
            rows = parse_day(market, day, response.json())
            if len(rows) < (700 if market == 'twse' else 500):
                raise ValueError('Historical quote coverage too low')
            return rows
        except (requests.RequestException, ValueError):
            if attempt:
                raise RuntimeError('Official historical quotes unavailable') from None
            time.sleep(2)


def backfill(store, companies, days):
    markets = sorted({c['market'] for c in companies})
    ids = {c['code'] for c in companies}
    pending = [(m, d) for m in markets for d in days if not store.meta(f'history:{m}:{d}')]
    failed, fetched, cached = 0, 0, len(markets)*len(days)-len(pending)
    def one(job):
        m, d = job
        try:
            result = fetch_day(m, d)
            return m, d, result
        except Exception:
            return m, d, None
        finally:
            time.sleep(.6)
    # No SQLite writes occur in worker threads. Only two public API requests in flight.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for market, day, rows in pool.map(one, pending):
            if rows is None:
                failed += 1
                continue
            store.ingest({(market, 'price'): [r for r in rows if r['code'] in ids]})
            store.meta(f'history:{market}:{day}', 'ok')
            fetched += 1
    return {'fetched_days': fetched, 'cached_days': cached, 'failed_days': failed}
