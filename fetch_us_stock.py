"""
用 yfinance 抓美股（S&P 500）的歷史股價。
跟台股不同，yfinance 可以一次批次下載一大批股票，速度快很多，不需要像台股那樣一檔一檔慢慢抓。
"""
import yfinance as yf

import config


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
            rows.append({
                "date": date_idx.strftime("%Y-%m-%d"),
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
