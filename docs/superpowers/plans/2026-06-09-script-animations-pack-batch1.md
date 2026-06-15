# SCRIPT Animations Pack — Batch 1 (Geometry Pack) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three deterministic, clock-synced full-screen animations (`lissajous`, `phyllotaxis`, `wireframeCube`) to the existing SCRIPT playback engine, plus the operator-facing infrastructure (animation catalog + a playlist-editor SCRIPT dropdown) that every later batch reuses.

**Architecture:** Each animation is a pure ES5 function `fn(ctx, tMs, w, h)` added to the `animations` registry in `index.html` (the iPad-1 display client). The same function is mirrored verbatim into `tests/unit/js/_animations_mirror.js` so Node `--test` can verify determinism (same `tMs` → identical draw-op log) without a browser. The admin console (`admin.html`, modern JS) gets `js/timeline/animations-catalog.js` — a hand-maintained list of `{key, label, description}` — which the playlist editor uses to render a `<select>` of animation names whenever an item's play mode is `SCRIPT`. No server changes: the SCRIPT slice already maps the per-item `playmode:'SCRIPT'` string to `PlayMode.SCRIPT` (`mosaicmesh/render.py:_build_media_elements`) and emits `playmode` in the PLAY payload (`_media_item_payload`).

**Tech Stack:** ES5 + Canvas2D (display client), ES modules + Alpine 3.x (admin), Node 20 `node --test` (determinism unit tests), Playwright via `tests/e2e/run.js` (browser smoke). No build step.

---

## Background the implementer needs

**The existing SCRIPT plumbing (already shipped — do not re-implement):**

- `index.html` defines `var animations = { bouncingBalls: function(ctx, tMs, w, h){...} };` at ~line 425. Each entry is a **pure function of `tMs`** (elapsed ms into the item) and canvas size. This is what makes the wall synchronized: same inputs → same frame on every screen.
- `runScriptLoop(canvas, name)` (~line 445) is the rAF loop. Each frame it computes the playlist position from the shared `GoTime` clock, calls `ctx.clearRect(...)` then `animations[name](ctx, pos.offsetMs, canvas.width, canvas.height)` **guarded by `if (animations[name])`** (unknown name → blank canvas, no crash).
- `showItem(i, offsetMs)` (~line 556) branches on `item.playmode === 'SCRIPT'`: builds a full-viewport `<canvas>`, sets `playback.scriptIndex = i`, calls `runScriptLoop(cnv, item.file)`. So **`item.file` is the animation registry key** (e.g. `'lissajous'`), not a media URL.
- ES5 RAF shims `_raf` / `_caf` already exist (~line 418).

**The data path (no server work in this batch):**

