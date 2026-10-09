"""Short-swing entry diagnostics. Thresholds are hypotheses, not win probabilities."""
import datetime as dt
import json
import math
from statistics import median
from .sources import number
from radar import net_rr
import config

VERSION='short-entry-quality-v1'
MAX_SPREAD_PCT=.5
MAX_TARGET_ATR=3.0
MIN_VOLUME_DAYS=5
MIN_VOLUME_RATIO=1.3


def book(row):
    bid=number(str(row.get('b','')).split('_')[0])
    ask=number(str(row.get('a','')).split('_')[0])
    if bid is None or ask is None or not 0<bid<=ask:
        return None
    return dict(bid=bid,ask=ask,spread_pct=(ask-bid)/((ask+bid)/2)*100)


def inspect(store,item,row,now,record=True):
    p=item.get('plan') or {}
    blockers=[]
    depth=book(row)
    if depth is None:
        blockers.append('missing_or_invalid_bid_ask')
    elif depth['spread_pct']>MAX_SPREAD_PCT:
        blockers.append('spread_above_0.5pct')
    if depth and not p.get('entry_low',math.inf)<=depth['ask']<=p.get('entry_high',-math.inf):
        blockers.append('ask_outside_entry_range')
    atr=number(p.get('atr'))
    target=number(p.get('target'))
    last=number(row.get('z'))
    price=depth['ask'] if depth else last
    target_atr=(target-price)/atr if target and price and atr and atr>0 else None
    if target_atr is None:
        blockers.append('missing_target_or_atr')
    elif not 0<target_atr<=MAX_TARGET_ATR:
        blockers.append('target_beyond_3atr')
    if depth and p.get('stop') and p.get('target') and net_rr(depth['ask'],p['stop'],p['target'])<config.RADAR_MIN_RR:
        blockers.append('ask_net_reward_risk_insufficient')
    day=now.date().isoformat()
    traded=dt.datetime.combine(now.date(),dt.time.fromisoformat(row['t']),tzinfo=now.tzinfo)
    # Match actual quote clock, not scheduler start time. One day contributes once.
    minute=traded.hour*60+traded.minute
    history=store.history('entry-volume',row['c'])
    baseline={}
    for old in history:
        age=(now.date()-dt.date.fromisoformat(old['date'])).days
        if 0<age<=35 and abs(old['minute']-minute)<=2 and old['volume']>0:
            prior=baseline.get(old['date'])
            if prior is None or abs(old['minute']-minute)<abs(prior['minute']-minute):
                baseline[old['date']]=old
    samples=[v['volume'] for _,v in sorted(baseline.items())[-20:]]
    volume=number(row.get('v'))
    ratio=volume/median(samples) if volume is not None and volume>0 and len(samples)>=MIN_VOLUME_DAYS else None
    volume_state='pending' if ratio is None else 'supports' if ratio>=MIN_VOLUME_RATIO else 'weak'
    result=dict(version=VERSION,checked_at=now.isoformat(),quote_at=traded.isoformat(),
        hard_passed=not blockers,blockers=blockers,book=depth,target_atr=target_atr,
        same_time_volume=dict(status=volume_state,ratio=ratio,sample_days=len(samples),threshold=MIN_VOLUME_RATIO,role='comparison_only'),
        notice='3ATR與0.5%價差為待驗證的保守篩選設定；不是3日達標機率。量能缺同時段歷史時待補，不當成0或不合格。')
    if record:
        if volume is not None and volume>0:
            store.ingest({('quote','entry-volume'):[dict(code=row['c'],period=f'{day}:{minute}',date=day,minute=minute,volume=volume)]})
        store.ingest({('research','entry-quality'):[dict(code=row['c'],period=f'{day}:{minute}',date=day,**result)]})
    return result


def summary(store):
    rows=[json.loads(r[0]) for r in store.db.execute("SELECT body FROM facts WHERE kind='entry-quality' ORDER BY period")]
    latest={}
    for row in rows:latest[row['code']]=row
    return dict(version=VERSION,items=list(latest.values()),
        notice='逐檔保留擋下原因與量能比較；不宣稱新增條件已提升勝率。')


def observe_volume(store,row,now):
    volume=number(row.get('v'))
    if volume is None or volume<=0:return
    traded=dt.time.fromisoformat(row['t'])
    minute=traded.hour*60+traded.minute
    day=now.date().isoformat()
    store.ingest({('quote','entry-volume'):[dict(code=row['c'],period=f'{day}:{minute}',date=day,minute=minute,volume=volume)]})
