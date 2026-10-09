"""Scheduled official quote alerts for the explicit watchlist, never trading signals."""
import datetime as dt
import json
import time
import requests
from market_clock import session_dates
from . import sources, notify, reports, validation, execution

URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
WATCH = {'2330': 'tse', '6274': 'otc', '3042': 'tse'}

# Explicit user watchlist. Exchange membership is resolved from stored official roster.
TRACKED = ('6443','2406','3576','6477','4956','6168','3339','2332','2419','2444',
           '3027','3062','3380','3704','2449','8227','3037','6278','6182','3532',
           '6488','3016','2426','3042','2409','3105','4576')


def universe(store, now, holdings, cache=None):
    wanted = set(TRACKED) | set(holdings) | set(WATCH) | execution.active_codes(store)
    wanted.update(i["id"] for i in (cache or {}).get("radar",{}).get("items",[]) if i.get("plan"))
    snap = store.latest() or {}
    # A stale report must not keep nominating "current" opportunities forever.
    if sources.date(snap.get('day')) and 0 <= (now.date()-dt.date.fromisoformat(snap['day'])).days <= 4:
        wanted.update(r['code'] for r in snap.get('stocks', [])[:10] if r.get('candidate'))
    result = dict(WATCH)
    result.update({t["code"]:t["exchange"] for t in execution.records(store)
                   if t["status"] in ("alert_pending","awaiting_fill","open") and t.get("exchange") in ("tse","otc")})
    for code in sorted(wanted):
        roster = store.history('universe',code)
        if roster:
            row = roster[-1]
            day = sources.date(row.get('source_date'))
            if day and 0 <= (now.date()-dt.date.fromisoformat(day)).days <= 10 and row.get('market') in ('twse','tpex'):
                result[code] = 'tse' if row['market']=='twse' else 'otc'
    return result


def fetch(watchlist=None):
    watchlist = WATCH if watchlist is None else watchlist
    result = []
    items = sorted(watchlist.items())
    with requests.Session() as session:
        for start in range(0,len(items),50):
            try:
                response = session.get(URL, params={'ex_ch': '|'.join(f'{m}_{c}.tw' for c,m in items[start:start+50]),
                                                   'json': '1', 'delay': '0'}, timeout=25)
                response.raise_for_status()
                data = response.json()
                if data.get('rtcode') != '0000' or not isinstance(data.get('msgArray'), list):
                    continue
                result.extend(r for r in data['msgArray'] if isinstance(r,dict))
            except (requests.RequestException, ValueError):
                # Other batches can still contain valid holding risk observations.
                continue
    return result


def signals(row, now, watchlist=None):
    watchlist = WATCH if watchlist is None else watchlist
    if not valid_quote(row,now,watchlist):
        return []
    code = row.get('c')
    if code not in watchlist or row.get('ex') != watchlist[code] or sources.date(row.get('d')) != now.date().isoformat():
        return []
    try:
        traded = dt.datetime.combine(now.date(), dt.time.fromisoformat(row['t']), tzinfo=now.tzinfo)
    except (KeyError, ValueError, TypeError):
        return []
    if traded > now or traded.time() < dt.time(9) or traded.time() > dt.time(13,30):
        return []
    if now.time() < dt.time(13,30) and (now-traded).total_seconds() > 180:
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


def valid_quote(row, now, watched):
    code = row.get('c')
    if code not in watched or row.get('ex') != watched[code] or sources.date(row.get('d')) != now.date().isoformat():
        return False
    try:
        traded = dt.datetime.combine(now.date(),dt.time.fromisoformat(row['t']),tzinfo=now.tzinfo)
        values = [sources.number(row.get(k)) for k in ('y','z','h','l')]
        if any(v is None or v<=0 for v in values):
            return False
        previous,last,high,low = values
        return (traded <= now and dt.time(9)<=traded.time()<=dt.time(13,30)
                and low<=last<=high and ((now.time()>=dt.time(13,30) and traded.time()>=dt.time(13,25))
                or (now.time()<dt.time(13,30) and (now-traded).total_seconds()<=180)))
    except (KeyError,ValueError,TypeError):
        return False


