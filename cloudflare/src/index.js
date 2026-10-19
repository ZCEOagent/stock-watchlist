// Cloudflare Worker: on-time workflow_dispatch + external dead-man switch.
//
// Bindings (see wrangler.toml / README.md):
//   KV:      RADAR_KV
//   secrets: GH_PAT (fine-grained, this repo only, Actions: read & write),
//            HEARTBEAT_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
//   vars:    GH_REPO, GH_REF, ACTIVE_FROM (ISO time), TW_CLOSED_DATES (comma list)
//
// KV layout keeps one writer per key to avoid read-modify-write races between
// the cron handler and heartbeat requests:
//   w:<day>:<slot>        worker-owned dispatch/alert state (cron only)
//   hb:<day>:<slot>       heartbeat summary (heartbeat handler only)
//   closed:<day>          TW session closed, reported by a job
import { SLOTS, STATUSES, attribute, plan } from './schedule.js';

const TTL = 14 * 86400;

async function getJSON(kv, key) {
  const raw = await kv.get(key);
  return raw ? JSON.parse(raw) : null;
}

function authorized(request, env) {
  const header = request.headers.get('Authorization') || '';
  return env.HEARTBEAT_TOKEN && header === 'Bearer ' + env.HEARTBEAT_TOKEN;
}

async function telegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: '台股雷達｜排程監控（外部）\n' + text }),
  });
  return res.ok;
}

async function dispatch(env, action) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${action.workflow}/dispatches`, {
      method: 'POST',
      headers: {
        Authorization: 'Bearer ' + env.GH_PAT, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'stock-watchlist-dispatcher',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ ref: env.GH_REF || 'master', inputs: { ...action.inputs, dispatch_id: action.id } }),
    });
  return { ok: res.status >= 200 && res.status < 300, status: res.status };
}

async function handleHeartbeat(request, env) {
  if (!authorized(request, env)) return new Response('unauthorized', { status: 401 });
  let beat;
  try { beat = await request.json(); } catch { return new Response('bad json', { status: 400 }); }
  const now = Date.now();
  const target = attribute(beat, now);
  if (!target) return new Response(JSON.stringify({ accepted: false }), { status: 202 });
  const key = `hb:${target.day}:${target.slot}`;
  const summary = (await getJSON(env.RADAR_KV, key)) || { statuses: {} };
  summary.statuses[beat.status] = summary.statuses[beat.status] || now;
  const keep = ['status', 'at', 'run_id', 'run_url', 'dispatch_id', 'trigger', 'valid_ratio',
                'iterations', 'quote_errors', 'degraded_streak', 'detail', 'late_start'];
  summary.last = Object.fromEntries(keep.filter((k) => beat[k] !== undefined).map((k) => [k, beat[k]]));
  summary.last.received_at = new Date(now).toISOString();
  summary.last.via_dispatch = target.viaDispatch;
  await env.RADAR_KV.put(key, JSON.stringify(summary), { expirationTtl: TTL });
  if (beat.status === 'closed' && SLOTS.find((s) => s.name === target.slot).market === 'tw') {
    await env.RADAR_KV.put(`closed:${target.day}`, '1', { expirationTtl: TTL });
  }
  return new Response(JSON.stringify({ accepted: true, day: target.day, slot: target.slot }), {
    headers: { 'content-type': 'application/json' } });
}

async function loadStore(env, nowMs) {
  const days = [new Date(nowMs + 8 * 3600000).toISOString().slice(0, 10),
                new Date(nowMs + 8 * 3600000 - 86400000).toISOString().slice(0, 10)];
  const states = {}, beats = {}, closed = new Set();
  for (const day of days) {
    if (await env.RADAR_KV.get(`closed:${day}`)) closed.add(day);
    for (const slot of SLOTS) {
      states[`${day}:${slot.name}`] = await getJSON(env.RADAR_KV, `w:${day}:${slot.name}`);
      beats[`${day}:${slot.name}`] = await getJSON(env.RADAR_KV, `hb:${day}:${slot.name}`);
    }
  }
  return { closedDays: closed, state: (d, s) => states[`${d}:${s}`], beats: (d, s) => beats[`${d}:${s}`] };
}

export async function tick(env, nowMs = Date.now()) {
  const store = await loadStore(env, nowMs);
  const config = { closedDates: env.TW_CLOSED_DATES || '',
                   activeFromMs: env.ACTIVE_FROM ? Date.parse(env.ACTIVE_FROM) : 0 };
  const { actions, states } = plan(nowMs, store, config);
  const log = [];
  for (const action of actions) {
    const key = `${action.day}:${action.slot}`;
    const state = states[key];
    if (action.type === 'dispatch') {
      let result;
      try { result = await dispatch(env, action); } catch (e) { result = { ok: false, status: 'network' }; }
      state.dispatches.push({ id: action.id, at: new Date(nowMs).toISOString(), ok: result.ok,
                              status: result.status, reason: action.reason });
      log.push({ ...action, result });
    } else {
      let sent = false;
      try { sent = await telegram(env, action.text); } catch (e) { sent = false; }
      if (!sent) delete state.alerts[action.kind];   // retry the alert on the next tick
      log.push({ type: 'alert', kind: action.kind, slot: action.slot, sent });
    }
  }
  for (const [key, state] of Object.entries(states)) {
    await env.RADAR_KV.put(`w:${key}`, JSON.stringify(state), { expirationTtl: TTL });
  }
  return log;
}

async function status(env, url) {
  const day = url.searchParams.get('day') || new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const out = { day, closed: Boolean(await env.RADAR_KV.get(`closed:${day}`)), slots: {} };
  for (const slot of SLOTS) {
    out.slots[slot.name] = { worker: await getJSON(env.RADAR_KV, `w:${day}:${slot.name}`),
                             heartbeats: await getJSON(env.RADAR_KV, `hb:${day}:${slot.name}`) };
  }
  return new Response(JSON.stringify(out, null, 2), { headers: { 'content-type': 'application/json' } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'POST' && url.pathname === '/heartbeat') return handleHeartbeat(request, env);
    if (!authorized(request, env)) return new Response('not found', { status: 404 });
    if (request.method === 'GET' && url.pathname === '/status') return status(env, url);
    if (request.method === 'POST' && url.pathname === '/tick') {
      return new Response(JSON.stringify(await tick(env), null, 2), { headers: { 'content-type': 'application/json' } });
    }
    return new Response('not found', { status: 404 });
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(tick(env, event.scheduledTime || Date.now()));
  },
};

export { STATUSES };
