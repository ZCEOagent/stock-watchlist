"""
依 config 設定的門檻，從所有算好指標的股票裡，挑出「今天值得注意」的標的。
符合下面任一條件就會被列入：
- 漲跌幅超過門檻
- 成交量是均量的門檻倍數以上
- 今天剛站上或跌破均線
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


def screen_market(name_and_indicators):
    """
    name_and_indicators: [(代號, 名稱, indicator_dict), ...]
    回傳: [{"id":..., "name":..., **indicator_dict, "reasons": [...]}], 依漲跌幅絕對值排序（大到小）
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
                **indicator,
            })

    watchlist.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return watchlist
