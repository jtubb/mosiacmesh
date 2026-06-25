# Keg Roll Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `kegroll` transition — a giant keg sprite rolls across the wall as the moving boundary of a directional cover, with physical roll rotation.

**Architecture:** Mask-family effect riding the existing additive `effect` descriptor + the in-canvas/overlay dispatch that Iris/Dissolve/Beerfill/Scatter already use. Three pure, node-tested geometry helpers (cover rect / keg position / roll angle) + one thin canvas drawer. The proven Wipe cover path is NOT touched — keg roll re-derives the reveal math in its own helper. No server render-token impact (visual-only; edits are instant).

**Tech Stack:** Python (`effects.py`, pytest), ES5 JavaScript (`js/transitions.js`, node `--test`), `index.html` (ES5 client glue), OpenCV/numpy (sprite asset), aiohttp (demo tool).

## Global Constraints

- **Display-client JS is ES5 ONLY** (`js/transitions.js`, `js/mesh-viewport.js`, `index.html` inline scripts): no `let`/`const`/arrow/template-literal/`class`/`Promise`/`fetch`. jQuery 1.x + SockJS retained.
- **Canvas ops allowed on Safari 5.1:** `fillRect`, `arc`+`fill`, rotated `drawImage` (via `mmStampSprite`'s `translate`/`rotate`/`scale`). **No** `clip()`, **no** `destination-*` compositing, **no** CSS filters.
- **Do NOT modify the Wipe cover path** (`mmWipeCoverRect` / `st.wipe`) — it is on-wall-proven; keg roll rides the parallel `st.effect` path.
- **Run tests via the runner**, never bare `pytest`: `python pytest_runner.py --unit` (Python), `python pytest_runner.py --js` or `node --test tests/unit/js/<file>` (JS). `pytest.ini` lives in `tests/`.
- **Server restarts require explicit user authorization.** The server is already running on port 3000 for this work.
- **Commit trailer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- Effect catalog response convention + `_afade` single-duration pattern as in the existing effects.

---

### Task 1: `effects.py` — `KegRollEffect` catalog entry

**Files:**
- Modify: `effects.py` (append a new `@register` class after `ScatterEffect`, ~line 225)
- Test: `tests/unit/test_effects.py`

**Interfaces:**
- Consumes: `Effect`, `ParamSpec`, `register`, `_afade` (existing in `effects.py`).
- Produces: `KegRollEffect` with `name = "kegroll"`; appears in `effect_catalog()`; `video_filters(role, params, ctx)` returns `([], _afade(role, params, ctx))` (single `duration`, audio-only). The catalog name set becomes `{fade, wipe, slide, zoom, iris, dissolve, beerfill, scatter, kegroll}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_effects.py`:

```python
def test_catalog_includes_kegroll():
    names = {e["name"] for e in effects.effect_catalog()}
    assert "kegroll" in names


def test_kegroll_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "kegroll")
    by = {p["key"]: p for p in e["params"]}
    assert by["sprite"]["type"] == "string" and by["sprite"]["default"] == "keg"
    assert by["direction"]["choices"] == ["left", "right", "up", "down"]
    assert by["direction"]["default"] == "right"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 2000
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_kegroll_audio_single_duration_role_aware():
    kr = effects.get_effect("kegroll")
    ctx = {"duration_ms": 6000}
    v, a = kr.video_filters("start", kr.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = kr.video_filters("end", kr.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4:d=2"]
    v3, a3 = kr.video_filters("end", kr.resolve({"duration": 2000, "audioFade": False}), ctx)
    assert v3 == [] and a3 == []
```

Also update the exhaustive catalog assertion `test_catalog_has_all_effects` (line 14):

```python
def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve",
                     "beerfill", "scatter", "kegroll"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v -k "kegroll or all_effects"`
Expected: FAIL — `kegroll` not in catalog; `get_effect("kegroll")` is `None`.

- [ ] **Step 3: Add the effect class**

Append to `effects.py` after `ScatterEffect` (after line 225):

```python
@register
class KegRollEffect(Effect):
    name = "kegroll"
    label = "Keg Roll"
    params = [ParamSpec("sprite", "string", "keg"),
              ParamSpec("direction", "choice", "right", choices=["left", "right", "up", "down"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2000, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual roll is client-side
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: PASS (all effects tests, including the new three + the updated exhaustive set).

- [ ] **Step 5: Commit**

```bash
git add effects.py tests/unit/test_effects.py
git commit -m "feat(effects): kegroll catalog entry (sprite/direction/scope/duration)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pure geometry helpers in `js/transitions.js`

**Files:**
- Modify: `js/transitions.js` (add 4 functions near the scatter helpers ~line 410-428; add 4 exports in the `root.*` block ~line 533-563)
- Test: `tests/unit/js/test_kegroll.js` (new)

**Interfaces:**
- Consumes: nothing (pure; no DOM/canvas).
- Produces (all exported on `root`):
  - `mmKegPhase(role)` → `'cover'` when `role === 'out'`, else `'reveal'`.
  - `mmKegCoverRect(prog, direction, phase, reg)` → `{x, y, w, h}` (global px) or `null`. `prog` = keg local phase progress 0→1; `reg` = `{x, y, w, h}` region in global px. `cover` phase fills behind the keg (grows 0→full); `reveal` fills ahead of the keg (shrinks full→0). `direction` = keg travel direction.
  - `mmKegPos(prog, direction, reg, kegD)` → `{cx, cy, dist}`. Keg center in global px; `dist` ≥ 0 distance traveled. Keg travels from fully off the start edge to fully off the far edge across `reg`-span + `kegD`.
  - `mmKegAngle(dist, kegRadius, direction)` → radians. Physical roll `dist / kegRadius`; negative for `left`/`up`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/test_kegroll.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
await import('../../../js/animations.js');       // mmMeshTransform (for the drawer task later)
await import('../../../js/mesh-viewport.js');     // mmMeshViewport + mmStampSprite
const g = globalThis;
const REG = { x: 0, y: 0, w: 300, h: 200 };

test('mmKegPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmKegPhase('out'), 'cover');
  assert.equal(g.mmKegPhase('in'), 'reveal');
});

