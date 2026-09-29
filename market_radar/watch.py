"""Scheduled official quote alerts for the explicit watchlist, never trading signals."""
import datetime as dt
import requests
from market_clock import session_dates
from . import sources, notify, reports

URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
WATCH = {'2330': 'tse', '6274': 'otc', '3042': 'tse'}


def fetch():
    with requests.Session() as session:
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
    if not dt.time(9) <= now.time().replace(tzinfo=None) <= dt.time(23) or not session_dates(today,today):
        return 0
    if now.time() >= dt.time(13,30) and store.meta('watch-close:' + today) == 'checked':
        return 0
    rows = fetch()
    if set(WATCH) - {r.get('c') for r in rows}:
        raise ValueError('watch quote response incomplete')
    if any(sources.date(r.get('d')) != today for r in rows if r.get('c') in WATCH):
        raise ValueError('watch quote date stale')
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
            extreme = '最高漲到' if signal['direction'] == 'up' else '最低跌到'
            last_change = (signal['last'] / signal['previous'] - 1) * 100
            text = (f"台股雷達｜{signal['name']}（{signal['code']}）價格提醒\n"
                    f"{period}\n\n"
                    '④ 本週真正值得考慮交易的標的\n'
                    '🟡 先觀察：這是價格異動，非 BUY（買進訊號）。\n\n'
                    f"今天發生什麼？\n"
                    f"• 盤中{extreme} {signal['change']:+.2f}%\n"
                    f"• 最後成交 {signal['last']:g} 元（較前收 {last_change:+.2f}%）\n"
                    f"• 前一交易日收盤 {signal['previous']:g} 元\n\n"
                    '接下來看什麼？\n'
                    '先確認獲利與股價是否合理，不能只因今天大漲或大跌就決定買進。\n\n'
                    f"行情時間：{reports.readable_time(signal['traded'])}\n"
                    f"檢查時間：{reports.readable_time(now.isoformat())}（台灣時間）\n"
                    '來源：證交所公開行情。這是排程檢查，不是觸價當下通知。')
            if send:
                notify.deliver(store,token,chat,reports.message_key(prefix,signal['level']),text,now.isoformat())
                store.mark_sent(prefix+str(signal['level']),now.isoformat())
            count += 1
    if send and now.time() >= dt.time(13,30) and all(sources.number(r.get('z')) and r.get('t') for r in rows if r.get('c') in WATCH):
        store.meta('watch-close:' + today, 'checked')
    return count
