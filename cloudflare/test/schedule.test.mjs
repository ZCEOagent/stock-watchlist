import test from 'node:test';
import assert from 'node:assert/strict';
import { SLOTS, at, taipei, attribute, decide, plan, minutes } from '../src/schedule.js';
import worker, { tick } from '../src/index.js';

const DAY = '2026-10-20'; // Tuesday
const slot = (name) => SLOTS.find((s) => s.name === name);
const tw = (hhmm, day = DAY) => at(day, minutes(hhmm));
const beats = (statuses, last = {}) => ({ statuses, last });

test('taipei clock conversion', () => {
  const t = taipei(Date.parse('2026-10-20T00:45:00Z'));
  assert.deepEqual(t, { day: DAY, weekday: 2, minute: 8 * 60 + 45 });
  assert.equal(tw('08:45'), Date.parse('2026-10-20T00:45:00Z'));
});

test('dispatches on time, not before', () => {
  assert.equal(decide(slot('intraday'), DAY, tw('08:44'), null, null).actions.length, 0);
  const { actions } = decide(slot('intraday'), DAY, tw('08:45'), null, null);
  assert.equal(actions.length, 1);
  assert.equal(actions[0].type, 'dispatch');
  assert.equal(actions[0].id, `${DAY}:intraday:1`);
  assert.deepEqual(actions[0].inputs, { mode: 'session', trigger: 'scheduler' });
});

test('no duplicate dispatch while waiting for start', () => {
  const state = { dispatches: [{ id: 'x', ok: true }], alerts: {} };
  assert.equal(decide(slot('intraday'), DAY, tw('08:55'), state, null).actions.length, 0);
});

test('not started by deadline: alert once and re-dispatch once', () => {
  let state = { dispatches: [{ id: 'a', ok: true }], alerts: {} };
  let r = decide(slot('intraday'), DAY, tw('09:05'), state, null);
  assert.deepEqual(r.actions.map((a) => a.type).sort(), ['alert', 'dispatch']);
  assert.equal(r.actions.find((a) => a.type === 'dispatch').attempt, 2);
  state = r.state; state.dispatches.push({ id: 'b', ok: true });
  r = decide(slot('intraday'), DAY, tw('09:10'), state, null);
  assert.equal(r.actions.length, 0);                        // no third dispatch, no repeat alert
  r = decide(slot('intraday'), DAY, tw('09:30'), r.state, null);
  assert.deepEqual(r.actions.map((a) => a.kind), ['not_started_after_retry']);
});

test('dispatch API failure retries every tick and alerts once', () => {
  let state = { dispatches: [{ id: 'a', ok: false, status: 401 }], alerts: {} };
  let r = decide(slot('tw-close'), DAY, tw('14:22'), state, null);
  assert.deepEqual(r.actions.map((a) => a.type).sort(), ['alert', 'dispatch']);
  state = r.state; state.dispatches.push({ id: 'b', ok: false });
  r = decide(slot('tw-close'), DAY, tw('14:27'), state, null);
  assert.deepEqual(r.actions.map((a) => a.type), ['dispatch']);
});

test('whole window missed is reported, not dispatched late', () => {
  const r = decide(slot('tw-close'), DAY, tw('17:00'), null, null);
  assert.deepEqual(r.actions.map((a) => a.kind), ['missed']);
});

test('intraday silence during session alerts once per 30 minutes', () => {
  const state = { dispatches: [{ id: 'a', ok: true }], alerts: {} };
  const b = beats({ started: tw('08:47') }, { status: 'alive', received_at: new Date(tw('10:00')).toISOString() });
  let r = decide(slot('intraday'), DAY, tw('10:10'), state, b);
  assert.equal(r.actions.length, 0);                        // 10 min < 12 min
  r = decide(slot('intraday'), DAY, tw('10:15'), state, b);
  assert.equal(r.actions.length, 1);
  assert.match(r.actions[0].kind, /^silent:/);
  const again = decide(slot('intraday'), DAY, tw('10:20'), r.state, b);
  assert.equal(again.actions.length, 0);
});

test('degraded heartbeat alerts; completion after alerts sends recovery', () => {
  const state = { dispatches: [{ id: 'a', ok: true }], alerts: {} };
  let r = decide(slot('intraday'), DAY, tw('10:15'), state,
    beats({ started: 1 }, { status: 'degraded', valid_ratio: 0.1, received_at: new Date(tw('10:14')).toISOString() }));
  assert.match(r.actions[0].kind, /^degraded:/);
  r = decide(slot('intraday'), DAY, tw('13:40'), r.state, beats({ started: 1, completed: 2 }, { status: 'completed' }));
  assert.deepEqual(r.actions.map((a) => a.kind), ['recovered']);
});

test('quiet when everything completes', () => {
  const state = { dispatches: [{ id: 'a', ok: true }], alerts: {} };
  const r = decide(slot('tw-close'), DAY, tw('16:50'), state, beats({ started: 1, completed: 2 }, { status: 'completed' }));
  assert.equal(r.actions.length, 0);
});

test('failure triggers one retry dispatch for retryable slots only', () => {
  const state = { dispatches: [{ id: 'a', ok: true }], alerts: {} };
  const failed = beats({ started: 1, failed: 2 }, { status: 'failed' });
  let r = decide(slot('tw-close'), DAY, tw('14:40'), state, failed);
  assert.deepEqual(r.actions.map((a) => a.type).sort(), ['alert', 'dispatch']);
  r = decide(slot('intraday'), DAY, tw('13:40'), state, failed);
  assert.deepEqual(r.actions.map((a) => a.type), ['alert']);
});

