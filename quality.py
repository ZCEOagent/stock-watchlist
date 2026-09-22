"""Reject stale, incomplete, non-finite and contradictory market data."""
import math
from collections import Counter


def finite_positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def clean_bars(rows, as_of, ohlc=False):
    by_date, errors = {}, []
    for row in rows:
        day = row.get("date", "")
        if not day or day > as_of:
            continue
        keys = ("open", "high", "low", "close", "volume") if ohlc else ("close", "volume")
        if not all(finite_positive(row.get(key)) for key in keys):
            errors.append(f"{day}:invalid")
            continue
        if ohlc and not (float(row["low"]) <= min(float(row["open"]), float(row["close"]))
                         <= max(float(row["open"]), float(row["close"])) <= float(row["high"])):
            errors.append(f"{day}:ohlc")
            continue
        if day in by_date and by_date[day] != row:
            errors.append(f"{day}:conflicting_duplicate")
        by_date[day] = row
    return [by_date[day] for day in sorted(by_date)], errors


def assess_market(universe, history, as_of, id_key, minimum=21, threshold=0.90):
    valid, rejected = {}, {}
    for stock in universe:
        sid = stock[id_key]
        bars, errors = clean_bars(history.get(sid, []), as_of)
        reason = None
        if not bars:
            reason = "missing"
        elif bars[-1]["date"] != as_of:
            reason = "stale"
        elif errors:
            reason = "invalid_bars"
        elif len(bars) < minimum:
            reason = "short_history"
        if reason:
            rejected[sid] = reason
        else:
            valid[sid] = bars
    coverage = len(valid) / len(universe) if universe else 0
    return valid, {"as_of": as_of, "coverage": round(coverage, 4), "passed": coverage >= threshold,
                   "valid": len(valid), "total": len(universe), "rejected": rejected,
                   "counts": dict(Counter(rejected.values()))}