- The playlist editor saves through `store.updatePlaylist` → `PUT /api/playlists/{name}`, which stores `items` as **raw dicts verbatim** (`mosaicmesh/api/playlists.py:117` → `p.items = list(body["items"])`). So an item `{id, file:'lissajous', duration, playmode:'SCRIPT'}` round-trips unchanged.
- When that playlist is assigned to a display (Play-Now's `ASSIGN_PLAYLIST`, or a schedule), `mosaicmesh/render.py:_build_media_elements` maps the string `'SCRIPT'` → `PlayMode.SCRIPT` (line 590-593). Other per-item playmode values (`'loop'`, `'once'`, anything else) fall through to `PlayMode.FULL` — unchanged by this batch.
- `_media_item_payload` (render.py:524) emits `"playmode": me.playmode.name`, so the PLAY payload the iPad receives carries `playmode:'SCRIPT'`, which `showItem` keys off.

**Known pre-existing quirk (OUT OF SCOPE — do not fix):** the playlist editor's "Play mode" `<select>` currently offers only `loop` / `once`, and both map to `PlayMode.FULL` server-side. The per-item `playmode` field is overloaded — the playback engine treats it as the `PlayMode` enum name, while the editor treats it as a loop-behavior hint. This batch **adds a third option (`SCRIPT`)** to that select without touching the `loop`/`once` semantics. Reconciling the overload is a separate concern; do not refactor it here.

**ES5 rules for `index.html` and the mirror module (the mirror is written ES5-style so it transcribes verbatim):**
- `var` only — no `let`/`const`. `function` only — no arrow functions. No template literals (use `+`). No `Array.prototype.includes`/`Array.from`/`Object.assign`. Canvas2D primitives only (`fillStyle`/`strokeStyle`/`lineWidth`/`beginPath`/`moveTo`/`lineTo`/`arc`/`fill`/`stroke`/`fillRect`/`clearRect`). No `Path2D`.
- `admin.html` and `js/timeline/*.js` are modern JS — `const`/`let`/arrow/template literals are fine there.

**Branch:** This stacks on the current feature branch (`feature/pr27-clients-came-online-broadcast`) which has uncommitted PR-28 (calibrate JSON) + PR-29 (play-now) work. The controller should ensure those are committed (or this work branched cleanly from them) before starting Task 1. Do NOT start on `main`.

---

## File Structure

| File | Responsibility | Create/Modify |
|------|----------------|---------------|
| `tests/unit/js/_canvas_stub.js` | Recording Canvas2D context stub — records ordered draw ops + property sets for determinism assertions. Shared by all animation tests (this batch + future batches). | Create |
| `tests/unit/js/_animations_mirror.js` | Verbatim ES5 mirror of `index.html`'s `animations` registry, exported for Node tests. | Create |
| `tests/unit/js/test_animations_lissajous.js` | Determinism + animates + draws-something specs for `lissajous`. | Create |
| `tests/unit/js/test_animations_phyllotaxis.js` | Same for `phyllotaxis`. | Create |
| `tests/unit/js/test_animations_wireframe.js` | Same for `wireframeCube`. | Create |
| `tests/unit/js/test_animations_registry_sync.js` | Cheap guard: every mirror key also appears as a registry key in `index.html` (catches "forgot to copy into index.html"). | Create |
| `index.html` | Add 3 animation functions to the `animations` registry (~line 437). | Modify |
| `js/timeline/animations-catalog.js` | `ANIMATIONS = [{key, label, description}]` catalog mirror for the admin UI. | Create |
| `js/timeline/modals/playlist-editor.js` | Add `SCRIPT` play-mode option; swap the File text input for an animation `<select>` when `playmode === 'SCRIPT'` (free-text fallback on `?`). | Modify |
| `tests/unit/js/test_timeline_smoke.js` | Add `animations-catalog.js` to the module-load smoke list. | Modify |
| `tests/e2e/test-script-animations.spec.js` | Browser smoke: a SCRIPT item renders a non-blank canvas; STOP tears it down. | Create |

---

### Task 1: Recording canvas stub + animations mirror scaffold

**Files:**
- Create: `tests/unit/js/_canvas_stub.js`
- Create: `tests/unit/js/_animations_mirror.js`
- Test: `tests/unit/js/test_canvas_stub.js` (created here, verifies the stub itself)

- [ ] **Step 1: Write the failing test for the stub**

Create `tests/unit/js/test_canvas_stub.js`:

```js
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
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_canvas_stub.js`
Expected: FAIL — `Cannot find module './_canvas_stub.js'`.

- [ ] **Step 3: Implement the stub**

Create `tests/unit/js/_canvas_stub.js`:

```js
/**
 * A Canvas2D-shaped recording stub for animation determinism tests.
 *
 * Every method call is pushed to an ordered `__ops` log as
 * {op, args}; every property assignment (fillStyle, strokeStyle,
 * lineWidth, globalAlpha, font, ...) as {set, value}. Two runs of a
 * pure-function-of-tMs animation against fresh stubs produce
 * deep-equal logs iff the animation is deterministic — which is the
 * cross-screen synchronization guarantee in testable form.
 *
 * `createLinearGradient` returns a tiny recording gradient (later
 * batches use it); its addColorStop calls are logged too.
 */
export function makeRecordingCtx() {
  const ops = [];
  const target = { __ops: ops };
  return new Proxy(target, {
    get(t, prop) {
      if (prop === '__ops') return ops;
      if (prop in t && typeof t[prop] !== 'function') return t[prop];
      return function (...args) {
        ops.push({ op: String(prop), args });
        if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
          return {
            addColorStop(offset, color) {
              ops.push({ op: 'addColorStop', args: [offset, color] });
            },
          };
        }
        return undefined;
      };
    },
    set(t, prop, value) {
      ops.push({ set: String(prop), value });
      t[prop] = value;
      return true;
    },
  });
}
```

- [ ] **Step 4: Run the stub test to confirm it passes**

Run: `node --test tests/unit/js/test_canvas_stub.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Create the empty mirror module**

Create `tests/unit/js/_animations_mirror.js`:

```js
/**
 * MIRROR of the `animations` registry in index.html.
 *
 * index.html is ES5 (must run on a 1st-gen iPad / Safari 5.1), so
 * these functions are written ES5-style (var / function, no arrows,
 * no template literals) — they are COPY-PASTE IDENTICAL to the
 * entries in index.html's `var animations = {...}`. The Node
 * determinism tests import from here; the real index.html copy is
 * covered by the Playwright smoke (renders non-blank) and the
 * registry-sync test (key presence).
 *
 * When you add/change an animation: edit it HERE and paste the exact
 * same function body into index.html (or vice-versa). Keep them in
 * lockstep.
 */
export const mirror = {};
```

- [ ] **Step 6: Commit**

```bash
git add tests/unit/js/_canvas_stub.js tests/unit/js/test_canvas_stub.js tests/unit/js/_animations_mirror.js
git commit -m "test(animations): recording canvas stub + mirror scaffold (batch 1)"
```

---

### Task 2: `lissajous` animation

**Files:**
- Modify: `tests/unit/js/_animations_mirror.js`
- Modify: `index.html` (animations registry, ~line 437)
- Test: `tests/unit/js/test_animations_lissajous.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_lissajous.js`:

```js
/**
 * lissajous: a morphing parametric curve. The three guarantees:
 *   1. determinism — same tMs ⇒ identical draw-op log (sync property)
 *   2. animates    — different tMs ⇒ different log (not a static frame)
 *   3. draws       — non-empty log (it actually renders)
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { mirror } from './_animations_mirror.js';
import { makeRecordingCtx } from './_canvas_stub.js';

const W = 1024, H = 768;

test('lissajous — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.lissajous(a, 12345, W, H);
  mirror.lissajous(b, 12345, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('lissajous — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.lissajous(a, 1000, W, H);
  mirror.lissajous(b, 9000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('lissajous — draws something', () => {
  const c = makeRecordingCtx();
  mirror.lissajous(c, 5000, W, H);
  assert.ok(c.__ops.length > 0, 'expected a non-empty draw-op log');
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_lissajous.js`
Expected: FAIL — `mirror.lissajous is not a function`.

- [ ] **Step 3: Add `lissajous` to the mirror**

In `tests/unit/js/_animations_mirror.js`, after the `export const mirror = {};` line, add:

```js
mirror.lissajous = function (ctx, tMs, w, h) {
  var N = 300, i;
  var a = 3 + 2 * Math.sin(tMs / 8000);
  var b = 4 + 2 * Math.sin(tMs / 11000);
  var phi = tMs / 3000;
  var cx = w / 2, cy = h / 2, ax = w * 0.35, ay = h * 0.35;
  ctx.strokeStyle = 'hsl(' + ((tMs / 40) % 360) + ', 70%, 60%)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (i = 0; i <= N; i++) {
    var s = (i / N) * Math.PI * 2;
    var x = cx + ax * Math.sin(a * s + phi);
    var y = cy + ay * Math.sin(b * s);
    if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
  }
  ctx.stroke();
};
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_lissajous.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Copy the SAME function into `index.html`**

In `index.html`, the `animations` object currently ends:

```js
		bouncingBalls: function(ctx, tMs, w, h) {
			...
		}
	};
```

Change the `bouncingBalls` closing `}` to `},` and insert the lissajous entry before the registry's closing `};`. Use TABS to match the file's indentation:

```js
		bouncingBalls: function(ctx, tMs, w, h) {
			var colors = ['#e74c3c', '#27ae60', '#2980b9', '#f1c40f'];
			var r = Math.max(12, Math.min(w, h) * 0.06), n = 4, i;
			for (i = 0; i < n; i++) {
				var px = (Math.sin(tMs / (900 + i * 220) + i) + 1) / 2;        // 0..1
				var py = (Math.sin(tMs / (700 + i * 180) + i * 1.7) + 1) / 2;  // 0..1
				ctx.fillStyle = colors[i % colors.length];
				ctx.beginPath();
				ctx.arc(r + px * (w - 2 * r), r + py * (h - 2 * r), r, 0, Math.PI * 2);
				ctx.fill();
			}
		},
		lissajous: function(ctx, tMs, w, h) {
			var N = 300, i;
			var a = 3 + 2 * Math.sin(tMs / 8000);
			var b = 4 + 2 * Math.sin(tMs / 11000);
			var phi = tMs / 3000;
			var cx = w / 2, cy = h / 2, ax = w * 0.35, ay = h * 0.35;
			ctx.strokeStyle = 'hsl(' + ((tMs / 40) % 360) + ', 70%, 60%)';
			ctx.lineWidth = 2;
			ctx.beginPath();
			for (i = 0; i <= N; i++) {
				var s = (i / N) * Math.PI * 2;
				var x = cx + ax * Math.sin(a * s + phi);
				var y = cy + ay * Math.sin(b * s);
				if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
			}
			ctx.stroke();
		}
	};
```

(The function body is byte-identical to the mirror; only indentation differs — tabs in `index.html`, two-spaces in the mirror. That's fine; determinism is about behavior, and the registry-sync test in Task 5 only checks key presence.)

- [ ] **Step 6: Commit**

```bash
git add tests/unit/js/test_animations_lissajous.js tests/unit/js/_animations_mirror.js index.html
git commit -m "feat(animations): lissajous curve (batch 1)"
```

---

### Task 3: `phyllotaxis` animation

**Files:**
- Modify: `tests/unit/js/_animations_mirror.js`
- Modify: `index.html` (animations registry)
- Test: `tests/unit/js/test_animations_phyllotaxis.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_phyllotaxis.js`:

```js
/**
 * phyllotaxis: a rotating golden-angle sunflower spiral of dots.
 * Same three guarantees as lissajous.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { mirror } from './_animations_mirror.js';
import { makeRecordingCtx } from './_canvas_stub.js';

const W = 1024, H = 768;

test('phyllotaxis — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.phyllotaxis(a, 33333, W, H);
  mirror.phyllotaxis(b, 33333, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('phyllotaxis — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.phyllotaxis(a, 2000, W, H);
  mirror.phyllotaxis(b, 7000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('phyllotaxis — draws 600 dots (one arc + fill each)', () => {
  const c = makeRecordingCtx();
  mirror.phyllotaxis(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 600);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_phyllotaxis.js`
Expected: FAIL — `mirror.phyllotaxis is not a function`.

- [ ] **Step 3: Add `phyllotaxis` to the mirror**

In `tests/unit/js/_animations_mirror.js`, append:

```js
mirror.phyllotaxis = function (ctx, tMs, w, h) {
  var N = 600, i;
  var GOLDEN = 137.508 * Math.PI / 180;
  var c = (Math.min(w, h) / (2 * Math.sqrt(N))) * 0.92;
  var cx = w / 2, cy = h / 2;
  var rot = tMs / 4000;
  for (i = 0; i < N; i++) {
    var theta = i * GOLDEN + rot;
    var r = c * Math.sqrt(i);
    var x = cx + r * Math.cos(theta);
    var y = cy + r * Math.sin(theta);
    var dotR = 3 + 2 * Math.sin(tMs / 1500 + i * 0.02);
    ctx.fillStyle = 'hsl(' + ((i / N) * 360) + ', 80%, 60%)';
    ctx.beginPath();
    ctx.arc(x, y, dotR, 0, Math.PI * 2);
    ctx.fill();
  }
};
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_phyllotaxis.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Copy the SAME function into `index.html`**

In `index.html`, change the `lissajous` entry's closing `}` to `},` and insert before the registry's closing `};` (tabs for indentation):

```js
		phyllotaxis: function(ctx, tMs, w, h) {
			var N = 600, i;
			var GOLDEN = 137.508 * Math.PI / 180;
			var c = (Math.min(w, h) / (2 * Math.sqrt(N))) * 0.92;
			var cx = w / 2, cy = h / 2;
			var rot = tMs / 4000;
			for (i = 0; i < N; i++) {
				var theta = i * GOLDEN + rot;
				var r = c * Math.sqrt(i);
				var x = cx + r * Math.cos(theta);
				var y = cy + r * Math.sin(theta);
				var dotR = 3 + 2 * Math.sin(tMs / 1500 + i * 0.02);
				ctx.fillStyle = 'hsl(' + ((i / N) * 360) + ', 80%, 60%)';
				ctx.beginPath();
				ctx.arc(x, y, dotR, 0, Math.PI * 2);
				ctx.fill();
			}
		}
	};
```

- [ ] **Step 6: Commit**

```bash
git add tests/unit/js/test_animations_phyllotaxis.js tests/unit/js/_animations_mirror.js index.html
git commit -m "feat(animations): phyllotaxis spiral (batch 1)"
```

---

### Task 4: `wireframeCube` animation

**Files:**
- Modify: `tests/unit/js/_animations_mirror.js`
- Modify: `index.html` (animations registry)
- Test: `tests/unit/js/test_animations_wireframe.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_wireframe.js`:

```js
/**
 * wireframeCube: a spinning 3D wireframe cube projected to 2D.
 * Same three guarantees, plus an edge-count check (12 edges →
 * 12 moveTo + 12 lineTo).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { mirror } from './_animations_mirror.js';
import { makeRecordingCtx } from './_canvas_stub.js';

const W = 1024, H = 768;

test('wireframeCube — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.wireframeCube(a, 44444, W, H);
  mirror.wireframeCube(b, 44444, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('wireframeCube — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.wireframeCube(a, 1000, W, H);
  mirror.wireframeCube(b, 8000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('wireframeCube — strokes 12 edges', () => {
  const c = makeRecordingCtx();
  mirror.wireframeCube(c, 5000, W, H);
  const moves = c.__ops.filter((o) => o.op === 'moveTo').length;
  const lines = c.__ops.filter((o) => o.op === 'lineTo').length;
  assert.equal(moves, 12);
  assert.equal(lines, 12);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_wireframe.js`
Expected: FAIL — `mirror.wireframeCube is not a function`.

- [ ] **Step 3: Add `wireframeCube` to the mirror**

In `tests/unit/js/_animations_mirror.js`, append:

```js
mirror.wireframeCube = function (ctx, tMs, w, h) {
  var V = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
  var E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
  var ax = tMs / 2500, ay = tMs / 3700, az = tMs / 5300;
  var cosx = Math.cos(ax), sinx = Math.sin(ax);
  var cosy = Math.cos(ay), siny = Math.sin(ay);
  var cosz = Math.cos(az), sinz = Math.sin(az);
  var s = Math.min(w, h) / 4, persp = 0.5, cx = w / 2, cy = h / 2;
  var proj = [], i;
  for (i = 0; i < V.length; i++) {
    var x = V[i][0], y = V[i][1], z = V[i][2];
    var y1 = y * cosx - z * sinx, z1 = y * sinx + z * cosx;     // Rx
    var x2 = x * cosy + z1 * siny, z2 = -x * siny + z1 * cosy;  // Ry
    var x3 = x2 * cosz - y1 * sinz, y3 = x2 * sinz + y1 * cosz;  // Rz
    var d = 1 + z2 * persp;
    proj.push([cx + s * x3 / d, cy + s * y3 / d]);
  }
  ctx.strokeStyle = 'hsl(' + ((tMs / 30) % 360) + ', 80%, 60%)';
  ctx.lineWidth = 3;
  ctx.beginPath();
  for (i = 0; i < E.length; i++) {
    var p0 = proj[E[i][0]], p1 = proj[E[i][1]];
    ctx.moveTo(p0[0], p0[1]);
    ctx.lineTo(p1[0], p1[1]);
  }
  ctx.stroke();
};
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_wireframe.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Copy the SAME function into `index.html`**

In `index.html`, change the `phyllotaxis` entry's closing `}` to `},` and insert before the registry's closing `};` (tabs for indentation):

```js
		wireframeCube: function(ctx, tMs, w, h) {
			var V = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
			var E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
			var ax = tMs / 2500, ay = tMs / 3700, az = tMs / 5300;
			var cosx = Math.cos(ax), sinx = Math.sin(ax);
			var cosy = Math.cos(ay), siny = Math.sin(ay);
			var cosz = Math.cos(az), sinz = Math.sin(az);
			var s = Math.min(w, h) / 4, persp = 0.5, cx = w / 2, cy = h / 2;
			var proj = [], i;
			for (i = 0; i < V.length; i++) {
				var x = V[i][0], y = V[i][1], z = V[i][2];
				var y1 = y * cosx - z * sinx, z1 = y * sinx + z * cosx;
				var x2 = x * cosy + z1 * siny, z2 = -x * siny + z1 * cosy;
				var x3 = x2 * cosz - y1 * sinz, y3 = x2 * sinz + y1 * cosz;
				var d = 1 + z2 * persp;
				proj.push([cx + s * x3 / d, cy + s * y3 / d]);
			}
			ctx.strokeStyle = 'hsl(' + ((tMs / 30) % 360) + ', 80%, 60%)';
			ctx.lineWidth = 3;
			ctx.beginPath();
			for (i = 0; i < E.length; i++) {
				var p0 = proj[E[i][0]], p1 = proj[E[i][1]];
				ctx.moveTo(p0[0], p0[1]);
				ctx.lineTo(p1[0], p1[1]);
			}
			ctx.stroke();
		}
	};
```

- [ ] **Step 6: Commit**

```bash
git add tests/unit/js/test_animations_wireframe.js tests/unit/js/_animations_mirror.js index.html
git commit -m "feat(animations): wireframe cube (batch 1)"
```

---

### Task 5: Registry-sync guard test

**Files:**
- Test: `tests/unit/js/test_animations_registry_sync.js`

This catches the most likely mistake in the copy-paste workflow: adding a function to the mirror but forgetting to add it to `index.html` (or vice-versa). It reads `index.html` as text and asserts every mirror key appears as a registry key.

- [ ] **Step 1: Write the test**

Create `tests/unit/js/test_animations_registry_sync.js`:

```js
/**
 * Guard against mirror/index.html drift: every animation in the test
 * mirror must also be registered in index.html's `animations` object.
 * We don't compare function bodies (indentation differs — tabs vs
 * spaces); the Playwright smoke covers behavior of the real copy.
 * This is the cheap "did you forget to paste it into index.html?"
 * check.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mirror } from './_animations_mirror.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

test('every mirror animation is registered in index.html', () => {
  const html = readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  for (const key of Object.keys(mirror)) {
    // Match `key: function` as it appears in `var animations = {...}`.
    const re = new RegExp('\\b' + key + '\\s*:\\s*function\\s*\\(');
    assert.ok(re.test(html), `index.html is missing animation "${key}"`);
  }
});
```

- [ ] **Step 2: Run it**

Run: `node --test tests/unit/js/test_animations_registry_sync.js`
Expected: PASS (Tasks 2-4 already added all three to `index.html`).

- [ ] **Step 3: Run the full JS suite to confirm nothing regressed**

Run: `python pytest_runner.py --js`
Expected: all tests pass, including the 3 new per-animation files + stub + sync.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/js/test_animations_registry_sync.js
git commit -m "test(animations): index.html registry-sync guard (batch 1)"
```

---

### Task 6: Animation catalog for the admin UI

**Files:**
- Create: `js/timeline/animations-catalog.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`
- Test: `tests/unit/js/test_animations_catalog.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_catalog.js`:

```js
/**
 * The admin-side animation catalog. The playlist editor reads this to
 * populate the SCRIPT-mode <select>. It must mirror the index.html
 * registry keys (bouncingBalls + the batch-1 three) and supply a
 * human label + description for each.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { ANIMATIONS } from '../../../js/timeline/animations-catalog.js';
import { mirror } from './_animations_mirror.js';

test('catalog — entries have key/label/description', () => {
  assert.ok(Array.isArray(ANIMATIONS) && ANIMATIONS.length >= 4);
  for (const a of ANIMATIONS) {
    assert.equal(typeof a.key, 'string');
    assert.equal(typeof a.label, 'string');
    assert.equal(typeof a.description, 'string');
    assert.ok(a.key.length > 0 && a.label.length > 0);
  }
});

test('catalog — includes the batch-1 animations', () => {
  const keys = ANIMATIONS.map((a) => a.key);
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `catalog missing "${k}"`);
  }
});

test('catalog — every batch-1 mirror animation has a catalog entry', () => {
  const keys = ANIMATIONS.map((a) => a.key);
  for (const k of Object.keys(mirror)) {
    assert.ok(keys.includes(k), `catalog missing mirrored animation "${k}"`);
  }
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_catalog.js`
Expected: FAIL — `Cannot find module '.../js/timeline/animations-catalog.js'`.

- [ ] **Step 3: Create the catalog**

Create `js/timeline/animations-catalog.js`:

```js
/**
 * Catalog of built-in SCRIPT animations, mirroring the `animations`
 * registry in index.html. The playlist editor reads this to render an
 * animation <select> when an item's play mode is SCRIPT, so the
 * operator picks from a list instead of memorizing registry keys.
 *
 * HAND-MAINTAINED: when an animation lands in index.html, add its
 * {key, label, description} here. `tests/unit/js/test_animations_catalog.js`
 * asserts this stays in sync with the test mirror (a proxy for
 * index.html).
 */
export const ANIMATIONS = [
  { key: 'bouncingBalls', label: 'Bouncing balls', description: 'Four balls drifting around the screen (the original).' },
  { key: 'lissajous',     label: 'Lissajous curve', description: 'A single morphing parametric curve that breathes over time.' },
  { key: 'phyllotaxis',   label: 'Phyllotaxis spiral', description: 'A rotating golden-angle sunflower-seed spiral.' },
  { key: 'wireframeCube', label: 'Wireframe cube', description: 'A spinning 3D wireframe cube.' },
];
```

- [ ] **Step 4: Run the catalog test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_catalog.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the catalog to the module-load smoke**

In `tests/unit/js/test_timeline_smoke.js`, the `MODULES` array ends with:

```js
  'js/timeline/track-header-context-menu.js',
  'js/timeline/modals/fleet-confirm.js',
  'js/timeline/modals/play-now.js',
];
```

Add the catalog:

```js
  'js/timeline/track-header-context-menu.js',
  'js/timeline/modals/fleet-confirm.js',
  'js/timeline/modals/play-now.js',
  'js/timeline/animations-catalog.js',
];
```

- [ ] **Step 6: Run the smoke**

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS — the new module loads.

- [ ] **Step 7: Commit**

```bash
git add js/timeline/animations-catalog.js tests/unit/js/test_animations_catalog.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): animations catalog for SCRIPT picker (batch 1)"
```

---

### Task 7: Playlist-editor SCRIPT mode + animation dropdown

**Files:**
- Modify: `js/timeline/modals/playlist-editor.js`

The editor currently offers play modes `loop` / `once` and edits `item.file` via a (listener-less) text input. This task:
1. Imports `ANIMATIONS`.
2. Adds a `SCRIPT` option to the play-mode `<select>`.
3. Adds an animation `<select>` to the sidebar grid (hidden by default).
4. When the selected item's `playmode === 'SCRIPT'`: show the animation select (hide the File text input unless the operator picks the `?` "Other…" sentinel), and write the chosen animation key to `item.file`. When `playmode !== 'SCRIPT'`: show the File text input (existing behavior).

There are no automated tests for this DOM rewrite in this task — the editor is browser-substance, covered by the Playwright smoke in Task 8 and the existing playlist-editor e2e specs. Verify manually per the steps below.

- [ ] **Step 1: Import the catalog**

At the top of `js/timeline/modals/playlist-editor.js`, after the existing imports:

```js
import { openModal, closeModal } from './modal-shell.js';
import { getDrag, clearDrag } from '../drag/dragstate.js';
```

add:

```js
import { ANIMATIONS } from '../animations-catalog.js';
```

- [ ] **Step 2: Add the SCRIPT option to the play-mode select + the animation field markup**

Find the sidebar form grid (the `<div class="mm-form-grid">` block, ~line 143-155). Replace it with:

```js
      <div class="mm-form-grid">
        <label>File <input type="text" data-field="file" disabled></label>
        <label data-field="anim-label" style="display:none">Animation
          <select data-field="file-anim" disabled></select>
        </label>
        <label>Play mode
          <select data-field="playmode" disabled>
            <option value="loop">Loop</option>
            <option value="once">Play once</option>
            <option value="SCRIPT">Animation (SCRIPT)</option>
          </select>
        </label>
        <label>Background color <input type="text" data-field="backgroundColor" placeholder="#000000 or rgb(0,0,0)" disabled></label>
        <label>Duration (s) <input type="number" data-field="duration" min="0.1" step="0.1" placeholder="auto" disabled>
          <span class="mm-plr-duration-hint" data-field="duration-hint"></span>
        </label>
      </div>
