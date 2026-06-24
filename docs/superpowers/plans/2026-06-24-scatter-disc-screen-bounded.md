# Scatter Backing-Disc Screen-Bounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the scatter backing disc screen-bounded — per screen, draw nothing until the disc arrives, the arc while its edge crosses, and a cheap `fillRect` once it fully covers — preserving the wall-wide radial reveal while killing the expensive fully-covered arc frames.

**Architecture:** Add a pure helper `mmScatterDiscCase(cx,cy,r,rect)→'none'|'arc'|'fill'` to `js/transitions.js`; branch the disc draw in `mmDrawScatter` on it when a viewport `vp` is present, else keep the current arc. No server change, no new module.

**Tech Stack:** Hand-written ES5 JavaScript, `node --test`.

## Global Constraints

- **ES5 only** in `js/transitions.js`: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`.
- Canvas primitives only: `arc` / `fillRect` / `fill` (no `clip()`, no compositing).
- The radius cases are exactly: `r < nearR` → `'none'`; `r >= farR` → `'fill'`; else `'arc'`. `nearR` = point-to-rect distance (0 when center inside), `farR` = max distance from `(cx,cy)` to the rect's four corners.
- When `vp` is null (scope:`screen` / uncalibrated / quad-less), the disc keeps its current `arc(cx,cy,c*maxR)` unchanged.
- The existing guard `if (!sd.nodisc && c * maxR >= 0.5)` and the `?sdisc=0` knob behavior are unchanged (the new branching lives *inside* that block).
- Do NOT change the erupting copies, the giant, the spin, or the `?sdisc=0` knob.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Run JS tests: `node --test tests/unit/js/test_scatter.js`; parse-check: `node --check js/transitions.js`.

## Reference: current disc block (`js/transitions.js`, in `mmDrawScatter`)

`vp` is already computed at the top of `mmDrawScatter` as
`var vp = (quad && !sd.nocull && typeof mmMeshViewport === 'function') ? mmMeshViewport(quad, GW, GH, canvasW, canvasH) : null;`.
The current disc block:

```js
    var c = mmScatterCover(phase, p);
    // backing disc (item bg) — guarantees a clean, gap-free cover. At scope:wall
    // its radius is the wall half-diagonal (a huge per-frame arc+fill); ?tdbg
    // ?sdisc=0 drops it to A/B its cost (NOTE: without it the cover can show gaps).
    if (!sd.nodisc && c * maxR >= 0.5) {
      ctx.fillStyle = bg || '#000000';
      ctx.beginPath(); ctx.arc(cx, cy, c * maxR, 0, 6.283185307); ctx.fill();
    }