def check(store, now, token=None, chat=None, send=False, holdings=None, cache=None):
    started = time.monotonic()
    holdings = holdings or {}
    today = now.date().isoformat()
    if not dt.time(9) <= now.time().replace(tzinfo=None) <= dt.time(23) or not session_dates(today,today):
        return 0
    if cache is None and now.time() >= dt.time(13,30) and store.meta('watch-close:' + today) == 'checked':
        return 0
    watched = universe(store,now,holdings,cache)
    rows = fetch(watched)
    # Include elapsed I/O time; a quote received during the request is not future data.
    now += dt.timedelta(seconds=max(0,time.monotonic()-started))
    grouped = {}
    for row in rows:
        if isinstance(row,dict) and row.get('c') in watched:
            grouped.setdefault(row['c'],[]).append(row)
    duplicates = sum(len(group)>1 for group in grouped.values())
    rows = [group[0] for group in grouped.values() if len(group)==1]
    execution_error = None
    if cache is not None:
        try:
            execution.process(store,[r for r in rows if valid_quote(r,now,watched)],cache,now,token,chat,send)
        except Exception as exc:
            execution_error = exc  # Still inspect other holding price risks and save quote health.
    missing_codes = set(watched) - {r.get('c') for r in rows}
    valid_count = 0
    for code in missing_codes:
        store.meta(f'watch-confirm:{today}:{code}', '{}')
        validation.observation(store,code,now)
        validation.observation(store,code,now,result='missing_or_duplicate')
    count = 0
    rows = sorted(rows,key=lambda r:(r.get('c') not in holdings,r.get('c') not in WATCH,r.get('c','')))
    for row in rows:
        selected = signals(row,now,watched)
        # Reuse the full quote validation independently of whether price has moved.
        valid = valid_quote(row,now,watched)
        valid_count += bool(valid)
        if not valid and row.get('c') in watched:
            store.meta(f"watch-confirm:{today}:{row['c']}", '{}')
        if valid and now.time() < dt.time(13,30):
            change = (sources.number(row['z'])/sources.number(row['y'])-1)*100
            direction = 'up' if change >= 0 else 'down'
            key = f"watch-confirm:{today}:{row['c']}"
            old = json.loads(store.meta(key) or '{}')
            current = dict(direction=direction,at=now.isoformat(),traded=row['t'],active=abs(change)>=3)
            store.meta(key,json.dumps(current))
            separation = (now-dt.datetime.fromisoformat(old['at'])).total_seconds() if old.get('at') else 0
            confirmed = (current['active'] and old.get('active') and old.get('direction')==direction
                         and 60 <= separation <= 720 and old.get('traded') != row['t'])
            if confirmed and not selected:
                selected = [dict(code=row['c'],name=row.get('n',row['c']),direction=direction,level=3,
                                 change=change,last=sources.number(row['z']),previous=sources.number(row['y']),
                                 high=sources.number(row['h']),low=sources.number(row['l']),traded=dt.datetime.combine(now.date(),dt.time.fromisoformat(row['t']),tzinfo=now.tzinfo).isoformat(),date=today)]
        validation.observation(store,row['c'],now,valid=valid,eligible=selected if send else [])
        if not valid:
            validation.observation(store,row['c'],now,result='invalid_quote')
        elif not selected:
            validation.observation(store,row['c'],now,result='not_triggered_or_unconfirmed')
        if selected:
            store.ingest({('watch','watch-quote'):[dict(row,code=row['c'],date=today,source_date=today,source=URL,fetched_at=now.isoformat())]})
        for signal in selected:
            prefix = f"watch-price:{today}:{signal['code']}:{signal['direction']}:"
            delivered_levels=[level for level in (3,5,8) if store.sent(prefix+str(level))]
            if any(level >= signal['level'] for level in delivered_levels):
                validation.observation(store,row['c'],now,result='already_delivered',direction=signal['direction'],level=max(delivered_levels))
                continue
            if signal['code'] not in holdings and int(store.meta('watch-public-count:'+today) or '0') >= 6:
                validation.observation(store,row['c'],now,result='public_budget')
                continue
            if signal['level']==3 and signal['code'] not in holdings and int(store.meta('watch-early-count:'+today) or '0') >= 3:
                validation.observation(store,row['c'],now,result='early_budget')
                continue
            period = '收盤後回顧' if now.time() >= dt.time(13,30) else '盤中排程檢查'
            extreme = '最高漲到' if signal['direction'] == 'up' else '最低跌到'
            last_change = (signal['last'] / signal['previous'] - 1) * 100
            text = (f"台股雷達｜{signal['name']}（{signal['code']}）價格提醒\n"
                    f"{period}\n\n"
                    '④ 本週真正值得考慮交易的標的\n'
                    '🟡 先觀察：這是價格異動，非 BUY（買進訊號）。\n\n'
                    f"今天發生什麼？\n" +
                    (f"• 連續兩次確認：目前漲跌 {signal['change']:+.2f}%\n" if signal['level']==3 else f"• 盤中{extreme} {signal['change']:+.2f}%\n") +
                    f"• 最後成交 {signal['last']:g} 元（較前收 {last_change:+.2f}%）\n"
                    f"• 前一交易日收盤 {signal['previous']:g} 元\n\n"
                    '接下來看什麼？\n'
                    '先確認獲利與股價是否合理，不能只因今天大漲或大跌就決定買進。\n\n'
                    f"行情時間：{reports.readable_time(signal['traded'])}\n"
                    f"檢查時間：{reports.readable_time(now.isoformat())}（台灣時間）\n"
                    '來源：證交所公開行情。這是排程檢查，不是觸價當下通知。')
            if send:
                delivery_key = reports.message_key(prefix,signal['level'])
                evidence_key = 'watch-evidence:' + delivery_key
                # Persist the quoted observation before sending. If the response was
                # accepted before a crash, never relabel a later quote as that alert.
                observation = dict(code=signal['code'],
                    period=today+':'+signal['direction']+':'+str(signal['level']),date=today,
                    price=signal['last'],direction=signal['direction'],level=signal['level'],
                    traded=signal['traded'],observed_at=now.isoformat(),source=URL)
                if store.sent(delivery_key+':0') or store.meta('delivery:'+delivery_key+':0') in ('sending','uncertain'):
                    observation = json.loads(store.meta(evidence_key) or 'null')
                else:
                    store.meta(evidence_key,json.dumps(observation))
                try:
                    notify.deliver(store,token,chat,delivery_key,text,now.isoformat())
                except Exception:
                    validation.observation(store,row['c'],now,result='delivery_not_confirmed')
                    raise
                validation.observation(store,row['c'],now,result='delivered',direction=signal['direction'],level=signal['level'])
                store.mark_sent(prefix+str(signal['level']),now.isoformat())
                if observation:
                    store.ingest({('watch','watch-signal'):[observation]})
                if signal['code'] not in holdings:
                    store.meta('watch-public-count:'+today,int(store.meta('watch-public-count:'+today) or '0')+1)
                if signal['level']==3 and signal['code'] not in holdings:
                    store.meta('watch-early-count:'+today,int(store.meta('watch-early-count:'+today) or '0')+1)
            count += 1
    old_health = json.loads(store.meta('watch-health') or '{}')
    gap = (now-dt.datetime.fromisoformat(old_health['checked_at'])).total_seconds() if old_health.get('checked_at') else None
    store.meta('watch-health',json.dumps(dict(checked_at=now.isoformat(),requested=len(watched),valid=valid_count,missing=len(missing_codes),duplicate_codes=duplicates,interval_seconds=gap)))
    if not valid_count:
        raise ValueError('No valid watch quotes; inspection incomplete, not zero signals')
    if execution_error is not None:
        raise execution_error
    if send and now.time() >= dt.time(13,30) and valid_count == len(watched):
        store.meta('watch-close:' + today, 'checked')
    return count


