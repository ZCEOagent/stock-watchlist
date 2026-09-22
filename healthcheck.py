"""Read-only freshness check; nonzero exit alerts workflow health monitoring."""
import argparse
from datetime import datetime, timezone
import pandas as pd
from market_clock import last_completed_session, close_time
from storage import read_json
import config


def check(market, cache, now=None):
    now = now or datetime.now(timezone.utc)
    expected = last_completed_session(market, now)
    close = close_time(expected, market)
    # TW enrichment runs in the evening; US gets a shorter post-close allowance.
    grace = pd.Timedelta(hours=10 if market == "tw" else 5)
    if pd.Timestamp(now) < close + grace:
        return True, "仍在收盤後資料整理寬限期"
    good = cache.get("as_of") == expected and cache.get("quality", {}).get("passed") is True
    return good, f"{market} 預期行情日期 {expected}，現有 {cache.get('as_of', '未知')}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["tw", "us"], required=True)
    args = parser.parse_args()
    good, message = check(args.market, read_json(config.TW_CACHE_PATH if args.market == "tw" else config.US_CACHE_PATH, {}))
    print(message)
    raise SystemExit(0 if good else 1)
