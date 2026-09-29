"""Scheduled official quote alerts for the explicit watchlist, never trading signals."""
import datetime as dt
import requests
from market_clock import session_dates
from . import sources, notify, reports

URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
WATCH = {'2330': 'tse', '6274': 'otc', '3042': 'tse'}


def fetch():
    with requests.Session() as session:
        session.get('https://mis.twse.com.tw/stock/index.jsp', timeout=25).raise_for_status()
        response = session.get(URL, params={'ex_ch': '|'.join(f'{m}_{c}.tw' for c,m in WATCH.items()),
                                           'json': '1', 'delay': '0'}, timeout=25)
        response.raise_for_status()
        data = response.json()
    if data.get('rtcode') != '0000' or not isinstance(data.get('msgArray'), list):
        raise ValueError('official quote response invalid')
    return data['msgArray']


def signals(row, now):
    code = row.get('c')
    if code not in WATCH or row.get('ex') != WATCH[code] or sources.date(row.get('d')) != now.date().isoformat():
        return []
    try:
        traded = dt.datetime.combine(now.date(), dt.time.fromisoformat(row['t']), tzinfo=now.tzinfo)
    except (KeyError, ValueError, TypeError):
        return []
    if traded > now or traded.time() < dt.time(9) or traded.time() > dt.time(13,30):
        return []
    if now.time() < dt.time(13,30) and (now-traded).total_seconds() > 900:
        return []
    previous, last, high, low = [sources.number(row.get(k)) for k in ('y','z','h','l')]
    if any(v is None or v <= 0 for v in (previous,last,high,low)) or not low <= last <= high:
        return []
    up, down = (high/previous-1)*100, (low/previous-1)*100
    result = []
    for direction, change in [('up',up), ('down',down)]:
        magnitude = change if direction == 'up' else -change
        level = 8 if magnitude >= 8 else 5 if magnitude >= 5 else None
        if level is not None:
            result.append(dict(code=code, name=row.get('n',code), direction=direction, level=level,
                               change=change, last=last, previous=previous, high=high, low=low,
                               traded=traded.isoformat(), date=now.date().isoformat()))
    return result


def check(store, now, token=None, chat=None, send=False):
    today = now.date().isoformat()
    if not dt.time(9) <= now.time().replace(tzinfo=None) <= dt.time(15) or not session_dates(today,today):
        return 0
    rows = fetch()
    if set(WATCH) - {r.get('c') for r in rows}:
        raise ValueError('watch quote response incomplete')
    count = 0
    for row in rows:
        selected = signals(row,now)
        if selected:
            store.ingest({('watch','watch-quote'):[dict(row,code=row['c'],date=today,source_date=today,source=URL,fetched_at=now.isoformat())]})
        for signal in selected:
            prefix = f"watch-price:{today}:{signal['code']}:{signal['direction']}:"
            if any(store.sent(prefix+str(level)) for level in (5,8) if level >= signal['level']):
                continue
            period = '收盤後回顧' if now.time() >= dt.time(13,30) else '盤中排程檢查'
            text = (f"台股雷達｜{period}（非觸價即時通知）\n\n"
                    f"④ 本週真正值得考慮交易的標的｜觀察名單價格異動，非 BUY\n"
                    f"{signal['code']} {signal['name']}｜當日曾達 {signal['change']:+.2f}%\n"
                    f"前收 {signal['previous']:g}；當日高／低 {signal['high']:g}／{signal['low']:g}\n"
                    f"來源最後成交 {signal['last']:g}，時間 {signal['traded']}\n"
                    f"檢查時間 {now.isoformat()}\n"
                    '價格異動不代表基本面或估值已通過；觀察名單不等於持股。\n'
                    '來源：https://mis.twse.com.tw/stock/index.jsp')
            if send:
                notify.deliver(store,token,chat,reports.message_key(prefix,signal['level']),text,now.isoformat())
                store.mark_sent(prefix+str(signal['level']),now.isoformat())
            count += 1
    return count