test('mmKegCoverRect: cover grows from start edge in travel direction', () => {
  assert.equal(g.mmKegCoverRect(0, 'right', 'cover', REG), null);      // nothing covered yet
  let r = g.mmKegCoverRect(0.5, 'right', 'cover', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 150, h: 200 });                 // left half covered
  r = g.mmKegCoverRect(1, 'right', 'cover', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 200 });                 // fully covered
});

test('mmKegCoverRect: reveal shrinks the ahead-of-keg region to nothing', () => {
  let r = g.mmKegCoverRect(0, 'right', 'reveal', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 200 });                 // fully covered at start
  r = g.mmKegCoverRect(0.5, 'right', 'reveal', REG);
  assert.deepEqual(r, { x: 150, y: 0, w: 150, h: 200 });               // right half still covered
  assert.equal(g.mmKegCoverRect(1, 'right', 'reveal', REG), null);     // fully revealed
});

test('mmKegCoverRect: left anchors at the far (right) edge for cover', () => {
  const r = g.mmKegCoverRect(0.5, 'left', 'cover', REG);
  assert.deepEqual(r, { x: 150, y: 0, w: 150, h: 200 });
});

test('mmKegCoverRect: down covers the vertical axis, full width', () => {
  const r = g.mmKegCoverRect(0.5, 'down', 'cover', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 100 });
});

test('mmKegCoverRect: honors region offset', () => {
  const reg = { x: 10, y: 20, w: 300, h: 200 };
  const r = g.mmKegCoverRect(0.5, 'right', 'cover', reg);
  assert.deepEqual(r, { x: 10, y: 20, w: 150, h: 200 });
});

