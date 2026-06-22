# Transition Effects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fade and directional wipe transitions at start and/or end of any media item with a configurable duration (client-side, all media types), plus baked audio fades for videos.

**Architecture:** Visual transitions are applied **client-side** by the ES5 display client (opacity ramp for fade; an opaque cover `<div>` slid via `-webkit-transform` for wipe), timed from the shared clock so every panel moves in lockstep. Audio fades stay **baked** (ffmpeg `afade`) on per-screen-rendered video, because iOS Safari makes `video.volume` read-only. `startEffect`/`endEffect` (`{name, params}`) carry the config; the render token drops visual bits and keeps only an audio-fade signature so visual edits never re-render.

**Tech Stack:** Python (`effects.py`, `mosaicmesh/render.py`), ES5 JS (`index.html`, new `js/transitions.js`), Alpine/ESM admin (`js/timeline/modals/playlist-editor.js`), node `--test`, pytest (`tests/pytest.ini`), Playwright.

**Spec:** `docs/superpowers/specs/2026-06-22-transition-effects-design.md`

**Test runners:** `python pytest_runner.py --unit` (pass `-c tests/pytest.ini` for single files), `python pytest_runner.py --js` (or `node --test tests/unit/js/<file>.js`).

---

## File structure

- `effects.py` (root, MODIFY) — schema registry; `fade`/`wipe` declare params; `video_filters` returns audio-only (`afade`) when `audioFade` on; add `"boolean"` ParamSpec type; remove `AudioFadeEffect`.
- `mosaicmesh/render.py` (MODIFY) — `_resolve_effect_filters` drops video fragments (audio-only); `render_token`/`compute_render_token` item tuple uses an audio-fade signature instead of raw `startEffect`/`endEffect`.
- `js/transitions.js` (CREATE, ES5 dual-use like `js/animations.js`) — pure `mmTransitionState(...)` + DOM helpers `mmApplyFade`, `mmMakeWipeCover`, exported on `window`/`globalThis`.
- `index.html` (MODIFY) — `<script src="js/transitions.js">`; `showItem` records `playback.currentEl` + mounts a wipe cover when needed; a clock-driven `transitionTick` applies fade/wipe each frame while a transition window is active.
- `js/timeline/modals/playlist-editor.js` (MODIFY) — Start/End effect controls (dropdown + params) in the item inspector, driven by a cached `/api/effects` catalog.
- `js/timeline/store.js` (MODIFY) — fetch + cache the effect catalog on load (`store.effectCatalog`).
- Tests: `tests/unit/test_effects.py` (MODIFY/CREATE), `tests/unit/js/test_transitions.js` (CREATE), render-token assertions in `tests/unit/test_mosaic.py` (MODIFY), Playwright spec `tests/e2e/test-transition-editor.spec.js` (CREATE, light).

---

## Task 1: effects.py — schema (fade/wipe + boolean param) and audio-only baking

**Files:**
- Modify: `effects.py`
- Test: `tests/unit/test_effects.py`

- [ ] **Step 1: Write the failing test**

