import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/video-buffer.js');
const nextPlaylistIndex = globalThis.nextPlaylistIndex;

test('nextPlaylistIndex — middle advances by one', () => {
  assert.strictEqual(nextPlaylistIndex(0, 3, false), 1);
  assert.strictEqual(nextPlaylistIndex(1, 3, false), 2);
});
test('nextPlaylistIndex — last item, no loop => -1 (nothing to warm)', () => {
  assert.strictEqual(nextPlaylistIndex(2, 3, false), -1);
});
test('nextPlaylistIndex — last item, loop => wraps to 0', () => {
  assert.strictEqual(nextPlaylistIndex(2, 3, true), 0);
});
test('nextPlaylistIndex — single item, loop => -1 (no swap needed)', () => {
  assert.strictEqual(nextPlaylistIndex(0, 1, true), -1);
});
test('nextPlaylistIndex — empty / bad input => -1', () => {
  assert.strictEqual(nextPlaylistIndex(0, 0, true), -1);
  assert.strictEqual(nextPlaylistIndex(-1, 3, true), -1);
});
