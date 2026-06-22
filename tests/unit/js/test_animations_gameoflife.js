/**
 * gameOfLife (incremental FIELD animation): the board cache is unchanged, but
 * each frame now writes live=green / dead=black (or the dim "warming up" noise
 * tint) into a 48x36 RGBA buffer that the framework blits crisply. gen 0 is
 * seeded on the first frame, so tMs=0 renders the live board deterministically +
 * seeded (the cross-screen sync guarantee). A far-ahead gen on a fresh seed isn't
 * computed yet in a single call, so it renders the seeded coordinated noise grid.
 * Tests exercise shade() directly (pure, no DOM); each uses a DISTINCT seed so the
 * shared closure cache resets deterministically regardless of call order.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const gol = globalThis.MM_ANIMATIONS.find((a) => a.key === 'gameOfLife');
const GW = 48, GH = 36;

function shadeBuf(tMs, seed) {
  const data = new Uint8ClampedArray(GW * GH * 4);
  gol.shade(data, GW, GH, tMs, 0, seed);
  return data;
}
function countColor(data, r, g, b) {
  let n = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] === r && data[i + 1] === g && data[i + 2] === b) n++;
  }
  return n;
}
const LIVE = [124, 252, 0];     // #7CFC00
const NOISE = [58, 90, 58];     // #3a5a3a

test('gameOfLife — declares a 48x36 field grid, crisp (smooth:false)', () => {
  assert.deepStrictEqual(gol.grid, { cols: GW, rows: GH });
  assert.equal(gol.smooth, false);
  assert.equal(typeof gol.shade, 'function');
  assert.equal(typeof gol.draw, 'function');   // framework auto-wraps shade()
});

test('gameOfLife — gen 0 deterministic at same (tMs=0, seed)', () => {
  assert.deepStrictEqual(shadeBuf(0, 42), shadeBuf(0, 42));
});

test('gameOfLife — gen 0 seeded (different seed differs)', () => {
  assert.notDeepStrictEqual(shadeBuf(0, 101), shadeBuf(0, 202));
});

test('gameOfLife — gen 0 renders live (green) + dead (black) covering every cell', () => {
  const data = shadeBuf(0, 303);
  const green = countColor(data, LIVE[0], LIVE[1], LIVE[2]);
  const black = countColor(data, 0, 0, 0);
  assert.ok(green > 0 && green < GW * GH, `unexpected live count ${green}`);
  assert.equal(green + black, GW * GH, 'every cell must be live-green or dead-black');
});

test('gameOfLife — far-ahead gen on a fresh seed renders the seeded noise tint', () => {
  const tFar = 250 * 100;   // G=300, 100ms/gen; gen 250 >> STEP_PER_FRAME computed
  assert.deepStrictEqual(shadeBuf(tFar, 404), shadeBuf(tFar, 404), 'noise must be deterministic');
  const data = shadeBuf(tFar, 405);   // fresh seed -> far gen uncomputed -> noise
  assert.ok(countColor(data, NOISE[0], NOISE[1], NOISE[2]) > 0, 'expected warming-up noise tint');
});

test('gameOfLife — board evolves across generations (gen 0 vs gen 5)', () => {
  // seed 808 warm cache: first call seeds gen 0 + evolves STEP_PER_FRAME gens, so
  // gen 5 is computed and differs from gen 0 (guards a silent no-op evolve loop).
  assert.notDeepStrictEqual(shadeBuf(0, 808), shadeBuf(500, 808));
});

test('gameOfLife — same seed: early gen renders board, far-ahead renders noise', () => {
  const board = shadeBuf(0, 707);
  const noise = shadeBuf(250 * 100, 707);
  assert.ok(countColor(board, LIVE[0], LIVE[1], LIVE[2]) > 0, 'gen 0 should have live green');
  assert.ok(countColor(noise, NOISE[0], NOISE[1], NOISE[2]) > 0, 'far gen should be noise tint');
});

test('gameOfLife — gen wraps at G*100ms back to gen 0', () => {
  assert.deepStrictEqual(shadeBuf(0, 909), shadeBuf(300 * 100, 909));
});