def outcomes(store, as_of):
    """Forward observation, not a trade backtest; no manufactured fills or fees."""
    records = [json.loads(r[0]) for r in store.db.execute("SELECT body FROM facts WHERE kind='watch-signal'")]
    records = [r for r in records if r.get('level')==3 and r['date']<=as_of]
    horizons = {}
    for horizon in (1,3,5):
        changes = []
        for row in records:
            days = [d for d in session_dates(row['date'],as_of) if d>row['date']]
            if len(days)<horizon:
                continue
            prices = {p['date']:p for p in store.history('price',row['code']) if p.get('date')}
            target = prices.get(days[horizon-1],{}).get('close')
            if not target or target<=0 or row.get('price',0)<=0:
                continue
            # Negative change supports a downward warning; still not short P&L.
            changes.append((target/row['price']-1)*100*(1 if row['direction']=='up' else -1))
        horizons[str(horizon)] = dict(samples=len(changes),pending=len(records)-len(changes),
            positive_ratio=sum(x>0 for x in changes)/len(changes) if changes else None,
            opposite_ratio=sum(x<0 for x in changes)/len(changes) if changes else None,
            mean_directional_pct=sum(changes)/len(changes) if changes else None)
    return dict(records=len(records),as_of=as_of,horizons=horizons,
                method='observed-quote-to-future-close; unadjusted gross directional change, not trade win rate')
