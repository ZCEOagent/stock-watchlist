"""Only four Telegram sections; portfolio data stays in this transient projection."""
import hashlib
from .engine import PRIORITY

DELIVERY_VERSION = "readable-1"

HEADINGS = ('① 今日新進雷達', '② 評分大幅變化', '③ 持股風險警報', '④ 本週真正值得考慮交易的標的')


def holding_risks(stocks, holdings):
    lookup, result = {s['code']: s for s in stocks}, []
    for code, holding in holdings.items():
        s = lookup.get(code)
        if not s:
            result.append({'code': code, 'name': '', 'status': 'WATCH', 'risks': ['本輪找不到持股資料，請核對停牌／市場名單'], 'score': None, 'coverage': 0})
            continue
        r = dict(s, risks=list(s['risks']), status='REMOVE' if s['status'] == 'REMOVE' else 'HOLD')
        if holding.get('stop') and s.get('close') and s['close'] <= holding['stop']:
            r['risks'].append(f"收盤低於自訂停損價 {holding['stop']:.2f}；先確認最新價格")
        if s['coverage'] < 80:
            r['risks'].append(f"資料完整度僅 {s['coverage']}%，持股風險未完整核實")
        if r['risks']:
            result.append(r)
    return result


def select(snapshot, previous, holdings, priority=PRIORITY):
    stocks = snapshot['stocks']
    old = {r['code']: r for r in (previous or {}).get('stocks', [])}
    comparable_version = previous and previous.get('version') == snapshot.get('version')
    new, changed = [], []
    if comparable_version:
        for r in stocks:
            p = old.get(r['code'])
            if p and set(r['parts']) == set(p['parts']) and r['candidate'] and not p['candidate'] and not r['risks']:
                new.append(r)
            # Changed availability/model is not a change in business performance.
            if (p and set(r['parts']) == set(p['parts']) and r['coverage'] >= 60 and
                    r['score'] is not None and p['score'] is not None and
                    not any('來源失敗' in risk or '價格資料缺漏' in risk for risk in r['risks'])):
                delta = round(r['score'] - p['score'], 1)
                if abs(delta) >= 10:
                    changed.append(dict(r, delta=delta))
    changed.sort(key=lambda r: -abs(r['delta']))
    trades = [r for r in stocks if r['status'] == 'BUY' and r['code'] not in holdings][:5]
    follow = [r for r in stocks if r['code'] in priority and r not in trades]
    return new[:3], changed[:3], holding_risks(stocks, holdings), trades, follow


def research(snapshot, holdings):
    """Bounded full-market research, independent of BUY and baseline comparisons."""
    expected = snapshot.get('freshness', {}).get('expected_date')
    rows = [r for r in snapshot['stocks'] if r['candidate'] and r['status'] == 'WATCH'
            and r['code'] not in holdings and r['code'] not in PRIORITY
            and not r['risks'] and r['coverage'] >= 80 and r['score'] is not None
            and (not expected or r.get('price_date') == expected)]
    return sorted(rows, key=lambda r: (-r['score'], -r['coverage'], r['code']))[:3]


def brief(r):
    score = '未知' if r['score'] is None else str(r['score'])
    return f"{r['code']} {r['name']}｜{r['status']}｜{score} 分・完整度 {r['coverage']}%"


import datetime as dt


def readable_time(value):
    try:
        stamp = dt.datetime.fromisoformat(value)
        if stamp.tzinfo:
            stamp = stamp.astimezone(dt.timezone(dt.timedelta(hours=8)))
        return stamp.strftime('%m/%d %H:%M')
    except (TypeError, ValueError):
        return '時間未記錄'


