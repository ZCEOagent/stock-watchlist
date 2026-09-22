"""Transparent swing plans. No price-limit filter and no invented catalyst."""
from collections import defaultdict
from datetime import date, datetime
from statistics import mean, median
from urllib.parse import urlparse
import config
from market_clock import completed_weeks, advance_session, sessions_after
from quality import clean_bars

LABELS = {"observe": "觀察", "unconfirmed": "待確認", "waiting": "等待觸發",
          "triggered": "收盤觸發｜待盤中確認", "invalid": "失效", "expired": "已過期",
          "review": "到期重評", "target": "目標已到｜重評"}


def market_context(universe, history):
    sectors, all_returns, above = defaultdict(list), [], []
    for item in universe:
        bars = history.get(item["stock_id"], [])
        if len(bars) < 21:
            continue
        ret = bars[-1]["close"] / bars[-21]["close"] - 1
        all_returns.append(ret)
        sectors[item.get("sector", "未分類")].append(ret)
        above.append(bars[-1]["close"] >= mean(r["close"] for r in bars[-20:]))
    return {"benchmark": median(all_returns) if all_returns else None,
            "breadth": mean(above) if above else None,
            "sectors": {key: {"return": median(values), "count": len(values)} for key, values in sectors.items()},
            "label": "有效樣本20日報酬中位數／站上20日線比例（非官方指數，初篩用）"}


def shortlist(universe, history, forced_ids=()):
    ranked = []
    for item in universe:
        bars = history.get(item["stock_id"], [])
        if len(bars) < 21:
            continue
        close = bars[-1]["close"]
        turnover = mean(r["close"] * r["volume"] for r in bars[-20:])
        ma20 = mean(r["close"] for r in bars[-20:])
        # Rank proximity, not today's absolute price change or number of MA crosses.
        ranked.append((item["stock_id"] in forced_ids,
                       turnover >= config.RADAR_MIN_AVG_TURNOVER,
                       -abs(close / ma20 - 1), item))
    ranked.sort(key=lambda v: v[:3], reverse=True)
    selected = [r[3] for r in ranked[:config.RADAR_CANDIDATE_LIMIT]]
    selected_ids = {r["stock_id"] for r in selected}
    selected.extend(r[3] for r in ranked if r[0] and r[3]["stock_id"] not in selected_ids)
    return selected


def net_rr(entry, stop, target):
    fee, tax, slip = config.RADAR_FEE_RATE, config.RADAR_SELL_TAX_RATE, config.RADAR_SLIPPAGE_RATE
    cost = entry * (1 + slip) * (1 + fee)
    proceeds = lambda p: p * (1 - slip) * (1 - fee - tax)
    risk, reward = cost - proceeds(stop), proceeds(target) - cost
    return reward / risk if risk > 0 and reward > 0 else 0.0


def verified_catalyst(evidence, as_of):
    """Only explicitly reviewed, dated source evidence qualifies as a catalyst.

    RSS titles and FinMind period dates do not imply an earnings/news surprise.
    """
    if not evidence or evidence.get("reviewed") is not True:
        return False
    try:
        observed = date.fromisoformat(evidence["observed_on"])
        published = datetime.fromisoformat(evidence["published_at"])
        expiry = date.fromisoformat(evidence["valid_until"])
        today = date.fromisoformat(as_of)
        source = urlparse(evidence["source_url"])
        return bool(published.tzinfo and published.date() <= observed <= today <= expiry
                    and source.scheme == "https" and source.netloc
                    and evidence.get("thesis") and evidence.get("impact")
                    and evidence.get("priced_in_risk") and evidence.get("cancel_if"))
    except (KeyError, ValueError, TypeError):
        return False


