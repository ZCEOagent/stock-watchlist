"""
依 config 設定的門檻，從所有算好指標的股票裡，挑出「今天值得注意」的標的。
符合下面任一條件就會被列入：
- 漲跌幅超過門檻
- 成交量是均量的門檻倍數以上
- 今天剛站上或跌破均線

另外還會做兩件事，讓報告更好讀：
1. 幫每檔標的算「訊號數」（同時符合幾個條件）——同時符合越多條件，代表這個訊號越不像雜訊。
2. 貼幾個好認的標籤（漲停/跌停、爆量、均線同步），純粹是規則判斷，不是AI猜的。
"""
import config


def screen_one(indicator: dict):
    """回傳符合的理由清單（空清單代表不符合任何條件）"""
    reasons = []

    if abs(indicator["change_pct"]) >= config.PRICE_CHANGE_PCT_THRESHOLD:
        direction = "上漲" if indicator["change_pct"] > 0 else "下跌"
        reasons.append(f"{direction}{abs(indicator['change_pct'])}%")

    if indicator["volume_ratio"] is not None and indicator["volume_ratio"] >= config.VOLUME_RATIO_THRESHOLD:
        reasons.append(f"成交量達均量{indicator['volume_ratio']}倍")

    if indicator["cross_short"]:
        reasons.append(f"{indicator['cross_short']}{config.MA_SHORT}日均線")

    if indicator["cross_long"]:
        reasons.append(f"{indicator['cross_long']}{config.MA_LONG}日均線")

    return reasons


def compute_tags(indicator: dict, market: str):
    """
    純規則判斷的標籤，不是AI推論：
    - 漲停/跌停：台股當天漲跌幅接近或達到10%（台股法定單日漲跌限制）
    - 爆量：成交量是均量的極端倍數以上（比一般門檻更高的量）
    - 均線同步：短期、長期均線同一天同方向被突破，代表短中期趨勢一致
    """
    tags = []

    if market == "tw" and abs(indicator["change_pct"]) >= config.TW_LIMIT_THRESHOLD_PCT:
        tags.append("漲停" if indicator["change_pct"] > 0 else "跌停")

    if indicator["volume_ratio"] is not None and indicator["volume_ratio"] >= config.EXTREME_VOLUME_RATIO:
        tags.append("爆量")

    if indicator["cross_short"] and indicator["cross_long"] and indicator["cross_short"] == indicator["cross_long"]:
        tags.append("均線同步")

    return tags


def screen_market(name_and_indicators, market="tw"):
    """
    name_and_indicators: [(代號, 名稱, indicator_dict), ...]
    market: "tw" 或 "us"，只影響漲停/跌停標籤的判斷
    回傳: [{"id":..., "name":..., "reasons":[...], "signal_count":.., "tags":[...], **indicator_dict}]
          依漲跌幅絕對值排序（大到小）
    """
    watchlist = []
    for code, name, indicator in name_and_indicators:
        if indicator is None:
            continue
        reasons = screen_one(indicator)
        if reasons:
            watchlist.append({
                "id": code,
                "name": name,
                "reasons": reasons,
                "signal_count": len(reasons),
                "tags": compute_tags(indicator, market),
                **indicator,
            })

    watchlist.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return watchlist


def pick_highlights(watchlist, top_n=3):
    """
    從觀察清單裡挑出「最值得看」的幾檔：
    優先看同時符合幾個條件（signal_count），條件越多代表訊號越不像單純雜訊；
    條件數一樣時，再比漲跌幅誰比較大。
    """
    ranked = sorted(watchlist, key=lambda x: (x["signal_count"], abs(x["change_pct"])), reverse=True)
    return ranked[:top_n]