def plain(text):
    translations = (
        ('同交易日有效本益比', '還缺同一天的股價估值與同業比較，無法確認價格是否合理。'),
        ('價格資料缺漏', '最新股價還沒補齊，先不要依這份資料做買進判斷。'),
        ('營收／財報期別', '營收或財報還不是需要的最新一期，先等資料更新。'),
        ('本輪來源失敗', '部分資料暫時抓不到，這次判斷不完整。'),
        ('營業現金流為負', '營運現金流出多於流入，需要先查清楚原因。'),
        ('累計 EPS 非正', '這期每股盈餘未大於零，暫不列入買進候選。'),
        ('業外損益比重偏高', '獲利受本業以外的項目影響較大，要確認能否持續。'),
        ('收盤低於 MA20', '收盤價明顯低於近20個交易日平均，走勢偏弱。'),
        ('原波段策略尚未通過', '尚未通過原本的交易規則，目前只觀察。'),
        ('重大公告需人工核實', '有重要公告，需要先看原文，不能只靠關鍵字判斷。'),
    )
    return next((meaning for key, meaning in translations if key in text), text)


def stock_card(r):
    labels = {'BUY': '🟢 條件通過，仍需人工決定', 'WATCH': '🟡 先觀察',
              'HOLD': '🔵 已持有，繼續觀察', 'REMOVE': '🔴 暫不列入買進候選'}
    lines = [f"{r['name']}（{r['code']}）｜{labels.get(r['status'], '待確認')}"]
    reasons = r.get('reasons', [])
    revenue = next((x for x in reasons if '營收年增' in x), None)
    earnings = next((x for x in reasons if '同期間 EPS 年增' in x), None)
    period = r.get('financial_period') or '同一期財報'
    for suffix, label in (('Q1', '年第一季'), ('Q2', '年上半年'), ('Q3', '年前三季'), ('Q4', '年全年')):
        if period.endswith(suffix):
            period = period[:-2] + label
            break
    evidence = [x for x in (revenue, earnings) if x]
    if evidence:
        lines.append('看點：' + '；'.join(evidence).replace('同期間 EPS 年增', period + '每股獲利比去年同期成長'))
    caveats = r.get('risks', []) or r.get('missing', [])
    if caveats:
        lines.extend('留意：' + plain(item) for item in caveats[:2])
    elif r['status'] == 'WATCH':
        lines.append('下一步：等買進條件全部確認，目前只是觀察名單。')
    if r.get('close') is not None:
        lines.append(f"收盤 {r['close']:g} 元（{r.get('price_date') or '日期未知'}）")
    return '\n'.join(lines)


def render(snapshot, previous, holdings, weekly=False):
    new, changed, risks, trades, follow = select(snapshot, previous, holdings)
    baseline = previous is None or previous.get('version') != snapshot.get('version')
    lines = [f"台股雷達｜{snapshot['day']} {'週一決策' if weekly else '每日重點'}", '',
             ('今日結論：有標的通過篩選，請先看條件與風險，再決定是否交易。' if trades else
              '今日結論：先觀察，目前沒有可列為買進的標的。')]
    if snapshot.get('strategy_mode') == 'shadow':
        lines.append('目前只做觀察與研究，不發正式買進訊號。')
    sections = [([stock_card(r) for r in new] or
                 ['今天先建立比較基準，還不能判斷哪些是新加入的。' if baseline else '今天沒有新加入的觀察標的。']),
                ([f"{r['name']}（{r['code']}）｜評分{'上升' if r['delta'] > 0 else '下降'} {abs(r['delta']):g} 分\n評分變化不等於股價漲跌；仍要看資料與風險。" for r in changed] or
                 ['還沒有可比較的舊評分。' if baseline else '沒有明顯變化（增減未達10分）。']),
                ([stock_card(r) for r in risks[:3]] or
                 ['你目前沒有設定持股，所以沒有持股警報。' if not holdings else '本輪沒有觸發持股警報。']),
                [stock_card(r) for r in trades[:3]]]
    candidates = research(snapshot, holdings)
    if candidates:
        sections[3] += ['值得先研究的股票（尚未達買進條件）：', *[stock_card(r) for r in candidates]]
    elif not trades:
        sections[3].append('本輪沒有符合條件的研究候選。')
    if follow:
        sections[3] += ['你指定持續追蹤：', *[stock_card(r) for r in follow]]
    if len(risks) > 3:
        sections[2].append(f'另有 {len(risks)-3} 檔持股警報，請看完整報告。')
    for heading, items in zip(HEADINGS, sections):
        lines += ['', heading, '\n\n'.join(items)]
    lines += ['', '──────────']
    completion = snapshot.get('completion', {})
    if completion:
        f, h = completion['financials'], completion['history']
        lines.append(f"資料狀況：一般產業財報已核對 {f['verified']} 家，還有 {f['pending']} 家待補。")
        if f.get('excluded'):
            lines.append(f"另 {f['excluded']} 家不適用這套財報評分。")
        if h.get('pending_days', h.get('failed_days', 0)):
            lines.append('部分歷史股價仍待補，受影響股票先保留觀察。')
    freshness = snapshot.get('freshness', {})
    if freshness and freshness['current_prices'] < snapshot['scanned']:
        lines.append(f"⚠️ {snapshot['scanned']-freshness['current_prices']} 檔行情未更新到 {freshness['expected_date']}，不列入買進判斷。")
    if any(not h['ok'] for h in snapshot.get('health', [])):
        lines.append('⚠️ 部分資料來源暫時失敗，受影響股票暫停買進判斷。')
    lines.append(f"更新：{readable_time(snapshot.get('scan_completed_at') or snapshot.get('fetched_at'))}（台灣時間）｜掃描 {snapshot['scanned']} 檔")
    lines.append('觀察名單不是買進建議；不會自動下單。')
    if snapshot.get('run_url'):
        lines += ['本次掃描紀錄：' + snapshot['run_url']]
    return '\n'.join(lines)


