"""Deterministic research scoring. Missing data is unknown, not zero or neutral."""
import datetime as dt
import statistics
from indicators import compute_indicators

VERSION = 'radar-1.0'
PRIORITY = ('2330', '6274')


def fresh(value, today, days):
    try:
        age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(value)).days
        return 0 <= age <= days
    except (ValueError, TypeError):
        return False


def latest(store, kind, code):
    rows = store.history(kind, code)
    return rows[-1] if rows else {}


def evaluate(company, store, today, peers, failed, supplements=None, expected_date=None):
    code, market = company['code'], company['market']
    rev, fin, val, price = [latest(store, k, code) for k in ('revenue', 'financial', 'valuation', 'price')]
    parts, missing, reasons, risks = {}, [], [], []
    # Each tuple is (earned points, observed weight). Total possible weight is 100.
    def add(key, weight, earned, explanation):
        if earned is None:
            missing.append(explanation)
        else:
            parts[key] = [round(earned, 2), weight]
            reasons.append(explanation)

    rev_ok = ('revenue' not in failed and rev.get('period') and
              fresh(rev['period'] + '-01', today, 85) and fresh(rev.get('source_date'), today, 45))
    fin_ok = ('financial' not in failed and fresh(fin.get('period_end'), today, 210) and
              fresh(fin.get('source_date'), today, 10))
    price_ok = ('price' not in failed and fresh(price.get('date'), today, 7) and (price.get('close') or 0) > 0
                and (expected_date is None or price.get('date') == expected_date))
    val_ok = ('valuation' not in failed and fresh(val.get('source_date'), today, 7) and (val.get('pe') or 0) > 0
              and val.get('source_date') == price.get('date'))
    y = rev.get('yoy') if rev_ok else None
    add('revenue_yoy', 20, None if y is None else 20 if y >= 30 else 15 if y >= 15 else 10 if y > 0 else 0,
        '營收年增待補／過期' if y is None else f"{rev['period']} 營收年增 {y:.1f}%")
    yp = rev.get('ytd_yoy') if rev_ok else None
    add('revenue_acceleration', 5, None if y is None or yp is None else 5 if y > yp and y > 0 else 0,
        '營收加速待補' if y is None or yp is None else f'單月與累計年增差 {y-yp:+.1f} 個百分點（加速代理指標）')
    eps = fin.get('eps') if fin_ok else None
    add('eps_positive', 5, None if eps is None else 5 if eps > 0 else 0,
        '一般業 EPS 待補／過期；金融業另需模型' if eps is None else f"{fin['period']} 累計 EPS {eps:.2f} 元（不可直接年化）")
    prior_period = str(int(fin['period'][:4])-1) + fin['period'][4:] if fin else None
    prev = next((r for r in store.history('financial', code) if r['period'] == prior_period and r.get('basis') == 'YTD'), {})
    extra = (supplements or {}).get(code, {})
    # Supplements are accepted only with an explicit source, availability date and exact period.
    extra_ok = (extra.get('period') == fin.get('period') and extra.get('basis') == 'YTD' and
                str(extra.get('source', '')).startswith('https://') and
                fresh(extra.get('available_at'), today, 210))
    prev_eps = prev.get('eps')
    if prev_eps is None and extra_ok:
        prev_eps = extra.get('prior_year_eps')
    growth = (eps / prev_eps - 1) * 100 if eps is not None and prev_eps is not None and prev_eps > 0 else None
    add('eps_growth', 15, None if growth is None else 15 if growth >= 30 else 10 if growth >= 15 else 5 if growth > 0 else 0,
        '缺去年同期累計 EPS／去年為負，成長率不適用' if growth is None else f'同期間 EPS 年增 {growth:.1f}%')
    revenue, op, pre, nonop = [fin.get(k) if fin_ok else None for k in ('revenue', 'operating', 'pretax', 'nonoperating')]
    margin = 100 * op / revenue if op is not None and revenue is not None and revenue > 0 else None
    add('operating_margin', 10, None if margin is None else 10 if margin >= 15 else 7 if margin >= 8 else 3 if margin > 0 else 0,
        '營益率待補' if margin is None else f'營益率 {margin:.1f}%（跨產業門檻僅初篩）')
    dependence = abs(nonop) / abs(pre) if nonop is not None and pre is not None and pre != 0 else None
    add('earnings_quality', 5, None if dependence is None else 5 if dependence <= .2 else 2 if dependence <= .5 else 0,
        '業外占比待補' if dependence is None else f'業外／稅前損益絕對值 {dependence:.0%}')
    cfo, net = (extra.get('operating_cash_flow'), fin.get('net')) if extra_ok and fin_ok else (None, None)
    cf_ratio = cfo / net if cfo is not None and net is not None and net > 0 else None
    add('cash_flow', 5, None if cf_ratio is None else 5 if cf_ratio >= 1 else 2 if cf_ratio > 0 else 0,
        '現金流品質未核實' if cf_ratio is None else f'同期間營業現金流／淨利 {cf_ratio:.2f}')
    pe = val.get('pe') if val_ok else None
    sector_values = peers.get(company.get('sector'), [])
    median = statistics.median(sector_values) if len(sector_values) >= 5 else None
    relative = pe / median if pe and median else None
    add('valuation', 20, None if relative is None else 20 if relative <= .8 else 15 if relative <= 1 else 8 if relative <= 1.3 else 0,
        '有效本益比／至少 5 家同業樣本待補' if relative is None else f'本益比 {pe:.1f} 倍；同業中位數 {median:.1f} 倍')
    history = [r for r in store.history('price', code) if r.get('date') and r['date'] <= today]
    indicator = compute_indicators(history) if price_ok else None
    technical_ok = indicator and indicator.get('latest_date') == price.get('date') and indicator.get('ma_long') is not None
    close, ma = price.get('close'), indicator.get('ma_long') if technical_ok else None
    vr = indicator.get('volume_ratio') if technical_ok else None
    add('trend', 10, None if ma is None else 10 if ma <= close <= ma * 1.15 else 5 if close > ma else 0,
        '近 20 個交易日行情待累積／價格過期' if ma is None else f'收盤 {close:.2f}；MA20 {ma:.2f}')
    add('volume', 5, None if vr is None else 5 if 1 <= vr <= 3 else 2 if vr < 1 else 0,
        '量比待補' if vr is None else f'20 日量比 {vr:.2f}')
    if not price_ok:
        risks.append('價格資料缺漏／過期／不符最近完成交易日，禁止新進決策')
    if failed:
        risks.append('本輪來源失敗：' + '、'.join(sorted(failed)))
    if eps is not None and eps <= 0:
        risks.append('累計 EPS 非正')
    if dependence is not None and dependence > .5:
        risks.append('業外損益比重偏高，須查一次性因素')
    if cfo is not None and cfo < 0:
        risks.append('營業現金流為負')
    if ma is not None and close < ma * .95:
        risks.append('收盤低於 MA20 超過 5%')
    if indicator and abs(indicator['change_pct']) >= 15:
        risks.append('單日價格斷層：先核對除權息／分割，暫停技術判讀')
    earned, coverage = sum(p[0] for p in parts.values()), sum(p[1] for p in parts.values())
    score = round(100 * earned / coverage, 1) if coverage else None
    events = [e for e in store.events_since((dt.date.fromisoformat(today)-dt.timedelta(days=7)).isoformat()) if e['code'] == code and e['review_required']]
    if events:
        risks.append('重大公告需人工核實（關鍵字僅提醒，不等於已證實風險）')
    # WATCH -> research only; HOLD is applied exclusively to real holdings in report projection.
    status = 'WATCH'
    veto = (eps is not None and eps <= 0) or (cfo is not None and cfo < 0)
    if veto or (coverage >= 80 and score is not None and score < 50 and not failed):
        status = 'REMOVE'
    elif (coverage >= 90 and score is not None and score >= 80 and not risks and
          cf_ratio is not None and cf_ratio > 0 and growth is not None and growth > 0 and
          relative is not None and relative <= 1 and ma is not None and close >= ma):
        status = 'BUY'
    candidate = coverage >= 60 and score is not None and score >= 70 and status != 'REMOVE' and price_ok and not failed
    return {'code': code, 'name': company['name'], 'market': market, 'sector': company.get('sector'),
            'score': score, 'coverage': coverage, 'earned': round(earned, 2), 'parts': parts,
            'status': status, 'candidate': candidate, 'reasons': reasons, 'missing': missing,
            'risks': risks, 'close': close, 'price_date': price.get('date'), 'ma20': ma,
            'revenue_period': rev.get('period'), 'financial_period': fin.get('period'),
            'pe': pe, 'peer_median': median, 'valuation_date': val.get('source_date'),
            'condition_price_ceiling': round(min(ma * 1.02, close * median / pe), 2) if ma and close and median and pe else None,
            'sources': sorted(set(r['source'] for r in (rev, fin, val, price) if r.get('source')) |
                              ({extra['source']} if extra_ok else set()))}


