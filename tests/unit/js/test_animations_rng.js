/**
 * Coordinated-seed PRNG helpers. The sync guarantee: same seed -> identical
 * stream on every engine, so xorshift32 (bitwise-only) is mandatory — no
 * Math.imul (absent on Safari 5.1), no >2^53 multiply (engine-divergent).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
await import('../../../js/animations.js');
const { MM_RNG, mmDeriveSeed } = globalThis;

test('MM_RNG — same seed yields identical stream', () => {
  const a = MM_RNG(12345), b = MM_RNG(12345);
  const sa = [], sb = [];
  for (let i = 0; i < 20; i++) { sa.push(a()); sb.push(b()); }
  assert.deepStrictEqual(sa, sb);
});

test('MM_RNG — different seeds diverge', () => {
  const a = MM_RNG(1), b = MM_RNG(2);
  const sa = [], sb = [];
  for (let i = 0; i < 20; i++) { sa.push(a()); sb.push(b()); }
  assert.notDeepStrictEqual(sa, sb);
});

test('MM_RNG — values in [0,1), not constant, seed 0 is valid', () => {
  for (const seed of [0, 1, 0xFFFFFFFF, 42]) {
    const r = MM_RNG(seed);
    const vals = [];
    for (let i = 0; i < 50; i++) { const v = r(); assert.ok(v >= 0 && v < 1, `out of range: ${v}`); vals.push(v); }
    assert.ok(new Set(vals).size > 1, `seed ${seed} produced a constant stream`);
  }
});

test('mmDeriveSeed — deterministic + distinct per index', () => {
  assert.equal(mmDeriveSeed(777, 3), mmDeriveSeed(777, 3));
  const seen = new Set();
  for (let i = 0; i < 64; i++) seen.add(mmDeriveSeed(777, i));
  assert.equal(seen.size, 64, 'index collision in mmDeriveSeed');
});

test('portability guard — no Math.imul in js/animations.js', () => {
  const src = readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../js/animations.js'), 'utf8');
  assert.ok(!/Math\.imul/.test(src), 'MM_RNG must avoid Math.imul (absent on Safari 5.1)');
});
