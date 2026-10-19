"""Per-day slot ledger: which scheduled work ran, when, by which run, how well.

The ledger lives in the same SQLite state as delivery receipts, so a repeated
trigger (external dispatcher retry or late GitHub backup cron) sees completed
work and exits instead of rescanning or re-sending.
"""
import datetime as dt
import json
import os

TERMINAL = ('completed', 'closed')


def key(day, slot):
    return f'slot:{day}:{slot}'


def get(store, day, slot):
    return json.loads(store.meta(key(day, slot)) or 'null')


def run_identity():
    return dict(run_id=os.environ.get('GITHUB_RUN_ID'), run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
                dispatch_id=os.environ.get('RADAR_DISPATCH_ID') or None,
                trigger=os.environ.get('RADAR_TRIGGER') or os.environ.get('EVENT_NAME') or 'local')


def claim(store, day, slot, now):
    """Start a slot unless it already finished. A 'started' entry from another
    run means that run died before finishing (state is uploaded on failure), so
    it is taken over and the takeover is recorded."""
    old = get(store, day, slot)
    if old and old.get('status') in TERMINAL:
        return False, old
    entry = dict(status='started', day=day, slot=slot, started_at=now.isoformat(), **run_identity())
    if old:
        entry['previous'] = {k: old.get(k) for k in ('status', 'started_at', 'run_id', 'error')}
    store.meta(key(day, slot), json.dumps(entry, ensure_ascii=False))
    return True, entry


def update(store, day, slot, **fields):
    entry = get(store, day, slot) or dict(day=day, slot=slot, **run_identity())
    entry.update(fields)
    store.meta(key(day, slot), json.dumps(entry, ensure_ascii=False))
    return entry


def finish(store, day, slot, now, status='completed', **fields):
    return update(store, day, slot, status=status, finished_at=now.isoformat(), **fields)


def for_day(store, day):
    rows = store.db.execute('SELECT key, value FROM meta WHERE key LIKE ?', (f'slot:{day}:%',))
    return {k.split(':', 2)[2]: json.loads(v) for k, v in rows}


def taipei_now():
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo('Asia/Taipei'))
