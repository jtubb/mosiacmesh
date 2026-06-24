# Scatter Giant Size-Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap the scatter transition's central giant by replacing its hardcoded `1.43 ×` wall-height multiplier with a tunable `giantScale` param (default 0.6) plus a live `?sgscale` on-wall knob, so the giant stops halving the iPad-1 frame rate.

**Architecture:** Add `giantScale` to `ScatterEffect.params` (`effects.py`); `mmDrawScatter` (`js/transitions.js`) resolves the effective scale (live knob → param → 0.6 fallback) and uses `gh = reg.h * gs * c`; `index.html` parses `?sgscale=N` into the existing `window._mmSdbg` knob object. Visuals stay client-side; no new module, no `settings.dat` change.

**Tech Stack:** Python (effects catalog), hand-written ES5 JavaScript (no build step), pytest + `node --test`.

## Global Constraints

- **ES5 only** in `js/transitions.js` and `index.html` inline scripts: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`.
- Canvas primitives only (`drawImage`/`fillRect`/`arc`); this change touches sizing math only — no new canvas calls, no `clip()`/compositing.
- **Default `giantScale` is `0.6`** (down from the old hardcoded `1.43`). Legacy scatter items with no `giantScale` field resolve to `0.6` via a `!= null` fallback — no migration.
- `giantScale = 0` disables the giant (disc + copies still cover).
- Effective-scale resolution order in `mmDrawScatter`: `sd.gscale` (the `?sgscale` knob) → `params.giantScale` → `0.6`. The existing `?sgiant=0` knob still suppresses the giant regardless.
- Do NOT change copies, culling, the backing disc, the 360° spin, or `mmStampSprite`.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Tests: Python via `python pytest_runner.py --unit` (or a single file with `-c tests/pytest.ini`); JS via `node --test tests/unit/js/<file>.js`.

## Reference: current code

`effects.py` `ScatterEffect.params` (the list this task extends):
```python
params = [ParamSpec("sprite", "string", "hop"),
          ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
          ParamSpec("count", "number", 40, minimum=1, maximum=120),
          ParamSpec("fillMs", "number", 2500, minimum=0),
          ParamSpec("drainMs", "number", 2500, minimum=0),
          ParamSpec("audioFade", "boolean", True)]
```

`js/transitions.js` `mmDrawScatter` giant block (current):
```js
    // giant center (?tdbg: ?sgiant=0 drops it to A/B whether it's the cost)
    var gh = reg.h * 1.43 * c;
    if (gh > 2 && !sd.nogiant) {
      _gdrew = mmStampSprite(ctx, vp, img, cx, cy, gh, mmScatterGiantAngle(phase, p));
    }
    if (_dbg) { root._mmScatterStat = { drawn: _drawn, culled: _culled, total: count, giant: !!_gdrew }; }
  }
```
(`sd = root._mmSdbg || {}` is already established at the top of `mmDrawScatter`.)

`index.html` scatter knob parser (current):
```js
		var h = location.href, m, sd = {};
		if ((m = /[?&]scount=(\d+)/.exec(h))) { sd.count = parseInt(m[1], 10); }
		if (/[?&]sgiant=0/.test(h)) { sd.nogiant = true; }
		if (/[?&]snocull=1/.test(h)) { sd.nocull = true; }
		window._mmSdbg = sd;
