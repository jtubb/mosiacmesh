import { test, describe } from 'node:test';
import assert from 'node:assert';
import { pxToHour, hourToHHMM, snapTo15min, isoDateAddHour } from '../../../js/timeline/util/snap.js';

describe('pxToHour', () => {
  test('0px -> 0h, full width -> 24h', () => {
    assert.equal(pxToHour(0, 240), 0);
    assert.equal(pxToHour(240, 240), 24);
    assert.equal(pxToHour(120, 240), 12);
  });
  test('clips to [0, 24]', () => {
    assert.equal(pxToHour(-10, 240), 0);
    assert.equal(pxToHour(500, 240), 24);
  });
});

describe('snapTo15min', () => {
  test('rounds to nearest 0.25', () => {
    assert.equal(snapTo15min(9.0),  9.0);
    assert.equal(snapTo15min(9.07), 9.0);
    assert.equal(snapTo15min(9.13), 9.25);
    assert.equal(snapTo15min(9.4),  9.5);
    assert.equal(snapTo15min(9.7),  9.75);
    assert.equal(snapTo15min(9.9), 10.0);
  });
});

describe('hourToHHMM', () => {
  test('integer hours and quarter hours', () => {
    assert.equal(hourToHHMM(0),    '00:00');
    assert.equal(hourToHHMM(9.25), '09:15');
    assert.equal(hourToHHMM(13.5), '13:30');
    assert.equal(hourToHHMM(13.75),'13:45');
    assert.equal(hourToHHMM(24),   '23:59');  // clamp end of day
  });
});

describe('isoDateAddHour', () => {
  test('returns same date for in-range hour', () => {
    assert.equal(isoDateAddHour('2026-06-05', 9), '2026-06-05');
  });
  test('rolls to next date when hour >= 24', () => {
    assert.equal(isoDateAddHour('2026-06-05', 25), '2026-06-06');
  });
});
