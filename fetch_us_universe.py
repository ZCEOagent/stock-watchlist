"""
抓「美股全市場代表清單」，用 S&P 500 成分股當作範圍。
資料來源：公開免費的 S&P 500 成分股 CSV（GitHub datasets 專案維護）。
"""
import io
import requests

import config

def get_us_universe():
    """回傳 [{"symbol": "AAPL", "name": "Apple Inc."}, ...]"""
    resp = requests.get(config.SP500_LIST_URL, timeout=30)
    resp.raise_for_status()

    import csv
    reader = csv.DictReader(io.StringIO(resp.text))
    universe = []
    for row in reader:
        symbol = row["Symbol"].strip()
        # yfinance 用 "-" 表示股份等級（例如 BRK-B），CSV 裡是用 "."（BRK.B），要轉換
        symbol = symbol.replace(".", "-")
        universe.append({"symbol": symbol, "name": row["Security"].strip()})

    universe.sort(key=lambda x: x["symbol"])
    return universe


if __name__ == "__main__":
    result = get_us_universe()
    print(f"共取得 {len(result)} 檔美股（S&P 500）")
    for item in result[:5]:
        print(item)