def full_report(snapshot, previous, holdings):
    _, _, risks, trades, follow = select(snapshot, previous, holdings)
    lines = [render(snapshot, previous, holdings, True), '', '決策明細（最多 5 個交易候選＋3 個研究候選＋優先追蹤＋全部持股警報）']
    lines += ['資料擷取開始：' + snapshot.get('fetched_at', '未記錄')]
    picked = {r['code']: r for r in trades + research(snapshot, holdings) + follow + risks}
    for r in picked.values():
        lines += ['', brief(r), f"行情 {r.get('price_date')}；營收 {r.get('revenue_period')}；財報 {r.get('financial_period')}；估值 {r.get('valuation_date')}"]
        lines += ['依據：' + x for x in r.get('reasons', [])]
        lines += ['待補：' + x for x in r.get('missing', [])]
        lines += ['風險／否決條件：' + x for x in r.get('risks', [])]
        ceiling = r.get('condition_price_ceiling')
        if ceiling:
            lines.append(f'觀察價格上限 {ceiling:.2f} 元＝min(MA20×1.02，收盤×同業PE中位數/個股PE)。僅篩選條件，非目標價；須核對即時價格及成長差異。')
        if r.get('swing_plan'):
            p = r['swing_plan']
            lines.append(f"沿用原凍結計畫：進場 {p['entry_low']}～{p['entry_high']}；失效 {p['stop']}；目標 {p['target']}；有效至 {r['expires_on']}")
        lines.append('進場前：確認現金流、公告原文、估值與流動性；依個人資金與最大可承受損失設定部位。')
        lines += ['來源：' + u for u in r.get('sources', [])]
    lines += ['', '規則：營收 25／EPS 20／獲利品質 20／同業估值 20／技術面 15。',
              '分數＝已知項目得分÷已知權重×100；完整度＝已知權重。缺值不補零，也不給中性分。',
              'BUY：≥80 分、完整度≥90%、EPS年增>0、現金流核實為正、價格在MA20上且PE不高於同業中位數、無風險旗標。',
              '股價與公告並非逐筆即時資料；例假日／停牌／除權息需人工確認。EPS為累計，不將單月自結加到季度報表。',
              '產業、EPS虧轉盈、一次性損益與公司治理仍需人工研究；目前不是經回測驗證的獲利策略。',
              '全市場排名保存供查核，不會把全部股票推送到 Telegram。']
    return '\n'.join(lines)


def message_key(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()
