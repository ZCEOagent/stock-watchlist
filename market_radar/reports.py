"""Only four Telegram sections; portfolio data stays in this transient projection."""
import hashlib

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


def select(snapshot, previous, holdings, priority=('2330', '6274')):
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


def brief(r):
    score = '未知' if r['score'] is None else str(r['score'])
    return f"{r['code']} {r['name']}｜{r['status']}｜{score} 分・完整度 {r['coverage']}%"


def render(snapshot, previous, holdings, weekly=False):
    new, changed, risks, trades, follow = select(snapshot, previous, holdings)
    baseline = previous is None or previous.get('version') != snapshot.get('version')
    lines = [f"台股雷達｜{'週一決策' if weekly else '每日摘要'} {snapshot['day']}",
             f"掃描 {snapshot['scanned']} 檔｜行情日期 {', '.join(snapshot['price_dates']) or '未知'}"]
    if snapshot.get('strategy_mode') == 'shadow':
        lines.append('沿用原波段策略影子模式；本報告提供研究摘要，不啟用交易訊號。')
    completion = snapshot.get('completion', {})
    if completion:
        f, h = completion['financials'], completion['history']
        lines.append(f"財報交叉核實 {f['verified']}/{f['eligible']} 檔；待補 {f['pending']} 檔；歷史行情失敗 {h['failed_days']} 個市場交易日。")
    failed = [h for h in snapshot['health'] if not h['ok']]
    if failed:
        lines.append(f'本輪 {len(failed)} 項來源失敗；受影響股票不產生新進決策。')
    if baseline:
        lines.append('首次／模型更新：建立基準，今天不把既有候選誤稱新進。')
    sections = [([brief(r) for r in new] or ['本次無可確認的新進標的。']),
                ([brief(r) + f"｜{r['delta']:+.1f} 分" for r in changed] or ['同口徑評分無 ≥10 分變化。']),
                ([brief(r) + '\n  ' + '；'.join(r['risks'][:2]) for r in risks[:3]] or
                 ['尚未設定持股，風險監測未啟用。' if not holdings else '已設定持股，本輪未觸發警報；仍須留意資料限制。']),
                ([brief(r) for r in trades[:3]] or ['目前沒有符合 BUY 門檻的標的，不需為週期而交易。'])]
    sections[3] += [brief(r) + '\n  優先追蹤：' + ('；'.join(r['risks'][:2]) or '；'.join(r['missing'][:2]) or '；'.join(r['reasons'][:2])) for r in follow]
    if len(risks) > 3:
        sections[2].append(f'另 {len(risks)-3} 檔觸發持股警報，完整清單見附檔。')
    for heading, items in zip(HEADINGS, sections):
        lines += ['', heading, *items]
    lines += ['', 'BUY＝研究條件通過、待人工決策；HOLD＝已持有續觀察；WATCH＝待確認；REMOVE＝移出候選／檢討持股。無自動下單。']
    return '\n'.join(lines)


def full_report(snapshot, previous, holdings):
    _, _, risks, trades, follow = select(snapshot, previous, holdings)
    lines = [render(snapshot, previous, holdings, True), '', '決策明細（最多 5 個交易候選＋優先追蹤＋全部持股警報）']
    picked = {r['code']: r for r in trades + follow + risks}
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
