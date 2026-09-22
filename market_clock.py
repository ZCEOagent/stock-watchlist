"""Exchange sessions, explicit time zones and completed-bar boundaries."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
import exchange_calendars as xcals
import pandas as pd
from storage import read_json, write_json

_tw_sessions = None


def refresh_tw_sessions(persist=True):
    """Provider calendar includes announced extra closures missing in XTAI."""
    import requests
    import config
    headers = {"Authorization": f"Bearer {config.FINMIND_TOKEN}"} if config.FINMIND_TOKEN else {}
    response = requests.get("https://api.finmindtrade.com/api/v4/data", headers=headers,
                            params={"dataset": "TaiwanStockTradingDate"}, timeout=20)
    response.raise_for_status()
    data = response.json()
    dates = sorted({r["date"] for r in data.get("data", [])})
    today = now_tw().date().isoformat()
    if data.get("status") != 200 or len(dates) < 250 or dates[-1] < today:
        raise RuntimeError("交易日清單不足，無法確認全市場休市與個股停牌")
    global _tw_sessions
    _tw_sessions = dates
    if persist:
        write_json("state/calendar.json", {"updated_at": now_tw().isoformat(), "dates": dates,
                                           "source": "FinMind TaiwanStockTradingDate"})


def session_dates(start, end, market="tw"):
    global _tw_sessions
    if market == "tw" and _tw_sessions is None:
        _tw_sessions = read_json("state/calendar.json", {}).get("dates", [])
    baseline = [s.date().isoformat() for s in calendar(market).sessions_in_range(start, end)]
    if market == "tw" and _tw_sessions:
        outside = [d for d in baseline if not _tw_sessions[0] <= d <= _tw_sessions[-1]]
        return sorted(outside + [d for d in _tw_sessions if start <= d <= end])
    return baseline


def close_time(day, market):
    if market == "tw":
        return pd.Timestamp(datetime.fromisoformat(day + "T13:30:00").replace(tzinfo=ZoneInfo("Asia/Taipei")))
    return calendar(market).session_close(day)


@lru_cache(maxsize=2)
def calendar(market):
    return xcals.get_calendar("XTAI" if market == "tw" else "XNYS")


def now_tw():
    return datetime.now(ZoneInfo("Asia/Taipei"))


def last_completed_session(market, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timestamp must include a time zone")
    cal = calendar(market)
    zone = ZoneInfo("Asia/Taipei" if market == "tw" else "America/New_York")
    local_date = now.astimezone(zone).date().isoformat()
    start = (datetime.fromisoformat(local_date).date() - timedelta(days=40)).isoformat()
    dates = session_dates(start, local_date, market)
    completed = [day for day in dates if pd.Timestamp(now) >= close_time(day, market) + pd.Timedelta(minutes=30)]
    if not completed:
        raise RuntimeError("近期交易日資料不足")
    return completed[-1]


def sessions_after(start, end, market="tw"):
    return [day for day in session_dates(start, end, market) if day > start]


def advance_session(day, count, market="tw"):
    end = (datetime.fromisoformat(day).date() + timedelta(days=max(60, count * 3))).isoformat()
    dates = session_dates(day, end, market)
    return dates[count]


def completed_weeks(rows, as_of, market="tw"):
    groups = {}
    for row in rows:
        if row["date"] > as_of:
            continue
        date = datetime.fromisoformat(row["date"]).date()
        monday = date - timedelta(days=date.weekday())
        groups.setdefault(monday, []).append(row)
    result = []
    for monday, bars in sorted(groups.items()):
        friday = monday + timedelta(days=4)
        dates = session_dates(monday.isoformat(), friday.isoformat(), market)
        if not dates or dates[-1] > as_of or sorted(b["date"] for b in bars) != dates:
            continue
        bars = sorted(bars, key=lambda b: b["date"])
        result.append({"date": bars[-1]["date"], "open": bars[0]["open"],
                       "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars),
                       "close": bars[-1]["close"], "volume": sum(b["volume"] for b in bars)})
    return result
