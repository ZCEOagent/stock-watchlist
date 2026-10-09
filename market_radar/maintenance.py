"""Independent public-data maintenance; never owns reports or Telegram receipts."""
from contextlib import closing
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from market_clock import last_completed_session, session_dates
from . import financials, history, sources
from .store import Store
from .watch import TRACKED


def import_public(store, path, kinds=('supplement', 'price'), history_markers=True):
    """Merge newer public facts only. Never copy snapshots, receipts or delivery state."""
    if not Path(path).is_file():
        return 0
    count = 0
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as db:
        for kind in kinds:
            rows = []
            for code, period, body in db.execute('SELECT code,period,body FROM facts WHERE kind=?', (kind,)):
                incoming = json.loads(body)
                old = store.db.execute('SELECT body FROM facts WHERE kind=? AND code=? AND period=?', (kind,code,period)).fetchone()
                if old:
                    old = json.loads(old[0])
                    if (old.get('fetched_at') or '') >= (incoming.get('fetched_at') or ''):
                        continue
                rows.append(incoming)
            store.ingest({('maintenance',kind):rows})
            count += len(rows)
        if history_markers:
            for key, value in db.execute("SELECT key,value FROM meta WHERE key LIKE 'history:%'"):
                if value == 'ok':
                    store.meta(key,value)
    return count


def run(args):
    now = dt.datetime.now(ZoneInfo('Asia/Taipei'))
    stamp, today = now.isoformat(), now.date().isoformat()
    store = Store(args.state)
    try:
        # Migration is monotonic and can run again after either workflow completes.
        import_public(store,args.seed,('supplement','price','financial','universe'))
        feeds, health = sources.collect(stamp,kinds={'universe','financial'})
        if any(not h['ok'] for h in health):
            raise RuntimeError('maintenance bulk source unavailable')
        for key, rows in feeds.items():
            feeds[key] = [r for r in rows if r.get('source_date') and r['source_date'] <= today]
        companies = [r for (_,kind),rows in feeds.items() if kind == 'universe' for r in rows]
        if sum(c['market']=='twse' for c in companies)<700 or sum(c['market']=='tpex' for c in companies)<500:
            raise RuntimeError('maintenance universe incomplete')
        store.ingest(feeds)
        # Read private priority transiently; do not copy it to the public artifact.
        holdings = json.loads(os.environ.get('RADAR_HOLDINGS_JSON', '{}'))
        if not isinstance(holdings, dict):
            raise ValueError('invalid holdings configuration')
        snapshot = Store(args.seed) if Path(args.seed).is_file() else None
        try:
            latest = snapshot.latest() if snapshot else None
        finally:
            if snapshot:
                snapshot.close()
        candidates = [r['code'] for r in (latest or {}).get('stocks', []) if r.get('candidate')][:10]
        _, fin = financials.complete(store,companies,stamp,limit=args.limit,budget=args.budget,
                                    priority=set(holdings) | set(candidates) | set(TRACKED))
        as_of = last_completed_session('tw')
        days = session_dates((dt.date.fromisoformat(as_of)-dt.timedelta(days=65)).isoformat(),as_of)[-30:]
        hist = history.backfill(store,companies,days)
        status = {'fetched_at':stamp,'finished_at':dt.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
                  'financials':fin,'history':hist,'scanned':len(companies)}
        store.meta('maintenance:status',json.dumps(status))
        print(json.dumps(status),flush=True)
    finally:
        store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',default='.maintenance/state.sqlite')
    parser.add_argument('--seed',default='.radar/state.sqlite')
    parser.add_argument('--limit',type=int,choices=range(1,101),default=100)
    parser.add_argument('--budget',type=int,choices=range(1,601),default=600)
    run(parser.parse_args())


if __name__ == '__main__':
    main()
