# Coaster Flip Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a `coasterflip` transition — the content folds edge-on (scale on a chosen axis 1→0, dim + cardboard edge sliver) then opens back out, faked in 2D for the iPad-1.

**Architecture:** Transform-family effect riding the existing slide/zoom transform path (additive `effect` descriptor with `family:'transform'`, raw-progress `front:p`). One pure helper (`mmFlipFactor`) + a coaster-color palette; apply is a scale + alpha in the in-canvas mesh path and an `-webkit-transform: scale` on the element path, plus a mesh-only cardboard edge sliver. Procedural — no sprite.

**Tech Stack:** Python (`effects.py`, pytest), ES5 JavaScript (`js/transitions.js`, node `--test`), `index.html` (ES5 client glue), aiohttp (demo tool).

## Global Constraints

- **Display-client JS is ES5 ONLY** (`js/transitions.js`, `index.html` inline scripts): no `let`/`const`/arrow/template-literal/`class`/`Promise`/`fetch`. `var`/`function` only. (Node `--test` files may use modern JS.)
- **A flip is 2D only:** `ctx.scale` / `globalAlpha` / `fillRect` / `ctx.setTransform` and `-webkit-transform: scale`. **No** CSS 3D, **no** `matrix3d`, **no** WebGL, **no** `clip()`/`destination-*`/filters.
- **Do NOT modify the Wipe path or any existing effect branch** — coasterflip is purely additive (added to the transform-family branch/dispatch alongside slide/zoom).
- Run tests via the runner, never bare `pytest`: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`; full unit `python pytest_runner.py --unit`; JS `node --test tests/unit/js/<file>` or `python pytest_runner.py --js`.
- **Single `duration` schema** (matches kegroll/beerfill/frostcreep): a coasterflip instance only folds (endEffect) or opens (startEffect).
- **Raw progress front** (like slide/zoom — NO local-progress inversion): `front = p`. Out role `p`:1→0 (open→edge); in role `p`:0→1 (edge→open); `scale = front`.
- **Commit trailer on EVERY commit (exact, incl. parenthetical):**
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `effects.py` — `CoasterFlipEffect` catalog entry + render-token guard

**Files:**
- Modify: `effects.py` (append a `@register` class after `FrostCreepEffect`)
- Test: `tests/unit/test_effects.py`, `tests/unit/test_mosaic.py`

**Interfaces:**
- Consumes: `Effect`, `ParamSpec`, `register`, `_afade` (existing).
- Produces: `CoasterFlipEffect` with `name = "coasterflip"`; appears in `effect_catalog()`; `video_filters` returns `([], _afade(role, params, ctx))` (single `duration`, audio-only). Catalog name set gains `coasterflip`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_effects.py`:

```python
def test_catalog_includes_coasterflip():
    names = {e["name"] for e in effects.effect_catalog()}
    assert "coasterflip" in names


def test_coasterflip_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "coasterflip")
    by = {p["key"]: p for p in e["params"]}
    assert by["axis"]["choices"] == ["horizontal", "vertical"] and by["axis"]["default"] == "horizontal"
    assert by["coaster"]["choices"] == ["kraft", "cork", "slate"] and by["coaster"]["default"] == "kraft"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 700
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_coasterflip_audio_single_duration():
    cf = effects.get_effect("coasterflip")
    ctx = {"duration_ms": 6000}
    v, a = cf.video_filters("start", cf.resolve({"duration": 700, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=0.7"]
    v2, a2 = cf.video_filters("end", cf.resolve({"duration": 700, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=5.3:d=0.7"]
    v3, a3 = cf.video_filters("end", cf.resolve({"audioFade": False}), ctx)
    assert v3 == [] and a3 == []
```

Update the exhaustive catalog assertion `test_catalog_has_all_effects`:

```python
def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve",
                     "beerfill", "scatter", "kegroll", "frostcreep", "coasterflip"}
```

Add to `tests/unit/test_mosaic.py`, directly after `test_token_unchanged_by_frostcreep_visual_param_change` (use that test as the template for `self._token_setup`/`server.compute_render_token` usage):