Create/extend `tests/unit/test_effects.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import effects


def test_catalog_has_fade_and_wipe_only():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe"}          # audiofade folded into the audioFade toggle


def test_fade_params_include_duration_and_audioFade_boolean():
    fade = next(e for e in effects.effect_catalog() if e["name"] == "fade")
    by_key = {p["key"]: p for p in fade["params"]}
    assert by_key["duration"]["type"] == "number" and by_key["duration"]["default"] == 600
    assert by_key["audioFade"]["type"] == "boolean" and by_key["audioFade"]["default"] is True


def test_wipe_params_include_direction_scope_duration_audioFade():
    wipe = next(e for e in effects.effect_catalog() if e["name"] == "wipe")
    by_key = {p["key"]: p for p in wipe["params"]}
    assert by_key["direction"]["choices"] == ["left", "right", "up", "down"]
    assert by_key["scope"]["choices"] == ["screen", "wall"]
    assert by_key["duration"]["type"] == "number"
    assert by_key["audioFade"]["type"] == "boolean"


def test_fade_bakes_audio_only_when_audioFade_on():
    fade = effects.get_effect("fade")
    ctx = {"duration_ms": 5000}
    v, a = fade.video_filters("start", fade.resolve({"duration": 600, "audioFade": True}), ctx)
    assert v == []                                  # visual is client-side, never baked
    assert a == ["afade=t=in:st=0:d=0.6"]
    v2, a2 = fade.video_filters("end", fade.resolve({"duration": 600, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.4:d=0.6"]


def test_fade_bakes_nothing_when_audioFade_off():
    fade = effects.get_effect("fade")
    v, a = fade.video_filters("start", fade.resolve({"duration": 600, "audioFade": False}), {"duration_ms": 5000})
    assert v == [] and a == []


def test_wipe_bakes_audio_only_when_audioFade_on():
    wipe = effects.get_effect("wipe")
    v, a = wipe.video_filters("start", wipe.resolve({"duration": 600, "audioFade": True}), {"duration_ms": 5000})
    assert v == [] and a == ["afade=t=in:st=0:d=0.6"]


def test_wipe_bakes_nothing_when_audioFade_off():
    wipe = effects.get_effect("wipe")
    v, a = wipe.video_filters("start", wipe.resolve({"duration": 600, "audioFade": False}), {"duration_ms": 5000})
    assert v == [] and a == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: FAIL (catalog has `audiofade`; `fade` has no `audioFade` param; fade bakes a video `fade=` fragment).

- [ ] **Step 3: Implement**

In `effects.py`: add a `"boolean"` param type (no schema change needed beyond accepting it — `ParamSpec.to_dict` already emits `type`/`default`). Replace the three effect classes with two, audio-only:

```python
def _afade(role, params, ctx):
    """afade fragment list when audioFade is on for this role, else []."""
    if not params.get("audioFade"):
        return []
    st, d = _fade_st_d(role, params, ctx)
    typ = "in" if role == "start" else "out"
    return ["afade=t=" + typ + ":st=" + st + ":d=" + d]


