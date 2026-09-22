"""Telegram delivery validation with a persistent outbox.

No live sending unless explicitly invoked with --send. Ambiguous timeouts
remain uncertain: Telegram sendMessage has no idempotency key, so blindly
retrying can duplicate a message. A human can reconcile uncertain delivery.
"""
import argparse
import hashlib
import os
import time
import subprocess
from pathlib import Path
import requests
import config
from radar import LABELS
from storage import read_json, write_json


def send_telegram(text, token, chat_id, post=requests.post, sleep=time.sleep):
    for attempt in range(3):
        try:
            response = post(f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
        except requests.RequestException:
            return "uncertain", "傳輸中斷，無法確認是否送達；不自動重送"
        try:
            body = response.json()
        except ValueError:
            return "uncertain", "回應格式異常，需核對送達狀態"
        if response.status_code == 200 and body.get("ok") is True:
            return "sent", None
        if response.status_code == 429 or body.get("error_code") == 429:
            delay = body.get("parameters", {}).get("retry_after", 2 ** attempt)
            sleep(min(max(float(delay), 1), 60))
            continue
        return "failed", "Telegram 拒絕通知，請檢查權限與收件設定"
    return "failed", "Telegram 限流，等待下次補送"


def build_messages(market, cache):
    if not cache.get("quality", {}).get("passed"):
        return []
    day = cache["as_of"]
    label = "台股" if market == "tw" else "美股"
    messages = [(f"report:{market}:{day}", f"{label}收盤報告已更新｜行情日期 {day}\nhttps://zceoagent.github.io/stock-watchlist/")]
    if market == "tw" and config.RADAR_MODE == "live":
        for item in cache.get("radar", {}).get("items", []):
            if item["status"] not in ("waiting", "triggered", "invalid", "expired", "review", "target") or not item.get("plan"):
                continue
            p = item["plan"]
            # One notification per state per plan, not per daily scan.
            key = f"radar:{item['id']}:{item['created_on']}:{item['status']}"
            text = (f"【波段雷達｜{LABELS[item['status']]}】{item['id']} {item['name']}\n"
                    f"行情日期：{day}（收盤資料，非即時報價）\n"
                    f"觀察進場區：{p['entry_low']:.2f}～{p['entry_high']:.2f}\n"
                    f"失效：{p['stop']:.2f}｜目標：{p['target']:.2f}\n"
                    f"扣成本 R:R：{item.get('current_rr', p['rr']):.2f}\n"
                    f"有效至：{item['expires_on']}｜預計2～4週，第5交易日重評\n"
                    "實際進場仍需即時價格與可成交性確認。")
            messages.append((key, text))
    return messages


def checkpoint_git(path):
    """Persist outbox BEFORE transmission; failure prevents sending."""
    resolved = Path(path).resolve()
    root = Path.cwd().resolve()
    if resolved != root / "state" / "notifications.json":
        raise ValueError("Git checkpoint only supports state/notifications.json")
    relative = "state/notifications.json"
    subprocess.run(["git", "add", "--", relative], check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet", "--", relative]).returncode
    if changed == 1:
        subprocess.run(["git", "commit", "-m", "Persist notification delivery state", "--", relative], check=True)
        subprocess.run(["git", "push"], check=True)
    elif changed != 0:
        raise RuntimeError("Cannot verify staged delivery state")


def deliver(messages, send=False, state_path=None, checkpoint=None):
    path = state_path or config.NOTIFICATION_STATE_PATH
    state = read_json(path, {})
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    failed = False
    for logical_key, text in messages:
        key = hashlib.sha256(logical_key.encode()).hexdigest()
        previous = state.get(key, {}).get("status")
        if previous in ("sent", "sending", "uncertain"):
            failed |= previous != "sent"
            continue
        if not send:
            print(f"DRY RUN: {text}")
            continue
        if not token or not chat_id:
            raise RuntimeError("缺少 Telegram 環境變數；沒有發送通知")
        state[key] = {"status": "sending", "key": logical_key}
        write_json(path, state)
        if checkpoint:
            checkpoint(path)
        status, error = send_telegram(text, token, chat_id)
        state[key] = {"status": status, "key": logical_key, "error": error}
        write_json(path, state)
        if checkpoint:
            checkpoint(path)
        failed |= status != "sent"
    return not failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--failure", action="store_true")
    parser.add_argument("--checkpoint-git", action="store_true")
    args = parser.parse_args()
    if args.failure:
        run = os.environ.get("GITHUB_RUN_ID", "manual")
        messages = [(f"failure:{args.market}:{run}", f"股票雷達 {args.market} 執行失敗／資料品質未通過，請查看 GitHub Actions。")]
    else:
        cache = read_json(config.TW_CACHE_PATH if args.market == "tw" else config.US_CACHE_PATH, {})
        messages = build_messages(args.market, cache)
    raise SystemExit(0 if deliver(messages, send=args.send, checkpoint=checkpoint_git if args.checkpoint_git else None) else 1)