```

- [ ] **Step 3: Grab the new field refs + populate the animation select**

Find the `fields` object (~line 171-176):

```js
  const fields = {
    file: root.querySelector('[data-field="file"]'),
    playmode: root.querySelector('[data-field="playmode"]'),
    backgroundColor: root.querySelector('[data-field="backgroundColor"]'),
    duration: root.querySelector('[data-field="duration"]'),
  };
```

Replace with:

```js
  const fields = {
    file: root.querySelector('[data-field="file"]'),
    fileAnim: root.querySelector('[data-field="file-anim"]'),
    animLabel: root.querySelector('[data-field="anim-label"]'),
    playmode: root.querySelector('[data-field="playmode"]'),
    backgroundColor: root.querySelector('[data-field="backgroundColor"]'),
    duration: root.querySelector('[data-field="duration"]'),
  };

  // Populate the animation <select> once: one option per catalog
  // entry plus a "?" sentinel that drops back to free-text entry (so
  // an operator can type a brand-new animation name not yet in the
  // catalog — forward-compat with index.html entries that landed
  // before the catalog was updated).
  for (const a of ANIMATIONS) {
    const opt = document.createElement('option');
    opt.value = a.key;
    opt.textContent = a.label;
    opt.title = a.description;
    fields.fileAnim.appendChild(opt);
  }
  const otherOpt = document.createElement('option');
  otherOpt.value = '?';
  otherOpt.textContent = 'Other (type a name)…';
  fields.fileAnim.appendChild(otherOpt);
  const ANIM_KEYS = ANIMATIONS.map((a) => a.key);
