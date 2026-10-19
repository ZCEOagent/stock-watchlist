"""Report slot progress to the external dead-man switch (Cloudflare Worker).

GitHub-hosted monitoring cannot notice GitHub not running at all, so every
scheduled slot reports start, liveness and finish to a service outside GitHub.
Missing configuration is reported, never fatal; delivery failures never stop
market work. The payload is public operational data only: no holdings, prices
of personal positions, tokens or message text.
"""
import argparse
import datetime as dt
import json
import os
import time
from zoneinfo import ZoneInfo
import requests

STATUSES = ('started', 'alive', 'completed', 'failed', 'closed', 'skipped', 'degraded')


def payload(slot, status, now=None, **fields):
    now = now or dt.datetime.now(ZoneInfo('Asia/Taipei'))
    body = dict(slot=slot, status=status, day=fields.pop('day', None) or now.date().isoformat(),
                at=now.isoformat(), dispatch_id=os.environ.get('RADAR_DISPATCH_ID') or None,
                trigger=os.environ.get('RADAR_TRIGGER') or os.environ.get('EVENT_NAME') or 'local',
                run_id=os.environ.get('GITHUB_RUN_ID'))
    if os.environ.get('GITHUB_REPOSITORY') and os.environ.get('GITHUB_RUN_ID'):
        body['run_url'] = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    body.update({k: v for k, v in fields.items() if v is not None})
    return body


def send(slot, status, now=None, post=None, sleep=time.sleep, **fields):
    if status not in STATUSES:
        raise ValueError('unknown heartbeat status')
    url, token = os.environ.get('HEARTBEAT_URL'), os.environ.get('HEARTBEAT_TOKEN')
    body = payload(slot, status, now, **fields)
    if not url or not token:
        print('Heartbeat not configured; external dead-man switch cannot see this slot: ' + json.dumps(body, ensure_ascii=False))
        return False
    post = post or requests.post
    for attempt, delay in enumerate((1, 3, None)):
        try:
            response = post(url.rstrip('/') + '/heartbeat', json=body, timeout=10,
                            headers={'Authorization': 'Bearer ' + token})
            if 200 <= response.status_code < 300:
                return True
            print(f'Heartbeat rejected HTTP {response.status_code}')
        except requests.RequestException as exc:
            print(f'Heartbeat delivery failed ({type(exc).__name__})')
        if delay is not None:
            sleep(delay)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', required=True)
    parser.add_argument('--status', required=True, choices=STATUSES)
    parser.add_argument('--detail', default=None)
    args = parser.parse_args()
    send(args.slot, args.status, detail=args.detail)


if __name__ == '__main__':
    main()
