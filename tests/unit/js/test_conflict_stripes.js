/**
 * Math + DOM-output tests for the shared conflict-stripes helper.
 * Day and Week variants share the underlying fraction math; the two
 * exported renderers differ only in which CSS axis they project onto.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const modUrl = pathToFileURL(path.join(here, '../../../js/timeline/timeline/conflict-stripes.js')).href;
const { renderDayStripesHtml, renderWeekStripesHtml } = await import(modUrl);

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const DAY = Date.UTC(2026, 5, 6);   // 2026-06-06

test('renderDayStripesHtml: empty ranges -> empty string', () => {
  assert.equal(renderDayStripesHtml([], DAY, 9, 17), '');
});

test('renderDayStripesHtml: single overlap in the middle', () => {
  // Clip 09:00-17:00 (8h). Conflict at 12:00-14:00 → left 3/8=37.5%, width 2/8=25%.
  const ranges = [{ overlapStartMs: DAY + 12 * HOUR_MS, overlapEndMs: DAY + 14 * HOUR_MS }];
  const html = renderDayStripesHtml(ranges, DAY, 9, 17);
  assert.match(html, /left:37.5%/);
  assert.match(html, /width:25%/);
  assert.match(html, /class="mm-clip-stripe"/);
  // Default (horizontal) variant must NOT carry the vertical class
  assert.ok(!/mm-clip-stripe-vertical/.test(html));
});

test('renderWeekStripesHtml: single overlap in the middle', () => {
  // Same fractions as above, projected onto Y-axis.
  const ranges = [{ overlapStartMs: DAY + 12 * HOUR_MS, overlapEndMs: DAY + 14 * HOUR_MS }];
  const html = renderWeekStripesHtml(ranges, DAY, 9, 17);
  assert.match(html, /top:37.5%/);
  assert.match(html, /height:25%/);
  assert.match(html, /mm-clip-stripe-vertical/);
});

test('renderWeekStripesHtml: multiple ranges produce multiple stripes', () => {
  const ranges = [
    { overlapStartMs: DAY + 9 * HOUR_MS, overlapEndMs: DAY + 10 * HOUR_MS },
    { overlapStartMs: DAY + 15 * HOUR_MS, overlapEndMs: DAY + 17 * HOUR_MS },
  ];
  const html = renderWeekStripesHtml(ranges, DAY, 9, 17);
  const matches = html.match(/mm-clip-stripe-vertical/g) || [];
  assert.equal(matches.length, 2);
  // First range: 0/8 → 1/8 → top:0% height:12.5%
  assert.match(html, /top:0%; height:12.5%/);
  // Second range: 6/8 → 8/8 → top:75% height:25%
  assert.match(html, /top:75%; height:25%/);
});

test('zero-duration clip -> no stripes (avoids divide-by-zero)', () => {
  const ranges = [{ overlapStartMs: DAY + 10 * HOUR_MS, overlapEndMs: DAY + 11 * HOUR_MS }];
  assert.equal(renderDayStripesHtml(ranges, DAY, 10, 10), '');
  assert.equal(renderWeekStripesHtml(ranges, DAY, 10, 10), '');
});