```

- [ ] **Step 4: Wire the new controls**

Find the sidebar wiring block. After the existing `fields.playmode` change listener (~line 179-182):

```js
  fields.playmode.addEventListener('change', () => {
    if (selectedIdx < 0) return;
    draft.items[selectedIdx].playmode = fields.playmode.value;
  });
```

Replace it with a version that re-syncs the file/animation swap and seeds a default animation when switching into SCRIPT mode:

```js
  fields.playmode.addEventListener('change', () => {
    if (selectedIdx < 0) return;
    const it = draft.items[selectedIdx];
    it.playmode = fields.playmode.value;
    // Switching INTO SCRIPT with a non-animation file (e.g. a media
    // URL left over from when it was a FULL item): seed the first
    // catalog animation so the item is immediately playable.
    if (it.playmode === 'SCRIPT' && ANIM_KEYS.indexOf(it.file) === -1) {
      it.file = ANIM_KEYS[0];
    }
    syncSidebar();
    renderRibbon();
  });

  // Free-text File input: keep it.file live (covers SCRIPT "Other…"
  // entry AND any future free-text use). Pre-SCRIPT this input had no
  // listener because files arrived via drag/drop; SCRIPT mode needs
  // it editable.
  fields.file.addEventListener('input', () => {
    if (selectedIdx < 0) return;
    draft.items[selectedIdx].file = fields.file.value.trim();
    renderRibbon();
  });

  // Animation <select>: pick a catalog animation, or "?" to reveal the
  // free-text File input for an off-catalog name.
  fields.fileAnim.addEventListener('change', () => {
    if (selectedIdx < 0) return;
    const it = draft.items[selectedIdx];
    if (fields.fileAnim.value === '?') {
      // Reveal the free-text input; leave it.file as-is for editing.
      fields.file.parentElement.style.display = '';
      fields.file.disabled = false;
      fields.file.focus();
    } else {
      it.file = fields.fileAnim.value;
      fields.file.parentElement.style.display = 'none';
      renderRibbon();
    }
  });
