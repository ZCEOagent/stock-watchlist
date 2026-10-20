// Pure scheduling / dead-man logic. No I/O here so it can be tested with node:test.
//
// Why this exists: GitHub `schedule` for this repo measured a 3-5.5 h median
// delay and ~90% missing triggers (2026-09-30..10-19). workflow_dispatch is sent
// from here on time, and every slot must report heartbeats back; silence is an
// alert even when GitHub itself never ran anything.

export const TZ_OFFSET_MIN = 8 * 60; // Asia/Taipei, no DST

const WEEKDAYS = [1, 2, 3, 4, 5];

// startBy / finishBy are minutes after the dispatch time.
export const SLOTS = [
  // Weekly research runs on a Monday holiday too (as the original cron did).
  { name: 'radar-weekly', market: 'any', days: [1], at: '07:40', workflow: 'market-radar.yml',
    inputs: { mode: 'weekly', trigger: 'scheduler' }, startBy: 20, finishBy: 50, retryOnFailure: true },
  { name: 'intraday', market: 'tw', days: WEEKDAYS, at: '08:45', workflow: 'market-radar.yml',
    inputs: { mode: 'session', trigger: 'scheduler' }, startBy: 20, finishBy: 305, retryOnFailure: false,
    live: { from: '09:10', to: '13:30', maxSilenceMin: 12 } },
  // Not 14:17: on 2026-10-20 the on-time run failed because TPEx's official daily batch
  // first appeared between 14:52 and 14:54. 15:15 leaves margin; a failure is retried
  // once after 15 minutes instead of 5.
  { name: 'tw-close', market: 'tw', days: WEEKDAYS, at: '15:15', workflow: 'daily-tw.yml',
    inputs: { slot: 'tw-close' }, startBy: 20, finishBy: 150, retryOnFailure: true, retryAfterMin: 15 },
  { name: 'radar-daily', market: 'any', days: [0, 1, 2, 3, 4, 5, 6], at: '18:20', workflow: 'market-radar.yml',
    inputs: { mode: 'daily', trigger: 'scheduler' }, startBy: 20, finishBy: 50, retryOnFailure: true },
  { name: 'tw-evening', market: 'tw', days: WEEKDAYS, at: '21:17', workflow: 'daily-tw.yml',
    inputs: { slot: 'tw-evening' }, startBy: 20, finishBy: 150, retryOnFailure: true },
  // Not 06:37: every US failure (6/6, 2026-10-01..17) started 00:41-01:30 UTC and every
  // success 01:48-03:09 UTC; Yahoo's latest US daily bar is unavailable in that window.
  // 11:30 Taipei = 03:30 UTC: 2 h after the window observed under EDT, ~1 h if it
  // shifts one hour later with EST from 2026-11-01.
  { name: 'us-close', market: 'us', days: [2, 3, 4, 5, 6], at: '11:30', workflow: 'daily-us.yml',
    inputs: { slot: 'us-close' }, startBy: 20, finishBy: 90, retryOnFailure: true },
];

export const STATUSES = ['started', 'alive', 'completed', 'failed', 'closed', 'skipped', 'degraded'];
const TERMINAL = ['completed', 'failed', 'closed', 'skipped'];
const MAX_DISPATCHES = 2;

export function minutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

export function taipei(ms) {
  const local = new Date(ms + TZ_OFFSET_MIN * 60000);
  return {
    day: local.toISOString().slice(0, 10),
    weekday: local.getUTCDay(),
    minute: local.getUTCHours() * 60 + local.getUTCMinutes(),
  };
}

// Epoch ms of a Taipei wall-clock time on a Taipei calendar day.
export function at(day, minute) {
  return Date.parse(day + 'T00:00:00Z') + (minute - TZ_OFFSET_MIN) * 60000;
}

export function dispatchId(day, slot, attempt) {
  return `${day}:${slot}:${attempt}`;
}

export function parseDispatchId(id) {
  const m = /^(\d{4}-\d{2}-\d{2}):([a-z-]+):(\d+)$/.exec(id || '');
  return m ? { day: m[1], slot: m[2], attempt: Number(m[3]) } : null;
}

