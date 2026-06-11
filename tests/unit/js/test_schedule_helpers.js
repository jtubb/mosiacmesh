import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';
import {
  groupPlacementsByGroup, groupPlacementsByDay, formatRecurrence,
  sparklineSegments, isNowPlacement,
} from '../../../js/timeline/schedule/util.js';

test('store exposes isMobile (default false) and setIsMobile', () => {
  const s = makeStore();
  assert.equal(s.isMobile, false);
  s.setIsMobile(true);
  assert.equal(s.isMobile, true);
  s.setIsMobile(false);
  assert.equal(s.isMobile, false);
});

const P = (startMs, endMs, displayID, scheduleId, playlistName = 'PL') =>
  ({ startMs, endMs, displayID, scheduleId, playlistName, priority: 0 });

test('groupPlacementsByGroup buckets by displayID, sorted by startMs', () => {
  const out = groupPlacementsByGroup([
    P(30, 40, 'B', 's3'), P(10, 20, 'A', 's1'), P(5, 8, 'A', 's2'),
  ]);
  assert.deepEqual(Object.keys(out).sort(), ['A', 'B']);
  assert.deepEqual(out.A.map(p => p.scheduleId), ['s2', 's1']); // 5<10
  assert.deepEqual(out.B.map(p => p.scheduleId), ['s3']);
});

test('groupPlacementsByDay buckets by UTC day iso, only for listed days', () => {
  const d1 = Date.UTC(2026, 5, 1, 9);   // 2026-06-01 09:00Z
  const d2 = Date.UTC(2026, 5, 2, 13);  // 2026-06-02 13:00Z
  const out = groupPlacementsByDay(
    [P(d1, d1 + 3600e3, 'A', 's1'), P(d2, d2 + 3600e3, 'A', 's2')],
    ['2026-06-01', '2026-06-02', '2026-06-03']);
  assert.deepEqual(out['2026-06-01'].map(p => p.scheduleId), ['s1']);
  assert.deepEqual(out['2026-06-02'].map(p => p.scheduleId), ['s2']);
  assert.deepEqual(out['2026-06-03'], []);
});

test('formatRecurrence covers freq/interval/byweekday/once', () => {
  assert.equal(formatRecurrence({ freq: 'DAILY', interval: 1 }), 'Daily');
  assert.equal(formatRecurrence({ freq: 'DAILY', interval: 3 }), 'Every 3 days');
  assert.equal(formatRecurrence({ freq: 'WEEKLY', byweekday: [0,1,2,3,4] }), 'Mon–Fri');
  assert.equal(formatRecurrence({ freq: 'WEEKLY', byweekday: [5,6] }), 'Sat, Sun');
  assert.equal(formatRecurrence({ freq: 'WEEKLY', byweekday: [], interval: 2 }), 'Every 2 weeks');
  assert.equal(formatRecurrence({ freq: 'MONTHLY', interval: 1 }), 'Monthly');
  assert.equal(formatRecurrence({ freq: 'YEARLY', interval: 1 }), 'Yearly');
  assert.equal(formatRecurrence({ freq: 'DAILY', interval: 1, end: { type: 'count', count: 1 } }), 'Once');
});

test('sparklineSegments derives kind + fraction from items', () => {
  const pl = { items: [
    { file: 'a.jpg', duration: 10 },
    { file: 'b.mp4', duration: 30 },
    { file: 'lissajous', playmode: 'SCRIPT', duration: 10 },
  ] };
  const segs = sparklineSegments(pl);
  assert.deepEqual(segs.map(s => s.kind), ['image', 'video', 'animation']);
  // total 50 -> 0.2 / 0.6 / 0.2
  assert.ok(Math.abs(segs[1].frac - 0.6) < 1e-9);
});

test('sparklineSegments falls back to equal slices when no durations', () => {
  const segs = sparklineSegments({ items: [{ file: 'a.jpg' }, { file: 'b.jpg' }] });
  assert.ok(Math.abs(segs[0].frac - 0.5) < 1e-9);
});

test('sparklineSegments treats Auto (no duration) items as 20s, not zero', () => {
  // image=10s explicit, video=Auto(20s) -> total 30 -> 1/3, 2/3 (Auto NOT zero)
  const segs = sparklineSegments({ items: [
    { file: 'a.jpg', duration: 10 },
    { file: 'b.mp4' },
  ] });
  assert.ok(Math.abs(segs[0].frac - (1 / 3)) < 1e-9, `image frac ${segs[0].frac}`);
  assert.ok(segs[1].frac > 0, 'Auto video segment must not be zero-width');
  assert.ok(Math.abs(segs[1].frac - (2 / 3)) < 1e-9, `video frac ${segs[1].frac}`);
});

test('isNowPlacement is half-open [start, end)', () => {
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 10), true);
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 19), true);
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 20), false);
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 9), false);
});

import { agendaRowHtml } from '../../../js/timeline/schedule/agenda-row.js';

const playlist = { name: 'Lunch', items: [{ file: 'a.mp4', duration: 30 }, { file: 'b.jpg', duration: 10 }] };
const placement = {
  startMs: Date.UTC(2026, 5, 1, 12), endMs: Date.UTC(2026, 5, 1, 13),
  playlistName: 'Lunch', displayID: 'Lobby', priority: 0, scheduleId: 'sched-1',
};

test('agendaRowHtml renders time, name, data-schedule-id and a sparkline', () => {
  const html = agendaRowHtml(placement, playlist, { isNow: false, conflict: false, recurrenceText: 'Daily' });
  assert.match(html, /12:00–13:00/);
  assert.match(html, /Lunch/);
  assert.match(html, /data-schedule-id="sched-1"/);
  assert.match(html, /mm-agenda-spark/);
  assert.match(html, /Daily/);
});

test('agendaRowHtml flags now and conflict', () => {
  const now = agendaRowHtml(placement, playlist, { isNow: true, conflict: false, recurrenceText: '' });
  assert.match(now, /mm-agenda-now/);
  const conflict = agendaRowHtml(placement, playlist, { isNow: false, conflict: true, recurrenceText: '' });
  assert.match(conflict, /mm-agenda-conflict/);
});

test('agendaRowHtml escapes the playlist name', () => {
  const html = agendaRowHtml({ ...placement, playlistName: '<x>' }, { name: '<x>', items: [] },
    { isNow: false, conflict: false, recurrenceText: '' });
  assert.doesNotMatch(html, /<x>/);
  assert.match(html, /&lt;x&gt;/);
});
