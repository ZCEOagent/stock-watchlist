"""Forward signal outcomes; never represent simulations as personal holdings."""
import hashlib
from collections import Counter
from statistics import mean
from evaluation import replay

VERSION = 'forward-next-open-v2'


def signal_snapshots(events):
    result = {}
    for event in events:
        snap = event.get('snapshot', {})
        if snap.get('status') != 'triggered' or not snap.get('plan') or not snap.get('triggered_on'):
            continue
        key = f"{snap['id']}:{snap['created_on']}:{snap['triggered_on']}"
        # First recorded decision only; subsequent evidence cannot rewrite entry.
        result.setdefault(key, snap)
    return result


def evaluate_signals(events, histories, as_of, previous=None):
    previous = previous or {}
    records = {}
    for key, snap in signal_snapshots(events).items():
        if snap['triggered_on'] > as_of:
            continue
        old = previous.get(key, {})
        if old.get('outcome', {}).get('status') in ('closed', 'not_filled'):
            records[key] = old
            continue
        bars = [b for b in histories.get(snap['id'], []) if b['date'] <= as_of]
        basis = next((b['close'] for b in bars if b['date'] == snap['as_of']), None)
        observed = snap.get('observed_close')
        if not bars or bars[-1]['date'] != as_of:
            outcome = {'status': 'unconfirmed', 'reason': 'missing_or_stale_prices'}
        elif not observed or basis is None or abs(basis / observed - 1) > .005:
            outcome = {'status': 'unconfirmed', 'reason': 'price_basis_changed_or_missing'}
        else:
            outcome = replay(snap['plan'], snap['triggered_on'], bars)
        records[key] = {'signal_id': hashlib.sha256(key.encode()).hexdigest()[:24],
                        'id': snap['id'], 'signal_date': snap['triggered_on'],
                        'evaluated_on': as_of, 'method': VERSION, 'outcome': outcome}
    counts = dict(Counter(r['outcome']['status'] for r in records.values()))
    closed = [r['outcome']['net_return_pct'] for r in records.values() if r['outcome']['status'] == 'closed']
    return {'as_of': as_of, 'method': VERSION, 'records': records,
            'summary': {'counts': counts, 'closed': len(closed),
                        'win_rate': sum(x > 0 for x in closed) / len(closed) if closed else None,
                        'mean_net_return_pct': mean(closed) if closed else None},
            'notice': '訊號次日開盤模擬；不是實際成交或投資組合報酬，無訊號時不產生績效。'}