```

- [ ] **Step 5: Teach `syncSidebar` to swap File ↔ Animation**

Find `syncSidebar` (~line 228). In the `enabled` branch (after `const it = draft.items[selectedIdx];` and the existing field-population lines, ~line 244-249), add the swap logic. Locate:

```js
    const it = draft.items[selectedIdx];
    selTitle.textContent = `Item ${selectedIdx + 1}: ${basename(it.file)}`;
    fields.file.value = it.file || '';
    fields.playmode.value = it.playmode || 'loop';
    fields.backgroundColor.value = it.backgroundColor || '';
```

Insert immediately after the `fields.playmode.value = ...` line:

```js
    // SCRIPT items edit `file` via the animation <select>; everything
    // else uses the free-text File input. Toggle the two controls.
    const isScript = it.playmode === 'SCRIPT';
    fields.animLabel.style.display = isScript ? '' : 'none';
    fields.fileAnim.disabled = !isScript;
    if (isScript) {
      const known = ANIM_KEYS.indexOf(it.file) !== -1;
      fields.fileAnim.value = known ? it.file : '?';
      // Free-text File input visible only for the "Other…" sentinel.
      fields.file.parentElement.style.display = known ? 'none' : '';
      fields.file.disabled = known;
    } else {
      fields.fileAnim.value = ANIM_KEYS[0];
      fields.file.parentElement.style.display = '';
      fields.file.disabled = false;
    }