@register
class FadeEffect(Effect):
    name = "fade"
    label = "Fade"
    params = [ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual fade is client-side


@register
class WipeEffect(Effect):
    name = "wipe"
    label = "Wipe"
    params = [ParamSpec("direction", "choice", "left", choices=["left", "right", "up", "down"]),
              ParamSpec("scope", "choice", "screen", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual wipe is client-side
```

Delete the `AudioFadeEffect` class. Keep `_fade_st_d` and `_fmt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add effects.py tests/unit/test_effects.py
git commit -m "feat(effects): fade/wipe schema; bake audio-only (visual goes client-side)"
```

---

## Task 2: render.py — audio-only hook + render-token narrowed to audio signature

**Files:**
- Modify: `mosaicmesh/render.py` (`_resolve_effect_filters` ~504-519; item tuple in the token builder ~366-368)
- Test: `tests/unit/test_mosaic.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mosaic.py` (it already imports `server`/render helpers; mirror the existing import style at the top of that file):

```python
def test_resolve_effect_filters_returns_audio_only(fresh_settings_or_equivalent=None):
    from mosaicmesh import render as R
    from mosaicmesh.state import MediaElement, PlayMode
    me = MediaElement(); me.id = "a"; me.file = "v.mp4"; me.duration = 5000
    me.playmode = PlayMode.SEGMENT
    me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": True}}
    me.endEffect = None
    vfs, afs = R._resolve_effect_filters(me, 5000, 1280, 720)
    assert vfs == []                              # no baked video filter anymore
    assert afs == ["afade=t=in:st=0:d=0.6"]


def test_resolve_effect_filters_audio_off_is_empty():
    from mosaicmesh import render as R
    from mosaicmesh.state import MediaElement, PlayMode
    me = MediaElement(); me.id = "a"; me.file = "v.mp4"; me.duration = 5000
    me.playmode = PlayMode.SEGMENT
    me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": False}}
    vfs, afs = R._resolve_effect_filters(me, 5000, 1280, 720)
    assert vfs == [] and afs == []


def test_render_token_uses_audio_signature_not_visual_params():
    """Changing a visual-only param (direction/scope, or duration with audioFade off)
    must NOT change the token; toggling audioFade or its duration must."""
    from mosaicmesh import render as R
    base = _two_video_items_with_effect({"name": "fade", "params": {"duration": 600, "audioFade": True}})
    # visual-only change: switch fade->wipe + add direction, keep same audio sig
    visual = _two_video_items_with_effect({"name": "wipe", "params": {"direction": "up", "duration": 600, "audioFade": True}})
    audio_off = _two_video_items_with_effect({"name": "fade", "params": {"duration": 600, "audioFade": False}})
    t_base = R.render_token(base, "G")
    t_visual = R.render_token(visual, "G")
    t_audio_off = R.render_token(audio_off, "G")
    assert t_base == t_visual, "visual-only change must not invalidate the render"
    assert t_base != t_audio_off, "audio-fade change must invalidate the render"
```

Add the helper near the top of the test module (build two SEGMENT video `MediaElement`s with the given effect on item 0; reuse the file's existing display/group fixture pattern — match how other `render_token`/`compute_render_token` tests in this file construct `media_elements` and a calibrated `"G"` display). If the file lacks such a fixture, construct the display inline exactly as the nearest existing render-token test does.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_mosaic.py -c tests/pytest.ini -k "effect or token" -v`
Expected: FAIL (`_resolve_effect_filters` still returns the old video fragment via the audio-only effect is fine, but the token test fails because the token tuple hashes raw `startEffect`/`endEffect`, so the visual-only change DOES change the token).

- [ ] **Step 3: Implement**

`_resolve_effect_filters` needs no change (it already concatenates whatever `video_filters` returns, which is now audio-only) — but confirm it still works. The token change: in the item-tuple builder (render.py ~366-368), replace the raw effect fields with an audio signature. Add a helper and use it:

```python
def _audio_fade_sig(field):
    """Token contribution for an effect field: ('role-irrelevant', duration) only when
    audioFade is on; None otherwise. Visual params are deliberately excluded so visual
    edits don't invalidate the render."""
    spec = _normalize_effect(field)
    if not spec:
        return None
    p = (spec.get("params") or {})
    if not p.get("audioFade", True if spec.get("name") in ("fade", "wipe") else False):
        return None
    return ("afade", p.get("duration", 600))
```

Then change the tuple (render.py ~366-368) from:

```python
        items.append((me.id, me.file, me.duration, pm,
                      getattr(me, "backgroundColor", "#000000"),
                      getattr(me, "startEffect", None), getattr(me, "endEffect", None)))
```

to:

```python
        items.append((me.id, me.file, me.duration, pm,
                      getattr(me, "backgroundColor", "#000000"),
                      _audio_fade_sig(getattr(me, "startEffect", None)),
                      _audio_fade_sig(getattr(me, "endEffect", None))))
```

(Note the default: a bare-name `fade`/`wipe` with no params defaults `audioFade` True per the schema; the helper mirrors that so legacy data still bakes audio. A legacy bare `"audiofade"` name → not in `("fade","wipe")` → no signature; its audio is handled by normalization in Task 7. Keep `_normalize_effect` as-is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_mosaic.py -c tests/pytest.ini -k "effect or token" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_mosaic.py
git commit -m "feat(render): effects bake audio only; token keyed on audio-fade signature"
```

---

## Task 3: js/transitions.js — pure mmTransitionState

**Files:**
- Create: `js/transitions.js`
- Test: `tests/unit/js/test_transitions.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_transitions.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const S = globalThis.mmTransitionState;

const FADE = { name: 'fade', params: { duration: 1000, audioFade: true } };
const WIPE_SCREEN = { name: 'wipe', params: { direction: 'left', scope: 'screen', duration: 1000 } };
const WIPE_WALL = { name: 'wipe', params: { direction: 'left', scope: 'wall', duration: 1000 } };

test('fade-in: opacity ramps 0->1 over startDur', () => {
  assert.equal(S(FADE, null, 0, 10000, null).opacity, 0);
  assert.ok(Math.abs(S(FADE, null, 500, 10000, null).opacity - 0.5) < 1e-9);
  assert.equal(S(FADE, null, 1000, 10000, null).role, 'none');   // past the window
});

test('fade-out: opacity ramps 1->0 over endDur before the end', () => {
  const st = S(null, FADE, 9500, 10000, null);    // 500ms into the 1000ms out-window
  assert.equal(st.role, 'out');
  assert.ok(Math.abs(st.opacity - 0.5) < 1e-9);
  assert.ok(Math.abs(S(null, FADE, 10000, 10000, null).opacity - 0) < 1e-9);
});

test('no transition mid-item -> role none, opacity 1', () => {
  const st = S(FADE, FADE, 5000, 10000, null);
  assert.equal(st.role, 'none');
  assert.equal(st.opacity, 1);
  assert.equal(st.wipe, null);
});

test('wipe per-screen: reveal == progress, opacity 1', () => {
  const st = S(WIPE_SCREEN, null, 250, 10000, null);
  assert.equal(st.opacity, 1);
  assert.ok(Math.abs(st.wipe.reveal - 0.25) < 1e-9);
  assert.equal(st.wipe.direction, 'left');
});

test('wipe wall (left): a panel reveals over its own [a,b] sub-window', () => {
  // panel spans global x [0.5, 0.667]; front F=offset/dur. At F=0.5 reveal=0; at F=0.667 reveal=1.
  const rect = { x: 0.5, y: 0, w: 1 / 6, h: 1 };
  assert.equal(S(WIPE_WALL, null, 400, 10000, rect).wipe.reveal, 0);          // F=0.4 < a -> 0
  assert.ok(Math.abs(S(WIPE_WALL, null, 5000, 10000, rect).wipe.reveal - 0.5) < 1e-3); // mid-panel
  assert.equal(S(WIPE_WALL, null, 7000, 10000, rect).wipe.reveal, 1);          // F=0.7 > b -> 1
});

test('wipe wall falls back to per-screen when rect is null', () => {
  assert.ok(Math.abs(S(WIPE_WALL, null, 250, 10000, null).wipe.reveal - 0.25) < 1e-9);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_transitions.js`
Expected: FAIL (`mmTransitionState` undefined).

- [ ] **Step 3: Implement**

Create `js/transitions.js` (ES5, dual-use IIFE like `js/animations.js`):

```javascript
/* js/transitions.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Pure transition math
 * + DOM apply helpers. mmTransitionState is a pure function of the shared-clock
 * offset, so every panel computes the same state and transitions in lockstep. */
(function (root) {
  function _dur(eff) { return (eff && eff.params && +eff.params.duration) || 0; }

  // Per-screen reveal for a wall-spanning wipe: the global front F sweeps 0..1
  // along the wipe axis; a panel spanning [a,b] reveals (F-a)/(b-a) clamped.
  // right/down invert the axis. rect = {x,y,w,h} normalized global bbox.
  function _wallReveal(F, direction, rect) {
    var a, b;
    if (direction === 'left' || direction === 'right') { a = rect.x; b = rect.x + rect.w; }
    else { a = rect.y; b = rect.y + rect.h; }
    if (direction === 'right' || direction === 'down') {
      var na = 1 - b, nb = 1 - a; a = na; b = nb;     // sweep from the far edge
    }
    if (b <= a) { return F >= b ? 1 : 0; }
    var r = (F - a) / (b - a);
    return r < 0 ? 0 : (r > 1 ? 1 : r);
  }

  // startEff/endEff: {name,params}|null. offsetMs, durationMs in ms. rect: normalized
  // global bbox for wall wipes, or null. Returns {role,opacity,wipe}.
  function mmTransitionState(startEff, endEff, offsetMs, durationMs, rect) {
    var sd = _dur(startEff), ed = _dur(endEff), role = 'none', eff = null, p = 1;
    if (startEff && sd > 0 && offsetMs < sd) { role = 'in'; eff = startEff; p = offsetMs / sd; }
    else if (endEff && ed > 0 && offsetMs > durationMs - ed) {
      role = 'out'; eff = endEff; p = (durationMs - offsetMs) / ed;
    }
    if (p < 0) { p = 0; } if (p > 1) { p = 1; }
    if (role === 'none') { return { role: 'none', opacity: 1, wipe: null }; }
    if (eff.name === 'wipe') {
      var dir = (eff.params && eff.params.direction) || 'left';
      var scope = (eff.params && eff.params.scope) || 'screen';
      // overall front position 0..1: 'in' grows the reveal; 'out' shrinks it back.
      var F = role === 'in' ? p : p;     // p already 1->0 on out, so reveal follows p
      var reveal = (scope === 'wall' && rect) ? _wallReveal(F, dir, rect) : p;
      return { role: role, opacity: 1, wipe: { reveal: reveal, direction: dir } };
    }
    return { role: role, opacity: p, wipe: null };   // fade
  }

  root.mmTransitionState = mmTransitionState;
  root._mmWallReveal = _wallReveal;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/unit/js/test_transitions.js`
Expected: PASS (6).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_transitions.js
git commit -m "feat(transitions): pure mmTransitionState (fade/wipe, per-screen + wall)"
```

---

## Task 4: js/transitions.js — DOM apply helpers

**Files:**
- Modify: `js/transitions.js`
- Test: `tests/unit/js/test_transitions.js`

- [ ] **Step 1: Write the failing test** (append)

```javascript
test('mmApplyTransition sets opacity for fade', () => {
  const el = { style: {} };
  globalThis.mmApplyTransition(el, null, S(FADE, null, 500, 10000, null));
  assert.equal(el.style.opacity, '0.5');
});

test('mmApplyTransition slides the cover for a wipe (reveal 0.25, left)', () => {
  const el = { style: {} };
  const cover = { style: {} };
  globalThis.mmApplyTransition(el, cover, S(WIPE_SCREEN, null, 250, 10000, null));
  assert.equal(el.style.opacity, '1');
  // left wipe revealing 25%: cover translated left by 25% of its width
  assert.ok(/translate/.test(cover.style.webkitTransform || cover.style.transform));
});
```

- [ ] **Step 2: Run** `node --test tests/unit/js/test_transitions.js` → FAIL (`mmApplyTransition` undefined).

- [ ] **Step 3: Implement** (append helpers + export inside the IIFE, before the `root.` assignments)

```javascript
  // Apply a transition state to a mounted element. `cover` is an opaque overlay
  // div (item background color) sized to the element, used for wipes; null for
  // fade. ES5 / Safari-5.1: opacity + -webkit-transform only (no clip-path).
  function mmApplyTransition(el, cover, st) {
    if (!el) { return; }
    if (st.wipe && cover) {
      el.style.opacity = '1';
      cover.style.display = 'block';
      var r = st.wipe.reveal, d = st.wipe.direction, tx = 0, ty = 0;
      // reveal r of the element by sliding the opaque cover off in `direction`.
      if (d === 'left') { tx = -r * 100; }
      else if (d === 'right') { tx = r * 100; }
      else if (d === 'up') { ty = -r * 100; }
      else { ty = r * 100; }
      var t = 'translate(' + tx + '%,' + ty + '%)';
      cover.style.webkitTransform = t; cover.style.transform = t;
      if (r >= 1) { cover.style.display = 'none'; }      // fully revealed
    } else {
      if (cover) { cover.style.display = 'none'; }
      el.style.opacity = '' + st.opacity;
    }
  }
```

Add `root.mmApplyTransition = mmApplyTransition;`.

- [ ] **Step 4: Run** `node --test tests/unit/js/test_transitions.js` → PASS (8).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_transitions.js
git commit -m "feat(transitions): mmApplyTransition DOM helper (opacity / cover slide)"
```

---

## Task 5: index.html — wire the client transition engine

**Files:**
- Modify: `index.html` (add `<script src="js/transitions.js">` near the `js/animations.js` script tag; `showItem` ~685; `runScriptLoop` ~514; add a `transitionTick`)

No new unit test (DOM/integration; covered by the pure helper tests + the e2e smoke in Task 8). This task is wiring; verify by manual reload.

- [ ] **Step 1: Load the module**

Add next to the existing `<script src="js/animations.js"></script>`:

```html
<script src="js/transitions.js"></script>
```

- [ ] **Step 2: Record the mounted element + build a wipe cover in `showItem`**

In `showItem` (index.html ~685), after each branch mounts its element, set `playback.currentEl` and create/clear the cover. Concretely:
- SCRIPT branch: after `document.getElementById('canvas').appendChild(cnv);` add `playback.currentEl = cnv;`
- video branch: after `playback.video = v;` add `playback.currentEl = v;`
- image branch: change the `else` to capture the img:
  ```javascript
  } else {
      var im = document.createElement('img');
      im.src = item.file; im.style.maxWidth = '100%'; im.style.maxHeight = '100%';
      $('#canvas').empty(); document.getElementById('canvas').appendChild(im);
      playback.currentEl = im;
  }
  ```
- After the branch, build a reusable cover div over `#canvas` when either effect is a wipe:
  ```javascript
  playback.transCover = ensureCover(item);     // see Step 3
  applyTransitionNow(i, offsetMs || 0);         // set initial state immediately
  ```

Add `currentEl: null, transCover: null` to the `playback` initializer (index.html ~106).

- [ ] **Step 3: Cover helper + the clock-driven tick**

Add near `renderPlayback`:

```javascript
function ensureCover(item) {
    var s = item.startEffect, e = item.endEffect;
    var needs = (s && s.name === 'wipe') || (e && e.name === 'wipe');
    var host = document.getElementById('canvas');
    var c = document.getElementById('mmTransCover');
    if (!needs) { if (c) { c.style.display = 'none'; } return null; }
    if (!c) {
        c = document.createElement('div'); c.id = 'mmTransCover';
        c.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;z-index:50;pointer-events:none;';
        host.appendChild(c);
    }
    c.style.background = item.backgroundColor || '#000000';
    c.style.display = 'block';
    return c;
}

// Apply the transition for the item currently shown, from the shared clock.
function applyTransitionNow(i, offsetMs) {
    var item = playback.items[i];
    if (!item || (!item.startEffect && !item.endEffect) || typeof mmTransitionState !== 'function') {
        if (playback.currentEl) { playback.currentEl.style.opacity = '1'; }
        return;
    }
    // wall-wipe needs this screen's normalized global rect (from the mesh payload).
    var rect = null;
    if (item.meshCells && item.meshQuad) {
        // this screen's own quad bbox in normalized global space
        var q = item.meshQuad, xs = [], ys = [], k;
        for (k = 0; k < q.length; k++) { xs.push(q[k][0]); ys.push(q[k][1]); }
        rect = { x: Math.min.apply(null, xs), y: Math.min.apply(null, ys),
                 w: Math.max.apply(null, xs) - Math.min.apply(null, xs),
                 h: Math.max.apply(null, ys) - Math.min.apply(null, ys) };
    }
    var dur = item.duration || 0;
    var st = mmTransitionState(item.startEffect || null, item.endEffect || null, offsetMs, dur, rect);
    mmApplyTransition(playback.currentEl, playback.transCover, st);
}

function transitionTick() {
    if (!playback.active || playback.paused) { return; }
    var durations = [], i;
    for (i = 0; i < playback.items.length; i++) { durations.push(playback.items[i].duration); }
    var pos = playlistIndex(GoTime.now() - playback.startEpoch, durations, playback.loop);
    if (pos !== null && pos.index === playback.shownIndex) { applyTransitionNow(pos.index, pos.offsetMs); }
    playback.transRaf = _raf(transitionTick);
}
```

Start `transitionTick()` once in the PLAY handler (next to where playback starts), and guard with `if (playback.transRaf) { _caf(playback.transRaf); }` in `stopPlayback`/`clearScript`. Add `transRaf: null` to the `playback` initializer.

- [ ] **Step 4: Manual verify**

Restart server (`index.html` serves fresh), reload one client with `?tdbg`, play a playlist with a `fade`/`wipe` start+end on an image and a SCRIPT item. Confirm: image fades in/out; SCRIPT fades; wipe slides. (Automated coverage is the pure helpers + Task 8 e2e.)

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): clock-driven transition engine (fade/wipe) for all media types"
```

---

## Task 6: store + editor — fetch catalog and add effect controls

**Files:**
- Modify: `js/timeline/store.js` (add `effectCatalog` fetch on load)
- Modify: `js/timeline/modals/playlist-editor.js` (item inspector ~160-225)
- Test: covered by Task 8 Playwright

- [ ] **Step 1: Fetch the catalog in the store**

In `js/timeline/store.js`, where other initial GETs happen, add:

```javascript
this.effectCatalog = [];
try { const r = await fetch('/api/effects'); if (r.ok) this.effectCatalog = (await r.json()).effects || []; } catch (e) {}
```

Match the file's existing async-init pattern (place beside the `/api/displays` / `/api/media` hydration).

- [ ] **Step 2: Add Start/End effect controls to the inspector**

In `playlist-editor.js`, after the `backgroundColor` input block (~221), add a helper that renders one effect control and append two (start, end). The control: a `<select>` (`None` + one option per catalog effect by `label`), and when an effect is chosen, its params from the catalog (`number` → number input; `choice` → `<select>`; `boolean` → checkbox, shown only when the item is a video). Edits write `it.startEffect`/`it.endEffect = {name, params}` or delete it for `None`. Use the catalog from `store.effectCatalog` (passed into the editor or read off the store). Reference the existing playmode/scriptSpan `<select>` wiring in the same file for the exact DOM idiom (createElement, option loop, `addEventListener('change', ...)`).

Show the `audioFade` checkbox only when `!isAnim(it) && isVideo(it.file)` (add an `isVideo` helper mirroring server `_VIDEO_EXTS`: `/\.(mp4|m4v|mov|webm|ogv)$/i`).

- [ ] **Step 3: Manual verify**

Reload `admin.html`, open the playlist editor, select an item: Start/End effect dropdowns show `Fade`/`Wipe`; choosing `Wipe` reveals direction + scope; a video item shows the audio-fade checkbox; selections persist in the item and survive Save.

- [ ] **Step 4: Commit**

```bash
git add js/timeline/store.js js/timeline/modals/playlist-editor.js
git commit -m "feat(editor): start/end transition controls driven by /api/effects"
```

---

## Task 7: legacy normalization + cross-type integration test

**Files:**
- Modify: `mosaicmesh/render.py` `_normalize_effect` (tolerate legacy `audiofade`)
- Test: `tests/unit/test_playlists.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_playlists.py` (mirror its existing playlist round-trip test style):

```python
def test_legacy_audiofade_normalizes_to_no_visual_audio_on():
    from mosaicmesh import render as R
    assert R._normalize_effect("audiofade") == {"name": "fade", "params": {"audioFade": True}}
    assert R._normalize_effect({"name": "fade", "params": {"duration": 800}})["name"] == "fade"
    assert R._normalize_effect(None) is None
    assert R._normalize_effect("") is None
```

- [ ] **Step 2: Run** `python -m pytest tests/unit/test_playlists.py -c tests/pytest.ini -k legacy -v` → FAIL (bare `"audiofade"` normalizes to `{"name":"audiofade","params":{}}`).

- [ ] **Step 3: Implement** — extend `_normalize_effect` (render.py ~493):

```python
def _normalize_effect(field):
    """Tolerate an effect field as {name, params} | bare-string name | None.
    Legacy 'audiofade' (now folded into the audioFade toggle) -> a no-visual
    fade with audio on."""
    if not field:
        return None
    if isinstance(field, str):
        if field == "audiofade":
            return {"name": "fade", "params": {"audioFade": True}}
        return {"name": field, "params": {}}
    if isinstance(field, dict) and field.get("name"):
        return field
    return None
```

- [ ] **Step 4: Run** the test → PASS. Then run `python pytest_runner.py --unit` (full) → all green.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_playlists.py
git commit -m "feat(effects): normalize legacy audiofade into the audioFade toggle"
```

---

## Task 8: full suite + light Playwright editor smoke

**Files:**
- Create: `tests/e2e/test-transition-editor.spec.js`

- [ ] **Step 1: Write the Playwright smoke** (follow `tests/e2e/run.js` + an existing spec's structure; needs the dev server on `MM_BASE_URL`)

Assert: open the playlist editor, select an item, the Start-effect `<select>` contains `Fade` and `Wipe`; selecting `Wipe` shows `direction` and `scope` controls; selecting `Fade` on a video item shows the `audioFade` checkbox; the chosen effect is written to the item (read back via the store/DOM). Clean up its own `__e2e_`-prefixed playlist (mirror existing specs).

- [ ] **Step 2: Run the full suites**

```bash
python pytest_runner.py --unit
python pytest_runner.py --js
python pytest_runner.py --e2e   # if a dev server is available
```

Expected: all green (unit incl. test_effects/test_mosaic/test_playlists; js incl. test_transitions; e2e incl. the editor smoke).

- [ ] **Step 3: On-wall manual sign-off**

Server restart (for `effects.py`/`render.py`), reload fleet + admin. Verify on `OEB Sign 1`: fade in/out on image, SCRIPT, and video; per-screen wipe; wall-spanning wipe sweeping across panels; a video with `audioFade` re-renders and its audio fades; toggling a visual-only param does NOT trigger a re-render (instant).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test-transition-editor.spec.js
git commit -m "test(e2e): transition-editor smoke; full suite green"
```

---

## Self-review notes

- **Spec coverage:** data model (T1 params, T7 legacy), effects.py audio-only (T1), render hook + token (T2), client engine fade/wipe all media types (T3-T5), per-screen + wall wipe (T3 `_wallReveal`, T5 rect from `meshQuad`), audio baked video-only (T1/T2), editor controls (T6), sync (clock-driven `transitionTick`, T5), testing (T1-T8). FULL-video audio fade is a documented non-goal (no task) — correct.
- **Type consistency:** `mmTransitionState(startEff,endEff,offsetMs,durationMs,rect)` and `mmApplyTransition(el,cover,st)` with `st={role,opacity,wipe:{reveal,direction}}` used identically in T3/T4/T5. `_audio_fade_sig`/`_normalize_effect` names consistent T2/T7. `playback.currentEl/transCover/transRaf` introduced in T5 initializer and used there.
- **Durations:** `mmTransitionState` works in ms; client passes `item.duration` (ms in the playback payload) and effect `params.duration` (ms). Editor stores effect `duration` in ms (param default 600), distinct from the item's duration field.