test('started but never finished is reported', () => {
  const state = { dispatches: [{ id: 'a', ok: true }], alerts: {} };
  const r = decide(slot('tw-close'), DAY, tw('16:50'), state, beats({ started: 1 }, { status: 'started' }));
  assert.deepEqual(r.actions.map((a) => a.kind), ['not_finished']);
});

test('heartbeat attribution: dispatch id authoritative; late backup cannot satisfy next day', () => {
  assert.deepEqual(attribute({ slot: 'tw-evening', status: 'started', dispatch_id: `${DAY}:tw-evening:1` }, tw('21:20')),
    { day: DAY, slot: 'tw-evening', viaDispatch: true });
  // Backup of 2026-10-19 21:17 arriving at 02:00 on 10-20, reporting day 10-20.
  assert.equal(attribute({ slot: 'tw-evening', status: 'started', day: DAY }, tw('02:00')), null);
  assert.deepEqual(attribute({ slot: 'tw-evening', status: 'started', day: DAY }, tw('21:30')),
    { day: DAY, slot: 'tw-evening', viaDispatch: false });
  assert.equal(attribute({ slot: 'unknown', status: 'started', day: DAY }, tw('21:30')), null);
  assert.equal(attribute({ slot: 'intraday', status: 'bogus', day: DAY }, tw('09:30')), null);
});

test('plan skips TW slots on closed days and weekends, honors activation time', () => {
  const empty = { closedDays: new Set(), state: () => null, beats: () => null };
  const sat = plan(at('2026-10-24', minutes('08:45')), empty).actions;
  assert.equal(sat.filter((a) => a.slot === 'intraday' && a.day === '2026-10-24').length, 0);
  const closed = { ...empty, closedDays: new Set([DAY]) };
  assert.equal(plan(tw('14:17'), closed).actions.filter((a) => a.slot === 'tw-close' && a.day === DAY).length, 0);
  assert.equal(plan(tw('14:17'), empty, { closedDates: DAY }).actions.filter((a) => a.slot === 'tw-close' && a.day === DAY).length, 0);
  const fresh = plan(tw('15:00'), empty, { activeFromMs: tw('14:55') }).actions;
  assert.equal(fresh.filter((a) => a.kind === 'missed').length, 0);  // deployed after those slots
  const noActivation = plan(tw('15:00'), empty).actions;
  assert.ok(noActivation.some((a) => a.kind === 'missed'));
});

test('plan never dispatches yesterday work', () => {
  const empty = { closedDays: new Set(), state: () => null, beats: () => null };
  const { actions } = plan(tw('00:10', '2026-10-21'), empty, { activeFromMs: tw('00:00') });
  assert.equal(actions.filter((a) => a.type === 'dispatch' && a.day === DAY).length, 0);
});

// ---- Worker I/O with fake KV and fetch ----
class FakeKV {
  constructor() { this.data = new Map(); }
  async get(k) { return this.data.has(k) ? this.data.get(k) : null; }
  async put(k, v) { this.data.set(k, v); }
}

function env(kv) {
  return { RADAR_KV: kv, GH_PAT: 'pat', GH_REPO: 'o/r', GH_REF: 'master', HEARTBEAT_TOKEN: 'hb',
           TELEGRAM_BOT_TOKEN: 't', TELEGRAM_CHAT_ID: '1', ACTIVE_FROM: '2026-10-20T00:00:00Z' };
}

test('tick dispatches through GitHub API and records state', async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, body: JSON.parse(init.body) }); return new Response(null, { status: 204 }); };
  const kv = new FakeKV();
  const log = await tick(env(kv), tw('08:46'));
  const d = calls.find((c) => c.url.includes('/dispatches'));
  assert.ok(d.url.endsWith('/repos/o/r/actions/workflows/market-radar.yml/dispatches'));
  assert.deepEqual(d.body, { ref: 'master', inputs: { mode: 'session', trigger: 'scheduler', dispatch_id: `${DAY}:intraday:1` } });
  assert.equal(JSON.parse(kv.data.get(`w:${DAY}:intraday`)).dispatches[0].ok, true);
  assert.ok(log.some((l) => l.type === 'dispatch'));
  const second = await tick(env(kv), tw('08:51'));
  assert.equal(second.filter((l) => l.type === 'dispatch').length, 0);
});

test('heartbeat endpoint requires token and stores attributed beats', async () => {
  const kv = new FakeKV();
  const req = (token, body) => new Request('https://w.example/heartbeat', {
    method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: JSON.stringify(body) });
  assert.equal((await worker.fetch(req('wrong', {}), env(kv))).status, 401);
  const res = await worker.fetch(req('hb', { slot: 'intraday', status: 'closed', dispatch_id: `${DAY}:intraday:1` }), env(kv));
  assert.equal((await res.json()).accepted, true);
  assert.ok(JSON.parse(kv.data.get(`hb:${DAY}:intraday`)).statuses.closed);
  assert.equal(kv.data.get(`closed:${DAY}`), '1');
});

test('failed Telegram delivery keeps the alert pending for the next tick', async () => {
  globalThis.fetch = async (url) => new Response(null, { status: url.includes('telegram') ? 500 : 204 });
  const kv = new FakeKV();
  await kv.put(`w:${DAY}:tw-close`, JSON.stringify({ dispatches: [{ id: 'a', ok: true }], alerts: {} }));
  await tick(env(kv), tw('14:40'));
  const state = JSON.parse(kv.data.get(`w:${DAY}:tw-close`));
  assert.equal(state.alerts.not_started, undefined);
});