```

Also update the `!enabled` branch (~line 234-242) so the swap resets cleanly when no item is selected. Find:

```js
    if (!enabled) {
      selTitle.textContent = 'No item selected';
      fields.file.value = '';
      fields.playmode.value = 'loop';
      fields.backgroundColor.value = '';
      fields.duration.value = '';
      fields.duration.removeAttribute('max');
      durationHint.textContent = '';
      return;
    }
```

Replace with:

```js
    if (!enabled) {
      selTitle.textContent = 'No item selected';
      fields.file.value = '';
      fields.file.parentElement.style.display = '';
      fields.playmode.value = 'loop';
      fields.animLabel.style.display = 'none';
      fields.fileAnim.disabled = true;
      fields.backgroundColor.value = '';
      fields.duration.value = '';
      fields.duration.removeAttribute('max');
      durationHint.textContent = '';
      return;
    }
```

- [ ] **Step 6: Also disable the animation select alongside the others**

In `syncSidebar`, the enable/disable block at the top (~line 229-233):

```js
    const enabled = selectedIdx >= 0;
    removeBtn.disabled = !enabled;
    fields.playmode.disabled = !enabled;
    fields.backgroundColor.disabled = !enabled;
    fields.duration.disabled = !enabled;
```

Leave this as-is — `fields.fileAnim.disabled` is managed by the SCRIPT-aware logic below it (Step 5), which always runs after this block for an enabled item. No change needed; this step is a no-op confirmation that the disabled-state ordering is correct (the `isScript` block sets `fileAnim.disabled` last, winning).

- [ ] **Step 7: Verify the JS suite still passes**

Run: `python pytest_runner.py --js`
Expected: all pass. (No new unit test here; the editor change is DOM-level and covered by Task 8's browser smoke. The smoke-load test confirms `playlist-editor.js` still imports cleanly with the new `ANIMATIONS` import.)

- [ ] **Step 8: Manual smoke (dev server must be running on :3000)**

1. Open `http://localhost:3000/admin.html`.
2. Right-click a clip → "Edit playlist items" (or drill in + double-click an item) to open the editor.
3. Select an item. Set Play mode → **Animation (SCRIPT)**. Confirm the File text input is replaced by an **Animation** dropdown listing Bouncing balls / Lissajous curve / Phyllotaxis spiral / Wireframe cube.
4. Pick **Lissajous curve**. Confirm the ribbon clip title updates to `lissajous`.
5. Pick **Other (type a name)…**. Confirm a free-text File input appears; type `phyllotaxis`; confirm the clip title updates.
6. Switch Play mode back to **Loop**. Confirm the File text input returns and the Animation dropdown hides.
7. Save. Reload the page, reopen the editor on the same playlist, confirm the SCRIPT item persisted with its animation name (play mode shows Animation (SCRIPT), dropdown shows the chosen animation).