def technical_plan(rows, as_of):
    bars, errors = clean_bars(rows, as_of, ohlc=True)
    if errors or len(bars) < config.RADAR_MIN_HISTORY or bars[-1]["date"] != as_of:
        return None, "還原日K不足或過期"
    expected = sessions_after(bars[-61]["date"], as_of)
    if [b["date"] for b in bars[-60:]] != expected:
        return None, "近60個交易日有缺漏／停牌，待確認"
    weeks = completed_weeks(bars, as_of)
    if len(weeks) < 11:
        return None, "已完成週K不足"
    close = bars[-1]["close"]
    ma20 = mean(r["close"] for r in bars[-20:])
    week_ma = mean(w["close"] for w in weeks[-10:])
    week_prev_ma = mean(w["close"] for w in weeks[-11:-1])
    if not (close >= ma20 and weeks[-1]["close"] >= week_ma >= week_prev_ma):
        return None, "日週K趨勢尚未配合"
    tr = [max(bars[i]["high"] - bars[i]["low"], abs(bars[i]["high"] - bars[i-1]["close"]),
              abs(bars[i]["low"] - bars[i-1]["close"])) for i in range(len(bars)-14, len(bars))]
    atr = mean(tr)
    if atr <= 0:
        return None, "波動資料不足"
    resistance = max(b["high"] for b in bars[-21:-1])
    if abs(close - resistance) <= atr:
        kind, trigger = "breakout", resistance
        stop = min(b["low"] for b in bars[-11:-1]) - 0.2 * atr
    elif bars[-1]["low"] <= ma20 + 0.5 * atr and close > bars[-1]["open"]:
        kind, trigger = "pullback", bars[-1]["high"]
        stop = min(b["low"] for b in bars[-6:]) - 0.2 * atr
    else:
        return None, "尚未接近突破或回測觸發區"
    # Independent historical resistance: never manufacture a 2R target.
    pivots = [bars[i]["high"] for i in range(2, len(bars)-21)
              if bars[i]["high"] >= max(b["high"] for b in bars[i-2:i+3])
              and bars[i]["high"] > trigger + atr]
    if not pivots:
        return None, "缺乏可驗證上方目標，待人工情境評估"
    target = min(pivots)
    upper = trigger + 0.5 * atr
    # Solve the cost-adjusted maximum entry analytically.
    f, tax, s, ratio = config.RADAR_FEE_RATE, config.RADAR_SELL_TAX_RATE, config.RADAR_SLIPPAGE_RATE, config.RADAR_MIN_RR
    max_rr_entry = (target + ratio * stop) * (1-s) * (1-f-tax) / ((1+ratio)*(1+s)*(1+f))
    upper = min(upper, max_rr_entry)
    if stop <= 0 or trigger <= stop or upper < trigger or trigger - stop < 0.5 * atr:
        return None, "現有支撐壓力無法提供合格風險報酬"
    turnover = mean(b["close"] * b["volume"] for b in bars[-20:])
    if turnover < config.RADAR_MIN_AVG_TURNOVER:
        return None, "成交金額不足"
    avg_volume = mean(b["volume"] for b in bars[-21:-1])
    return {"kind": kind, "entry_low": round(trigger, 4), "entry_high": round(upper, 4),
            "stop": round(stop, 4), "target": round(target, 4), "target_basis": "120日以上歷史轉折高點",
            "atr": round(atr, 4), "rr": round(net_rr(max(close, trigger), stop, target), 3),
            "close": close, "volume_ratio": bars[-1]["volume"] / avg_volume,
            "return20": close / bars[-21]["close"] - 1,
            "holding_sessions": list(config.RADAR_HOLD_SESSIONS),
            "max_holding_sessions": config.RADAR_MAX_HOLD_SESSIONS}, None


