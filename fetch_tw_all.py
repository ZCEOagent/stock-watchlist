"""
把「台股資料抓取」的完整邏輯組在一起：
- 上市（TWSE）：優先用證交所官方 API（快、沒有次數限制）
- 上櫃（TPEx）：用 FinMind（官方目前找不到穩定的歷史查詢 API）
- 如果官方 API 這次抓取品質不好（例如官網改版、暫時故障），自動改用 FinMind 幫上市股票也抓一次，
  確保「今天還是有報告」，只是會比較慢。

回傳統一格式，讓 main.py 不用管背後是哪個資料來源：
    { "2330": [ {"date":.., "close":.., "volume":.., "open":.., "high":.., "low":..}, ... ], ... }
"""
from fetch_tw_official import get_twse_bulk_history
from fetch_tw_stock import get_tw_stock_history


def _normalize_finmind_rows(rows):
    return [
        {
            "date": r["date"],
            "open": r.get("open"),
            "high": r.get("max"),
            "low": r.get("min"),
            "close": r.get("close"),
            "volume": r.get("Trading_Volume"),
        }
        for r in rows
    ]


def get_tw_history(tw_universe):
    """
    tw_universe: fetch_tw_universe.get_tw_universe() 回傳的清單
    回傳: (history_dict, data_source_label)
      data_source_label 是給報告顯示用的文字，例如「官方API＋FinMind」或「FinMind備援」
    """
    twse_universe = [u for u in tw_universe if u["type"] == "twse"]
    tpex_universe = [u for u in tw_universe if u["type"] == "tpex"]
    twse_ids = {u["stock_id"] for u in twse_universe}

    history = {}
    used_fallback_for_twse = False

    print(f"正在用官方API抓上市股票資料（{len(twse_universe)} 檔，預計很快）...")
    official_history, trading_days, ok = get_twse_bulk_history(twse_ids)

    if ok:
        history.update(official_history)
        print(f"官方API抓取成功，共取得 {trading_days} 個交易日、{len(official_history)} 檔上市股票資料")
    else:
        used_fallback_for_twse = True
        print("官方API這次抓取品質不如預期，改用FinMind幫上市股票補抓（會比較慢）...")
        finmind_twse = get_tw_stock_history(twse_universe)
        for sid, rows in finmind_twse.items():
            history[sid] = _normalize_finmind_rows(rows)

    print(f"正在用FinMind抓上櫃股票資料（{len(tpex_universe)} 檔，需要一段時間，請耐心等候）...")
    finmind_tpex = get_tw_stock_history(tpex_universe)
    for sid, rows in finmind_tpex.items():
        history[sid] = _normalize_finmind_rows(rows)

    if used_fallback_for_twse:
        data_source_label = "FinMind 備援（今日官方API資料來源異常）"
    else:
        data_source_label = "官方API（上市）＋ FinMind（上櫃）"

    return history, data_source_label


if __name__ == "__main__":
    test_universe = [
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse"},
        {"stock_id": "5480", "stock_name": "商丞", "type": "tpex"},
    ]
    history, label = get_tw_history(test_universe)
    print("資料來源:", label)
    for sid, rows in history.items():
        print(sid, "共", len(rows), "筆")
