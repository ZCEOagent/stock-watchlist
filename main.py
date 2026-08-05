"""
主程式：串起整套「每日觀察清單」流程。

一般執行（跑全市場，台股會花較久時間，屬正常現象，見 README）：
    python main.py

本機小規模測試（只抓前 N 檔台股，不用等好幾小時）：
    python main.py --tw-limit 20
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
import config


def build_watchlist(universe, history, id_key, name_key, market, close_key="close", volume_key="volume"):
    items = []
    for entry in universe:
        code = entry[id_key]
        name = entry[name_key]
        indicator = compute_indicators(
            history.get(code, []), close_key=close_key, volume_key=volume_key
        )
        items.append((code, name, indicator))
    return screen_market(items, market=market)


def main():
    parser = argparse.ArgumentParser(description="產生每日股票觀察清單報告")
    parser.add_argument("--tw-limit", type=int, default=None, help="只抓前 N 檔台股（測試用）")
    parser.add_argument("--us-limit", type=int, default=None, help="只抓前 N 檔美股（測試用）")
    args = parser.parse_args()

    start = time.time()
    print(f"[{datetime.datetime.now():%H:%M:%S}] 開始執行")

    print("正在取得台股清單...")
    tw_universe = get_tw_universe()
    if args.tw_limit:
        tw_universe = tw_universe[: args.tw_limit]
    print(f"台股清單共 {len(tw_universe)} 檔")

    print("正在取得美股（S&P 500）清單...")
    us_universe = get_us_universe()
    if args.us_limit:
        us_universe = us_universe[: args.us_limit]
    print(f"美股清單共 {len(us_universe)} 檔")

    print("正在抓台股股價（上市優先用官方API，上櫃用FinMind）...")
    tw_history, tw_data_source = get_tw_history(tw_universe)

    print("正在抓美股股價...")
    us_history = get_us_stock_history(us_universe)

    print("正在計算技術指標並篩選...")
    tw_watchlist = build_watchlist(
        tw_universe, tw_history, "stock_id", "stock_name", market="tw",
        close_key="close", volume_key="volume",
    )
    us_watchlist = build_watchlist(
        us_universe, us_history, "symbol", "name", market="us",
        close_key="close", volume_key="volume",
    )

    print("正在抓財經新聞...")
    tw_news = get_tw_news()
    us_news = get_us_news()

    print("正在整理今日焦點與追蹤紀錄...")
    today_str = datetime.date.today().isoformat()
    tw_highlights = pick_highlights(tw_watchlist, config.HIGHLIGHT_COUNT_PER_MARKET)
    us_highlights = pick_highlights(us_watchlist, config.HIGHLIGHT_COUNT_PER_MARKET)

    log = tracking.load_log()
    followups = tracking.compute_followups(log, tw_history, us_history, today_str)

    is_full_run = not args.tw_limit and not args.us_limit
    if is_full_run:
        # 只有跑「全市場」時才把今天的焦點寫進追蹤記錄，
        # 避免小規模測試（--tw-limit 之類）產生不具代表性的焦點污染記錄檔
        log = tracking.append_highlights(log, tw_highlights, "tw", today_str)
        log = tracking.append_highlights(log, us_highlights, "us", today_str)
        log = tracking.prune_log(log, today_str)
        tracking.save_log(log)
    else:
        print("（測試模式：這次的焦點不會寫進追蹤記錄）")

    print("正在產生報告...")
    path = save_report(
        tw_watchlist, us_watchlist, tw_highlights, us_highlights,
        tw_news, us_news, followups,
        tw_scanned=len(tw_universe), tw_success=len(tw_history),
        us_scanned=len(us_universe), us_success=len(us_history),
        tw_data_source=tw_data_source,
    )

    elapsed_min = (time.time() - start) / 60
    print(f"[{datetime.datetime.now():%H:%M:%S}] 完成！耗時 {elapsed_min:.1f} 分鐘")
    print(f"報告已存到：{path}")
    print(f"台股篩出 {len(tw_watchlist)} 檔，美股篩出 {len(us_watchlist)} 檔")


if __name__ == "__main__":
    main()
