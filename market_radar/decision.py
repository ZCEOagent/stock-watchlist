"""Explain existing gates and compare public facts; never change scores or trading rules."""
DATA_PREFIXES = ('營收／財報期別', '價格資料缺漏', '本輪來源失敗',
                 '本輪找不到持股資料', '資料完整度僅')
POLICY_PREFIX = '原波段策略尚未通過'


def issues(row):
    data, risks, conditions = [], [], []
    for text in row.get('risks', []):
        (data if text.startswith(DATA_PREFIXES) else risks).append(text)
    for text in row.get('missing', []):
        if text.startswith(POLICY_PREFIX):
            conditions.append('原交易規則尚未放行，先確認交易計畫與審核。')
        elif text.startswith('缺去年同期累計 EPS') and row.get('metrics', {}).get('prior_year_eps') is not None and row['metrics']['prior_year_eps'] <= 0:
            conditions.append('去年同期未獲利，年增率不適用；需要另外研究轉虧為盈，不能套用成長率門檻。')
        else:
            data.append(text)
    if row.get('swing_status') == 'not_reviewed':
        conditions.append('原波段策略尚未審核這檔；資料補齊後，仍需通過交易計畫審核。')
    parts = row.get('parts', {})
    for key, text in (
        ('eps_growth', '每股獲利需高於去年同期，目前尚未達成。'),
        ('cash_flow', '營運現金流必須核實為正，目前尚未達成。'),
        ('trend', '收盤需站上近20個交易日平均價，目前尚未達成。'),
    ):
        if key in parts and parts[key][0] == 0:
            conditions.append(text)
    if row.get('pe') and row.get('peer_median') and row['pe'] > row['peer_median']:
        conditions.append(f"本益比 {row['pe']:g} 倍，高於同業中位數 {row['peer_median']:g} 倍；估值條件尚未通過。")
    if row.get('score') is not None and row['score'] < 80:
        conditions.append(f"初篩分數 {row['score']:g} 分，尚未達80分；缺資料時分數仍可能改變。")
    if row.get('coverage', 0) < 90:
        data.append(f"目前已核實的評分項目占 {row.get('coverage', 0):g}%，買進判斷至少需要90%。")
    return {'data': list(dict.fromkeys(data)), 'risks': list(dict.fromkeys(risks)),
            'conditions': list(dict.fromkeys(conditions))}


def changes(row, previous):
    if previous is None:
        return ['第一次列出／尚無已送達報告可比較。']
    result = []
    current, old = issues(row), issues(previous)
    same_parts = set(row.get('parts', {})) == set(previous.get('parts', {}))
    if set(current['data']) != set(old['data']) or not same_parts:
        result.append('資料完整性有變化，不代表公司經營突然變好或變差。')
    added = set(current['risks']) - set(old['risks'])
    removed = set(old['risks']) - set(current['risks'])
    if added:
        result.append('新增風險：' + '；'.join(sorted(added)))
    if removed:
        result.append('先前部分風險本輪未再觸發，仍需核對原因。')
    if row.get('swing_status') != previous.get('swing_status'):
        result.append('原交易策略的審核進度有變化。')
    if row.get('status') != previous.get('status'):
        result.append('觀察狀態已改變，請看下方原因。')
    if row.get('revenue_period') != previous.get('revenue_period') or row.get('financial_period') != previous.get('financial_period'):
        result.append('營收或財報期別已更新。')
    metrics, old_metrics = row.get('metrics', {}), previous.get('metrics', {})
    keys = ('revenue_yoy', 'eps', 'eps_growth', 'operating_margin', 'cash_flow_ratio')
    if any(metrics.get(k) is not None and old_metrics.get(k) is not None and metrics[k] != old_metrics[k] for k in keys):
        result.append('已核實營收／獲利數字有變化。')
    if same_parts and row.get('score') is not None and previous.get('score') is not None:
        delta = row['score'] - previous['score']
        if abs(delta) >= 10:
            result.append(f'同口徑評分 {delta:+.1f} 分。')
    if row.get('candidate') != previous.get('candidate'):
        result.append('是否進入研究池的結果有變化。')
    if same_parts and row.get('score') is not None and previous.get('score') is not None and (row['score'] >= 80) != (previous['score'] >= 80):
        result.append('80分初篩門檻的通過狀態有變化。')
    # Compare gate pass/fail, not changing numeric wording or daily MA values.
    for key, label, passed in (
        ('valuation', '估值條件', lambda p: p[0] >= 15),
        ('trend', '站上20日平均價條件', lambda p: p[0] > 0),
    ):
        a, b = row.get('parts', {}).get(key), previous.get('parts', {}).get(key)
        if a is not None and b is not None and passed(a) != passed(b):
            result.append(label + ('已通過。' if passed(a) else '不再通過。'))
    return result


def price_reference(row):
    if issues(row)['data'] or issues(row)['risks'] or not row.get('ma20') or not row.get('condition_price_ceiling'):
        return None
    floor, ceiling = row['ma20'], row['condition_price_ceiling']
    if floor > ceiling:
        return '價格參考：目前趨勢與估值觀察區沒有交集，不硬湊買點。'
    return f'價格參考：{floor:g}～{ceiling:g} 元（依本次均價與估值試算，非正式買點）。'
