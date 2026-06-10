/**
 * Meta-test: the recording canvas stub used by every animation
 * determinism test. Verifies it records method calls (in order, with
 * args) and property assignments, and exposes them via __ops.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';

test('recording ctx — records method calls with args, in order', () => {
  const c = makeRecordingCtx();
  c.beginPath();
  c.arc(10, 20, 5, 0, 6.283);
  c.fill();
  assert.deepStrictEqual(c.__ops, [
    { op: 'beginPath', args: [] },
    { op: 'arc', args: [10, 20, 5, 0, 6.283] },
    { op: 'fill', args: [] },
  ]);
});

test('recording ctx — records property sets', () => {
  const c = makeRecordingCtx();
  c.fillStyle = '#abc';
  c.lineWidth = 3;
  assert.deepStrictEqual(c.__ops, [
    { set: 'fillStyle', value: '#abc' },
    { set: 'lineWidth', value: 3 },
  ]);
});

test('recording ctx — two instances are independent', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  a.fill();
  assert.equal(a.__ops.length, 1);
  assert.equal(b.__ops.length, 0);
});