```

`cx, cy` = region center; `maxR` = region half-diagonal; `vp.globalRect` = `{x,y,w,h}` (this screen's global view).

## File Structure

- **Modify** `js/transitions.js` — add `mmScatterDiscCase` (+ `root` export) just before `mmDrawScatter`; branch the disc block on it.
- **Modify** `tests/unit/js/test_scatter.js` — helper cases + `mmDrawScatter` fill/arc behavior.

---

### Task 1: Screen-bounded backing disc

**Files:**
- Modify: `js/transitions.js` (`mmScatterDiscCase` + disc block in `mmDrawScatter`)
- Test: `tests/unit/js/test_scatter.js`

**Interfaces:**
- Consumes: `vp.globalRect` from `mmMeshViewport` (already built in `mmDrawScatter`); `cx, cy, maxR, c` locals.
- Produces: `mmScatterDiscCase(cx, cy, r, rect) -> 'none' | 'arc' | 'fill'` (pure, on `root`).

- [ ] **Step 1: Write the failing tests**

In `tests/unit/js/test_scatter.js` (it imports transitions.js/animations.js/mesh-viewport.js; `g = globalThis`; `recCtx` records `arcs`, `rects` via `fillRect`, `imgs`), add:

```js
test('mmScatterDiscCase: none/arc/fill by radius vs an off-center rect', () => {
  const rect = { x: 1000, y: 1000, w: 200, h: 200 };   // nearR≈1414, farR≈1697 from (0,0)
  assert.equal(g.mmScatterDiscCase(0, 0, 1000, rect), 'none');   // disc hasn't reached
  assert.equal(g.mmScatterDiscCase(0, 0, 1500, rect), 'arc');    // edge crossing
  assert.equal(g.mmScatterDiscCase(0, 0, 1800, rect), 'fill');   // fully covers
});
test('mmScatterDiscCase: center inside rect never none; fills past far corner', () => {
  const rect = { x: -100, y: -100, w: 200, h: 200 };   // center (0,0) inside; farR≈141
  assert.equal(g.mmScatterDiscCase(0, 0, 50, rect), 'arc');      // inside, edge still within
  assert.equal(g.mmScatterDiscCase(0, 0, 200, rect), 'fill');    // past far corner
});
test('mmDrawScatter: disc uses fillRect (no arc) when it fully covers the screen', () => {
  const c = recCtx();
  const im = { width: 100, height: 120 };
  const FULL = [[0, 0], [1, 0], [1, 1], [0, 1]];        // screen == whole wall
  // p=1 cover -> c=1 -> disc radius = maxR (full) -> covers the screen -> 'fill'
  g.mmDrawScatter(c, { count: 5 }, 'cover', 1, 1000, 800, FULL, 'wall', 7, im, '#140d06', 1000, 800);
  assert.equal(c.arcs, 0);              // no arc tessellation
  assert.ok(c.rects.length >= 1);       // disc drawn as a fillRect
});
test('mmDrawScatter: disc uses the arc while its edge crosses a partial screen', () => {
  const c = recCtx();
  const im = { width: 100, height: 120 };
  const LEFT = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]];    // left half; wall center on its edge
  // c=0.5 -> r=0.5*maxR (<farR) with center on the rect -> 'arc'
  g.mmDrawScatter(c, { count: 5 }, 'cover', 0.5, 1000, 800, LEFT, 'wall', 7, im, '#140d06', 200, 160);
  assert.ok(c.arcs >= 1);               // curved edge drawn
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: FAIL — `mmScatterDiscCase is not a function`, and the fill test fails because the current code always draws an `arc` (so `c.arcs` is 1, not 0).

- [ ] **Step 3: Add the helper**

In `js/transitions.js`, immediately before `function mmDrawScatter(`, add:

```js
  // Per-screen backing-disc case for a radial disc of radius r centered at global
  // (cx,cy) vs this screen's global rect {x,y,w,h}: 'none' (disc not yet reached),
  // 'arc' (edge crossing -> draw the curved edge), 'fill' (fully covers -> cheap
  // solid, no huge-circle tessellation). Pure.
  function mmScatterDiscCase(cx, cy, r, rect) {
    var dx = Math.max(rect.x - cx, 0, cx - (rect.x + rect.w));
    var dy = Math.max(rect.y - cy, 0, cy - (rect.y + rect.h));
    var nearR = Math.sqrt(dx * dx + dy * dy);
    if (r < nearR) { return 'none'; }
    var x2 = rect.x + rect.w, y2 = rect.y + rect.h;
    var xs = [rect.x, x2, x2, rect.x], ys = [rect.y, rect.y, y2, y2];
    var farR = 0, i, ex, ey, d;
    for (i = 0; i < 4; i++) {
      ex = xs[i] - cx; ey = ys[i] - cy; d = Math.sqrt(ex * ex + ey * ey);
      if (d > farR) { farR = d; }
    }
    return (r >= farR) ? 'fill' : 'arc';
  }
```

- [ ] **Step 4: Branch the disc draw**

Replace the current disc block (shown in Reference above) with:

