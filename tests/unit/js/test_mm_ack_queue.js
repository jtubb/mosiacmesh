// Node --test suite for js/mmAckQueue.js.
//
//   python pytest_runner.py --js     (or: node --test tests/unit/js/test_mm_ack_queue.js)
//
// WHY THIS EXISTS (measured on-wall 2026-09-26):
// index.html wired the cache ack as
//
//     mmCache.onAck = function (req, payload) {
//       if (sock && sock.readyState === SockJS.OPEN) { sock.send(...); }
//     };
//
// with no else, no queue and no retry, so an ack raised while the transport was
// momentarily not OPEN was dropped silently and forever.
//
// It failed ASYMMETRICALLY, which is why it hid for months. A download ack fires
// minutes after page load, when the socket is certainly open, so it lands. A HIT
// ack (segment already on disk) fires milliseconds after the grant arrives, so it
// is the one that vanishes -- exactly matching sign1screen23, which logged
//   [mmcache] HIT token=seg_4298b787efa0_0 bytes=66665122 (already on disk)
// while /api/discovery/devices still reported propagationPercent 0.0 and
// cachedSegments without that token.
//
// The cost is not one stale field. A terminal ack is what calls
// PrecacheWindow.advance(), and reconcile_group_cache() returns early while
// `display_id in precache_windows` -- so one dropped ack freezes the WHOLE
// group's cache self-heal until that grant's size-aware deadline expires (up to
// PRECACHE_ACK_MAX = 1800s). Losing the ack converts a completed pull into a
// half-hour stall for every other screen in the group.

import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
await import('../../../js/mmAckQueue.js');
const mmAckQueue = globalThis.mmAckQueue;

// A transport whose open-ness and failures the test controls.
function rig(opts) {
    opts = opts || {};
    const sent = [];
    const state = { open: opts.open !== false, throwOnSend: false };
    const q = mmAckQueue(
        function (req, payload) {
            if (state.throwOnSend) { throw new Error('socket closed under us'); }
            sent.push([req, payload && payload.token]);
        },
        function () { return state.open; },
        opts.cap
    );
    return { q: q, sent: sent, state: state };
}

test('an ack sent while open goes straight out and is not retained', function () {
    const r = rig();
    r.q.push('CACHED', { token: 'seg_a_0' });
    assert.deepStrictEqual(r.sent, [['CACHED', 'seg_a_0']]);
    assert.strictEqual(r.q.length(), 0, 'nothing should stay queued once delivered');
});

test('THE BUG: an ack raised while closed is retained, not dropped', function () {
    const r = rig({ open: false });
    r.q.push('CACHED', { token: 'seg_a_0' });
    assert.deepStrictEqual(r.sent, [], 'cannot send while closed');
    assert.strictEqual(r.q.length(), 1, 'the ack must survive to be retried');
});

test('a retained ack is delivered on the next flush after the transport opens', function () {
    const r = rig({ open: false });
    r.q.push('CACHED', { token: 'seg_a_0' });
    r.q.flush();                              // still closed: no-op, still queued
    assert.strictEqual(r.q.length(), 1);
    r.state.open = true;
    r.q.flush();
    assert.deepStrictEqual(r.sent, [['CACHED', 'seg_a_0']]);
    assert.strictEqual(r.q.length(), 0);
});

test('order is preserved: CACHED then CACHE_FAILED for one token must not invert', function () {
    // The server treats these as set add / set discard on cachedSegments, so a
    // replay in the wrong order would leave the record claiming a segment the
    // device does not hold -- and _per_client_items would route playback at a
    // localhost file that is not there.
    const r = rig({ open: false });
    r.q.push('CACHED', { token: 'seg_a_0' });
    r.q.push('CACHE_FAILED', { token: 'seg_a_0' });
    r.state.open = true;
    r.q.flush();
    assert.deepStrictEqual(r.sent, [['CACHED', 'seg_a_0'], ['CACHE_FAILED', 'seg_a_0']]);
});

