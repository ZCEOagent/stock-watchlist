"""
台股、美股現在是各自獨立排程（收盤後才跑），所以需要把「上次跑出來的結果」
存起來，這樣網站才能同時顯示「最新的台股」+「最新的美股」，
即使兩邊是不同時間分開更新的。
"""
import json
import os

import config


def save_market_cache(market: str, data: dict):
    # 內容可能有上千檔股票，不縮排存檔案比較小（反正是機器讀的，不是給人看的）
    path = config.TW_CACHE_PATH if market == "tw" else config.US_CACHE_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def load_market_cache(market: str):
    """回傳 None 代表這個市場還沒有任何一次執行紀錄（例如第一次部署時）"""
    path = config.TW_CACHE_PATH if market == "tw" else config.US_CACHE_PATH
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
