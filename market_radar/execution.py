"""Prospective, sampled-quote paper trading. Never orders or personal positions."""
import datetime as dt
import hashlib
import json
import math
from statistics import mean
import config
from market_clock import last_completed_session, advance_session, session_dates
from radar import verified_catalyst, net_rr
from . import notify

KIND = 'execution-paper'
VERSION = 'post-alert-quote-2-3-v1'
GATES = ('market','sector','fundamental','catalyst','technical','chips','relative_strength')


def records(store):
    return [json.loads(r[0]) for r in store.db.execute('SELECT body FROM facts WHERE kind=? ORDER BY period',(KIND,))]


def save(store, trade):
    store.ingest({('paper',KIND):[trade]})


def active_codes(store):
    return {r['code'] for r in records(store) if r['status'] not in ('closed','expired','cancelled')}


def stamp(row, now):
    return dt.datetime.combine(now.date(),dt.time.fromisoformat(row['t']),tzinfo=now.tzinfo)


def fresh(row, now):
    # Caller has validated symbol, exchange, prices and day. Close replay is forbidden.
    return 0 <= (now-stamp(row,now)).total_seconds() <= 180


def eligible(item, cache, now, price):
    p=item.get('plan') or {}
    try:
        return (cache.get('quality',{}).get('passed') is True
            and cache.get('as_of') == last_completed_session('tw',now)
            and item.get('as_of') == cache['as_of']
            and item.get('status') in ('waiting','triggered','review')
            and all(item.get('gates',{}).get(g) is True for g in GATES)
            and verified_catalyst(item.get('catalyst'),now.date().isoformat())
            and now.date().isoformat() <= item['expires_on']
            and all(math.isfinite(float(p[k])) for k in ('entry_low','entry_high','stop','target'))
            and 0 < p['stop'] < p['entry_low'] <= price <= p['entry_high'] < p['target']
            and net_rr(price,p['stop'],p['target']) >= config.RADAR_MIN_RR)
    except (KeyError,TypeError,ValueError):
        return False


def receipt_time(store, key):
    rows=list(store.db.execute('SELECT sent_at FROM receipts WHERE id=?',(key+':0',)))
    return rows[0][0] if rows else None


def send(store, trade, label, text, now, token, chat):
    key=f"paper:{trade['period']}:{label}"
    notify.deliver(store,token,chat,key,text,now.isoformat())
    accepted=receipt_time(store,key)
    if not accepted:
        raise RuntimeError('Execution notification has no confirmed receipt')
    return accepted


