"""
用 FinMind 抓台股每一檔的歷史股價（收盤價、成交量等），用來算均線跟量比。

注意：FinMind 免費額度有「每小時查詢次數上限」，所以這裡會依 config 設定的秒數，
一檔一檔慢慢抓（節流），抓全市場會需要一段時間，這是正常的，詳見 README 說明。
"""
import time
import datetime
import requests

import config

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _fetch_one(stock_id: str, start_date: str, end_date: str, max_retries: int = 2):
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    if config.FINMIND_TOKEN:
        params["token"] = config.FINMIND_TOKEN

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(FINMIND_URL, params=params, timeout=30)
            data = resp.json()
            if data.get("status") == 200:
                return data.get("data", [])
            # 額度用完或其他錯誤，稍等後重試
            time.sleep(3)
        except requests.RequestException:
            time.sleep(3)
    return []


def get_tw_stock_history(universe, progress_every: int = 100):
    """
    universe: fetch_tw_universe.get_tw_universe() 回傳的清單
    回傳: { "2330": [ {date, close, Trading_Volume, open, max, min}, ... ], ... }
    只回傳有抓到資料的股票。
    """
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=config.HISTORY_CALENDAR_DAYS)

    result = {}
    total = len(universe)
    for i, item in enumerate(universe, start=1):
        stock_id = item["stock_id"]
        rows = _fetch_one(stock_id, start_date.isoformat(), end_date.isoformat())
        if rows:
            result[stock_id] = rows

        if progress_every and i % progress_every == 0:
            print(f"  台股進度: {i}/{total}（已完成）")

        # 節流：避免超過 FinMind 每小時查詢上限
        if i < total:
            time.sleep(config.FINMIND_REQUEST_INTERVAL_SEC)

    return result


if __name__ == "__main__":
    # 小規模測試：只抓前 3 檔，確認程式邏輯正確，不要真的跑全市場
    test_universe = [
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse"},
        {"stock_id": "2317", "stock_name": "鴻海", "type": "twse"},
        {"stock_id": "0050", "stock_name": "元大台灣50", "type": "twse"},
    ]
    history = get_tw_stock_history(test_universe, progress_every=1)
    for sid, rows in history.items():
        print(sid, "共", len(rows), "筆，最新一筆:", rows[-1] if rows else None)
