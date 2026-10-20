"""One long-running intraday job instead of 66 five-minute GitHub crons.

Measured 2026-09-30..10-19: GitHub `schedule` delivered ~8-13 of ~122 daily
market-radar triggers with a 3-5.5 h median delay, so intraday checks almost
never ran during the session. An externally dispatched job started before the
open polls on its own clock; a late or duplicate trigger only covers the
remaining session or exits through the slot ledger.

Each iteration reuses the existing quote/event paths of `market_radar.cli`, so
alert rules, budgets, dedup and paper execution are unchanged.
"""
import argparse
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

from market_clock import session_dates
from . import cli, heartbeat, ledger
from .store import Store

TZ = ZoneInfo('Asia/Taipei')
SLOT = 'intraday'
OPEN = dt.time(9, 0, 20)
END = dt.time(13, 36)          # includes the post-close review checks
INTERVAL = 75                  # seconds between quote checks
EVENTS_EVERY = 30 * 60         # announcement polling folded into the same loop
HEARTBEAT_EVERY = 5 * 60
DEGRADED_RATIO = 0.5           # current snapshots / requested codes
DEGRADED_STREAK = 3


def now_tw():
    return dt.datetime.now(TZ)


def cli_args(mode, args):
    return argparse.Namespace(mode=mode, state=args.state, output=args.output, price_seed=args.price_seed,
                              send=args.send, delivery_slot='scheduled', maintenance_state=args.maintenance_state,
                              financial_limit=0)


def default_step(mode, args):
    cli.run(cli_args(mode, args))


def quote_health(path):
    store = Store(path)
    try:
        return json.loads(store.meta('watch-health') or '{}')
    finally:
        store.close()


def with_store(path, fn):
    store = Store(path)
    try:
        return fn(store)
    finally:
        store.close()


def run(args, clock=now_tw, sleep=time.sleep, step=default_step, beat=heartbeat.send):
    now = clock()
    day = now.date().isoformat()
    if not session_dates(day, day):
        with_store(args.state, lambda s: ledger.finish(s, day, SLOT, now, status='closed'))
        beat(SLOT, 'closed', now)
        print(f'{day} is not a Taiwan trading session; nothing to poll.')
        return 0
    if now.time() >= END:
        with_store(args.state, lambda s: ledger.update(s, day, SLOT, late_trigger_at=now.isoformat()))
        beat(SLOT, 'skipped', now, detail='triggered_after_session_end')
        print('Triggered after the session ended; recorded as a late trigger, no stale checks performed.')
        return 0
    claimed, entry = with_store(args.state, lambda s: ledger.claim(s, day, SLOT, now))
    if not claimed:
        print(f"Intraday slot already {entry['status']} by run {entry.get('run_id')}; duplicate trigger exits.")
        return 0
    beat(SLOT, 'started', now, late_start=now.time() > OPEN)
    stats = dict(iterations=0, good_iterations=0, quote_errors=0, event_errors=0, degraded_streak=0, max_degraded_streak=0,
                 first_check_at=None, last_check_at=None, last_ratio=None)
    last_events = None
    last_beat = clock()
    while True:
        now = clock()
        if now.time() >= END:
            break
        if now.time() < OPEN:
            wait = (dt.datetime.combine(now.date(), OPEN, tzinfo=now.tzinfo) - now).total_seconds()
            sleep(max(1, min(wait, 60)))
            continue
        started = time.monotonic()
        mode = 'events' if last_events is None or (now - last_events).total_seconds() >= EVENTS_EVERY else 'quotes'
        failed = False
        try:
            step(mode, args)
        except SystemExit as exc:
            failed = exc.code not in (0, None)
        except Exception as exc:  # one failed iteration must not end the session
            failed = True
            print(f'{mode} iteration failed ({type(exc).__name__})')
        if mode == 'events':
            last_events = now
        health = quote_health(args.state)
        requested = health.get('requested') or 0
        ratio = (health.get('snapshot_ok', health.get('valid', 0)) / requested) if requested else 0.0
        fresh_check = health.get('checked_at', '') >= now.isoformat()[:16]
        # After 13:30 the book stops updating, so snapshot age is meaningless: a correct
        # closing quote is classified stale from ~13:32 and would raise a false
        # "degraded" alarm every session. Post-close only a check that failed to run counts.
        post_close = now.time() >= dt.time(13, 30)
        degraded = (not fresh_check) or (not post_close and ratio < DEGRADED_RATIO)
        if failed and (mode == 'quotes' or degraded):
            stats['quote_errors'] += 1
        elif failed:
            stats['event_errors'] += 1   # announcement source failed; quotes in that run were still checked
        stats['good_iterations'] += not degraded and not (failed and mode == 'quotes')
        stats['degraded_streak'] = stats['degraded_streak'] + 1 if degraded else 0
        stats['max_degraded_streak'] = max(stats['max_degraded_streak'], stats['degraded_streak'])
        stats['iterations'] += 1
        stats['last_ratio'] = round(ratio, 4)
        stats['first_check_at'] = stats['first_check_at'] or now.isoformat()
        stats['last_check_at'] = now.isoformat()
        status = 'degraded' if stats['degraded_streak'] >= DEGRADED_STREAK else 'alive'
        if status == 'degraded' and stats['degraded_streak'] == DEGRADED_STREAK or \
                (clock() - last_beat).total_seconds() >= HEARTBEAT_EVERY:
            beat(SLOT, status, clock(), valid_ratio=stats['last_ratio'], iterations=stats['iterations'],
                 quote_errors=stats['quote_errors'], degraded_streak=stats['degraded_streak'])
            with_store(args.state, lambda s: ledger.update(s, day, SLOT, **stats))
            last_beat = clock()
        sleep(max(1, INTERVAL - (time.monotonic() - started)))
    finished = clock()
    # A session counts as completed only if most checks had usable, current quotes.
    ok = stats['iterations'] > 0 and stats['good_iterations'] * 2 >= stats['iterations']
    status = 'completed' if ok else 'failed'
    with_store(args.state, lambda s: ledger.finish(s, day, SLOT, finished, status=status, **stats))
    beat(SLOT, status, finished, valid_ratio=stats['last_ratio'], iterations=stats['iterations'],
         quote_errors=stats['quote_errors'], max_degraded_streak=stats['max_degraded_streak'])
    print('Intraday session: ' + json.dumps(dict(status=status, **stats), ensure_ascii=False))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='.radar/state.sqlite')
    parser.add_argument('--output', default='.radar/reports')
    parser.add_argument('--price-seed', default='.runtime/tw_history.json')
    parser.add_argument('--maintenance-state', default='.maintenance/state.sqlite')
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == '__main__':
    main()
