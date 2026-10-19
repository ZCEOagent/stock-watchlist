"""Normalize TWSE MIS rows into last-trade vs snapshot semantics.

Verified 2026-10-19 12:53 against live MIS: during continuous trading the
top-level ``z`` is ``"-"`` whenever the current snapshot carries no new trade,
and top-level ``t`` is the snapshot time. The last actual trade is in the
nested ``trade`` object (``trade.z`` at ``trade.t``). Reading only ``z``/``t``
rejected 14 of 15 live quotes and mislabeled snapshot time as trade time.

The normalized row keeps the legacy keys used by watch/execution, but with
corrected meaning: ``z`` = last trade price, ``t`` = last trade time,
``snapshot_t`` = quote snapshot time. A stale price is never relabeled fresh.
"""
import datetime as dt
from .sources import number

SNAPSHOT_MAX_AGE = 90      # seconds; book/snapshot must be current intraday
TRADE_MAX_AGE = 180        # seconds; same threshold the alert logic already used


def _time(value):
    try:
        return dt.time.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _snapshot_time(row, now):
    millis = number(row.get('tlong'))
    if millis and millis > 0:
        return dt.datetime.fromtimestamp(millis / 1000, tz=now.tzinfo).time().replace(microsecond=0)
    return _time(row.get('t'))


def normalize(row):
    """Return a copy with corrected last-trade fields plus provenance.

    Falls back to the legacy top-level fields when no ``trade`` object exists.
    Conflicting top-level and nested trades at the same time are quarantined.
    """
    if not isinstance(row, dict):
        return None
    out = dict(row)
    trade = row.get('trade') if isinstance(row.get('trade'), dict) else None
    top_price, top_time = number(row.get('z')), _time(row.get('t'))
    out['snapshot_t'] = row.get('t')
    out['quote_source'] = 'mis:top-level'
    reasons = []
    if trade is not None:
        price, traded = number(trade.get('z')), _time(trade.get('t'))
        out['quote_source'] = 'mis:trade'
        if price is None or traded is None:
            # No trade reported in the nested object: fall back only to a real top-level trade.
            price, traded = (top_price, top_time) if top_price is not None else (None, None)
        elif top_price is not None and top_time == traded and abs(top_price - price) > 1e-9:
            reasons.append('trade_source_conflict')
        if traded is not None and top_time is not None and traded > top_time:
            reasons.append('trade_after_snapshot')
        out['z'] = trade.get('z') if price is not None and trade.get('z') not in (None, '-') else row.get('z')
        out['t'] = traded.isoformat() if traded is not None else None
    if out.get('t') is None or number(out.get('z')) is None:
        reasons.append('no_last_trade')
    out['quote_reasons'] = reasons
    return out


def ages(row, now):
    """Seconds since snapshot and last trade, or None when unknown."""
    snap = _snapshot_time(dict(row, t=row.get('snapshot_t') or row.get('t')), now)
    trade = _time(row.get('t'))
    stamp = lambda t: (now - dt.datetime.combine(now.date(), t, tzinfo=now.tzinfo)).total_seconds() if t else None
    return stamp(snap), stamp(trade)


def classify(row, now):
    """fresh | stale_trade | invalid, for health reporting (not alert logic)."""
    if row.get('quote_reasons') and set(row['quote_reasons']) - {'no_last_trade'}:
        return 'invalid'
    snapshot_age, trade_age = ages(row, now)
    if snapshot_age is None or snapshot_age < -5:
        return 'invalid'
    if 'no_last_trade' in (row.get('quote_reasons') or []) or trade_age is None:
        return 'stale_trade' if snapshot_age <= SNAPSHOT_MAX_AGE else 'invalid'
    if snapshot_age > SNAPSHOT_MAX_AGE:
        return 'invalid'
    return 'fresh' if 0 <= trade_age <= TRADE_MAX_AGE else 'stale_trade'
