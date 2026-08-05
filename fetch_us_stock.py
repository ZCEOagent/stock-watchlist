"""
用 yfinance 抓美股（S&P 500）的歷史股價。
跟台股不同，yfinance 可以一次批次下載一大批股票，速度快很多，不需要像台股那樣一檔一檔慢慢抓。
"""
import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

import config

US_MARKET_CLOSE_WITH_BUFFER = datetime.time(16, 15)  # 收盤16:00後留15分鐘緩衝，確保資料已定案


def _is_todays_us_bar_still_incomplete():
    """
    美股當天收盤前，yfinance可能會回傳「還在跳動、尚未定案」的當天資料
    （不管程式是排程準時執行、還是有人手動臨時觸發，都可能發生）。
    這裡回傳「今天(美東)的日期」，以及「今天的資料是否還不能信任」。
    """
    now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
    still_incomplete = now_et.time() < US_MARKET_CLOSE_WITH_BUFFER
    return now_et.date().isoformat(), still_incomplete


def get_us_stock_history(universe):
    """
    universe: fetch_us_universe.get_us_universe() 回傳的清單
    回傳: { "AAPL": [ {date, close, volume, open, high, low}, ... ], ... }
    """
    symbols = [item["symbol"] for item in universe]

    data = yf.download(
        tickers=symbols,
        period=f"{config.HISTORY_CALENDAR_DAYS}d",
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )

    today_et, drop_todays_bar = _is_todays_us_bar_still_incomplete()

    result = {}
    for symbol in symbols:
        try:
            df = data[symbol].dropna(how="all")
        except (KeyError, IndexError):
            continue
        if df.empty:
            continue

        rows = []
        for date_idx, row in df.iterrows():
            if row.isna().get("Close", True):
                continue
            date_str = date_idx.strftime("%Y-%m-%d")
            if drop_todays_bar and date_str == today_et:
                continue  # 今天美股還沒收盤，這筆是還在變動的資料，不能當成「當日結果」
            rows.append({
                "date": date_str,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
            })
        if rows:
            result[symbol] = rows

    return result


if __name__ == "__main__":
    # 小規模測試：只抓 5 檔，確認邏輯正確
    test_universe = [
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
        {"symbol": "NVDA", "name": "Nvidia"},
        {"symbol": "BRK-B", "name": "Berkshire Hathaway"},
        {"symbol": "GOOGL", "name": "Alphabet"},
    ]
    history = get_us_stock_history(test_universe)
    for sym, rows in history.items():
        print(sym, "共", len(rows), "筆，最新一筆:", rows[-1] if rows else None)