- [ ] **Step 9: Commit**

```bash
git add js/timeline/modals/playlist-editor.js
git commit -m "feat(timeline): SCRIPT play-mode + animation picker in playlist editor (batch 1)"
```

---

### Task 8: Playwright browser smoke for SCRIPT animations

**Files:**
- Create: `tests/e2e/test-script-animations.spec.js`

This is a **light** smoke per the spec — the client is the substance, but we only assert the canvas renders non-blank and tears down. It drives the display client (`index.html`) directly via a synthesized SCRIPT PLAY, not through the admin UI, to keep the spec self-contained.

Check the existing e2e harness conventions first: specs live in `tests/e2e/`, use the `playwright` package directly (not `@playwright/test`), are launched by `tests/e2e/run.js [<substr>]`, target `MM_BASE_URL` (default `http://localhost:3000`), and each spec creates + cleans up its own `__e2e_`-prefixed fixtures. Read `tests/e2e/test-playlist-ribbon.spec.js` and `tests/e2e/helpers.js` to match the export shape (a spec exports an async function taking `{ page, baseURL }` or similar — match whatever the existing specs do).

- [ ] **Step 1: Read the harness to match the spec signature**

Run: `node --test` is NOT used here. Inspect:

```bash
sed -n '1,60p' tests/e2e/run.js
sed -n '1,40p' tests/e2e/test-playlist-ribbon.spec.js
```

Note the exact export signature and the `cleanupE2eOrphans` import path used by existing specs.

- [ ] **Step 2: Write the spec**

Create `tests/e2e/test-script-animations.spec.js`, matching the existing specs' export shape. The body should:

1. Navigate to `index.html` (the display client) on `baseURL`.
2. In the page context (`page.evaluate`), drive the existing SCRIPT path directly. The display client exposes `showItem`, `playback`, and the `animations` registry in its `<script>` scope — but these are function-scoped, not on `window`. So instead, simulate a minimal SCRIPT render by invoking the registry the same way `runScriptLoop` does, against a probe canvas, and assert non-blank pixels. Concretely, in `page.evaluate`:

```js
// Build a probe canvas, run one frame of each batch-1 animation,
// assert at least one non-transparent pixel (the animation drew).
const results = {};
const names = ['lissajous', 'phyllotaxis', 'wireframeCube'];
// The animations registry is closure-private in index.html. Re-derive
// the same functions by reading them off a SCRIPT playback if exposed;
// otherwise this probe re-declares them is NOT allowed (would test a
// copy). Prefer: trigger a real SCRIPT item and sample #canvas.
```

