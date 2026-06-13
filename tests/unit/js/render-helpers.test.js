// tests/unit/js/render-helpers.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import { isReadyFromEntry, renderBadge } from '../../../js/timeline/util/render-helpers.js';

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
