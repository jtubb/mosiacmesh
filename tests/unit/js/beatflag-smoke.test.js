import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';

test('beatflag.js sets window._mmClockBeat and defaults false', () => {
  const src = readFileSync(new URL('../../../js/beatflag.js', import.meta.url), 'utf8');
  assert.match(src, /window\._mmClockBeat\s*=\s*false/);
});
test('index.html loads beatflag.js before GoTime.js', () => {
  const html = readFileSync(new URL('../../../index.html', import.meta.url), 'utf8');
  const bf = html.indexOf('beatflag.js');
  const gt = html.indexOf('GoTime.js');
  assert.ok(bf !== -1 && gt !== -1 && bf < gt, 'beatflag.js must load before GoTime.js');
});
