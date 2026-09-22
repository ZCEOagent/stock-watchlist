"""Candidate-only enrichment; errors remain unknown, never bullish defaults.

FinMind schema: https://finmind.github.io/tutor/TaiwanMarket/Fundamental/
create_time is provider observation time, NOT the official release timestamp.
Financial statement dates are period ends and are NOT used as news timestamps.
"""
from datetime import date, timedelta
from pathlib import Path
import time
import requests
import yfinance as yf
import config
from storage import read_json, write_json
from quality import clean_bars, finite_positive
from market_clock import session_dates

URL = "https://api.finmindtrade.com/api/v4/data"


class EvidenceClient:
    def __init__(self, as_of):
        self.as_of = as_of
        self.last_request = 0

    def fetch(self, dataset, stock_id, days):
        path = Path(config.RUNTIME_CACHE_DIR) / "evidence" / f"{dataset}-{stock_id}.json"
        cached = read_json(path, {})
        current_chips = dataset != "TaiwanStockInstitutionalInvestorsBuySell" or any(
            r.get("date") == self.as_of for r in cached.get("rows", []))
        if cached.get("as_of") == self.as_of and current_chips:
            return cached
        result = {"as_of": self.as_of, "dataset": dataset, "source": URL,
                  "observed_on": date.today().isoformat(), "rows": [], "error": None}
        headers = {"Authorization": f"Bearer {config.FINMIND_TOKEN}"} if config.FINMIND_TOKEN else {}
        params = {"dataset": dataset, "data_id": stock_id,
                  "start_date": (date.fromisoformat(self.as_of) - timedelta(days=days)).isoformat(),
                  "end_date": self.as_of}
        for attempt in range(3):
            time.sleep(max(0, config.FINMIND_REQUEST_INTERVAL_SEC - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = requests.get(URL, headers=headers, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                if data.get("status") == 200 and isinstance(data.get("data"), list):
                    result["rows"] = data["data"]
                    write_json(path, result)
                    return result
            except (requests.RequestException, ValueError):
                pass  # Never log URLs/exception bodies which might include credentials.
            time.sleep(2 ** attempt)
        result["error"] = "資料來源失敗或權限不足"
        return result

    def get(self, stock_id):
        return {"revenue": self.fetch("TaiwanStockMonthRevenue", stock_id, 550),
                "financials": self.fetch("TaiwanStockFinancialStatements", stock_id, 550),
                "chips": self.fetch("TaiwanStockInstitutionalInvestorsBuySell", stock_id, 40)}


def adjusted_histories(universe, raw_history, as_of):
    """Rebase Yahoo adjusted OHLC to the current raw close; cross-check providers.

Not a point-in-time historical database. Store daily decisions for forward
evaluation; don't replay current revisions as historical fundamental knowledge.
"""
    symbols = {s["stock_id"]: s["stock_id"] + (".TW" if s["type"] == "twse" else ".TWO")
               for s in universe}
    if not symbols:
        return {}, {}
    result, issues = {}, {}
    try:
        data = yf.download(list(symbols.values()), period="1y", auto_adjust=False,
                           group_by="ticker", threads=4, progress=False)
    except Exception:
        return {}, {sid: "還原行情來源失敗" for sid in symbols}
    for sid, symbol in symbols.items():
        try:
            frame = data[symbol].dropna(subset=["Close", "Adj Close"])
            frame = frame[frame.index.strftime("%Y-%m-%d") <= as_of]
            latest = frame.iloc[-1]
            expected = raw_history[sid][-1]["close"]
            if frame.index[-1].strftime("%Y-%m-%d") != as_of:
                raise ValueError("還原行情過期")
            if abs(float(latest["Close"]) / expected - 1) > 0.01:
                raise ValueError("不同來源收盤價不一致")
            if not finite_positive(latest["Adj Close"]):
                raise ValueError("無有效還原基準")
            rebase = expected / float(latest["Adj Close"])
            rows = []
            confirmed_dates = set(session_dates(frame.index[0].strftime("%Y-%m-%d"), as_of))
            for idx, row in frame.iterrows():
                if idx.strftime("%Y-%m-%d") not in confirmed_dates:
                    continue  # Synthetic zero-volume bars on confirmed market holidays.
                if not finite_positive(row["Close"]):
                    continue
                factor = float(row["Adj Close"]) / float(row["Close"]) * rebase
                rows.append({"date": idx.strftime("%Y-%m-%d"), "volume": float(row["Volume"]),
                             **{key: float(row[key.title()]) * factor for key in ("open", "high", "low", "close")}})
            bars, errors = clean_bars(rows, as_of, ohlc=True)
            if errors or len(bars) < config.RADAR_MIN_HISTORY:
                raise ValueError("還原日K缺漏或歷史不足")
            result[sid] = bars
        except (KeyError, IndexError, ValueError, TypeError, ZeroDivisionError) as exc:
            issues[sid] = str(exc) if isinstance(exc, ValueError) else "還原資料格式不完整"
    return result, issues


def summarize_evidence(bundle, as_of):
    """Descriptive fundamentals/chips, with explicit unknowns."""
    revenue = bundle.get("revenue", {})
    rows = [r for r in revenue.get("rows", []) if r.get("date", "9999") <= as_of
            and (not r.get("create_time") or r["create_time"][:10] <= as_of)
            and r.get("country") in (None, "Taiwan")]
    indexed = {(int(r["revenue_year"]), int(r["revenue_month"])): r for r in rows
               if finite_positive(r.get("revenue"))}
    output = {"fundamental_ok": False, "chips_ok": False, "revenue_yoy": None,
              "revenue_period": None, "profit_period": None, "chips_date": None,
              "chip_net_5d": None, "source": URL, "observed_on": revenue.get("observed_on"),
              "notes": [], "errors": [v["error"] for v in bundle.values() if v.get("error")]}
    if indexed:
        year, month = max(indexed)
        current, previous = indexed[(year, month)], indexed.get((year - 1, month))
        output["revenue_period"] = f"{year}-{month:02d}"
        output["provider_observed_on"] = current.get("create_time") or None
        age = (date.fromisoformat(as_of).year - year) * 12 + date.fromisoformat(as_of).month - month
        if previous and 0 <= age <= 2:
            output["revenue_yoy"] = round((current["revenue"] / previous["revenue"] - 1) * 100, 2)
    profits = [r for r in bundle.get("financials", {}).get("rows", [])
               if r.get("type") == "IncomeAfterTaxes" and r.get("date", "9999") <= as_of]
    if profits:
        latest = max(profits, key=lambda r: r["date"])
        output["profit_period"] = latest["date"]
        fresh = (date.fromisoformat(as_of) - date.fromisoformat(latest["date"])).days <= 200
        output["fundamental_ok"] = bool(fresh and finite_positive(latest.get("value"))
                                         and output["revenue_yoy"] is not None and output["revenue_yoy"] > 0)
    chip_rows = [r for r in bundle.get("chips", {}).get("rows", [])
                 if r.get("date", "9999") <= as_of and r.get("name") in ("Foreign_Investor", "Investment_Trust")]
    dates = sorted({r["date"] for r in chip_rows})[-5:]
    if dates:
        output["chips_date"] = dates[-1]
        complete = len(dates) == 5 and all(
            {r["name"] for r in chip_rows if r["date"] == d} == {"Foreign_Investor", "Investment_Trust"}
            for d in dates)
        if complete:
            output["chip_net_5d"] = sum(float(r["buy"]) - float(r["sell"]) for r in chip_rows if r["date"] in dates)
            output["chips_ok"] = dates[-1] == as_of and output["chip_net_5d"] > 0
    output["notes"].append("營收年增與最新稅後盈餘只作基本面初篩；財報期末日不是公告日。")
    return output