**IMPORTANT design note for the implementer:** the `animations` object is private to `index.html`'s IIFE/script scope, so a browser test cannot call it directly without going through the real playback path. Two acceptable approaches — pick whichever the harness makes simpler:

   - **(A) Real-path (preferred):** POST a `__e2e_`-prefixed playlist containing a single SCRIPT item (`{file:'lissajous', duration:5, playmode:'SCRIPT'}`) via `/api/playlists`, assign it to a throwaway display group, and trigger PLAY through the same SockJS path the app uses. Then sample `#canvas canvas` pixels via `getImageData` and assert non-blank. Tear down the playlist after. This exercises the true end-to-end path.
   - **(B) Minimal-exposure:** add a tiny test-only hook in `index.html` — `window.__animations = animations;` guarded by a `?tdbg`-style flag — and have the spec sample against it. Only do this if (A) proves too heavy for the harness. If chosen, gate it so it never exposes in production (`if (location.search.indexOf('tdbg') !== -1) window.__animations = animations;`).

Default to **(A)**. The assertion in both cases: after a short wait (≥100ms for one rAF frame), `getImageData` over the canvas has at least one pixel with alpha > 0 (or non-background RGB).

3. Assert teardown: trigger STOP (or transition to a non-SCRIPT item) and confirm `#canvas canvas` is removed / cleared.

- [ ] **Step 3: Run the e2e spec (dev server + npm install required)**

Run: `node tests/e2e/run.js script-animations`
Expected: the spec passes — SCRIPT canvas renders non-blank, tears down on stop.

(If the e2e environment isn't set up — no `node_modules`/chromium — note that the manual smoke in Task 7 Step 8 plus the iPad-1 hardware check below cover the gap, and flag to the controller that this spec needs a Playwright-capable environment to run.)

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test-script-animations.spec.js
git commit -m "test(e2e): SCRIPT animation renders + tears down (batch 1)"
```

---

### Task 9: iPad-1 hardware sign-off (manual checklist)

**Files:** none — this is a manual verification gate, not automatable.

The platform's defining device is a 1st-gen iPad (iOS 5.1 / Safari 5.1). The determinism tests prove synchronization math; only hardware proves the frame budget.

- [ ] **Step 1: Assign a SCRIPT playlist to a real iPad-1 group**

Create a playlist with three SCRIPT items (`lissajous`, `phyllotaxis`, `wireframeCube`, 20s each, `playmode:'SCRIPT'`), assign to a group containing at least one iPad-1, and Play-Now it.

- [ ] **Step 2: Observe each animation on the iPad-1 for ~20s**

For each animation, confirm:
- It renders (not a blank/black screen).
- Motion is smooth — target 30+ FPS, no visible multi-second stalls. `phyllotaxis` (600 dots) is the most likely to stutter; if it does, reduce `N` from 600 to 400 in BOTH `index.html` and the mirror (and update the phyllotaxis test's arc-count assertion from 600 to 400), then re-run `node --test tests/unit/js/test_animations_phyllotaxis.js`.
- Two iPads in the same group are visually in lockstep (same frame at the same instant) — this is the synchronization payoff.

- [ ] **Step 3: Record the result**

Note pass/fail per animation in the PR description. If `phyllotaxis` needed the `N=400` reduction, mention it.

---

### Task 10: Final review + PR

- [ ] **Step 1: Run the full JS + unit suites**

Run:
```bash
python pytest_runner.py --js
python pytest_runner.py --unit
```
Expected: all pass. (No Python changed in this batch, but `--unit` confirms no accidental breakage.)

- [ ] **Step 2: Dispatch a final code review**

Use superpowers:requesting-code-review over the batch's commits. Focus areas: ES5 compliance in `index.html` (no `let`/`const`/arrows leaked in), mirror ↔ index.html fidelity, the playlist-editor swap logic edge cases (switching playmode with a media-URL file, the `?` free-text path, no-item-selected reset).

- [ ] **Step 3: Finish the branch**

Use superpowers:finishing-a-development-branch. The PR summary should note:
- Three new SCRIPT animations (`lissajous`, `phyllotaxis`, `wireframeCube`) + the shared catalog/picker infrastructure.
- No server changes (reuses the SCRIPT slice's `playmode` plumbing).
- iPad-1 hardware sign-off result (Task 9).
- That Batches 2-5 are pure leaf additions on top of this infrastructure.

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-09-script-animations-pack-design.md`, Batch 1 scope):

| Spec requirement | Task |
|---|---|
| `lissajous` animation, math as specified | Task 2 |
| `phyllotaxis` animation, golden-angle spiral, N=600 | Task 3 |
| `wireframeCube` animation, 3-axis rotation + projection | Task 4 |
| `animations-catalog.js` (ANIMATIONS mirror) | Task 6 |
| Playlist-editor SCRIPT dropdown, free-text fallback on `?` | Task 7 |
| Node determinism tests (same tMs → identical draw-op log) via mirror | Tasks 1-4 |
| Light Playwright smoke | Task 8 |
| ES5-only in index.html | Enforced in Tasks 2-4 (verified in Task 10 review) |
| iPad-1 frame budget (<8ms/frame) | Task 9 (manual) |
| No server changes | Confirmed in background section (data path verified) |

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — every code step has complete content. Task 8 deliberately presents two implementation approaches (A real-path / B minimal-exposure) with a default (A) because the exact e2e harness export signature must be read from the existing specs first; this is guidance, not a placeholder, and the assertion + teardown are fully specified.

**3. Type/name consistency:**
- `makeRecordingCtx` / `__ops` — consistent across Tasks 1-4, 6.
- `mirror.<key>` — consistent (`lissajous`, `phyllotaxis`, `wireframeCube`).
- `ANIMATIONS` (array of `{key, label, description}`) — consistent in Task 6 (defined) and Task 7 (consumed: `ANIMATIONS.map`, `a.key`/`a.label`/`a.description`).
- `fields.fileAnim` / `fields.animLabel` — defined in Task 7 Step 3, used in Steps 4-6 consistently.
- `ANIM_KEYS` — defined Task 7 Step 3, used Steps 4-5.
- Animation function signature `(ctx, tMs, w, h)` — consistent everywhere (no `nowMs` 5th arg; that's Batch 3, correctly out of scope here).

No gaps found.
