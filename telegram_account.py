"""Private single-user Telegram ledger worker. Never places broker orders.

Requires existing token/chat ID in environment and --account outside Git.
Refuses existing webhooks; does not change bot settings or erase pending updates.
Run one instance only on a persistent private host. No credentials in logs.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import os
import time
import requests
from market_clock import now_tw
from portfolio import calculate, risk_state
from private_account import private_path
from storage import read_json, write_json
from notify import send_telegram

HELP = ('私人記帳，非下單。\n'
        '/deposit 金額\n/withdrawal 金額\n'
        '/buy 股票代號 股數 成交價 手續費 稅金 成交時間\n'
        '/sell 股票代號 股數 成交價 手續費 稅金 成交時間\n'
        '成交時間格式：2026-09-22T10:30:00+08:00\n'
        '/status 查詢已記錄持倉與損益\n'
        '費用請填實付金額；零股用股數。記錯請先停止新增回報，聯絡維護者核對，不要重複送出。')


def summary(snapshot, risk):
    lines = [f"【私人帳本】估值日期 {snapshot['as_of']}（收盤資料，非即時）",
             f"可用現金 {snapshot['cash']:.2f}｜已實現損益 {snapshot['realized_pnl']:+.2f}"]
    if snapshot['net_pnl'] is None:
        lines.append('持股行情缺漏或未更新，總損益暫不估算；不提供新增部位。')
    else:
        lines.append(f"帳戶淨值 {snapshot['equity']:.2f}｜累積淨損益 {snapshot['net_pnl']:+.2f}")
    for sid, pos in snapshot['positions'].items():
        lines.append(f"{sid}：{pos['shares']}股｜未實現 {pos['unrealized_pnl']:+.2f}")
    if risk.get('paused'):
        lines.append('已達暫停規則：停止新增交易並檢討。入金不自動解除。')
    lines.append('已扣實付費稅，未實現損益未扣未來賣出費用；成交由本人回報。')
    return '\n'.join(lines)


def process_update(account, update, authorized_chat, marks, as_of):
    """Pure handler. Validate identity before inspecting private command text."""
    out = deepcopy(account)
    uid = update['update_id']
    if uid < out.get('next_update', 0):
        return out, None
    out['next_update'] = uid + 1
    msg = update.get('message', {})
    chat, sender = msg.get('chat', {}), msg.get('from', {})
    if chat.get('type') != 'private' or str(chat.get('id')) != str(authorized_chat) or sender.get('id') != chat.get('id') or sender.get('is_bot'):
        return out, None
    parts = msg.get('text', '').split()
    if not parts or parts[0] in ('/help', '/start'):
        return out, HELP
    command = parts[0]
    if command not in ('/deposit', '/withdrawal', '/buy', '/sell', '/status'):
        return out, HELP
    event_id = f'telegram:{uid}'
    additions = []
    if command != '/status' and not any(e['event_id'] == event_id for e in out.get('events', [])):
        try:
            at = datetime.fromtimestamp(msg['date'], now_tw().tzinfo).isoformat()
            entry = dict(event_id=event_id, kind=command[1:], at=at)
            if command in ('/deposit', '/withdrawal') and len(parts) == 2:
                entry['amount'] = parts[1]
            elif command in ('/buy', '/sell') and len(parts) == 7:
                entry.update(zip(('stock_id', 'shares', 'price', 'fee', 'tax', 'at'), parts[1:]))
            else:
                return out, '指令格式不正確，尚未記帳。\n' + HELP
            additions = [entry]
        except (ValueError, KeyError, TypeError):
            return out, '指令格式不正確，尚未記帳。'
    try:
        events = sorted(out.get('events', []) + additions, key=lambda e: datetime.fromisoformat(e['at']))
        snapshot = calculate(events, marks, as_of)
        risk = risk_state(snapshot, out['policy'], out.get('risk')) if snapshot['deposits'] else {'blocked': True}
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return out, '未記帳：請核對時間、股數、金額、現金及持股。可用 /status 查詢；不要重複回報已登記成交。'
    out.update(events=events, snapshot=snapshot, risk=risk, as_of=as_of)
    return out, ('已記帳。\n' if additions else '') + summary(snapshot, risk)


def public_marks():
    response = requests.get('https://raw.githubusercontent.com/ZCEOagent/stock-watchlist/master/docs/tw_cache.json', timeout=20)
    response.raise_for_status()
    cache = response.json()
    if not cache.get('quality', {}).get('passed'):
        return {}
    return {sid: {'date': cache['as_of'], 'price': price} for sid, price in cache.get('latest_prices', {}).items()}


def run(path):
    token, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat or not chat.isdigit():
        raise ValueError('Configure existing bot token and positive private chat ID locally')
    account = read_json(path, None)
    if not account or not account.get('policy'):
        raise ValueError('Private policy file required; no implicit deposits')
    base = f'https://api.telegram.org/bot{token}/'
    info = requests.get(base + 'getWebhookInfo', timeout=20)
    if info.status_code != 200 or info.json().get('ok') is not True or info.json()['result'].get('url'):
        raise ValueError('Cannot start polling with existing webhook or invalid bot access')
    marks, refreshed = {}, 0
    while True:
        try:
            if time.monotonic() - refreshed > 60:
                try:
                    marks = public_marks()
                except (requests.RequestException, ValueError, KeyError):
                    marks = {}
                refreshed = time.monotonic()
            today = now_tw().date().isoformat()
            if account.get('events') and account.get('daily_mark_sent') != today:
                snapshot = calculate(account['events'], marks, today)
                if snapshot['positions'] and not snapshot['missing_marks']:
                    risk = risk_state(snapshot, account['policy'], account.get('risk'))
                    account.update(snapshot=snapshot, risk=risk, as_of=today, daily_mark_sent=today)
                    write_json(path, account)
                    status, _ = send_telegram(summary(snapshot, risk), token, chat)
                    if status != 'sent':
                        print('Daily valuation delivery unconfirmed; use /status. No blind retry.')
            response = requests.post(base + 'getUpdates', json={'offset': account.get('next_update', 0),
                                      'timeout': 25, 'allowed_updates': ['message']}, timeout=35)
            if response.status_code == 409:
                raise RuntimeError('Another Telegram receiver is active; stopping this worker')
            response.raise_for_status()
            body = response.json()
            if body.get('ok') is not True:
                raise ValueError('Telegram request rejected')
            for update in body['result']:
                account, reply = process_update(account, update, chat, marks, today)
                # Persist both ledger and offset before sending any acknowledgement.
                write_json(path, account)
                if reply:
                    status, _ = send_telegram(reply, token, chat)
                    if status != 'sent':
                        print('Reply delivery unconfirmed; ledger saved. Query /status; no blind retry.')
        except (requests.RequestException, ValueError, KeyError):
            print('Source unavailable; retrying without exposing request details.')
            time.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    args = parser.parse_args()
    try:
        account_path = private_path(args.account)
        # One process per private ledger, including after abrupt process exits.
        with open(account_path.with_suffix('.lock'), 'a+b') as lock:
            lock.seek(0)
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(account_path)
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit('Worker stopped. Check private configuration, connection or receiver conflicts; no credentials logged.') from None
