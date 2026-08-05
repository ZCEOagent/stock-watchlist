"""
記錄每天「今日焦點」列出的標的，之後回頭看這些標的後來股價怎麼走。

重要說明：這不是在「驗證預測準不準」——今日焦點本來就不是預測，只是
「今天已經發生的異常」。這個檔案做的事情很單純：把當時的價格記下來，
之後跟現在的價格比較，讓你自己判斷這種訊號有沒有參考價值。純資料記錄，
不做任何判斷或建議。
"""
import json
import os
import datetime

import config


def load_log(path=None):
    path = path or config.HIGHLIGHTS_LOG_PATH
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_log(log, path=None):
    path = path or config.HIGHLIGHTS_LOG_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def append_highlights(log, highlights, market, date_str):
    """把今天的焦點加進記錄（回傳新的 log，不會動到原本已經記錄的資料）"""
    new_entries = [
        {
            "date": date_str,
            "market": market,
            "id": item["id"],
            "name": item["name"],
            "flagged_close": item["close"],
            "flagged_change_pct": item["change_pct"],
            "tags": item["tags"],
        }
        for item in highlights
    ]
    return log + new_entries


def prune_log(log, today_str, retention_days=None):
    """記錄檔不要無限長大，太舊的資料就不留了"""
    retention_days = retention_days or config.TRACKING_LOG_RETENTION_DAYS
    cutoff = datetime.date.fromisoformat(today_str) - datetime.timedelta(days=retention_days)
    return [e for e in log if datetime.date.fromisoformat(e["date"]) >= cutoff]


def _latest_close(history, stock_id):
    rows = history.get(stock_id)
    if not rows:
        return None
    return max(rows, key=lambda r: r["date"]).get("close")


def compute_followups(log, tw_history, us_history, today_str, display_days=None):
    """
    對「不是今天才記錄」的焦點，算出從被列為焦點那天到現在的累積漲跌。
    只回傳 display_days 天內的紀錄，避免報告越長越長。
    """
    display_days = display_days or config.TRACKING_LOOKBACK_DISPLAY_DAYS
    today = datetime.date.fromisoformat(today_str)
    cutoff = today - datetime.timedelta(days=display_days)

    results = []
    for entry in log:
        entry_date = datetime.date.fromisoformat(entry["date"])
        if entry_date == today or entry_date < cutoff:
            continue

        history = tw_history if entry["market"] == "tw" else us_history
        current_close = _latest_close(history, entry["id"])
        if current_close is None or not entry.get("flagged_close"):
            continue

        cumulative_pct = round(
            (current_close - entry["flagged_close"]) / entry["flagged_close"] * 100, 2
        )
        results.append({
            **entry,
            "current_close": round(current_close, 2),
            "cumulative_pct": cumulative_pct,
            "days_ago": (today - entry_date).days,
        })

    results.sort(key=lambda r: r["days_ago"])
    return results