```python
    def test_token_unchanged_by_coasterflip_visual_param_change(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "coasterflip", "params": {"axis": "horizontal", "coaster": "kraft",
                                                            "scope": "wall", "duration": 700, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        # Change only visual params (axis, coaster, scope); audioFade and duration unchanged.
        me.startEffect = {"name": "coasterflip", "params": {"axis": "vertical", "coaster": "slate",
                                                            "scope": "screen", "duration": 700, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "coasterflip visual-only param changes should not invalidate the render token"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_effects.py tests/unit/test_mosaic.py -c tests/pytest.ini -v -k "coasterflip or all_effects"`
Expected: FAIL — `coasterflip` not in catalog; `get_effect("coasterflip")` is `None`.

Also expect the existing API-effects list test to break once the effect is added — note it for Step 3 (it asserts the exact registered set).

- [ ] **Step 3: Add the effect class (and fix the API-list assertion if it breaks)**

Append to `effects.py` after `FrostCreepEffect`:

```python
@register
class CoasterFlipEffect(Effect):
    name = "coasterflip"
    label = "Coaster Flip"
    # Single `duration`: a coasterflip instance only folds (endEffect) or opens
    # (startEffect), never both.
    params = [ParamSpec("axis", "choice", "horizontal", choices=["horizontal", "vertical"]),
              ParamSpec("coaster", "choice", "kraft", choices=["kraft", "cork", "slate"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 700, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration
```

If `tests/unit/test_playlists.py::TestEffectsApi::test_api_effects_lists_registered` (or similar) fails because it asserts the exact registered-effect set with `==`, add `"coasterflip"` to that expected set — this is a necessary consequence of registering the effect, not scope creep. Report the file/line you touched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python pytest_runner.py --unit`
Expected: PASS (full unit suite green, incl. the new effects + token-guard tests + the updated exhaustive set).

- [ ] **Step 5: Commit**

```bash
git add effects.py tests/unit/test_effects.py tests/unit/test_mosaic.py
git commit -m "feat(effects): coasterflip catalog entry + render-token guard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pure helpers in `js/transitions.js`

**Files:**
- Modify: `js/transitions.js` (add `mmFlipFactor`, `_COASTER`, `mmCoasterColor` near the slide/zoom helpers; add the two function exports in the `root.*` block)
- Test: `tests/unit/js/test_coasterflip.js` (new)

**Interfaces:**
- Consumes: nothing (pure).
- Produces (exported on `root`):
  - `mmFlipFactor(front, axis)` → `{ sx, sy, alpha, edge }`. `f = clamp(front,0,1)`; horizontal drives `sx` (sy=1), vertical drives `sy` (sx=1); `alpha = 0.35 + 0.65*f`; `edge = 1 - f`.
  - `mmCoasterColor(name)` → hex string from `_COASTER` (`kraft`/`cork`/`slate`), default kraft.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/test_coasterflip.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;
const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmFlipFactor: horizontal drives sx; endpoints; alpha/edge ramps; clamp', () => {
  assert.deepEqual(g.mmFlipFactor(1, 'horizontal'), { sx: 1, sy: 1, alpha: 1, edge: 0 });   // open
  let f = g.mmFlipFactor(0, 'horizontal');
  assert.ok(C(f.sx, 0) && C(f.sy, 1) && C(f.alpha, 0.35) && C(f.edge, 1));                   // edge-on
  f = g.mmFlipFactor(0.5, 'horizontal');
  assert.ok(C(f.sx, 0.5) && C(f.sy, 1) && C(f.alpha, 0.675) && C(f.edge, 0.5));
  f = g.mmFlipFactor(1.5, 'horizontal');                                                     // clamp high
  assert.ok(C(f.sx, 1) && C(f.edge, 0));
  f = g.mmFlipFactor(-0.5, 'horizontal');                                                     // clamp low
  assert.ok(C(f.sx, 0) && C(f.edge, 1));
});

test('mmFlipFactor: vertical drives sy, sx stays 1', () => {
  const f = g.mmFlipFactor(0.4, 'vertical');
  assert.ok(C(f.sx, 1) && C(f.sy, 0.4));
});

