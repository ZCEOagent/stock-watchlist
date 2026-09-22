"""Conservative long-only, next-session-open replay of frozen plans.

Use point-in-time decision snapshots only. This evaluates execution/outcomes,
not historical signal generation from today's revised fundamentals/universe.
Same-bar stop/target ambiguity is stop-first. No implied real holdings.
"""
import config
from quality import clean_bars
from market_clock import sessions_after


def replay(plan, signal_day, bars, horizons=(3, 5, 10, 20, 40)):
    ordered, errors = clean_bars(bars, max((r.get("date", "") for r in bars), default=signal_day), ohlc=True)
    if errors:
        return {"status": "invalid_data"}
    future = [r for r in ordered if r["date"] > signal_day]
    if not future:
        return {"status": "pending"}
    if [r['date'] for r in future] != sessions_after(signal_day, future[-1]['date']):
        return {"status": "invalid_data", "reason": "missing_trading_sessions"}
    entry = future[0]["open"]
    if not plan["entry_low"] <= entry <= plan["entry_high"]:
        return {"status": "not_filled", "reason": "next_open_outside_entry_range"}
    fee, tax, slip = config.RADAR_FEE_RATE, config.RADAR_SELL_TAX_RATE, config.RADAR_SLIPPAGE_RATE
    buy = entry * (1+slip) * (1+fee)
    net = lambda price: (price*(1-slip)*(1-fee-tax)/buy - 1) * 100
    worst, best = entry, entry
    result = {"status": "open", "entry": entry, "entry_date": future[0]["date"], "horizons": {},
              "excursion_measurement": "daily_bar_bounds_not_intraday_path"}
    for horizon in horizons:
        if len(future) >= horizon:
            bar = future[horizon - 1]
            result["horizons"][str(horizon)] = {"date": bar["date"], "net_return_pct": round(net(bar["close"]), 4),
                                               "meaning": "hypothetical_hold_not_realized_trade"}
    for index, bar in enumerate(future[:max(horizons)], start=1):
        worst, best = min(worst, bar["low"]), max(best, bar["high"])
        exit_price, reason = None, None
        if bar["open"] <= plan["stop"]:
            exit_price, reason = bar["open"], "gap_stop"
        elif bar["low"] <= plan["stop"]:
            exit_price, reason = plan["stop"], "stop_first"
        elif bar["high"] >= plan["target"]:
            exit_price, reason = plan["target"], "target"
        elif index >= plan.get("simulation_holding_sessions", config.RADAR_HOLD_SESSIONS[1]):
            exit_price, reason = bar["close"], "time_exit"
        if reason:
            result.update(status="closed", exit=exit_price, exit_reason=reason,
                          exit_date=bar["date"], sessions_held=index, net_return_pct=round(net(exit_price), 4))
            break
    if result['status'] == 'open':
        result.update(mark_date=future[-1]['date'], mark=future[-1]['close'],
                      sessions_held=len(future), net_return_pct=round(net(future[-1]['close']), 4))
    result.update(mae_pct=round((worst/entry-1)*100, 4), mfe_pct=round((best/entry-1)*100, 4))
    return result
