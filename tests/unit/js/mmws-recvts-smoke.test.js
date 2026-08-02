import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../../../tweak/mmws/mmws.js', import.meta.url), 'utf8');
test('mmws __mmwsDispatch takes recvTs and sets window.__mmwsRecvTs before onmessage', () => {
  assert.match(src, /__mmwsDispatch\s*=\s*function\s*\([^)]*recvTs/);
  const msgIdx = src.indexOf("=== 'message'");
  const setIdx = src.indexOf('__mmwsRecvTs');
  const onmsgIdx = src.indexOf('ws.onmessage', msgIdx);
  assert.ok(setIdx > -1 && setIdx < onmsgIdx, '__mmwsRecvTs set before ws.onmessage');
});
