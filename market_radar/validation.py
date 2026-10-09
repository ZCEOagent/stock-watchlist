"""Prospective audit only: no inferred trades, manual approvals or unknown outcomes."""
import datetime as dt
import json
from collections import Counter
from urllib.parse import urlparse
from . import decision


def observation(store, code, now, valid=False, eligible=None, result=None, direction=None, level=0):
    day = now.date().isoformat()
    prior = next((r for r in store.history('watch-coverage',code) if r['period']==day), None)
    row = prior or dict(code=code,period=day,attempts=0,valid=0,max_gap_seconds=0,
                       eligible={},delivered={},reasons={})
    if result is None:
        if row.get('last_check'):
            gap=(now-dt.datetime.fromisoformat(row['last_check'])).total_seconds()
            row['max_gap_seconds']=max(row['max_gap_seconds'],gap)
        row['last_check']=now.isoformat()
        row['attempts']+=1
        row['valid']+=int(valid)
        for signal in eligible or []:
            key=signal['direction']
            row['eligible'][key]=max(row['eligible'].get(key,0),signal['level'])
    else:
        row['reasons'][result]=row['reasons'].get(result,0)+1
        if result in ('delivered','already_delivered'):
            row['delivered'][direction]=max(row['delivered'].get(direction,0),level)
    store.ingest({('audit','watch-coverage'):[row]})


def audit(store, day, outcomes):
    rows=[json.loads(r[0]) for r in store.db.execute("SELECT body FROM facts WHERE kind='watch-coverage' AND period=?",(day,))]
    reasons=Counter()
    eligible=uncovered=0
    for row in rows:
        reasons.update(row['reasons'])
        for direction,level in row['eligible'].items():
            eligible+=1
            uncovered+=row['delivered'].get(direction,0)<level
    attempts=sum(r['attempts'] for r in rows)
    return dict(as_of=day,tracked_codes=len(rows),quote_attempts=attempts,
                valid_quote_ratio=sum(r['valid'] for r in rows)/attempts if attempts else None,
                observed_eligible_directions=eligible,uncovered_directions=uncovered,
                coverage_ratio=(eligible-uncovered)/eligible if eligible else None,
                max_observed_gap_seconds=max((r['max_gap_seconds'] for r in rows),default=None),
                reasons=dict(reasons),forward_outcomes=outcomes,status='research_only',
                notice='只衡量實際觀測到的規則內異動；排程空窗與未追蹤股票不可推算全市場漏報率。後續反向不等於交易虧損。')


def reviews(snapshot, store, cache):
    """Public evidence bundle. Automated collection never sets reviewed=True."""
    from .engine import PRIORITY
    selected=[r for r in snapshot['stocks'] if r.get('candidate')][:10]
    selected += [r for r in snapshot['stocks'] if r['code'] in PRIORITY and r not in selected]
    swing={r['id']:r for r in cache.get('radar',{}).get('items',[])}
    result=[]
    for row in selected:
        code=row['code']
        facts=[]
        for kind in ('price','revenue','financial','supplement','valuation'):
            history=store.history(kind,code)
            if history:
                fact=history[-1]
                url=fact.get('source','')
                if urlparse(url).scheme=='https':
                    facts.append(dict(kind=kind,source_url=url,period=fact.get('period') or fact.get('date'),
                                      source_date=fact.get('source_date'),fetched_at=fact.get('fetched_at'),
                                      content_sha256=fact.get('content_sha256')))
        item=swing.get(code,{})
        current=bool(cache.get('quality',{}).get('passed') and cache.get('as_of')==row.get('price_date')
                     and item.get('as_of')==row.get('price_date') and row.get('price_date')==snapshot.get('freshness',{}).get('expected_date'))
        blockers=decision.issues(row)
        blockers['conditions']+=item.get('reasons',[]) if current else ['波段深度審核尚缺同交易日有效結果。']
        result.append(dict(code=code,name=row.get('name'),as_of=snapshot['day'],status='pending_review',
            automated_only=True,sources=facts,blockers=blockers,
            swing_gates=item.get('gates',{}) if current else {},
            reference_plan=(item.get('plan') or item.get('candidate_plan')) if current else None,
            catalyst=item.get('catalyst') if current else None,
            review_required=['核對公告原文與發布時間','說明成長催化與可能已反映於股價的風險','列明失效條件、有效期限與來源'],
            notice='來源清單不是已完成審核；參考計畫不是正式買進指令。'))
    return dict(as_of=snapshot['day'],items=result,approved_by_this_process=0)


def swing_reviews(cache):
    """Expose existing gate evidence without changing strategy or approving catalysts."""
    items=[]
    failures=Counter()
    for row in cache.get('radar',{}).get('items',[]):
        gates=row.get('gates',{})
        failures.update(k for k,v in gates.items() if v is not True)
        current=bool(cache.get('quality',{}).get('passed') and row.get('as_of')==cache.get('as_of'))
        items.append(dict(code=row['id'],name=row.get('name'),as_of=row.get('as_of'),
            status=row.get('status'),cache_quality_passed=current,gates=gates,
            blockers=row.get('reasons',[]),facts=row.get('facts',{}),catalyst=row.get('catalyst'),
            reference_plan=(row.get('plan') or row.get('candidate_plan')) if current else None,
            automated_only=True,manual_review_required=not bool(row.get('catalyst'))))
    return dict(as_of=cache.get('as_of'),generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        mode=cache.get('radar',{}).get('mode','shadow'),items=items,
        blocked_gate_counts=dict(failures),approved_by_this_process=0,
        notice='沿用既有資料與審核結果；本程序不核准催化、不產生交易，也不代表即時行情或已驗證勝率。')


if __name__=='__main__':
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=swing_reviews(json.loads(Path(args.cache).read_text(encoding='utf-8')))
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