def evaluate(item, rows, context, facts, catalyst, as_of, previous=None):
    sid = item["stock_id"]
    output = {"id": sid, "name": item["stock_name"], "sector": item.get("sector", ""),
              "as_of": as_of, "status": "unconfirmed", "reasons": [], "facts": facts,
              "catalyst": catalyst if verified_catalyst(catalyst, as_of) else None}
    plan, problem = technical_plan(rows, as_of)
    if problem:
        output["reasons"].append(problem)
    sector = context["sectors"].get(item.get("sector", ""), {})
    gates = {
        "market": context.get("breadth") is not None and context["breadth"] >= 0.45,
        "sector": sector.get("count", 0) >= 5 and context.get("benchmark") is not None
                  and sector.get("return", -1) >= context["benchmark"],
        "fundamental": facts.get("fundamental_ok", False),
        "catalyst": verified_catalyst(catalyst, as_of),
        "technical": plan is not None,
        "chips": facts.get("chips_ok", False),
    }
    if plan:
        gates["relative_strength"] = plan["return20"] >= sector.get("return", float("inf"))
    output["gates"] = gates
    names = {"market": "市場廣度", "sector": "族群強度或樣本數", "fundamental": "基本面",
             "catalyst": "有來源與時間的催化審核", "technical": "日週K／風險報酬", "chips": "最新籌碼",
             "relative_strength": "個股相對族群強度"}
    output["reasons"].extend(names[k] + "未通過／待確認" for k, passed in gates.items() if not passed)
    # Freeze an existing plan through its validity window; no moving goalposts.
    if previous and previous.get("plan") and previous["status"] in ("waiting", "triggered", "review", "unconfirmed"):
        output["plan"] = previous["plan"].copy()
        output["created_on"] = previous["created_on"]
        output["expires_on"] = previous["expires_on"]
        if previous.get("triggered_on"):
            output["triggered_on"] = previous["triggered_on"]
    elif plan and previous and previous.get("plan") and previous["status"] in ("expired", "invalid", "target") and abs(plan["entry_low"] - previous["plan"]["entry_low"]) < 0.2 * plan["atr"]:
        output.update(status=previous["status"], plan=previous["plan"],
                      created_on=previous["created_on"], expires_on=previous["expires_on"])
        output["reasons"].append("原型態已結束，等待新結構，不能每天延長舊買點")
        return output
    elif plan and all(gates.values()):
        output.update(plan=plan, created_on=as_of, expires_on=advance_session(as_of, config.RADAR_VALID_SESSIONS))
    else:
        output["status"] = "unconfirmed" if not gates["catalyst"] or not gates["fundamental"] or not gates["chips"] else "observe"
        output["candidate_plan"] = plan
        return output
    active = output["plan"]
    latest = rows[-1]
    # Inspect every intervening bar if the scheduler skipped days.
    since = previous.get("as_of", output["created_on"]) if previous else output["created_on"]
    intervening = [r for r in rows if since < r["date"] <= as_of]
    if any(r["low"] <= active["stop"] for r in intervening) or latest["close"] <= active["stop"]:
        output["status"] = "invalid"
        output["reasons"].append("失效價已觸及，原計畫取消")
    elif latest["close"] >= active["target"]:
        output["status"] = "target"
    elif not output.get("triggered_on") and as_of > output["expires_on"]:
        output["status"] = "expired"
    elif not all(gates[k] for k in ("market", "sector", "fundamental", "catalyst", "chips")):
        output["status"] = "unconfirmed"
    elif output.get("triggered_on"):
        elapsed = len(sessions_after(output["triggered_on"], as_of))
        extended = bool(catalyst and catalyst.get("extend_to_8_weeks") is True and gates["technical"])
        ceiling = config.RADAR_MAX_HOLD_SESSIONS if extended else config.RADAR_HOLD_SESSIONS[1]
        if elapsed >= ceiling:
            output["status"] = "expired"
            output["reasons"].append("交易計畫期限已到，不能自動延長持有")
        else:
            output["status"] = "review" if elapsed >= config.RADAR_REVIEW_SESSIONS else "triggered"
    elif (active["entry_low"] < latest["close"] <= active["entry_high"]
          and net_rr(latest["close"], active["stop"], active["target"]) >= config.RADAR_MIN_RR
          and latest["volume"] >= mean(r["volume"] for r in rows[-21:-1])):
        output["status"] = "triggered"
        output["triggered_on"] = as_of
    else:
        output["status"] = "waiting"
        if latest["close"] > active["entry_high"]:
            output["reasons"].append("價格已超過可接受進場區，不追價")
    output["current_rr"] = round(net_rr(latest["close"], active["stop"], active["target"]), 3)
    return output
