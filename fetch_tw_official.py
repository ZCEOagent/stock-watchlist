"""
台股上市（TWSE）資料的主要來源：證交所官方公開 API。
特色：不用 token、沒有查詢次數限制，而且「一次請求就能拿到當天全市場資料」，
跟 FinMind 要一檔一檔查完全不同，所以這裡是用「每個交易日打一次」的方式，
把最近 N 個交易日的全市場資料都抓下來，再拆成每一檔股票自己的歷史資料。

注意：這只涵蓋「上市」（TWSE）。上櫃（TPEx）目前找不到穩定的官方歷史查詢 API，
所以上櫃股票還是用 fetch_tw_stock.py（FinMind）來抓，見 main.py 的組裝邏輯。
"""
import datetime
import requests

import config

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"


def _clean_number(text):
    if text is None:
        return None
    text = text.replace(",", "").strip()
    if text in ("", "--", "---", "X"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _find_quote_table(tables):
    for t in tables:
        fields = t.get("fields") or []
        if fields and fields[0] == "證券代號":
            return t
    return None


def get_twse_daily(date: datetime.date):
    """
    抓 TWSE 某一天的全市場收盤資料。
    回傳 dict: { "2330": {"open":.., "high":.., "low":.., "close":.., "volume":..}, ... }
    如果那天不是交易日（例如假日），回傳空 dict（不算錯誤，是正常情況）。
    如果請求失敗（網路問題、官網格式變動等），回傳 None，呼叫端要當成「這次抓取失敗」處理。
    """
    date_str = date.strftime("%Y%m%d")
    try:
        resp = requests.get(
            TWSE_URL,
            params={"date": date_str, "type": "ALLBUT0999", "response": "json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None

    if data.get("stat") != "OK":
        # 非交易日、查無資料，官網會回傳非 OK 狀態，這是正常情況，不是程式錯誤
        return {}

    table = _find_quote_table(data.get("tables", []))
    if table is None:
        return None

    result = {}
    for row in table.get("data", []):
        stock_id = row[0]
        close = _clean_number(row[8])
        volume = _clean_number(row[2])
        open_ = _clean_number(row[5])
        high = _clean_number(row[6])
        low = _clean_number(row[7])
        if close is None or volume is None:
            continue
        result[stock_id] = {
            "date": date.isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    return result


def get_twse_bulk_history(stock_ids, days_needed=None, max_calendar_lookback=100, progress_every=10):
    """
    stock_ids: 要抓的 TWSE 股票代號集合（set 或 list）
    回傳: (history_dict, trading_days_collected, ok)
      history_dict: { "2330": [ {date, close, volume, ...}, ... ], ... }（只含實際有資料的股票）
      trading_days_collected: 實際抓到幾個交易日的資料
      ok: True/False，代表這次官方 API 抓取整體算不算成功（給 main.py 判斷要不要切換到備援）
    """
    days_needed = days_needed or (config.MA_LONG + 10)
    stock_ids = set(stock_ids)

    history = {sid: [] for sid in stock_ids}
    trading_days_collected = 0
    failed_requests = 0
    date = datetime.date.today()

    for i in range(max_calendar_lookback):
        day_data = get_twse_daily(date)

        if day_data is None:
            failed_requests += 1
        elif day_data:
            trading_days_collected += 1
            for sid, record in day_data.items():
                if sid in history:
                    history[sid].append(record)

        if progress_every and (i + 1) % progress_every == 0:
            print(f"  台股(上市)官方API進度: 已檢查 {i + 1} 天，取得 {trading_days_collected} 個交易日")

        if trading_days_collected >= days_needed:
            break
        date -= datetime.timedelta(days=1)

    history = {sid: rows for sid, rows in history.items() if rows}

    # 判斷這次官方 API 抓取算不算成功：
    # 交易日數不足、或請求失敗太多次、或抓到的股票數量明顯偏少，都算失敗，交給 main.py 切換備援
    enough_days = trading_days_collected >= min(days_needed, config.MA_LONG)
    too_many_failures = failed_requests > max_calendar_lookback * 0.3
    enough_coverage = len(history) >= len(stock_ids) * 0.8
    ok = enough_days and not too_many_failures and enough_coverage

    return history, trading_days_collected, ok


if __name__ == "__main__":
    # 小規模測試
    test_ids = {"2330", "2317", "0050"}
    history, days, ok = get_twse_bulk_history(test_ids, days_needed=25)
    print("成功?", ok, "交易日數:", days)
    for sid, rows in history.items():
        print(sid, "共", len(rows), "筆，最新一筆:", rows[-1] if rows else None)
