"""Build public radar output and durable, idempotent decision snapshots."""
import hashlib
import json
import config
from storage import read_json, write_json
from radar import market_context, shortlist, evaluate
from radar_data import EvidenceClient, adjusted_histories, summarize_evidence


def build_radar(universe, history, as_of, quality, persist=True):
    previous = read_json(config.RADAR_STATE_PATH, {})
    reviewed = read_json(config.RADAR_EVIDENCE_PATH, {})
    forced = read_json(config.RADAR_WATCHLIST_PATH, [])
    if not isinstance(forced, list) or any(not isinstance(s, str) or not s.isdigit() or len(s) != 4 for s in forced):
        raise ValueError("watchlist.json 必須是四碼代號字串陣列")
    active_ids = [sid for sid, value in previous.items() if value.get("plan") and value.get("status") in ("waiting", "triggered", "review", "unconfirmed")]
    context = market_context(universe, history)
    selected = shortlist(universe, history, set(forced + active_ids))
    result = {"as_of": as_of, "mode": config.RADAR_MODE, "context": context,
              "items": [], "coverage": {"market_valid": len(history), "deep_review": len(selected)},
              "notice": "收盤研究／影子驗證；尚無即時報價確認，不代表實際持倉或已成交。"}
    if not quality["passed"]:
        result["notice"] = "資料覆蓋不足，本次不產生或推進波段訊號；保留前次有效資料。"
        return result
    adjusted, failures = adjusted_histories(selected, history, as_of)
    client = EvidenceClient(as_of)
    updates, events = {}, read_json(config.RADAR_EVENTS_PATH, [])
    for item in selected:
        sid = item["stock_id"]
        prior = previous.get(sid)
        if sid not in adjusted:
            value = {"id": sid, "name": item["stock_name"], "as_of": as_of,
                     "status": "unconfirmed", "reasons": [failures.get(sid, "還原資料不足")], "facts": {}}
        else:
            try:
                facts = summarize_evidence(client.get(sid), as_of)
            except (ValueError, TypeError, KeyError, OverflowError):
                facts = {"fundamental_ok": False, "chips_ok": False, "errors": ["基本面／籌碼來源格式異常"]}
            value = evaluate(item, adjusted[sid], context, facts, reviewed.get(sid), as_of, prior)
            if prior and prior.get("plan") and prior.get("observed_close"):
                old_bar = next((b for b in adjusted[sid] if b["date"] == prior["as_of"]), None)
                if old_bar and abs(old_bar["close"] / prior["observed_close"] - 1) > 0.005:
                    value["status"] = "review"
                    value["reasons"].append("歷史價格基準變動（可能除權息／分割），原價位需重建")
            value["observed_close"] = adjusted[sid][-1]["close"]
        # Unknown data must not erase a frozen plan or restart its expiry clock.
        if prior and prior.get("plan") and not value.get("plan"):
            for key in ("plan", "created_on", "expires_on", "triggered_on"):
                if key in prior:
                    value[key] = prior[key]
        updates[sid] = value
        result["items"].append(value)
        event = {"id": sid, "as_of": as_of, "status": value["status"],
                 "previous_status": prior.get("status") if prior else None,
                 "snapshot": value}
        event["event_id"] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        if event["event_id"] not in {e["event_id"] for e in events}:
            events.append(event)
    selected_ids = {i["stock_id"] for i in selected}
    lookup = {i["stock_id"]: i for i in universe}
    for sid in set(forced + active_ids) - selected_ids:
        result["items"].append({"id": sid, "name": lookup.get(sid, {}).get("stock_name", sid),
                                "status": "unconfirmed", "as_of": as_of,
                                "reasons": ["指定／原追蹤股票行情缺漏或過期，保留原計畫待確認"]})
    if persist:
        write_json(config.RADAR_STATE_PATH, {**previous, **updates})
        # Forward snapshots are not fabricated historical backtests.
        write_json(config.RADAR_EVENTS_PATH, events)
    return result