def process(store, rows, cache, now, token=None, chat=None, enabled=False):
    """Rows must be validated by watch.valid_quote; no effects in a dry run."""
    if not enabled or not session_dates(now.date().isoformat(),now.date().isoformat()):
        return
    quotes={r['c']:r for r in rows if fresh(r,now)}
    items={i['id']:i for i in cache.get('radar',{}).get('items',[])}
    store.meta('execution-health',json.dumps(dict(checked_at=now.isoformat(),cache_as_of=cache.get('as_of'),
        required_cache_date=last_completed_session('tw',now),fresh_quotes=len(quotes),
        reviewed_plans=len(items),eligible_now=sum(eligible(i,cache,now,float(quotes[c]['z'])) for c,i in items.items() if c in quotes))))
    errors=[]
    existing=records(store)
    # Freeze evidence before notifying. Receipt recovery never changes the alert quote.
    if dt.time(9,5) <= now.time().replace(tzinfo=None) < dt.time(13,15):
        for code,item in items.items():
            row=quotes.get(code)
            if not row or not eligible(item,cache,now,float(row['z'])):
                store.meta('entry-confirm:'+code,'{}')
                continue
            key=hashlib.sha256(f"{code}:{item.get('created_on')}:{VERSION}".encode()).hexdigest()[:24]
            if any(t['period']==key or (t['code']==code and t['status'] in ('alert_pending','awaiting_fill','open')) for t in existing):
                continue
            old=json.loads(store.meta('entry-confirm:'+code) or '{}')
            observed=stamp(row,now).isoformat()
            store.meta('entry-confirm:'+code,json.dumps(dict(at=now.isoformat(),traded=observed,key=key)))
            gap=(now-dt.datetime.fromisoformat(old['at'])).total_seconds() if old.get('at') else 0
            if old.get('key')!=key or old.get('traded')==observed or not 60<=gap<=720:
                continue
            p=dict(item['plan'])
            trade=dict(code=code,exchange=row['ex'],name=item.get('name',code),period=key,status='alert_pending',version=VERSION,
                plan=p,created_at=now.isoformat(),alert_quote_at=observed,alert_quote=float(row['z']),
                valid_until=(now+dt.timedelta(minutes=10)).isoformat(),strategy_mode=config.RADAR_MODE,
                shares=100,fee_rate=config.RADAR_FEE_RATE,tax_rate=config.RADAR_SELL_TAX_RATE,
                slippage=config.RADAR_SLIPPAGE_RATE,minimum_fee=1,
                execution_model='100-share paper estimate using regular-market last trades; not odd-lot fills',
                data_gaps=0)
            save(store,trade);existing.append(trade)
    for t in existing:
        try:
            row=quotes.get(t['code'])
            price=float(row['z']) if row else None
            p=t['plan']
            if t['status'] in ('expired','cancelled') and t.get('alerted_at') and not t.get('cancel_sent'):
                send(store,t,'cancel',f"台股雷達｜本次模擬未進場｜{t['name']}\n提醒後行情缺漏、條件失效或價格超出區間，本次取消，不追價；不計入已成交績效。",now,token,chat)
                t['cancel_sent']=True;save(store,t)
            if t['status']=='alert_pending':
                key=f"paper:{t['period']}:entry"
                accepted=receipt_time(store,key)
                if not accepted and (now>dt.datetime.fromisoformat(t['valid_until']) or not row or not eligible(items.get(t['code'],{}),cache,now,price)):
                    # Ambiguous delivery stays visible; never automatically resend it.
                    if store.meta('delivery:'+key+':0') in ('sending','uncertain'):
                        t['delivery_status']='uncertain';save(store,t);continue
                    t['status']='expired';save(store,t);continue
                if not accepted:
                    label='模擬進場測試' if config.RADAR_MODE!='live' else '符合進場條件'
                    text=(f"台股雷達｜{label}｜{t['name']}（{t['code']}）\n"
                          f"基本面、催化、籌碼與技術門檻通過；盤中兩次確認價格在區間。\n"
                          f"進場區間 {p['entry_low']:g}–{p['entry_high']:g}；超出不追。\n"
                          f"停損 {p['stop']:g}；目標 {p['target']:g}。\n"
                          f"行情時間 {t['alert_quote_at']}；有效至 {t['valid_until']}。\n"
                          "提醒後下一筆合格行情才估算模擬進場；非實際成交。進場日算第1日，第2日重評，第3日13:20起時間出場。")
                    accepted=send(store,t,'entry',text,now,token,chat)
                t.update(status='awaiting_fill',alerted_at=accepted);save(store,t)
                continue  # Never fill with this call's pre-delivery quote.
            if t['status']=='awaiting_fill':
                if now>dt.datetime.fromisoformat(t['valid_until']):
                    t.update(status='expired',reason='no_post_alert_quote_in_window');save(store,t);continue
                if not row or stamp(row,now)<=dt.datetime.fromisoformat(t['alerted_at']):
                    continue
                if not eligible(items.get(t['code'],{}),cache,now,price):
                    t.update(status='cancelled',reason='entry_conditions_changed');save(store,t);continue
                fill=price*(1+t['slippage'])
                if not p['entry_low']<=fill<=p['entry_high']:
                    t.update(status='cancelled',reason='slipped_entry_outside_range');save(store,t);continue
                day=now.date().isoformat()
                t.update(status='open',entry=fill,entry_quote=price,entry_at=stamp(row,now).isoformat(),
                    entry_date=day,review_date=advance_session(day,1),exit_date_due=advance_session(day,2),
                    buy_cost=fill*t['shares']+max(t['minimum_fee'],fill*t['shares']*t['fee_rate']))
                save(store,t)
                continue
            if t['status']=='open':
                if not row or stamp(row,now)<=dt.datetime.fromisoformat(t['entry_at']):
                    t['data_gaps']+=1;save(store,t);continue
                traded=stamp(row,now).isoformat()
                if traded==t.get('last_quote_at'):
                    continue
                previous=dt.datetime.fromisoformat(t.get('last_quote_at',t['entry_at']))
                t.update(last_quote_at=traded,last_price=price,
                    max_observed_gap_seconds=max(t.get('max_observed_gap_seconds',0),(stamp(row,now)-previous).total_seconds()))
                day=now.date().isoformat()
                reason='stop' if price<=p['stop'] else 'target' if price>=p['target'] else None
                if not reason and (day>t['exit_date_due'] or (day==t['exit_date_due'] and now.time().replace(tzinfo=None)>=dt.time(13,20))):
                    reason='time_exit'
                if reason:
                    exit_price=price*(1-t['slippage'])
                    gross=exit_price*t['shares']
                    net=gross-max(t['minimum_fee'],gross*t['fee_rate'])-gross*t['tax_rate']-t['buy_cost']
                    t.update(status='closed',exit=exit_price,exit_at=traded,exit_reason=reason,
                        delayed_exit=(day>t['exit_date_due'] or (reason=='time_exit' and now.time().replace(tzinfo=None)>dt.time(13,25))),net_profit=round(net,2),net_return_pct=round(net/t['buy_cost']*100,4))
                    save(store,t)
                elif day>=t['review_date'] and not t.get('review_sent'):
                    send(store,t,'review',f"台股雷達｜第2日模擬重評｜{t['name']}\n目前 {price:g}；停損 {p['stop']:g}、目標 {p['target']:g}，第3日 {t['exit_date_due']} 時間出場，不延長。",now,token,chat)
                    t['review_sent']=True;save(store,t)
                else:
                    save(store,t)
            # Persist close before delivery, so failed delivery never changes exit price.
            if t['status']=='closed' and not t.get('exit_sent'):
                reason_text={'stop':'停損','target':'停利','time_exit':'第3日時間出場'}[t['exit_reason']]
                send(store,t,'exit',f"台股雷達｜模擬出場｜{t['name']}\n原因 {reason_text}；估計出場 {t['exit']:g}\n100股模型扣成本損益 {t['net_profit']:+.2f} 元（{t['net_return_pct']:+.2f}%）。非個人帳戶成交。",now,token,chat)
                t['exit_sent']=True;save(store,t)
        except Exception as exc:
            errors.append(type(exc).__name__)
    if errors:
        raise RuntimeError('Paper execution incomplete: '+','.join(sorted(set(errors))))


def summary(store):
    trades=records(store)
    closed=[t for t in trades if t['status']=='closed']
    return dict(version=VERSION,records=trades,closed=len(closed),
        health=json.loads(store.meta('execution-health') or '{}'),
        open=sum(t['status']=='open' for t in trades),
        unfilled=sum(t['status'] in ('expired','cancelled') for t in trades),
        win_rate=sum(t['net_profit']>0 for t in closed)/len(closed) if closed else None,
        mean_net_return_pct=mean(t['net_return_pct'] for t in closed) if closed else None,
        notice='提醒送達後行情的100股模擬；採一般行情與假設滑價，非零股實際成交。僅觀測時點觸價，排程空窗無法重建；缺行情不回填理想出場價。')
