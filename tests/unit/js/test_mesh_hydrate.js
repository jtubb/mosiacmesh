/**
 * mmHydrateMeshCells re-attaches the de-duplicated meshCells grid (sent once at the
 * PLAY/PREPARE/PRELOAD payload level) onto each item, so the per-item render sites that
 * read it.meshCells are unchanged. Pure; no DOM.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/mesh-viewport.js');
const g = globalThis;

test('mmHydrateMeshCells: attaches meshCells to every item (shared ref)', () => {
  const cells = [[[0, 0], [1, 0], [1, 1], [0, 1]]];
  const items = [{ file: 'a' }, { file: 'b' }, { file: 'c' }];
  const out = g.mmHydrateMeshCells(items, cells);
  assert.strictEqual(out, items);
  for (let i = 0; i < items.length; i++) assert.strictEqual(items[i].meshCells, cells);
});

test('mmHydrateMeshCells: no-op when meshCells is null/undefined', () => {
  const items = [{ file: 'a' }];
  g.mmHydrateMeshCells(items, null);
  assert.strictEqual('meshCells' in items[0], false);
  g.mmHydrateMeshCells(items, undefined);
  assert.strictEqual('meshCells' in items[0], false);
});

test('mmHydrateMeshCells: tolerates falsy/empty items', () => {
  assert.doesNotThrow(() => g.mmHydrateMeshCells(undefined, [[[0, 0]]]));
  assert.doesNotThrow(() => g.mmHydrateMeshCells(null, [[[0, 0]]]));
  assert.deepStrictEqual(g.mmHydrateMeshCells([], [[[0, 0]]]), []);
});