test('mmCoasterColor: known tones + default', () => {
  assert.equal(g.mmCoasterColor('kraft'), '#b9935f');
  assert.equal(g.mmCoasterColor('slate'), '#5a5e63');
  assert.equal(g.mmCoasterColor('nope'), g.mmCoasterColor('kraft'));   // unknown -> kraft
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_coasterflip.js`
Expected: FAIL — `mmFlipFactor`/`mmCoasterColor` not functions.

- [ ] **Step 3: Add the helpers**

In `js/transitions.js`, add after `mmZoomFactor` (or near the slide/zoom helpers), before the `root.*` export block:

```javascript
  // Coaster flip (transform family). front = flip openness (1 open .. 0 edge-on). sx/sy
  // scale the chosen axis only; alpha dims the content toward edge-on; edge is the
  // cardboard edge-sliver opacity (strongest at edge-on). Pure.
  function mmFlipFactor(front, axis) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var vert = (axis === 'vertical');
    return { sx: vert ? 1 : f, sy: vert ? f : 1, alpha: 0.35 + 0.65 * f, edge: 1 - f };
  }

  var _COASTER = { kraft: '#b9935f', cork: '#c8a06a', slate: '#5a5e63' };
  function mmCoasterColor(name) { return _COASTER[name] || _COASTER.kraft; }
```

Add exports in the `root.*` block:

```javascript
  root.mmFlipFactor = mmFlipFactor;
  root.mmCoasterColor = mmCoasterColor;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_coasterflip.js`
Expected: PASS (all 3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_coasterflip.js
git commit -m "feat(transitions): coaster-flip pure helpers (mmFlipFactor + coaster color)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `mmTransitionState` — `coasterflip` (transform family)

**Files:**
- Modify: `js/transitions.js` (`mmTransitionState`, add `coasterflip` to the existing slide/zoom/iris/dissolve branch condition)
- Test: `tests/unit/js/test_coasterflip.js` (append)

**Interfaces:**
- Consumes: nothing new. `_dur` already returns `(+eff.params.duration) || 0` for any non-beerfill/scatter effect, so coasterflip's single `duration` is honored with **no `_dur` change**.
- Produces: for `eff.name === 'coasterflip'`, `mmTransitionState` returns `{ role, opacity:1, wipe:null, effect: { name:'coasterflip', family:'transform', front: p, scope, params } }` (`fam` derives to `transform` since it is not iris/dissolve).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/js/test_coasterflip.js`:

```javascript
test('mmTransitionState: coasterflip = transform family, front=p (raw, both roles)', () => {
  const S = g.mmTransitionState;
  // end window [5300,6000], ed=700; offset 5650 -> p=(6000-5650)/700=0.5
  const end = { name: 'coasterflip', params: { axis: 'horizontal', duration: 700 } };
  let st = S(null, end, 5650, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'coasterflip');
  assert.equal(st.effect.family, 'transform');
  assert.ok(Math.abs(st.effect.front - 0.5) < 1e-9);    // raw p, NOT inverted
  assert.equal(st.effect.scope, 'wall');                // default
  // start window [0,700], sd=700; offset 350 -> p=0.5
  const start = { name: 'coasterflip', params: { duration: 700 } };
  st = S(start, null, 350, 6000, null, null);
  assert.equal(st.role, 'in');
  assert.equal(st.effect.family, 'transform');
  assert.ok(Math.abs(st.effect.front - 0.5) < 1e-9);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_coasterflip.js`
Expected: FAIL — `st.effect` undefined for `coasterflip` (falls through to the fade return).

- [ ] **Step 3: Add coasterflip to the transform branch condition**

In `js/transitions.js`, change the branch condition (the line currently reading `if (eff.name === 'slide' || eff.name === 'zoom' || eff.name === 'iris' || eff.name === 'dissolve') {`) to include coasterflip:

```javascript
    if (eff.name === 'slide' || eff.name === 'zoom' || eff.name === 'coasterflip' || eff.name === 'iris' || eff.name === 'dissolve') {
```

(The branch body is unchanged: `var fam = (eff.name === 'iris' || eff.name === 'dissolve') ? 'mask' : 'transform';` already yields `transform` for coasterflip, and it returns `front: p`, `scope`, `params`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_coasterflip.js && node --test tests/unit/js/test_transitions.js`
Expected: PASS — coasterflip branch test passes; existing slide/zoom/iris/dissolve transition tests stay green.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_coasterflip.js
git commit -m "feat(transitions): mmTransitionState coasterflip transform descriptor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `index.html` apply — in-canvas (scale + alpha + edge sliver) + element (scale + opacity)

**Files:**
- Modify: `index.html` (in-canvas transform block in `runScriptLoop`; the post-content block for the edge sliver; the element transform block in `applyTransitionNow`)
- Test: `python pytest_runner.py --js` (module-load smoke) + a wiring-count sanity check (on-wall is Task 6).

**Interfaces:**
- Consumes: `mmFlipFactor`, `mmCoasterColor` (Task 2). The transform-family dispatch already exists at both sites (slide/zoom); this adds `coasterflip` cases.
- Produces: no new exports — wires the flip into the live client. The in-canvas mesh affine variable `m` (set via `ctx.setTransform(m.a..m.f)` earlier in the block) is reused to draw the edge sliver under the pure mesh affine.

- [ ] **Step 1: Add the in-canvas pre-content scale (transform block)**

In `index.html`, in the in-canvas transform block, add a `coasterflip` case immediately after the `zoom` case (after the `ctx.globalAlpha = _zf.alpha;` line and its closing `}`, before the block's closing `}`):

```javascript
							} else if (stc.effect.name === 'coasterflip' && typeof mmFlipFactor === 'function') {
								var _ff = mmFlipFactor(stc.effect.front, (stc.effect.params && stc.effect.params.axis) || 'horizontal');
								var _fcx = it.meshGlobal[0] / 2, _fcy = it.meshGlobal[1] / 2;
								ctx.translate(_fcx, _fcy); ctx.scale(_ff.sx, _ff.sy); ctx.translate(-_fcx, -_fcy);
								ctx.globalAlpha = _ff.alpha;
```

- [ ] **Step 2: Add the in-canvas edge sliver (post-content block)**

In `index.html`, in the post-content `if (stc) { ... }` block, immediately after the `if (stc.effect && stc.effect.family === 'mask') { ... }` block closes, add:

```javascript
								if (stc.effect && stc.effect.name === 'coasterflip' && typeof mmFlipFactor === 'function') {
									var _cf = mmFlipFactor(stc.effect.front, (stc.effect.params && stc.effect.params.axis) || 'horizontal');
									if (_cf.edge > 0.01) {
										ctx.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);   // drop the flip scale -> pure mesh affine
										ctx.globalAlpha = _cf.edge;
										ctx.fillStyle = mmCoasterColor(stc.effect.params && stc.effect.params.coaster);
										var _cgw = it.meshGlobal[0], _cgh = it.meshGlobal[1];
										if ((stc.effect.params && stc.effect.params.axis) === 'vertical') {
											var _cbt = _cgh * 0.012; ctx.fillRect(0, _cgh / 2 - _cbt / 2, _cgw, _cbt);
										} else {
											var _cbw = _cgw * 0.012; ctx.fillRect(_cgw / 2 - _cbw / 2, 0, _cbw, _cgh);
										}
										ctx.globalAlpha = 1;
									}
								}
