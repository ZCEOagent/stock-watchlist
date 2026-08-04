"""
抓「台股有哪些股票代號」的清單。
資料來源：FinMind 的 TaiwanStockInfo。
只保留：4碼純數字、上市(twse)或上櫃(tpex)、排除 ETF 和存託憑證(TDR)的一般股票。
"""
import re
import requests

import config

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
EXCLUDED_CATEGORIES = {"ETF", "存託憑證"}
INCLUDED_TYPES = {"twse", "tpex"}


def get_tw_universe():
    """回傳 [{"stock_id": "2330", "stock_name": "台積電", "type": "twse"}, ...]"""
    params = {"dataset": "TaiwanStockInfo"}
    if config.FINMIND_TOKEN:
        params["token"] = config.FINMIND_TOKEN
    resp = requests.get(FINMIND_URL, params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json().get("data", [])

    # 同一檔股票在清單裡可能有多筆歷史紀錄（例如產業分類曾經變更），只保留最新一筆
    latest_by_id = {}
    for row in rows:
        sid = row["stock_id"]
        if sid not in latest_by_id or row["date"] > latest_by_id[sid]["date"]:
            latest_by_id[sid] = row

    universe = []
    for row in latest_by_id.values():
        if row["type"] not in INCLUDED_TYPES:
            continue
        if not re.fullmatch(r"\d{4}", row["stock_id"]):
            continue
        if row["industry_category"] in EXCLUDED_CATEGORIES:
            continue
        universe.append({
            "stock_id": row["stock_id"],
            "stock_name": row["stock_name"],
            "type": row["type"],
        })

    universe.sort(key=lambda x: x["stock_id"])
    return universe


if __name__ == "__main__":
    result = get_tw_universe()
    print(f"共取得 {len(result)} 檔台股（上市+上櫃）")
    for item in result[:5]:
        print(item)
