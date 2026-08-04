"""
把一檔股票的歷史股價（日期、收盤價、成交量）算成幾個簡單的技術指標：
- 漲跌幅 (%)
- 短期均線 (MA5)、長期均線 (MA20)
- 成交量 vs 20日均量 的倍數
- 是否「今天剛站上/跌破」均線
"""
import config


def _sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def compute_indicators(rows, close_key="close", volume_key="volume", date_key="date"):
    """
    rows: 依日期由舊到新或由新到舊都可以，函式內部會自己排序
    回傳 None 表示資料不夠（例如剛上市沒多久，天數不足）
    """
    if not rows:
        return None

    # 有些資料來源（例如FinMind）對「當天完全沒有成交」的股票，
    # 會回傳收盤價0、成交量0的假資料列，而不是乾脆不回傳那一天。
    # 這種列要整列排除，不然會被誤判成「暴跌到0元」。
    sorted_rows = sorted(
        (r for r in rows if r.get(close_key) not in (None, 0) and r.get(volume_key) not in (None, 0)),
        key=lambda r: r[date_key],
    )
    closes = [float(r[close_key]) for r in sorted_rows]
    volumes = [float(r[volume_key]) for r in sorted_rows]

    if len(closes) < 2 or len(volumes) < 2:
        return None

    latest_date = sorted_rows[-1][date_key]
    latest_close = closes[-1]
    prev_close = closes[-2]
    change_pct = ((latest_close - prev_close) / prev_close * 100) if prev_close else 0.0
    latest_volume = volumes[-1]

    ma_short = _sma(closes, config.MA_SHORT)
    ma_long = _sma(closes, config.MA_LONG)
    avg_volume_long = _sma(volumes, config.MA_LONG)
    volume_ratio = (latest_volume / avg_volume_long) if avg_volume_long else None

    ma_short_prev = _sma(closes[:-1], config.MA_SHORT)
    ma_long_prev = _sma(closes[:-1], config.MA_LONG)

    cross_short = _cross_status(prev_close, latest_close, ma_short_prev, ma_short)
    cross_long = _cross_status(prev_close, latest_close, ma_long_prev, ma_long)

    return {
        "latest_date": latest_date,
        "close": round(latest_close, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(latest_volume),
        "ma_short": round(ma_short, 2) if ma_short is not None else None,
        "ma_long": round(ma_long, 2) if ma_long is not None else None,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "cross_short": cross_short,
        "cross_long": cross_long,
    }


def _cross_status(prev_close, latest_close, ma_prev, ma_now):
    """回傳 '站上' / '跌破' / None"""
    if ma_prev is None or ma_now is None:
        return None
    was_above = prev_close >= ma_prev
    is_above = latest_close >= ma_now
    if is_above and not was_above:
        return "站上"
    if not is_above and was_above:
        return "跌破"
    return None
