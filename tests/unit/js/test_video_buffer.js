import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/video-buffer.js');
const nextPlaylistIndex = globalThis.nextPlaylistIndex;

test('nextPlaylistIndex — middle advances by one', () => {
  assert.strictEqual(nextPlaylistIndex(0, 3, false), 1);
  assert.strictEqual(nextPlaylistIndex(1, 3, false), 2);
});
test('nextPlaylistIndex — last item, no loop => -1 (nothing to warm)', () => {
  assert.strictEqual(nextPlaylistIndex(2, 3, false), -1);
});
test('nextPlaylistIndex — last item, loop => wraps to 0', () => {
  assert.strictEqual(nextPlaylistIndex(2, 3, true), 0);
});
test('nextPlaylistIndex — single item, loop => -1 (no swap needed)', () => {
  assert.strictEqual(nextPlaylistIndex(0, 1, true), -1);
});
test('nextPlaylistIndex — empty / bad input => -1', () => {
  assert.strictEqual(nextPlaylistIndex(0, 0, true), -1);
  assert.strictEqual(nextPlaylistIndex(-1, 3, true), -1);
});

const makeVideoBuffer = globalThis.makeVideoBuffer;
function mkMock() {
  return { _src: null, loaded: 0, played: 0, style: {},
    get src() { return this._src; }, set src(v) { this._src = v; },
    load: function () { this.loaded++; }, play: function () { this.played++; return null; },
    pause: function () {} };
}
function makeDeps() {
  var made = [];
  return { made: made,
    mkVideo: function () { var m = mkMock(); made.push(m); return m; },
    mount: function () {}, isVideo: function (f) { return /\.mp4$/i.test(f); } };
}

test('setup(false) => one element, active is it, no buffer', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(false);
  assert.strictEqual(d.made.length, 1);
  assert.strictEqual(vb.active(), d.made[0]);
});
test('setup(true) => two elements', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  assert.strictEqual(d.made.length, 2);
});
test('warmNext no-op when not warmable', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(false);
  vb.warmNext({ file: 'b.mp4' });
  assert.strictEqual(d.made.length, 1);        // no buffer created/loaded
});
test('warmNext loads next on buffer, idempotent, skips non-video', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  var active = vb.active(), buffer = d.made[1];
  vb.warmNext({ file: 'b.mp4' });
  assert.strictEqual(buffer.src, 'b.mp4'); assert.strictEqual(buffer.loaded, 1);
  vb.warmNext({ file: 'b.mp4' });               // idempotent
  assert.strictEqual(buffer.loaded, 1);
  vb.warmNext({ file: 'anim.script' });         // non-video => skip
  assert.strictEqual(buffer.src, 'b.mp4');
  assert.strictEqual(active.loaded, 0);         // active untouched
});
test('flipTo warm => swaps active to the warm buffer; returns it', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  var first = vb.active(), buf = d.made[1];
  vb.warmNext({ file: 'b.mp4' });
  var el = vb.flipTo('b.mp4');
  assert.strictEqual(el, buf);                  // returned the warm element
  assert.strictEqual(vb.active(), buf);         // active is now the warm one
  assert.notStrictEqual(vb.active(), first);
});
test('flipTo cold (buffer not warm / not warmable) => null', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  assert.strictEqual(vb.flipTo('never-warmed.mp4'), null);
  var d2 = makeDeps(); var vb2 = makeVideoBuffer(d2); vb2.setup(false);
  assert.strictEqual(vb2.flipTo('b.mp4'), null); // not warmable
});

/* ---- destroy(): required by the latch breaker -----------------------------
 * Recovering a latched element means rebuilding it. The buffer keeps BOTH
 * elements in a closure and only exposes active(), so without destroy() the
 * hidden buffer element would be orphaned in the DOM on every recreate.
 */
function harness(warm) {
  const made = [], mounted = [], unmounted = [];
  const mk = () => {
    const el = { style: {}, paused: false, _src: 'x',
                 pause() { this.paused = true; },
                 removeAttribute(a) { if (a === 'src') this._src = null; },
                 load() { this.loaded = (this.loaded || 0) + 1; } };
    made.push(el); return el;
  };
  const buf = makeVideoBuffer({
    mkVideo: mk,
    mount: (el) => mounted.push(el),
    unmount: (el) => unmounted.push(el),
    isVideo: () => true,
  });
  buf.setup(warm);
  return { buf, made, mounted, unmounted };
}

test('destroy — unmounts BOTH elements, not just the active one', () => {
  const h = harness(true);
  assert.strictEqual(h.made.length, 2, 'warmable makes two elements');
  h.buf.destroy();
  assert.strictEqual(h.unmounted.length, 2, 'the hidden buffer element leaked');
});

test('destroy — single-element (non-warmable) case', () => {
  const h = harness(false);
  h.buf.destroy();
  assert.strictEqual(h.unmounted.length, 1);
});

test('destroy — pauses and releases the source so the decoder is freed', () => {
  const h = harness(true);
  h.buf.destroy();
  h.made.forEach((el) => {
    assert.ok(el.paused, 'element left playing');
    assert.strictEqual(el._src, null, 'src not released');
  });
});

test('destroy — active() is null afterwards, so a stale element cannot be reused', () => {
  const h = harness(true);
  h.buf.destroy();
  assert.strictEqual(h.buf.active(), null);
});

test('destroy — is safe to call twice', () => {
  const h = harness(true);
  h.buf.destroy();
  h.buf.destroy();
  assert.strictEqual(h.unmounted.length, 2, 'double destroy re-unmounted');
});

test('setup after destroy rebuilds fresh elements', () => {
  const h = harness(true);
  h.buf.destroy();
  h.buf.setup(true);
  assert.strictEqual(h.made.length, 4, 'rebuild did not create new elements');
  assert.ok(h.buf.active(), 'no active element after rebuild');
});
