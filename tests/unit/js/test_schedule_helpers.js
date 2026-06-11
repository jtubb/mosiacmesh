import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('store exposes isMobile (default false) and setIsMobile', () => {
  const s = makeStore();
  assert.equal(s.isMobile, false);
  s.setIsMobile(true);
  assert.equal(s.isMobile, true);
  s.setIsMobile(false);
  assert.equal(s.isMobile, false);
});