```

## File Structure

- **Modify** `effects.py` — add the `giantScale` `ParamSpec` to `ScatterEffect`.
- **Modify** `tests/unit/test_effects.py` — assert the new param in the scatter catalog test.
- **Modify** `js/transitions.js` — `mmDrawScatter` resolves `gs` and uses it for `gh`.
- **Modify** `index.html` — parse `?sgscale=N` into `window._mmSdbg.gscale`.
- **Modify** `tests/unit/js/test_scatter.js` — assert giant size scales with `giantScale` (param, default, knob).

---

### Task 1: `giantScale` param in `effects.py`

**Files:**
- Modify: `effects.py` (`ScatterEffect.params`)
- Test: `tests/unit/test_effects.py` (`test_scatter_params`)

**Interfaces:**
- Produces: `scatter` effect catalog entry gains a `giantScale` param — `{type:"number", default:0.6, min:0, max:2}`.

- [ ] **Step 1: Extend the failing test**

In `tests/unit/test_effects.py`, the existing `test_scatter_params` ends with the `audioFade` assertion. Add a `giantScale` assertion right after it:

```python
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True
    assert by["giantScale"]["type"] == "number" and by["giantScale"]["default"] == 0.6
    assert by["giantScale"]["min"] == 0 and by["giantScale"]["max"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_effects.py::test_scatter_params -c tests/pytest.ini -v`
Expected: FAIL with `KeyError: 'giantScale'`.

- [ ] **Step 3: Add the ParamSpec**

In `effects.py`, in `ScatterEffect.params`, add the `giantScale` spec after `audioFade`:

```python
    params = [ParamSpec("sprite", "string", "hop"),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("count", "number", 40, minimum=1, maximum=120),
              ParamSpec("fillMs", "number", 2500, minimum=0),
              ParamSpec("drainMs", "number", 2500, minimum=0),
              ParamSpec("audioFade", "boolean", True),
              ParamSpec("giantScale", "number", 0.6, minimum=0, maximum=2)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: PASS (all scatter/catalog tests, including `test_catalog_has_all_effects`).

- [ ] **Step 5: Commit**

```bash
git add effects.py tests/unit/test_effects.py
git commit -m "feat(scatter): giantScale param (giant peak as fraction of region height)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `mmDrawScatter` resolves `giantScale` + `?sgscale` knob

**Files:**
- Modify: `js/transitions.js` (`mmDrawScatter` giant block)
- Modify: `index.html` (scatter knob parser)
- Test: `tests/unit/js/test_scatter.js` (new giant-size test)

**Interfaces:**
- Consumes: `params.giantScale` (Task 1); `window._mmSdbg` (existing knob object, holds `count`/`nogiant`/`nocull`, now also optional `gscale`).
- Produces: giant height `gh = reg.h * gs * c` where `gs` resolves `sd.gscale` → `params.giantScale` → `0.6`.

- [ ] **Step 1: Write the failing test**

In `tests/unit/js/test_scatter.js`, add this test (the file already imports transitions.js/animations.js/mesh-viewport.js and defines `recCtx`; `g = globalThis`). It spies on the global `mmStampSprite` to read the giant's `globalSize` (the giant is the final stamp each call):

```js
test('mmDrawScatter: giant size scales with giantScale (param, default 0.6, knob)', () => {
  const im = { width: 100, height: 120 };
  function giantSize(params, sd) {
    const calls = [];
    const orig = g.mmStampSprite;
    g.mmStampSprite = function (ctx, vp, img, gx, gy, globalSize, angle) { calls.push(globalSize); return true; };
    if (sd) { g._mmSdbg = sd; }
    try {
      g.mmDrawScatter(recCtx(), params, 'cover', 0.6, 1000, 800, null, 'wall', 7, im, '#000');
    } finally {
      g.mmStampSprite = orig;
      if (sd) { delete g._mmSdbg; }
    }
    return calls[calls.length - 1];           // last stamp == giant
  }
  const base = giantSize({ count: 5, giantScale: 0.6 });
  const half = giantSize({ count: 5, giantScale: 0.3 });
  assert.ok(Math.abs(half / base - 0.5) < 1e-6);   // linear in giantScale
  const dflt = giantSize({ count: 5 });             // missing giantScale -> 0.6, NOT old 1.43
  assert.ok(Math.abs(dflt - base) < 1e-6);
  const knob = giantSize({ count: 5, giantScale: 0.6 }, { gscale: 0.3 });
  assert.ok(Math.abs(knob - half) < 1e-6);          // ?sgscale overrides the param
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: FAIL — `dflt` equals `base` only if the default is 0.6, but the current code uses the hardcoded `1.43`, so `dflt/base` = 1.43/0.6 ≠ 1 (the `dflt` assertion fails); the knob assertion also fails (no `gscale` handling yet).

- [ ] **Step 3: Resolve `gs` in `mmDrawScatter`**

In `js/transitions.js`, replace the giant block:

```js
    // giant center (?tdbg: ?sgiant=0 drops it to A/B whether it's the cost)
    var gh = reg.h * 1.43 * c;
    if (gh > 2 && !sd.nogiant) {
      _gdrew = mmStampSprite(ctx, vp, img, cx, cy, gh, mmScatterGiantAngle(phase, p));
    }
```

with (resolve the effective scale: live knob → param → 0.6 fallback):

```js
    // giant center. giantScale = peak height as a fraction of the region height
    // (?tdbg: ?sgscale=N overrides it live; ?sgiant=0 drops it). Default 0.6;
    // legacy items with no giantScale fall back to 0.6 (not the old 1.43).
    var gs = (sd.gscale != null) ? sd.gscale
           : ((params && params.giantScale != null) ? params.giantScale : 0.6);
    var gh = reg.h * gs * c;
    if (gh > 2 && !sd.nogiant) {
      _gdrew = mmStampSprite(ctx, vp, img, cx, cy, gh, mmScatterGiantAngle(phase, p));
    }
```

- [ ] **Step 4: Add the `?sgscale` knob in `index.html`**

In `index.html`, in the scatter knob parser, add the `sgscale` line (parseFloat, accepts decimals) before `window._mmSdbg = sd;`:

```js
		if ((m = /[?&]scount=(\d+)/.exec(h))) { sd.count = parseInt(m[1], 10); }
		if (/[?&]sgiant=0/.test(h)) { sd.nogiant = true; }
		if (/[?&]snocull=1/.test(h)) { sd.nocull = true; }
		if ((m = /[?&]sgscale=([\d.]+)/.exec(h))) { sd.gscale = parseFloat(m[1]); }
		window._mmSdbg = sd;
```

Also surface it on the perf HUD knobs line (the `tdbg3` `knobs` string) so the active value is visible. Find this line in the `tdbg3` `setInterval`:

```js
			var knobs = (sd.count ? ' count=' + sd.count : '') +
				(sd.nogiant ? ' NOGIANT' : '') + (sd.nocull ? ' NOCULL' : '');
```

and change it to also show `gscale`:

```js
			var knobs = (sd.count ? ' count=' + sd.count : '') +
				(sd.gscale != null ? ' gscale=' + sd.gscale : '') +
				(sd.nogiant ? ' NOGIANT' : '') + (sd.nocull ? ' NOCULL' : '');
```

- [ ] **Step 5: Run tests + parse-checks to verify they pass**

Run: `node --test tests/unit/js/test_scatter.js tests/unit/js/test_mesh_viewport.js`
Expected: PASS — the new giant-size test plus all existing scatter/viewport tests (existing tests assert stamp counts, not giant size, so the 1.43→0.6 default change leaves them green; the giant is still drawn).

Run: `node --check js/transitions.js`
Expected: parse OK.

Verify the `index.html` inline JS still parses (extract inline `<script>` blocks and check):
```bash
python - <<'PY'
import re
html = open('index.html', encoding='utf-8').read()
blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
open('_inline.js','w',encoding='utf-8').write('\n;\n'.join(blocks))
PY
node --check _inline.js && echo "index.html inline JS parse OK" && rm -f _inline.js
```
Expected: `index.html inline JS parse OK`.

- [ ] **Step 6: Commit**

```bash
git add js/transitions.js index.html tests/unit/js/test_scatter.js
git commit -m "feat(scatter): cap giant via giantScale + ?sgscale live knob

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: iPad-1 on-wall tuning + sign-off

**Files:** none (manual acceptance on the physical wall; requires a deploy the **user must authorize**).

**Interfaces:** Consumes Tasks 1–2 deployed; uses the `?tdbg` perf heartbeat (fps) already in the server log.

- [ ] **Step 1: Deploy** — on user authorization, restart the server and reload screen3 fresh (`killall MobileSafari` then `uiopen 'http://192.168.1.60:3000/?tdbg'`) so it picks up the new `transitions.js` + `index.html`.

- [ ] **Step 2: Baseline** — with the new default (`giantScale 0.6`), run the Scatter Demo on `OEB Sign 1`; read the fps from the `hb` CLIENTLOG rows for screen3 in `mm_server.log` during a scatter transition (`sc.giant: true`).

- [ ] **Step 3: Dial the knob** — sweep `?tdbg&sgscale=0.5`, `0.4`, `0.3` (kill+reopen Safari each time so the query reloads), recording fps at each. Find the value that recovers frame rate (target: close to the ~30 fps measured with the giant off) while keeping an acceptable giant size on the wall.

- [ ] **Step 4: Record the chosen value.** If the winning value differs from `0.6`, update the default in `effects.py` (`ParamSpec("giantScale", "number", <value>, ...)`) and the `0.6` fallback in `mmDrawScatter` to match, re-run `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini` + `node --test tests/unit/js/test_scatter.js` (adjust the test's expected default), and commit. Otherwise note that 0.6 stands.

- [ ] **Step 5: Sign-off** — confirm scatter holds an acceptable frame rate on the wall with the chosen `giantScale`; record the outcome and (if good) finish via `superpowers:finishing-a-development-branch`.

---

## Plan Self-Review

**1. Spec coverage:**
- `giantScale` param (number, 0.6, 0–2), editor-visible → Task 1. ✓
- `gh = reg.h * giantScale * c` → Task 2 Step 3. ✓
- Resolution order knob→param→0.6; legacy fallback to 0.6; `?sgiant=0` still wins → Task 2 Step 3 (the `!sd.nogiant` guard is preserved). ✓
- `?sgscale=N` live knob + HUD surfacing → Task 2 Step 4. ✓
- `giantScale = 0` disables giant → falls out of `gh = …*0*c = 0`, `gh > 2` false → not drawn. ✓ (no dedicated test; covered by the linear-scaling assertion and the existing `gh > 2` guard).
- Testing: effects catalog (Python), giant-size scaling (Node), on-wall fps → Tasks 1–3. ✓
- Default-is-starting-point / dial-on-wall → Task 3 Steps 3–4. ✓
- Out of scope (copies/culling/disc/spin/mmStampSprite untouched) → only the giant block + param + knob change. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output. ✓

**3. Type consistency:** `giantScale` (param, number) and `sd.gscale` (knob, parseFloat) are distinct names by design (param vs knob-override), unified in `mmDrawScatter`'s `gs`. `?sgscale` regex `[\d.]+` + `parseFloat` matches the decimal knob. The `sd`/`window._mmSdbg` object name is consistent across `index.html` and `mmDrawScatter`. ✓
