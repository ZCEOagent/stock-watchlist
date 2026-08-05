"""
主程式：串起整套「每日觀察清單」流程。

台股、美股現在是各自獨立排程（各自收盤後才跑，見 README），
所以這支程式可以只跑其中一個市場，也可以兩個都跑：

    python main.py --market tw      只跑台股（收盤後排程用）
    python main.py --market us      只跑美股（收盤後排程用）
    python main.py                  兩個都跑（本機測試、或手動補跑用，預設）

不管跑哪個模式，最後都會用「台股、美股各自最新一次的結果」組合出網站，
所以只跑一個市場也會產生完整的報告（另一個市場顯示上次跑的結果）。

本機小規模測試（只抓前 N 檔，不用等好幾小時）：
    python main.py --market tw --tw-limit 20
"""
import argparse
import time
import datetime

from fetch_tw_universe import get_tw_universe
from fetch_us_universe import get_us_universe
from fetch_tw_all import get_tw_history
from fetch_us_stock import get_us_stock_history
from indicators import compute_indicators
from screener import screen_market, pick_highlights
from fetch_news import get_tw_news, get_us_news
from report import save_report
import tracking
import data_cache
import config

EMPTY_MARKET_CACHE = {
    "scanned": 0, "success": 0, "data_source": "尚無資料（這個市場還沒執行過）",
    "watchlist": [], "highlights": [], "news": [],
}


def build_watchlist(universe, history, id_key, name_key, market, close_key="close", volume_key="volume"):
    items = []
    for entry in universe:
        code = entry[id_key]
        name = entry[name_key]
        indicator = compute_indicators(
            history.get(code, []), close_key=close_key, volume_key=volume_key
        )
        items.append((code, name, indicator))
    watchlist = screen_market(items, market=market)

    sector_lookup = {entry[id_key]: entry.get("sector", "") for entry in universe}
    for item in watchlist:
        item["sector"] = sector_lookup.get(item["id"], "")
    return watchlist


def _latest_prices(history):
    prices = {}
    for stock_id, rows in history.items():
        if rows:
            prices[stock_id] = max(rows, key=lambda r: r["date"])["close"]
    return prices


def _update_tracking_log(highlights, market, is_full_run):
    if not is_full_run:
        print("（測試模式：這次的焦點不會寫進追蹤記錄）")
        return
    today_str = datetime.date.today().isoformat()
    log = tracking.load_log()
    log = tracking.append_highlights(log, highlights, market, today_str)
    log = tracking.prune_log(log, today_str)
    tracking.save_log(log)


def run_tw(limit=None):
    print("正在取得台股清單...")
    universe = get_tw_universe()
    if limit:
        universe = universe[:limit]
    print(f"台股清單共 {len(universe)} 檔")

    print("正在抓台股股價（上市優先用官方API，上櫃用FinMind）...")
    history, data_source = get_tw_history(universe)

    print("正在計算技術指標並篩選...")
    watchlist = build_watchlist(universe, history, "stock_id", "stock_name", market="tw")
    highlights = pick_highlights(watchlist, config.HIGHLIGHT_COUNT_PER_MARKET)

    print("正在抓台股新聞...")
    news = get_tw_news()

    _update_tracking_log(highlights, "tw", is_full_run=not limit)

    cache = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "scanned": len(universe),
        "success": len(history),
        "data_source": data_source,
        "watchlist": watchlist,
        "highlights": highlights,
        "news": news,
        "latest_prices": _latest_prices(history),
    }
    data_cache.save_market_cache("tw", cache)
    print(f"台股篩出 {len(watchlist)} 檔")
    return cache


def run_us(limit=None):
    print("正在取得美股（S&P 500）清單...")
    universe = get_us_universe()
    if limit:
        universe = universe[:limit]
    print(f"美股清單共 {len(universe)} 檔")

    print("正在抓美股股價...")
    history = get_us_stock_history(universe)

    print("正在計算技術指標並篩選...")
    watchlist = build_watchlist(universe, history, "symbol", "name", market="us")
    highlights = pick_highlights(watchlist, config.HIGHLIGHT_COUNT_PER_MARKET)

    print("正在抓美股新聞...")
    news = get_us_news()

    _update_tracking_log(highlights, "us", is_full_run=not limit)

    cache = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "scanned": len(universe),
        "success": len(history),
        "data_source": "yfinance",
        "watchlist": watchlist,
        "highlights": highlights,
        "news": news,
        "latest_prices": _latest_prices(history),
    }
    data_cache.save_market_cache("us", cache)
    print(f"美股篩出 {len(watchlist)} 檔")
    return cache


def render():
    print("正在讀取台股/美股最新快取，產生報告...")
    tw = data_cache.load_market_cache("tw") or EMPTY_MARKET_CACHE
    us = data_cache.load_market_cache("us") or EMPTY_MARKET_CACHE

    today_str = datetime.date.today().isoformat()
    log = tracking.load_log()
    followups = tracking.compute_followups(
        log, tw.get("latest_prices", {}), us.get("latest_prices", {}), today_str
    )

    path = save_report(
        tw["watchlist"], us["watchlist"], tw["highlights"], us["highlights"],
        tw["news"], us["news"], followups,
        tw_scanned=tw["scanned"], tw_success=tw["success"],
        us_scanned=us["scanned"], us_success=us["success"],
        tw_data_source=tw["data_source"],
    )
    print(f"報告已存到：{path}")


def main():
    parser = argparse.ArgumentParser(description="產生每日股票觀察清單報告")
    parser.add_argument("--market", choices=["tw", "us", "both"], default="both",
                         help="只跑台股(tw)、只跑美股(us)，或兩個都跑(both，預設)")
    parser.add_argument("--tw-limit", type=int, default=None, help="只抓前 N 檔台股（測試用）")
    parser.add_argument("--us-limit", type=int, default=None, help="只抓前 N 檔美股（測試用）")
    args = parser.parse_args()

    start = time.time()
    print(f"[{datetime.datetime.now():%H:%M:%S}] 開始執行（市場：{args.market}）")

    if args.market in ("tw", "both"):
        run_tw(args.tw_limit)
    if args.market in ("us", "both"):
        run_us(args.us_limit)

    render()

    elapsed_min = (time.time() - start) / 60
    print(f"[{datetime.datetime.now():%H:%M:%S}] 完成！耗時 {elapsed_min:.1f} 分鐘")


if __name__ == "__main__":
    main()
