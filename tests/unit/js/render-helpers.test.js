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