def scan(feeds, health, store, today, supplements=None, expected_date=None):
    companies = [r for (m, k), rows in feeds.items() if k == 'universe' for r in rows]
    market_counts = {m: sum(c['market'] == m for c in companies) for m in ('twse', 'tpex')}
    if (len(companies) < 1000 or market_counts['twse'] < 700 or market_counts['tpex'] < 500 or
            len({c['code'] for c in companies}) != len(companies) or
            any(not fresh(c.get('source_date'), today, 10) for c in companies) or
            any(not h['ok'] for h in health if h['kind'] == 'universe')):
        raise RuntimeError('全市場名單不完整，保留上次排名；本輪不發新進／移除訊號')
    peers = {}
    for c in companies:
        v = latest(store, 'valuation', c['code'])
        if v.get('pe') and v['pe'] > 0 and fresh(v.get('source_date'), today, 7):
            peers.setdefault(c['sector'], []).append(v['pe'])
    results = []
    for c in companies:
        failed = {h['kind'] for h in health if h['market'] == c['market'] and not h['ok']}
        results.append(evaluate(c, store, today, peers, failed, supplements, expected_date))
    results.sort(key=lambda r: (not r['candidate'], -(r['score'] or 0), -r['coverage'], r['code']))
    for rank, r in enumerate(results, 1):
        r['rank'] = rank
    return {'day': today, 'version': VERSION, 'scanned': len(companies), 'health': health,
            'price_dates': sorted({r['price_date'] for r in results if r['price_date']}), 'stocks': results}


def apply_swing_gate(snapshot, cache, mode):
    """Never bypass the existing five-layer strategy, frozen plans or shadow setting."""
    items = {r['id']: r for r in cache.get('radar', {}).get('items', [])}
    snapshot['strategy_mode'] = mode
    for r in snapshot['stocks']:
        plan = items.get(r['code'], {})
        r['swing_status'] = plan.get('status', 'not_reviewed')
        if r['status'] != 'BUY':
            continue
        approved = (mode == 'live' and cache.get('quality', {}).get('passed') and
                    cache.get('as_of') == r['price_date'] and plan.get('as_of') == r['price_date'] and
                    plan.get('status') == 'triggered' and plan.get('plan') and
                    plan.get('expires_on', '') >= snapshot['day'])
        if not approved:
            r['status'] = 'WATCH'
            r['missing'].append('原波段策略尚未通過／影子模式，禁止升級 BUY')
        else:
            r['swing_plan'] = plan['plan']
            r['expires_on'] = plan['expires_on']