// Decide which (day, slot) a heartbeat belongs to. A heartbeat carrying our
// dispatch id is authoritative. Without one (a GitHub backup cron run) it only
// counts for the same Taipei day and only if it arrived after that day's
// dispatch time, so a 5-hour-late backup can never satisfy the next day's slot.
export function attribute(beat, receivedMs) {
  const slot = SLOTS.find((s) => s.name === beat.slot);
  if (!slot || !STATUSES.includes(beat.status)) return null;
  const parsed = parseDispatchId(beat.dispatch_id);
  if (parsed) {
    if (parsed.slot !== slot.name) return null;
    return { day: parsed.day, slot: slot.name, viaDispatch: true };
  }
  const local = taipei(receivedMs);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(beat.day || '') || beat.day !== local.day) return null;
  if (receivedMs < at(local.day, minutes(slot.at))) return null;
  return { day: local.day, slot: slot.name, viaDispatch: false };
}

function due(slot, local, closedDays, closedConfig) {
  if (!slot.days.includes(local.weekday)) return false;
  if (slot.market === 'tw' && (closedDays.has(local.day) || closedConfig.has(local.day))) return false;
  return true;
}

function once(state, kind) {
  state.alerts = state.alerts || {};
  return !state.alerts[kind];
}

// state: worker-owned per-slot record {dispatches:[{id, at, ok}], alerts:{kind: ms}}
// beats: heartbeat summary {last: {status, at, ...}, statuses: {status: ms}}
// Returns { actions, state } with the new worker-owned state.
export function decide(slot, day, nowMs, state, beats) {
  state = JSON.parse(JSON.stringify(state || { dispatches: [], alerts: {} }));
  beats = beats || { statuses: {} };
  const actions = [];
  const alert = (kind, text) => {
    if (!once(state, kind)) return;
    state.alerts[kind] = nowMs;
    actions.push({ type: 'alert', slot: slot.name, day, kind, text });
  };
  const t0 = at(day, minutes(slot.at));
  const startBy = t0 + slot.startBy * 60000;
  const finishBy = t0 + slot.finishBy * 60000;
  const ok = state.dispatches.filter((d) => d.ok);
  const has = (s) => Boolean(beats.statuses && beats.statuses[s]);
  const terminal = TERMINAL.find(has);
  const started = has('started') || has('alive') || has('degraded') || Boolean(terminal);
  const dispatch = (reason) => {
    const attempt = ok.length + 1;
    actions.push({ type: 'dispatch', slot: slot.name, day, workflow: slot.workflow, attempt, reason,
                   id: dispatchId(day, slot.name, attempt), inputs: slot.inputs });
  };

  if (nowMs < t0) return { actions, state };

  if (has('closed')) return { actions, state };

  if (!ok.length && !started) {
    if (nowMs < finishBy) {
      const failures = state.dispatches.length;
      if (failures) {
        const status = state.dispatches[failures - 1].status;
        const hint = { 401: 'GH_PAT 無效或已過期，請重新建立並上傳', 403: 'GH_PAT 缺少 Actions 讀寫權限',
                       404: 'GH_PAT 未授權此 repo，或 workflow 檔名錯誤', 422: 'workflow 輸入參數或分支不符' }[status];
        alert('dispatch_error', `${slot.name} 派送 workflow_dispatch 失敗（HTTP ${status}${hint ? '：' + hint : ''}），將每 5 分鐘重試（最多 6 次）。`);
      }
      if (failures < 6) dispatch('scheduled');
    } else {
      alert('missed', `${slot.name}（${day} ${slot.at}）整段時窗都沒有派送或執行紀錄。`);
    }
    return { actions, state };
  }

  if (has('skipped')) {
    alert('late_trigger', `${slot.name} 在時段結束後才開始執行，已記錄為延遲觸發，未用舊行情補做。`);
  }

  if (has('failed') && !has('completed')) {
    alert('failed', `${slot.name} 回報失敗；詳情見 ${beats.last && beats.last.run_url || 'GitHub Actions'}。`);
    const failedAt = beats.statuses.failed || nowMs;
    const retryReady = nowMs >= failedAt + (slot.retryAfterMin || 0) * 60000;
    if (slot.retryOnFailure && ok.length < MAX_DISPATCHES && nowMs < finishBy && !state.retriedFailure && retryReady) {
      state.retriedFailure = true;
      dispatch('failed');
    } else if (state.retriedFailure && nowMs >= finishBy) {
      alert('failed_after_retry', `${slot.name} 重試後仍未完成，需人工檢查。`);
    }
    return { actions, state };
  }

  if (!started && nowMs >= startBy) {
    if (ok.length < MAX_DISPATCHES) {
      alert('not_started', `${slot.name} 派送後 ${slot.startBy} 分鐘仍未開始執行；已重新派送一次。`);
      dispatch('not_started');
    } else if (nowMs >= t0 + slot.startBy * 2 * 60000) {
      alert('not_started_after_retry', `${slot.name} 重新派送後仍未開始執行，GitHub Actions 可能排隊或停擺，需人工檢查。`);
    }
    return { actions, state };
  }

  if (started && !terminal && nowMs >= finishBy) {
    alert('not_finished', `${slot.name} 已開始但超過 ${slot.finishBy} 分鐘未回報完成。`);
  }

  if (slot.live && started && !terminal) {
    const from = at(day, minutes(slot.live.from));
    const to = at(day, minutes(slot.live.to));
    const lastMs = beats.last ? Date.parse(beats.last.received_at || beats.last.at) : 0;
    if (nowMs >= from && nowMs <= to && nowMs - lastMs > slot.live.maxSilenceMin * 60000) {
      const bucket = Math.floor(nowMs / (30 * 60000));
      alert(`silent:${bucket}`, `${slot.name} 盤中已 ${Math.round((nowMs - lastMs) / 60000)} 分鐘沒有心跳；盤中監控可能中斷，期間不會有進場提醒。`);
    }
  }

  const inLive = !slot.live || (nowMs >= at(day, minutes(slot.live.from)) && nowMs <= at(day, minutes(slot.live.to)));
  if (beats.last && beats.last.status === 'degraded' && !terminal && inLive) {
    const bucket = Math.floor(nowMs / (60 * 60000));
    const ratio = beats.last.valid_ratio;
    alert(`degraded:${bucket}`, `${slot.name} 連續多輪行情不足（有效快照比例 ${ratio ?? '未知'}）；失效行情不會產生提醒。`);
  }

  if (has('completed')) {
    const hadIssue = Object.keys(state.alerts).some((k) => !k.startsWith('recovered'));
    if (hadIssue) alert('recovered', `${slot.name} 已完成（先前有異常）。`);
  }
  return { actions, state };
}

export function plan(nowMs, store, config = {}) {
  // store: { closedDays:Set, state(day,slot), beats(day,slot) }
  const actions = [];
  const states = {};
  const local = taipei(nowMs);
  const closedConfig = new Set((config.closedDates || '').split(',').map((s) => s.trim()).filter(Boolean));
  // Look at today and yesterday so evening slots that run past midnight still finish their checks.
  const yesterday = taipei(nowMs - 86400000);
  for (const view of [yesterday, local]) {
    for (const slot of SLOTS) {
      if (!due(slot, view, store.closedDays, closedConfig)) continue;
      // Slots scheduled before the dispatcher went live are not "missed".
      if (config.activeFromMs && at(view.day, minutes(slot.at)) < config.activeFromMs) continue;
      const result = decide(slot, view.day, nowMs, store.state(view.day, slot.name), store.beats(view.day, slot.name));
      if (view === yesterday && result.actions.some((a) => a.type === 'dispatch')) {
        // Never dispatch yesterday's work today.
        result.actions = result.actions.filter((a) => a.type !== 'dispatch');
      }
      if (result.actions.length) {
        actions.push(...result.actions);
        states[`${view.day}:${slot.name}`] = result.state;
      }
    }
  }
  return { actions, states };
}
