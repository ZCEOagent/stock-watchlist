"""Private cash-only ledger and risk sizing. No broker or Telegram side effects.

Caller supplies actual deposits/fills and dated marks. Never infer a fill from
a research signal. All monetary inputs are TWD. Do not publish ledger output.
"""
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime


def money(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError('Amounts must be finite and nonnegative')
    return value


def calculate(events, marks, as_of):
    cash = deposits = withdrawals = realized = Decimal(0)
    positions, ids = {}, set()
    last_time = None
    for event in events:
        if not event.get('event_id') or event['event_id'] in ids:
            raise ValueError('Missing or duplicate event_id')
        ids.add(event['event_id'])
        stamp = datetime.fromisoformat(event['at'])
        if stamp.tzinfo is None or (last_time and stamp < last_time):
            raise ValueError('Events require ordered timezone-aware timestamps')
        last_time = stamp
        if stamp.date().isoformat() > as_of:
            raise ValueError('Future event')
        kind = event['kind']
        if kind in ('deposit', 'withdrawal'):
            amount = money(event['amount'])
            if kind == 'deposit':
                cash += amount
                deposits += amount
            else:
                if amount > cash:
                    raise ValueError('Withdrawal exceeds available cash')
                cash -= amount
                withdrawals += amount
            continue
        if kind not in ('buy', 'sell'):
            raise ValueError('Unsupported event kind')
        qty = money(event['shares'])
        price, fee, tax = (money(event[k]) for k in ('price', 'fee', 'tax'))
        if qty <= 0 or qty != qty.to_integral_value() or price <= 0:
            raise ValueError('Positive integer shares and positive price required')
        sid = event['stock_id']
        if not isinstance(sid, str) or not sid.isdigit() or len(sid) != 4:
            raise ValueError('Four digit stock ID required')
        pos = positions.setdefault(sid, {'shares': Decimal(0), 'cost': Decimal(0)})
        gross = price * qty
        if kind == 'buy':
            amount = gross + fee + tax
            if amount > cash:
                raise ValueError('Purchase exceeds available cash')
            cash -= amount
            pos['shares'] += qty
            pos['cost'] += amount
        else:
            if qty > pos['shares']:
                raise ValueError('Cannot sell unowned shares')
            cost = pos['cost'] * qty / pos['shares']
            proceeds = gross - fee - tax
            cash += proceeds
            realized += proceeds - cost
            pos['shares'] -= qty
            pos['cost'] -= cost
    equity = cash
    missing, output = [], {}
    for sid, pos in positions.items():
        if not pos['shares']:
            continue
        mark = marks.get(sid, {})
        if mark.get('date') != as_of or not mark.get('price'):
            missing.append(sid)
            continue
        value = pos['shares'] * money(mark['price'])
        equity += value
        output[sid] = {'shares': int(pos['shares']), 'cost': float(pos['cost']),
                       'price': float(money(mark['price'])), 'mark_date': as_of,
                       'unrealized_pnl': float(value - pos['cost'])}
    net = equity - deposits + withdrawals if not missing else None
    return {'as_of': as_of, 'cash': float(cash), 'deposits': float(deposits),
            'withdrawals': float(withdrawals), 'realized_pnl': float(realized),
            'equity': float(equity) if not missing else None,
            'net_pnl': float(net) if net is not None else None,
            'positions': output, 'missing_marks': missing,
            'notice': '已扣實付費稅；未實現損益尚未扣未來賣出成本。'}


def risk_state(account, policy, prior=None):
    """Cashflow-neutral monetary drawdown: deposits never reset losses/pauses.

Peak consists of contributed net capital plus the highest recorded net P&L.
New deposits do not enlarge the locked drawdown allowance until a new P&L high.
"""
    prior = prior or {}
    loss_limit = float(money(policy['max_loss']))
    drawdown_rate = float(money(policy['drawdown_rate']))
    if loss_limit <= 0 or not 0 < drawdown_rate < 1:
        raise ValueError('Invalid loss policy')
    paused = bool(prior.get('paused', False))
    reasons = []
    if account['missing_marks'] or account['equity'] is None:
        return {**prior, 'paused': paused, 'blocked': True, 'reasons': ['stale_or_missing_marks']}
    pnl = account['net_pnl']
    peak_pnl = max(0, prior.get('peak_pnl', 0), pnl)
    allowance = prior.get('drawdown_allowance')
    if allowance is None or pnl > prior.get('peak_pnl', 0):
        allowance = max(0, account['equity']) * drawdown_rate
    drawdown = peak_pnl - pnl
    if pnl <= -loss_limit:
        reasons.append('cumulative_loss_limit')
    if allowance <= 0 or drawdown >= allowance:
        reasons.append('drawdown_limit')
    paused = paused or bool(reasons)
    return {'paused': paused, 'blocked': paused, 'reasons': reasons or (['manual_review_required'] if paused else []),
            'peak_pnl': peak_pnl, 'drawdown': drawdown, 'drawdown_allowance': allowance,
            'remaining_loss_budget': max(0, min(loss_limit + pnl, allowance - drawdown))}


def size_order(account, risk, entry, stop, policy, fee_rate=.001425, sell_tax=.003,
               slippage=.001, minimum_fee=20):
    """At most one actual holding. Cash and risk ceilings both constrain shares."""
    if risk.get('blocked') or account.get('missing_marks') or account.get('positions'):
        return {'shares': 0, 'reason': 'risk_block_or_existing_position'}
    e, s = money(entry), money(stop)
    if not 0 < s < e:
        raise ValueError('Stop must be below entry')
    f, t, slip, minimum = map(money, (fee_rate, sell_tax, slippage, minimum_fee))
    if max(f, t, slip) >= 1:
        raise ValueError('Invalid rate')
    equity, cash = money(account['equity']), money(account['cash'])
    per_trade, aggregate = money(policy['risk_rate']), money(policy['aggregate_risk_rate'])
    if not 0 < per_trade <= aggregate < 1:
        raise ValueError('Invalid risk rates')
    budget = min(equity * per_trade, money(policy['max_trade_loss']),
                 money(risk['remaining_loss_budget']), equity * aggregate)
    def costs(q):
        buy, sale = e * (1 + slip) * q, s * (1 - slip) * q
        total = buy + max(minimum, buy * f)
        loss = total - (sale - max(minimum, sale * f) - sale * t)
        return total, loss
    lo, hi = 0, int((cash / e).to_integral_value(rounding=ROUND_FLOOR))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cost, loss = costs(mid)
        if cost <= cash and loss <= budget:
            lo = mid
        else:
            hi = mid - 1
    cost, loss = costs(lo) if lo else (Decimal(0), Decimal(0))
    return {'shares': lo, 'estimated_cost': float(cost), 'planned_loss': float(loss),
            'risk_budget': float(budget), 'reason': 'ok' if lo else 'cost_or_risk_budget_too_small',
            'notice': '估算不是委託；跳空可能超出計畫風險，最低費用須依券商調整。'}
