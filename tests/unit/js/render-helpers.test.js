// tests/unit/js/render-helpers.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import { isReadyFromEntry, renderBadge, playlistGroupSummary } from '../../../js/timeline/util/render-helpers.js';

test('isReadyFromEntry: missing entry is not ready', () => {
  assert.equal(isReadyFromEntry(undefined), false);
});

test('isReadyFromEntry: READY is ready', () => {
  assert.equal(isReadyFromEntry({ state: 'READY' }), true);
});

test('renderBadge maps states to labels', () => {
  assert.equal(renderBadge({ state: 'RENDERING', percent: 40 }), 'rendering… 40%');
  assert.equal(renderBadge({ state: 'QUEUED' }), 'queued');
  assert.equal(renderBadge({ state: 'FAILED' }), 'render failed');
  assert.equal(renderBadge({ state: 'READY' }), 'ready');
  assert.equal(renderBadge(undefined), 'not rendered');
});

test('playlistGroupSummary counts states', () => {
  const groups = [{ displayID: 'A' }, { displayID: 'B' }];
  const renders = { A: { P: { state: 'READY' } }, B: { P: { state: 'FAILED' } } };
  const s = playlistGroupSummary('P', groups, renders, true);
  assert.equal(s.total, 2); assert.equal(s.ready, 1);
  assert.deepEqual(s.failed, ['B']);
});

test('playlistGroupSummary N/A short-circuits', () => {
  assert.deepEqual(playlistGroupSummary('P', [{ displayID: 'A' }], {}, false).total, 0);
});

test('playlistGroupSummary needsCalibration scopes denominator to calibrated groups', () => {
  const groups = [
    { displayID: 'A', calibratedCount: 24 },  // calibrated → eligible
    { displayID: 'B', calibratedCount: 0 },   // no calibrated screens → excluded
    { displayID: 'C', calibratedCount: 0 },   // excluded
  ];
  const renders = { A: { P: { state: 'READY' } } };
  // Mesh/per-screen playlist: only the calibrated group counts.
  const s = playlistGroupSummary('P', groups, renders, true, true);
  assert.equal(s.total, 1);   // not 3 — B and C can't render mesh
  assert.equal(s.ready, 1);
});

test('playlistGroupSummary mirror (no calibration) counts every group', () => {
  const groups = [{ displayID: 'A', calibratedCount: 0 }, { displayID: 'B', calibratedCount: 0 }];
  const s = playlistGroupSummary('P', groups, {}, true, false);
  assert.equal(s.total, 2);   // FULL/mirror renders anywhere
});

test('playlistGroupSummary falls back to all groups when calibratedCount absent', () => {
  // Older /api/displays without calibratedCount → no filtering (degrade gracefully).
  const groups = [{ displayID: 'A' }, { displayID: 'B' }];
  const s = playlistGroupSummary('P', groups, {}, true, true);
  assert.equal(s.total, 2);
});