test('mmKegPos: keg fully off both edges at the ends, centered perpendicular', () => {
  const kegD = 200;                                  // = REG.h for a horizontal roll
  let p = g.mmKegPos(0, 'right', REG, kegD);
  assert.ok(Math.abs(p.cx - (-100)) < 1e-9);          // center off the left by a radius
  assert.ok(Math.abs(p.cy - 100) < 1e-9);             // perpendicular center
  assert.ok(Math.abs(p.dist - 0) < 1e-9);
  p = g.mmKegPos(1, 'right', REG, kegD);
  assert.ok(Math.abs(p.cx - 400) < 1e-9);             // off the right by a radius (300 + 100)
  assert.ok(Math.abs(p.dist - 500) < 1e-9);           // span(300) + diameter(200)
  p = g.mmKegPos(0, 'left', REG, kegD);
  assert.ok(Math.abs(p.cx - 400) < 1e-9);             // left-roll starts off the right
});

test('mmKegAngle: physical roll = dist/radius, sign per direction', () => {
  assert.ok(Math.abs(g.mmKegAngle(500, 100, 'right') - 5) < 1e-9);
  assert.ok(Math.abs(g.mmKegAngle(500, 100, 'left') - (-5)) < 1e-9);
  assert.ok(Math.abs(g.mmKegAngle(500, 100, 'up') - (-5)) < 1e-9);
  assert.equal(g.mmKegAngle(500, 0, 'right'), 0);     // degenerate radius
  let prev = -1;
  for (let i = 0; i <= 10; i++) { const a = g.mmKegAngle(i * 50, 100, 'right'); assert.ok(a >= prev - 1e-9); prev = a; }
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: FAIL — `mmKegPhase`/`mmKegCoverRect`/`mmKegPos`/`mmKegAngle` are not functions.

- [ ] **Step 3: Add the helpers**

In `js/transitions.js`, add after `mmScatterSpriteUrl` (after line 428, before `mmBuildSpriteAtlas`):

```javascript
  // --- Keg roll (mask family): a giant keg sprite rolls across as the moving
  // boundary of a directional cover. Pure geometry; the wipe's reveal MATH is
  // re-derived here, the wipe's CODE is untouched. ---
  function mmKegPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // Directional cover rect in global px (or null when nothing is covered). prog =
  // keg local phase progress 0->1. 'cover' fills BEHIND the keg (where it has been;
  // grows 0->full); 'reveal' fills AHEAD of the keg (where it hasn't reached; shrinks
  // full->0). direction = keg travel direction; the rect always spans the full
  // perpendicular dimension. reg = {x,y,w,h} region (wall = full GWxGH, screen = bbox).
  function mmKegCoverRect(prog, direction, phase, reg) {
    var f = prog < 0 ? 0 : (prog > 1 ? 1 : prog);
    var horiz = (direction === 'left' || direction === 'right');
    var plus = (direction === 'right' || direction === 'down');   // travels toward the hi edge
    var S = horiz ? reg.w : reg.h;
    var lead = plus ? f * S : (1 - f) * S;        // keg leading-edge offset within [0..S]
    var lo, hi;
    if (phase === 'cover') {
      if (plus) { lo = 0; hi = lead; } else { lo = lead; hi = S; }
    } else {                                       // reveal: the not-yet-reached side
      if (plus) { lo = lead; hi = S; } else { lo = 0; hi = lead; }
    }
    var len = hi - lo;
    if (len <= 1e-9) { return null; }
    if (horiz) { return { x: reg.x + lo, y: reg.y, w: len, h: reg.h }; }
    return { x: reg.x, y: reg.y + lo, w: reg.w, h: len };
  }

  // Keg center (global px) + distance traveled. The keg travels from fully off the
  // start edge to fully off the far edge across (S + kegD), so it is never parked
  // half-on at an end. kegD = keg diameter in global px. Returns {cx, cy, dist>=0}.
  function mmKegPos(prog, direction, reg, kegD) {
    var f = prog < 0 ? 0 : (prog > 1 ? 1 : prog);
    var horiz = (direction === 'left' || direction === 'right');
    var plus = (direction === 'right' || direction === 'down');
    var S = horiz ? reg.w : reg.h;
    var path = S + kegD;
    var dist = f * path;
    var axis = plus ? (-kegD / 2 + dist) : (S + kegD / 2 - dist);   // center offset within region axis
    if (horiz) { return { cx: reg.x + axis, cy: reg.y + reg.h / 2, dist: dist }; }
    return { cx: reg.x + reg.w / 2, cy: reg.y + axis, dist: dist };
  }

  // Physical roll: rotation tied to distance (arc length = radius * angle). Sign
  // negative for left/up so the keg appears to roll in its travel direction. Pure.
  function mmKegAngle(dist, kegRadius, direction) {
    if (!(kegRadius > 0)) { return 0; }
    var sign = (direction === 'left' || direction === 'up') ? -1 : 1;
    return sign * dist / kegRadius;
  }
```

Add exports in the `root.*` block (after `root.mmScatterSpriteUrl = mmScatterSpriteUrl;`, line 561):

```javascript
  root.mmKegPhase = mmKegPhase;
  root.mmKegCoverRect = mmKegCoverRect;
  root.mmKegPos = mmKegPos;
  root.mmKegAngle = mmKegAngle;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_kegroll.js
git commit -m "feat(transitions): keg-roll pure geometry helpers (cover/pos/angle)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `mmTransitionState` — `kegroll` branch

**Files:**
- Modify: `js/transitions.js` (`mmTransitionState`, add a branch after the `scatter` branch ~line 134, before the `slide/zoom/iris/dissolve` branch)
- Test: `tests/unit/js/test_kegroll.js` (append)

**Interfaces:**
- Consumes: `mmKegPhase` (Task 2). The existing `_dur` already returns `(+eff.params.duration) || 0` for any effect not named `beerfill`/`scatter`, so `kegroll`'s single `duration` is honored with **no `_dur` change**.
- Produces: for `eff.name === 'kegroll'`, `mmTransitionState` returns `{ role, opacity:1, wipe:null, effect: { name:'kegroll', family:'mask', front, scope, params, phase } }` where `front` is local phase progress (`role 'out'` → `1-p`, `role 'in'` → `p`) and `phase` is `mmKegPhase(role)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/js/test_kegroll.js`:

```javascript
test('mmTransitionState: kegroll end=cover, start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'kegroll', params: { duration: 2000, scope: 'wall', direction: 'right' } };
  // offset 4500 of 6000, ed=2000 -> raw p=(6000-4500)/2000=0.75 -> front=1-0.75=0.25
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'kegroll');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);     // local progress, not raw p
  assert.equal(st.effect.scope, 'wall');
  const start = { name: 'kegroll', params: { duration: 2000 } };
  st = S(start, null, 500, 6000, null, null);             // in-window: raw p=0.25, front=0.25
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');                  // default when param omitted
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: FAIL — `st.effect` is `undefined` for `kegroll` (falls through to the fade return `{role, opacity:p, wipe:null}`).

- [ ] **Step 3: Add the branch**

In `js/transitions.js`, insert after the `scatter` branch (after line 134, before `if (eff.name === 'slide' || ...)`):

```javascript
    if (eff.name === 'kegroll') {
      var kgsc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1 (like scatter): `p` counts DOWN on the
      // 'out' window (1->0), so invert there; 'in' already counts up.
      var kglp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'kegroll', family: 'mask', front: kglp,
                         scope: kgsc, params: eff.params || {}, phase: mmKegPhase(role) } };
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_kegroll.js && node --test tests/unit/js/test_transitions.js && node --test tests/unit/js/test_scatter.js`
Expected: PASS — new kegroll branch test passes; existing transition + scatter tests stay green (untouched paths).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_kegroll.js
git commit -m "feat(transitions): mmTransitionState kegroll mask-family descriptor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `mmDrawKegRoll` drawer

**Files:**
- Modify: `js/transitions.js` (add `mmDrawKegRoll` after `mmDrawScatter` ~line 531; export it ~line 563)
- Test: `tests/unit/js/test_kegroll.js` (append)

**Interfaces:**
- Consumes: `_mmMaskRegion` (existing private in `transitions.js`), `mmKegCoverRect`/`mmKegPos`/`mmKegAngle` (Task 2), `mmMeshViewport` + `mmStampSprite` (from `js/mesh-viewport.js`, optional — culls off-screen).
- Produces: `mmDrawKegRoll(ctx, params, phase, prog, GW, GH, quad, scope, img, bg, canvasW, canvasH)` — draws the cover rect (item bg) then stamps the giant rolling keg. Draws cover-only when `img` is not yet decoded. Signature mirrors `mmDrawScatter` minus the `seed` arg (keg roll has no seeded particles). This is canvas glue — covered by a recording-context smoke test, then the on-wall sign-off (Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/test_kegroll.js`:

```javascript
function recCtxKeg() {
  return { rects: [], imgs: 0, fillStyle: '#000',
    save(){}, restore(){}, translate(){}, rotate(){}, scale(){}, setTransform(){},
    beginPath(){}, arc(){}, fill(){},
    fillRect(x, y, w, h){ this.rects.push({ x, y, w, h }); },
    drawImage(){ this.imgs++; } };
}
const kegImg = { width: 120, height: 120 };   // "loaded"
const kegNoImg = { width: 0, height: 0 };      // not decoded yet

test('mmDrawKegRoll: cover only (no stamp) when sprite not decoded', () => {
  const c = recCtxKeg();
  g.mmDrawKegRoll(c, { direction: 'right' }, 'cover', 0.5, 300, 200, null, 'wall', kegNoImg, '#3a241a');
  assert.equal(c.imgs, 0);            // no keg stamp
  assert.equal(c.rects.length, 1);    // cover rect drawn
  assert.deepEqual(c.rects[0], { x: 0, y: 0, w: 150, h: 200 });
});

test('mmDrawKegRoll: draws cover + keg stamp when loaded', () => {
  const c = recCtxKeg();
  g.mmDrawKegRoll(c, { direction: 'right' }, 'cover', 0.5, 300, 200, null, 'wall', kegImg, '#3a241a');
  assert.equal(c.rects.length, 1);    // cover
  assert.equal(c.imgs, 1);            // keg stamped (no viewport -> never culled)
});

test('mmDrawKegRoll: no cover rect at cover-phase start, still stamps keg', () => {
  const c = recCtxKeg();
  g.mmDrawKegRoll(c, { direction: 'right' }, 'cover', 0, 300, 200, null, 'wall', kegImg, '#3a241a');
  assert.equal(c.rects.length, 0);    // nothing covered at prog 0
  assert.equal(c.imgs, 1);            // keg present (rolling in from off-edge)
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: FAIL — `mmDrawKegRoll` is not a function.

- [ ] **Step 3: Add the drawer**

In `js/transitions.js`, add after `mmDrawScatter` (after line 531, before the `root.*` export block):

```javascript
  // Draw the keg-roll cover: a directional cover rect (item bg) + the giant rolling
  // keg sprite at the boundary. fillRect + (culled) drawImage only; no clip/composite.
  // Cover-only (graceful plain wipe) until the keg PNG decodes. Mirrors mmDrawScatter
  // (minus the seed arg). ctx is already under the mesh affine (in-canvas) or the
  // overlay matrix, so everything is drawn in GLOBAL coords -> wall-coherent.
  function mmDrawKegRoll(ctx, params, phase, prog, GW, GH, quad, scope, img, bg, canvasW, canvasH) {
    var vp = (quad && typeof mmMeshViewport === 'function')
      ? mmMeshViewport(quad, GW, GH, canvasW, canvasH) : null;
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var dir = (params && params.direction) || 'right';
    var horiz = (dir === 'left' || dir === 'right');
    var kegD = horiz ? reg.h : reg.w;             // giant roller: diameter = perpendicular dim
    var rect = mmKegCoverRect(prog, dir, phase, reg);
    if (rect) { ctx.fillStyle = bg || '#000000'; ctx.fillRect(rect.x, rect.y, rect.w, rect.h); }
    if (!img || !img.width) { return; }            // sprite not decoded -> cover only (plain wipe)
    var pos = mmKegPos(prog, dir, reg, kegD);
    var ang = mmKegAngle(pos.dist, kegD / 2, dir);
    mmStampSprite(ctx, vp, img, pos.cx, pos.cy, kegD, ang);   // globalSize (height) = kegD
  }
```

Add the export in the `root.*` block (after `root.mmDrawScatter = mmDrawScatter;`, line 563):

```javascript
  root.mmDrawKegRoll = mmDrawKegRoll;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: PASS (all kegroll tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_kegroll.js
git commit -m "feat(transitions): mmDrawKegRoll drawer (cover rect + rolling keg)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `index.html` apply wiring (in-canvas + overlay)

**Files:**
- Modify: `index.html` (in-canvas mask dispatch ~line 744-749; overlay mask dispatch ~line 1145-1151)
- Test: existing `tests/unit/js/test_transitions.js` module-load smoke + manual (on-wall is Task 7).

**Interfaces:**
- Consumes: `mmDrawKegRoll` (Task 4), `mmSprite(url)` + `mmScatterSpriteUrl(name)` (existing in `index.html` / `transitions.js`). The `st.effect.family === 'mask'` dispatch already exists for both paths; this adds a `kegroll` branch to each.
- Produces: no new exports — wires the drawer into the live client. The keg sprite URL resolves via `mmScatterSpriteUrl(params.sprite)` (generic name→`/media/server/images/<name>.png`); default `sprite` is `keg`.

- [ ] **Step 1: Add the in-canvas branch**

In `index.html`, in the in-canvas mask block, insert a `kegroll` branch immediately after the `scatter` branch (after line 748, before `} else if (typeof mmDrawMaskInCanvas === 'function') {`):

```javascript
								} else if (stc.effect.name === 'kegroll' && typeof mmDrawKegRoll === 'function') {
									mmDrawKegRoll(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
										mmSprite(mmScatterSpriteUrl(stc.effect.params && stc.effect.params.sprite)),
										it.backgroundColor || '#000000', canvas.width, canvas.height);
```

- [ ] **Step 2: Add the overlay branch**

In `index.html`, in the overlay mask block, insert a `kegroll` branch after the `scatter` branch (after line 1149, before `} else {` that calls `mmDrawMaskOverlay`):

```javascript
						} else if (st.effect.name === 'kegroll' && typeof mmDrawKegRoll === 'function') {
							mmDrawKegRoll(cmx, st.effect.params, st.effect.phase, st.effect.front,
								GWm, GHm, quad, st.effect.scope,
								mmSprite(mmScatterSpriteUrl(st.effect.params && st.effect.params.sprite)),
								item.backgroundColor || '#000000', cvm.width, cvm.height);
```

- [ ] **Step 3: Verify the module-load smoke + JS suite still pass**

Run: `python pytest_runner.py --js`
Expected: PASS — no regressions; `transitions.js` + `mesh-viewport.js` load and all JS unit tests are green. (The `index.html` glue itself is exercised on-wall in Task 7.)

- [ ] **Step 4: Sanity-check the edit didn't break the dispatch chain**

Run: `node -e "require('fs').readFileSync('index.html','utf8').match(/kegroll/g).length===2 || (function(){throw new Error('expected exactly 2 kegroll wiring sites')})()" && echo OK`
Expected: `OK` (one in-canvas + one overlay branch).

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): wire kegroll into in-canvas + overlay mask dispatch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `keg.png` sprite + "Keg Roll Demo" playlist tool

**Files:**
- Create: `tools/_make_keg_sprite.py` (generates `media/server/images/keg.png`)
- Create: `media/server/images/keg.png` (output of the generator; gitignored media dir — commit the generator, not necessarily the PNG)
- Create: `tools/_make_kegroll_demo.py`

**Interfaces:**
- Consumes: `cv2`, `numpy` (already runtime deps); the running server on `127.0.0.1:3000` (for the demo tool). The client resolves `sprite:"keg"` → `/media/server/images/keg.png`.
- Produces: a transparent keg PNG as the default sprite, and a `Keg Roll Demo` playlist (two plasma mesh items handing off via `kegroll`).

- [ ] **Step 1: Write the sprite generator**

Create `tools/_make_keg_sprite.py`:

```python
"""Render a simple side-view wooden keg/barrel PNG (transparent) to seed the
kegroll transition. Output: media/server/images/keg.png (BGRA). Replace with
nicer art later — the effect degrades to a plain wipe if the sprite is absent."""
import os
import numpy as np
import cv2

W = H = 900
img = np.zeros((H, W, 4), dtype=np.uint8)
cx, cy = W / 2.0, H / 2.0

BODY = (40, 95, 150, 255)        # BGRA — warm wood brown
BODY_D = (24, 60, 100, 255)
HOOP = (120, 120, 130, 255)      # steel hoop grey
OUTLINE = (12, 28, 45, 255)

# Barrel body: a vertical "staved" barrel that bulges in the middle. Build the
# silhouette as a closed polygon (left edge down, right edge up) using a cosine bulge.
half_h = H * 0.40
top_w = W * 0.30                 # half-width at the ends
mid_w = W * 0.42                 # half-width at the belly
n = 40
left, right = [], []
for i in range(n + 1):
    t = i / n                                   # 0 (top) .. 1 (bottom)
    y = cy - half_h + t * 2 * half_h
    bulge = top_w + (mid_w - top_w) * np.sin(t * np.pi)   # 0 at ends, max at middle
    left.append((cx - bulge, y))
    right.append((cx + bulge, y))
poly = np.array(left + right[::-1], dtype=np.int32)
cv2.fillPoly(img, [poly], BODY, lineType=cv2.LINE_AA)

# vertical stave shading lines
for k in range(-3, 4):
    x = int(cx + k * (mid_w / 3.5))
    cv2.line(img, (x, int(cy - half_h * 0.9)), (x, int(cy + half_h * 0.9)),
             BODY_D, thickness=3, lineType=cv2.LINE_AA)

# steel hoops (top, upper-belly, lower-belly, bottom) as horizontal bands
for ty, bw in [(0.16, top_w * 1.02), (0.40, mid_w * 1.0), (0.60, mid_w * 1.0), (0.84, top_w * 1.02)]:
    y = int(cy - half_h + ty * 2 * half_h)
    cv2.line(img, (int(cx - bw), y), (int(cx + bw), y), HOOP, thickness=14, lineType=cv2.LINE_AA)

# silhouette outline
cv2.polylines(img, [poly], True, OUTLINE, thickness=8, lineType=cv2.LINE_AA)

out = os.path.join("media", "server", "images", "keg.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
cv2.imwrite(out, img)
print("wrote", out, img.shape)
```

- [ ] **Step 2: Run the generator + verify the PNG**

Run: `python tools/_make_keg_sprite.py`
Expected: `wrote media/server/images/keg.png (900, 900, 4)`

Verify transparency + size:
Run: `python -c "import cv2; a=cv2.imread('media/server/images/keg.png', cv2.IMREAD_UNCHANGED); print(a.shape, 'has-alpha' if a.shape[2]==4 else 'NO-ALPHA', int(a[:,:,3].min()), int(a[:,:,3].max()))"`
Expected: `(900, 900, 4) has-alpha 0 255` (transparent corners, opaque keg).

- [ ] **Step 3: Write the demo-playlist tool**

Create `tools/_make_kegroll_demo.py`:

```python
"""Create a 'Keg Roll Demo' playlist: two plasma mesh items handing off via the
kegroll transition (sprite=keg, wall, roll right). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def kr():
    return {"name": "kegroll",
            "params": {"sprite": "keg", "direction": "right", "scope": "wall",
                       "duration": 2000, "audioFade": True}}

ITEMS = [
    {"id": "kr-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#1a0f06", "startEffect": None, "endEffect": kr()},
    {"id": "kr-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#1a0f06", "startEffect": kr(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Keg Roll Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Keg Roll Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Create the demo playlist + verify**

Run: `python tools/_make_kegroll_demo.py`
Expected: `sent Keg Roll Demo`

Run: `curl -s http://localhost:3000/api/playlists`
Expected: a `Keg Roll Demo` entry with 2 items whose `endEffect`/`startEffect` are named `kegroll`.

- [ ] **Step 5: Commit**

```bash
git add tools/_make_keg_sprite.py tools/_make_kegroll_demo.py
git commit -m "feat(tools): keg sprite generator + Keg Roll Demo playlist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(Note: `media/` is gitignored; the PNG is reproduced by running the generator. Commit the generator, not the binary, unless the repo already tracks `media/server/images/*.png`.)

---

### Task 7: iPad-1 on-wall sign-off (manual acceptance — gated on deploy)

**Files:** none (manual verification).

**Interfaces:**
- Consumes: Tasks 1-6 deployed; the running server (restart requires **explicit user authorization** to pick up `index.html`/`transitions.js`/`effects.py` changes); a calibrated mesh group; the `Keg Roll Demo` playlist.

- [ ] **Step 1: Request a server restart (explicit authorization required)**

Ask the user to authorize a restart so the client + effect changes load. Do NOT restart unprompted.

- [ ] **Step 2: Assign + play the demo**

Assign `Keg Roll Demo` to a calibrated mesh group and start playback (via the admin UI or the existing PLAY path). Reload the fleet.

- [ ] **Step 3: Observe acceptance criteria**

On the calibrated wall, with `kegroll` (sprite=keg, scope=wall, direction=right) as item A's `endEffect` → item B's `startEffect`:
  - The giant keg rolls smoothly across the wall (perpendicular dimension ≈ full wall height).
  - The cover paints behind the keg on the cover phase (item A hidden), and retreats behind it on the reveal phase (item B revealed) — a continuous left→right roll across the boundary.
  - Rotation reads as genuine **rolling** (tied to distance), not sliding/spinning-in-place.
  - No tearing at panel seams; smooth at wall scale (use `?tdbg` fps HUD if perf looks marginal — the per-frame cost is 1 `fillRect` + 1 culled `drawImage`, so it should be cheaper than scatter).

- [ ] **Step 4: Record the result**

Mark the sign-off task complete (or file follow-ups for any direction/size tuning). If the keg art needs work, iterate `tools/_make_keg_sprite.py` (or drop in real art at `media/server/images/keg.png`) — no code change needed.

---

## Self-Review

**1. Spec coverage** (checked each spec section against a task):
- Catalog entry + params (sprite/direction/scope/duration/audioFade) → Task 1. ✓
- `video_filters` audio-only, single duration → Task 1. ✓
- Pure helpers `mmKegCoverRect`/`mmKegPos`/`mmKegAngle` (+`mmKegPhase`) → Task 2. ✓
- `mmTransitionState` additive `effect` descriptor (mask family, local-progress `front`, phase) → Task 3. ✓
- `mmDrawKegRoll` drawer (cover rect + `mmStampSprite` keg, graceful pre-decode) → Task 4. ✓
- Both apply sites (in-canvas + overlay) → Task 5. ✓
- Wipe path untouched → confirmed (kegroll rides `st.effect`, never `st.wipe`). ✓
- No render-token impact → Task 1's `_afade`-only `video_filters`; the existing `_audio_fade_sig` reads only `audioFade`, so no new param enters the token (no code change needed, hence no task — explicitly noted here). ✓
- keg.png asset + Keg Roll Demo → Task 6. ✓
- On-wall sign-off → Task 7. ✓
- ES5 / canvas-op constraints → Global Constraints + helper/drawer code (no `let`/`const`/clip/composite). ✓

**2. Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N" — every code step shows complete code; the demo tool is given in full (not "copy the scatter one"). ✓

**3. Type consistency:** `mmDrawKegRoll` signature `(ctx, params, phase, prog, GW, GH, quad, scope, img, bg, canvasW, canvasH)` is identical between Task 4 (definition) and Task 5 (both call sites). `mmKegPhase`/`mmKegCoverRect`/`mmKegPos`/`mmKegAngle` names + arg orders match between Task 2 (defs), Task 3 (`mmKegPhase` use), and Task 4 (drawer uses). `effect.front`/`effect.phase`/`effect.scope`/`effect.params` keys match between Task 3 (producer) and Tasks 4-5 (consumers). ✓