```

- [ ] **Step 3: Add the element-path case (before the zoom catch-all)**

In `index.html`, in the element transform block in `applyTransitionNow`, the `zoom` branch is a `typeof mmZoomFactor` **catch-all** (no name check), so the `coasterflip` case MUST go before it. Insert between the `slide` `if` and the `} else if (typeof mmZoomFactor === 'function') {`:

```javascript
			} else if (st.effect.name === 'coasterflip' && typeof mmFlipFactor === 'function') {
				var ff2 = mmFlipFactor(st.effect.front, (st.effect.params && st.effect.params.axis) || 'horizontal');
				var t4 = 'scaleX(' + ff2.sx + ') scaleY(' + ff2.sy + ')';
				el.style.webkitTransform = t4; el.style.transform = t4; el.style.opacity = '' + ff2.alpha;
```

- [ ] **Step 4: Verify JS suite + wiring count**

Run: `python pytest_runner.py --js`
Expected: PASS — modules still load, all JS unit tests green.

Run: `node -e "var s=require('fs').readFileSync('index.html','utf8'); var n=(s.match(/coasterflip/g)||[]).length; if(n!==3){throw new Error('expected 3 coasterflip wiring sites, got '+n)} console.log('OK 3 sites')"`
Expected: `OK 3 sites` (in-canvas scale + edge sliver + element).

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): wire coasterflip (mesh scale+alpha+edge sliver, element scale)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: "Coaster Flip Demo" playlist tool

**Files:**
- Create: `tools/_make_coaster_demo.py`

**Interfaces:**
- Consumes: the running server on `127.0.0.1:3000` (SockJS). Mirrors `tools/_make_frost_demo.py`.
- Produces: a `Coaster Flip Demo` playlist (two plasma mesh items handing off via `coasterflip`).

- [ ] **Step 1: Write the demo tool**

Create `tools/_make_coaster_demo.py`:

```python
"""Create a 'Coaster Flip Demo' playlist: two plasma mesh items handing off via the
coasterflip transition (axis=horizontal, kraft). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def cf():
    return {"name": "coasterflip",
            "params": {"axis": "horizontal", "coaster": "kraft", "scope": "wall",
                       "duration": 700, "audioFade": True}}

ITEMS = [
    {"id": "cf-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#0a0a0a", "startEffect": None, "endEffect": cf()},
    {"id": "cf-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#0a0a0a", "startEffect": cf(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Coaster Flip Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Coaster Flip Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create the demo + verify**

Run: `python tools/_make_coaster_demo.py`
Expected: `sent Coaster Flip Demo`

Run: `curl -s http://localhost:3000/api/playlists`
Expected: a `Coaster Flip Demo` entry with 2 items whose `endEffect`/`startEffect` are named `coasterflip`.

- [ ] **Step 3: Commit**

```bash
git add tools/_make_coaster_demo.py
git commit -m "feat(tools): Coaster Flip Demo playlist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: iPad-1 on-wall sign-off (manual acceptance — gated on deploy)

**Files:** none.

**Interfaces:** Tasks 1-5 deployed; a server restart (for the `effects.py` catalog → editor shows the params; **requires explicit user authorization**) + fleet reload (mtime-cached static JS picks up on reload); a calibrated mesh group; the `Coaster Flip Demo`.

- [ ] **Step 1:** Request a server restart (for the catalog) — do NOT restart unprompted. Reload the OEB Sign 1 fleet so the iPads fetch the new `transitions.js` + `index.html`.
- [ ] **Step 2:** Assign `Coaster Flip Demo` to the calibrated mesh group and play.
- [ ] **Step 3:** Observe acceptance: item A folds edge-on (content scales to the center line, dims, a cardboard edge sliver shows at the fold), then item B opens back out; the whole wall folds about its center coherently; smooth at wall scale. Try `axis=vertical` (re-save the demo) to confirm the vertical fold.
- [ ] **Step 4:** Record the result; file any tuning follow-ups (edge thickness, dim amount, duration).

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- Catalog entry + params (axis/coaster/scope/duration/audioFade) → Task 1. ✓
- `video_filters` audio-only, single duration → Task 1. ✓
- Render-token regression guard → Task 1 (test_mosaic.py). ✓ (closes the gap the frostcreep final review flagged.)
- Pure helper `mmFlipFactor` + coaster palette → Task 2. ✓
- `mmTransitionState` transform descriptor (`front:p`, family transform) → Task 3. ✓
- Apply: in-canvas scale+alpha + edge sliver, element scale+opacity (sliver mesh-only) → Task 4. ✓
- Wipe path untouched / additive only → confirmed (coasterflip added to the transform branch + dispatch). ✓
- Demo → Task 5; procedural (no sprite). ✓
- On-wall sign-off → Task 6. ✓
- ES5 / 2D-only / no clip/3D/filters → Global Constraints + helper/apply code. ✓

**2. Placeholder scan:** No TBD/TODO/"similar to Task N"/"add error handling" — every code step shows complete code; the demo tool is given in full. ✓

**3. Type consistency:** `mmFlipFactor(front, axis)` → `{sx,sy,alpha,edge}` is identical between Task 2 (definition) and Task 4 (all three call sites). `mmCoasterColor(name)` matches between Task 2 (def) and Task 4 (edge-sliver fill). `effect.front`/`family`/`scope`/`params` keys match between Task 3 (producer) and Task 4 (consumers). The `axis` param key + `coaster` param key are consistent across effects.py (Task 1), the helper (Task 2), and the apply (Task 4). ✓