```js
    var c = mmScatterCover(phase, p);
    // backing disc (item bg) — guarantees gap-free cover. Screen-bounded when a
    // viewport is known: per screen, skip until the disc arrives, draw the arc
    // while its edge crosses, and a cheap fillRect once it fully covers (the long
    // covered tail, which used to be a wall-diagonal arc). ?sdisc=0 drops it.
    if (!sd.nodisc && c * maxR >= 0.5) {
      ctx.fillStyle = bg || '#000000';
      var _dc = vp ? mmScatterDiscCase(cx, cy, c * maxR, vp.globalRect) : 'arc';
      if (_dc === 'fill') {
        ctx.fillRect(vp.globalRect.x, vp.globalRect.y, vp.globalRect.w, vp.globalRect.h);
      } else if (_dc === 'arc') {
        ctx.beginPath(); ctx.arc(cx, cy, c * maxR, 0, 6.283185307); ctx.fill();
      }
      // 'none' -> disc hasn't reached this screen -> draw nothing
    }
```

- [ ] **Step 5: Export the helper**

In `js/transitions.js`, near the other `root.mmScatter*` exports (e.g. after `root.mmScatterParticles = mmScatterParticles;`), add:

```js
  root.mmScatterDiscCase = mmScatterDiscCase;
```

- [ ] **Step 6: Run tests + parse-check to verify they pass**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: PASS — the four new tests plus all existing scatter tests (the existing "disc only when sprite not loaded" test passes `quad=null` → `vp` null → `'arc'`, so it still records an arc; "cover=0 draws nothing visible" still has `c*maxR < 0.5` → no disc).

Run: `node --check js/transitions.js`
Expected: parse OK.

- [ ] **Step 7: Commit**

```bash
git add js/transitions.js tests/unit/js/test_scatter.js
git commit -m "feat(scatter): screen-bounded backing disc (skip/arc/fill per screen)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: iPad-1 on-wall verification

**Files:** none (manual acceptance; requires a deploy the **user must authorize**).

**Interfaces:** Consumes Task 1 deployed; uses the `?tdbg` fps heartbeat + the `?sdisc=0` knob.

- [ ] **Step 1: Deploy** — on user authorization, restart the server and reload screen3 fresh (`killall MobileSafari` then `uiopen 'http://192.168.1.60:3000/?tdbg'`).

- [ ] **Step 2: A/B** — with the Scatter Demo on `OEB Sign 1`, read screen3's fps heartbeat for the default (disc on, now screen-bounded) vs `?tdbg&sdisc=0` (disc off). The screen-bounded default should now sit **near** the `?sdisc=0` frame rate (the fully-covered frames are cheap fills), where before it cost ~5–10 fps.

- [ ] **Step 3: Confirm coverage** — visually verify the scatter cover is still gap-free (no outgoing/incoming content showing through during the handoff).

- [ ] **Step 4: Record the outcome** and restore screen3 to the clean display (`uiopen 'http://192.168.1.60:3000/'`).

---

## Plan Self-Review

**1. Spec coverage:**
- Three-case per-screen disc (`none`/`arc`/`fill`) keyed on `r` vs the screen rect → Task 1 Steps 3–4. ✓
- `nearR`/`farR` definitions (point-to-rect, max corner) → helper in Step 3. ✓
- `vp` null / scope:screen keeps the current arc → Step 4 `vp ? … : 'arc'`. ✓
- Guard + `?sdisc=0` unchanged (branch lives inside the block) → Step 4. ✓
- Pure helper, node-tested → Steps 1, 3, 5. ✓
- Testing: helper cases + fill/arc behavior + on-wall A/B → Tasks 1–2. ✓
- Out of scope (copies/giant/spin/knob untouched) → only the disc block + helper change. ✓

**2. Placeholder scan:** No TBD/TODO; complete code in every code step; commands have expected output. ✓

**3. Type consistency:** `mmScatterDiscCase(cx, cy, r, rect)` signature and the `'none'|'arc'|'fill'` return are identical in the helper, the wiring (`_dc`), and the tests. `vp.globalRect` `{x,y,w,h}` matches `mmMeshViewport`'s descriptor. The `recCtx` fields used (`arcs`, `rects`, `imgs`) exist in `tests/unit/js/test_scatter.js`. ✓