test('a send that throws leaves the ack queued and stops the drain', function () {
    // readyState can report OPEN and the send still fail if the socket dies
    // between the two. Shifting before a confirmed send would lose the ack for
    // exactly the reason this module exists.
    const r = rig({ open: false });
    r.q.push('CACHED', { token: 'seg_a_0' });
    r.q.push('CACHED', { token: 'seg_a_1' });
    r.state.open = true;
    r.state.throwOnSend = true;
    r.q.flush();
    assert.deepStrictEqual(r.sent, []);
    assert.strictEqual(r.q.length(), 2, 'both must still be queued after a throw');
    r.state.throwOnSend = false;
    r.q.flush();
    assert.deepStrictEqual(r.sent, [['CACHED', 'seg_a_0'], ['CACHED', 'seg_a_1']]);
    assert.strictEqual(r.q.length(), 0);
});

test('flush while closed is a harmless no-op', function () {
    const r = rig({ open: false });
    r.q.flush();
    r.q.flush();
    assert.strictEqual(r.q.length(), 0);
    assert.deepStrictEqual(r.sent, []);
});

test('the queue is bounded and keeps the NEWEST acks when it overflows', function () {
    // An unbounded queue on a device that has been offline for hours is a memory
    // leak on a 247MB iPad-1 where the webclip is already jetsam-adjacent. When
    // something must go it has to be the OLDEST: the newest ack is the current
    // truth about what the device holds.
    const r = rig({ open: false, cap: 3 });
    r.q.push('CACHED', { token: 't0' });
    r.q.push('CACHED', { token: 't1' });
    r.q.push('CACHED', { token: 't2' });
    r.q.push('CACHED', { token: 't3' });
    assert.strictEqual(r.q.length(), 3, 'cap must hold');
    r.state.open = true;
    r.q.flush();
    assert.deepStrictEqual(r.sent, [['CACHED', 't1'], ['CACHED', 't2'], ['CACHED', 't3']],
        'the oldest ack is the one dropped');
});

test('CACHE_PROGRESS is never queued -- it is liveness, not a fact', function () {
    // A progress report replayed later would refresh a grant deadline using
    // evidence that has since expired, which is the opposite of what the stall
    // timeout is for. Dropping a progress report costs nothing: another follows
    // every MM_PROGRESS_EVERY bytes.
    const r = rig({ open: false });
    r.q.push('CACHE_PROGRESS', { token: 'seg_a_0', bytes: 2097152 });
    assert.strictEqual(r.q.length(), 0, 'progress must not be retained');
    r.state.open = true;
    r.q.flush();
    assert.deepStrictEqual(r.sent, [], 'and must not be replayed');
});

test('CACHE_PROGRESS while open is sent immediately', function () {
    const r = rig();
    r.q.push('CACHE_PROGRESS', { token: 'seg_a_0', bytes: 2097152 });
    assert.deepStrictEqual(r.sent, [['CACHE_PROGRESS', 'seg_a_0']]);
    assert.strictEqual(r.q.length(), 0);
});

test('a flush mid-drain that closes the transport keeps the remainder', function () {
    const r = rig({ open: false, cap: 8 });
    r.q.push('CACHED', { token: 't0' });
    r.q.push('CACHED', { token: 't1' });
    r.state.open = true;
    // close the transport as soon as the first one goes out
    const realSent = r.sent;
    const q2 = mmAckQueue(
        function (req, payload) { realSent.push([req, payload.token]); r.state.open = false; },
        function () { return r.state.open; }, 8);
    q2.push('CACHED', { token: 'x0' });
    q2.push('CACHED', { token: 'x1' });
    assert.deepStrictEqual(realSent, [['CACHED', 'x0']]);
    assert.strictEqual(q2.length(), 1, 'x1 must remain queued after the transport closed');
});

test('ES5 only: the module source uses no let/const/arrow/template/class', function () {
    // The display client runs on 1st-gen iPad / Safari 5.1. Anything newer is a
    // parse error that takes the whole page down, not a degraded feature.
    const src = fs.readFileSync(
        new URL('../../../js/mmAckQueue.js', import.meta.url), 'utf8');
    const stripped = src
        .replace(/\/\*[\s\S]*?\*\//g, ' ')       // block comments
        .replace(/(^|[^:])\/\/.*$/gm, '$1 ');    // line comments
    assert.ok(!/\blet\s/.test(stripped), 'no let');
    assert.ok(!/\bconst\s/.test(stripped), 'no const');
    assert.ok(!/=>/.test(stripped), 'no arrow functions');
    assert.ok(!/`/.test(stripped), 'no template literals');
    assert.ok(!/\bclass\s+\w/.test(stripped), 'no class');
    assert.ok(!/\bPromise\b/.test(stripped), 'no Promise');
});
