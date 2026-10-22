"""Read the public daily shortlist for the next deep evaluation."""
import datetime as dt
import json
import sqlite3
from pathlib import Path
from market_clock import session_dates


def candidate_ids(path, as_of, limit=10):
    """Use at most one trading session old; never look ahead or write state."""
    if not Path(path).is_file():
        return []
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT day,body FROM snapshots WHERE day<=? ORDER BY day DESC LIMIT 1',
                         (as_of,)).fetchone()
    if not row:
        return []
    day, body = row
    start = dt.date.fromisoformat(day) + dt.timedelta(days=1)
    if start.isoformat() <= as_of and len(session_dates(start.isoformat(), as_of)) > 1:
        return []
    snapshot = json.loads(body)
    if snapshot.get('day') != day or snapshot.get('freshness', {}).get('expected_date') != day:
        return []
    codes = []
    for item in snapshot.get('stocks', []):
        code = item.get('code')
        if (item.get('candidate') and item.get('price_date') == day
                and isinstance(code, str) and len(code) == 4 and code.isdigit()
                and code not in codes):
            codes.append(code)
        if len(codes) >= limit:
            break
    return codes
