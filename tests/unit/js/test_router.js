import { test } from 'node:test';
import assert from 'node:assert';
import { parseHash } from '../../../js/timeline/shell/router.js';

test('parseHash maps valid hashes', () => {
  assert.equal(parseHash('#schedule'), 'schedule');
  assert.equal(parseHash('content'), 'content');
  assert.equal(parseHash('#fleet'), 'fleet');
  assert.equal(parseHash('#now'), 'now');
});

test('parseHash falls back to now for unknown/empty', () => {
  assert.equal(parseHash(''), 'now');
  assert.equal(parseHash('#bogus'), 'now');
  assert.equal(parseHash(undefined), 'now');
});
