# Coordinated Animation Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SCRIPT animations a server-minted per-run seed so they can be randomized while staying bit-identical across every screen in a group, with `plasma` as the first consumer.

**Architecture:** The server mints a 32-bit `Display.playSeed` once per fresh coordinated-start (`_begin_prepare`), reuses it on resume, and includes it in every PREPARE/PLAY/late-join payload for that run. The client stores `playback.seed`, derives a per-item seed, and passes it as a 6th `draw(ctx, tMs, w, h, nowMs, seed)` argument. A shared **xorshift32** PRNG (`MM_RNG`) + `mmDeriveSeed` live in `js/animations.js` — bitwise-only so output is identical on Safari 5.1 / Node / modern V8.

**Tech Stack:** Python (server, `random.getrandbits`), ES5 + Canvas2D (`index.html` + `js/animations.js`), Node 20 `node --test` + `python pytest_runner.py --js/--unit`.

**Spec:** `docs/superpowers/specs/2026-06-18-animation-seed-design.md`

---

## Background the implementer needs

**Why bitwise-only PRNG (the load-bearing constraint).** The output must be bit-identical on a 1st-gen iPad (Safari 5.1), Node, and modern browsers, or the "coordinated" property breaks. `Math.imul` does NOT exist on Safari 5.1. A multiplication LCG like `s*1103515245` exceeds 2^53 and loses exactly the low bits a mask keeps → engine-divergent. JS bitwise ops (`^`, `<<`, `>>>`) are spec-defined exact 32-bit (`ToUint32`/`ToInt32`) on every engine. So `MM_RNG` uses **xorshift32** (no multiply). `mmDeriveSeed`'s single `(idx+1)*0x9E3779B1` is safe only because `idx` is a tiny playlist index.

**Seed lifecycle (verified against the code).** A fresh `PLAY` (display.action != PAUSE) routes through `_begin_prepare(display_id)` (render.py ~1325) → PREPARE → READY → `_release_group` → `_start_group_playback` (render.py ~1256) → GO/PLAY. A resume (display.action == PAUSE) calls `_start_group_playback` directly. So: **mint in `_begin_prepare` only; `_start_group_playback` reads the already-set value** (fresh: minted moments earlier; resume: carried over). Never mint in `_start_group_playback`.

**The 5 payload sites that carry `startEpoch`/`prepareId` today and must also carry `seed`:**
1. `_begin_prepare` inline PREPARE — `mosaicmesh/render.py:1342-1345`
2. `_prepare_unsynced_clients` late PREPARE — `mosaicmesh/render.py:1386-1390`
3. `_broadcast_per_client_play` PLAY — `mosaicmesh/render.py:1206-1211`
4. `_start_group_playback` group-wide PLAY (non-renderable branch) — `mosaicmesh/render.py:1271-1274`
5. `sync_new_client_to_group` late-join PLAY — `mosaicmesh/api/discovery.py:235-238`

Read `display.playSeed` defensively as `getattr(display, "playSeed", 0)` at all 5 sites (a Display from a pre-feature `settings.dat` mid-boot, before migration, still works).

**Client wiring (verified).** PREPARE handler `index.html:949-956`, PLAY handler `index.html:958-968` (both read `data_obj.PAYLOAD`). `runScriptLoop` draw call `index.html:485` currently `animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now())`. `js/animations.js` is loaded before the inline script (`index.html:23`), so `mmDeriveSeed`/`MM_RNG` are global.

**ES5 rules** for `index.html` + `js/animations.js`: `var`/`function` only, no `let`/`const`/arrow/template-literals/`Math.imul`. The admin + Node import the same `js/animations.js`.

**Branch:** `feature/animation-seed` (already created off `main`, spec committed). Do NOT start on `main`.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `js/animations.js` | Add `MM_RNG` + `mmDeriveSeed` (exposed on `root`); retrofit `plasma` to use the seed. | 1, 2 |
| `tests/unit/js/test_animations_rng.js` | Determinism/range/distinctness + portability guard for the helpers. | 1 |
| `tests/unit/js/test_animations_plasma.js` | Extend: seeded determinism + different-seed-differs + 1200-count holds. | 2 |
| `mosaicmesh/state.py` | `Display.playSeed` field + migration backfill. | 3 |
| `tests/unit/test_render_registry.py` | `playSeed` default + migration test. | 3 |
| `mosaicmesh/render.py` | `_mint_play_seed()`; mint in `_begin_prepare`; add `seed` to sites 1-4; reuse in `_start_group_playback`. | 4 |
| `mosaicmesh/api/discovery.py` | Add `seed` to `sync_new_client_to_group` PLAY (site 5). | 4 |
| `tests/unit/test_animation_seed.py` | Server: mint, payloads carry seed, resume reuses, late-join carries seed. | 4 |
| `index.html` | `playback.seed`; set in PREPARE/PLAY; 6th draw arg via `mmDeriveSeed`. | 5 |

---

### Task 1: `MM_RNG` + `mmDeriveSeed` helpers

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_rng.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_rng.js`:

```js
/**
 * Coordinated-seed PRNG helpers. The sync guarantee: same seed -> identical
 * stream on every engine, so xorshift32 (bitwise-only) is mandatory — no
 * Math.imul (absent on Safari 5.1), no >2^53 multiply (engine-divergent).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
await import('../../../js/animations.js');
const { MM_RNG, mmDeriveSeed } = globalThis;

test('MM_RNG — same seed yields identical stream', () => {
  const a = MM_RNG(12345), b = MM_RNG(12345);
  const sa = [], sb = [];
  for (let i = 0; i < 20; i++) { sa.push(a()); sb.push(b()); }
  assert.deepStrictEqual(sa, sb);
});

test('MM_RNG — different seeds diverge', () => {
  const a = MM_RNG(1), b = MM_RNG(2);
  const sa = [], sb = [];
  for (let i = 0; i < 20; i++) { sa.push(a()); sb.push(b()); }
  assert.notDeepStrictEqual(sa, sb);
});

test('MM_RNG — values in [0,1), not constant, seed 0 is valid', () => {
  for (const seed of [0, 1, 0xFFFFFFFF, 42]) {
    const r = MM_RNG(seed);
    const vals = [];
    for (let i = 0; i < 50; i++) { const v = r(); assert.ok(v >= 0 && v < 1, `out of range: ${v}`); vals.push(v); }
    assert.ok(new Set(vals).size > 1, `seed ${seed} produced a constant stream`);
  }
});

test('mmDeriveSeed — deterministic + distinct per index', () => {
  assert.equal(mmDeriveSeed(777, 3), mmDeriveSeed(777, 3));
  const seen = new Set();
  for (let i = 0; i < 64; i++) seen.add(mmDeriveSeed(777, i));
  assert.equal(seen.size, 64, 'index collision in mmDeriveSeed');
});

test('portability guard — no Math.imul in js/animations.js', () => {
  const src = readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../js/animations.js'), 'utf8');
  assert.ok(!/Math\.imul/.test(src), 'MM_RNG must avoid Math.imul (absent on Safari 5.1)');
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_rng.js`
Expected: FAIL — `MM_RNG is not a function` (destructured `undefined`).

- [ ] **Step 3: Add the helpers to `js/animations.js`**

In `js/animations.js`, inside the `(function (root) {` IIFE, BEFORE `var animations = [`, add:

```js
  // Seeded PRNG for coordinated randomness. xorshift32 — BITWISE ONLY
  // (^, <<, >>>), so output is bit-identical on Safari 5.1 / Node / modern V8.
  // NO Math.imul (absent on Safari 5.1) and NO >2^53 multiply (engine-divergent
  // low bits). MM_RNG(seed) -> function(): float in [0,1).
  function MM_RNG(seed) {
    var s = (seed >>> 0) || 0x9E3779B9;   // 0 -> non-degenerate default
    return function () {
      s ^= s << 13; s >>>= 0;
      s ^= s >>> 17;
      s ^= s << 5;  s >>>= 0;
      return (s >>> 0) / 4294967296;
    };
  }

  // Per-item seed from the run seed + a SMALL playlist index. The single
  // (idx+1)*const multiply stays << 2^53 because idx is a tiny index.
  function mmDeriveSeed(runSeed, idx) {
    var s = ((runSeed >>> 0) ^ (((idx >>> 0) + 1) * 0x9E3779B1)) >>> 0;
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;  s >>>= 0;
    return s >>> 0;
  }
```

At the END of the IIFE, where `root.MM_ANIMATIONS = animations;` is, add:

```js
  root.MM_RNG = MM_RNG;
  root.mmDeriveSeed = mmDeriveSeed;
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_rng.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_rng.js
git commit -m "feat(animations): MM_RNG xorshift32 + mmDeriveSeed (bitwise-only, portable)"
```

---

### Task 2: `plasma` seed retrofit (first consumer)

**Files:**
- Modify: `js/animations.js` (the `plasma` entry)
- Test: `tests/unit/js/test_animations_plasma.js`

- [ ] **Step 1: Add seeded tests (extend the existing plasma test file)**

Append to `tests/unit/js/test_animations_plasma.js`:

```js
test('plasma — same seed deterministic, different seed differs', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx(), c = makeRecordingCtx();
  byKey.plasma(a, 5000, W, H, 0, 111);
  byKey.plasma(b, 5000, W, H, 0, 111);   // same (tMs, seed)
  byKey.plasma(c, 5000, W, H, 0, 222);   // different seed
  assert.deepStrictEqual(a.__ops, b.__ops);
  assert.notDeepStrictEqual(a.__ops, c.__ops);   // hue rotation + phase offsets shift
});

test('plasma — seed perturbs color/phase, NOT the 1200-cell grid', () => {
  const c = makeRecordingCtx();
  byKey.plasma(c, 5000, W, H, 0, 999);
  assert.equal(c.__ops.filter((o) => o.op === 'fillRect').length, 1200);
});
```

(The existing top-of-file lines `await import('../../../js/animations.js')` + `const byKey = ...` already provide `byKey`; `makeRecordingCtx`/`W`/`H` are already imported/defined there.)

- [ ] **Step 2: Run to confirm the new tests fail**

Run: `node --test tests/unit/js/test_animations_plasma.js`
Expected: FAIL — the `notDeepStrictEqual` assertion fails (plasma currently ignores `seed`, so seeds 111 and 222 produce identical ops).

- [ ] **Step 3: Retrofit the `plasma` entry**

In `js/animations.js`, replace the `plasma` entry's `draw` with (signature gains `nowMs, seed`; add `MM_RNG`-derived `hueShift` + 4 phase offsets; add each `phN` into its `sin` term; rotate hue):

```js
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var GW = 40, GH = 30, gx, gy;
        var k1 = 8, k2 = 12, k3 = 10, k4 = 14;
        var T1 = 2500, T2 = 3300, T3 = 4100, T4 = 1900;
        var rng = MM_RNG(seed);
        var hueShift = rng() * 360;          // per-run colorway rotation
        var ph1 = rng() * 6.283, ph2 = rng() * 6.283,
            ph3 = rng() * 6.283, ph4 = rng() * 6.283;
        var cw = w / GW, ch = h / GH;
        for (gy = 0; gy < GH; gy++) {
          for (gx = 0; gx < GW; gx++) {
            var u = gx / GW, v = gy / GH;
            var du = u - 0.5, dv = v - 0.5;
            var c = Math.sin(u * k1 + tMs / T1 + ph1)
                  + Math.sin(v * k2 + tMs / T2 + ph2)
                  + Math.sin((u + v) * k3 + tMs / T3 + ph3)
                  + Math.sin(Math.sqrt(du * du + dv * dv) * k4 + tMs / T4 + ph4);
            ctx.fillStyle = 'hsl(' + ((((c + 4) / 8) * 360 + hueShift) % 360) + ', 100%, 50%)';
            ctx.fillRect(gx * cw, gy * ch, cw + 1, ch + 1);
          }
        }
      }
```

- [ ] **Step 4: Run the full plasma file to confirm pass**

Run: `node --test tests/unit/js/test_animations_plasma.js`
Expected: PASS — the original 3 tests (the non-seeded ones still pass: with `seed` undefined, `MM_RNG(undefined)` → `0 >>> 0 || default` → a fixed stream, so the existing determinism/animates/1200-count tests hold) + the 2 new seeded tests.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_plasma.js
git commit -m "feat(animations): plasma uses seed for per-run hue rotation + phase offsets"
```

---

### Task 3: `Display.playSeed` field + migration

**Files:**
- Modify: `mosaicmesh/state.py` (Display `__init__` ~line 38, `migrate_client_objects` ~line 245)
- Test: `tests/unit/test_render_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render_registry.py`:

```python
def test_new_display_has_zero_playseed():
    from mosaicmesh.state import Display
    assert Display().playSeed == 0


def test_migration_backfills_playseed(fresh_settings):
    from mosaicmesh.state import Display, migrate_client_objects
    d = Display()
    del d.playSeed            # simulate a Display from a pre-feature settings.dat
    fresh_settings.displays["G1"] = d
    migrate_client_objects()
    assert fresh_settings.displays["G1"].playSeed == 0
```

- [ ] **Step 2: Run to confirm it fails**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -k playseed -v`
Expected: FAIL — `AttributeError: 'Display' object has no attribute 'playSeed'`.

- [ ] **Step 3: Add the field + migration**

In `mosaicmesh/state.py`, in `Display.__init__`, immediately after `self.playStartEpoch = 0   # ...` (line 38):

```python
        self.playSeed = 0         # per-run coordinated PRNG seed (minted at _begin_prepare)
```

In `migrate_client_objects`, in the display loop, after `if not hasattr(_disp, 'renders'): _disp.renders = {}` (line 245-246):

```python
        if not hasattr(_disp, 'playSeed'):
            _disp.playSeed = 0
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -k playseed -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/state.py tests/unit/test_render_registry.py
git commit -m "feat(state): Display.playSeed field + migration backfill"
```

---

### Task 4: Server — mint + deliver the seed

**Files:**
- Modify: `mosaicmesh/render.py` (`import random`; `_mint_play_seed`; `_begin_prepare`; sites 1-4)
- Modify: `mosaicmesh/api/discovery.py` (site 5)
- Test: `tests/unit/test_animation_seed.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_animation_seed.py`:

```python
# tests/unit/test_animation_seed.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import pytest
from unittest.mock import MagicMock, patch
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode, PlayState
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.socketmanager = MagicMock()
    yield server.settings
    server.settings = prev


def _synced_group(fresh_settings, did="G1"):
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    me = MediaElement(); me.id = "a"; me.file = "bouncingBalls"; me.playmode = PlayMode.SCRIPT
    me.duration = 1000
    d.mediaElements = [me]
    fresh_settings.displays[did] = d
    c = Client(); c.displayID = did; c.isOnline = True; c.synced = True
    fresh_settings.clients["c1"] = c
    return d, c


def test_begin_prepare_mints_32bit_seed(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    with patch.object(server, "broadcast_to_client"):
        server._begin_prepare("G1")
    assert isinstance(d.playSeed, int)
    assert 0 <= d.playSeed < 2**32
    assert d.playSeed != 0   # _mint_play_seed should not yield 0 in practice


def test_prepare_payload_carries_seed(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    with patch.object(server, "broadcast_to_client") as bc:
        server._begin_prepare("G1")
    payload = bc.call_args[0][1]["PAYLOAD"]
    assert payload["seed"] == d.playSeed


def test_play_payload_carries_seed(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    d.playSeed = 0xABCD1234
    # renderable=False here (SCRIPT) -> _start_group_playback group-broadcast path
    with patch.object(server, "broadcast_to_display_group") as bg:
        R._start_group_playback("G1")
    assert bg.call_args[0][1]["PAYLOAD"]["seed"] == 0xABCD1234


def test_start_group_playback_does_not_remint(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    d.playSeed = 555
    d.action = PlayState.PAUSE; d.pauseOffset = 0
    with patch.object(server, "broadcast_to_display_group"):
        R._start_group_playback("G1")   # resume path
    assert d.playSeed == 555            # reused, not re-minted


def test_late_join_play_carries_seed(fresh_settings):
    from mosaicmesh.api.discovery import sync_new_client_to_group
    d, c = _synced_group(fresh_settings)
    d.playSeed = 0x0BADF00D
    d.action = PlayState.PLAY
    with patch("mosaicmesh.api.discovery.broadcast_to_client") as bc:
        sync_new_client_to_group("c1", c)
    play = [call.args[1] for call in bc.call_args_list if call.args[1]["REQUEST"] == "PLAY"][0]
    assert play["PAYLOAD"]["seed"] == 0x0BADF00D
```

- [ ] **Step 2: Run to confirm it fails**

Run: `python -m pytest tests/unit/test_animation_seed.py -c tests/pytest.ini -v`
Expected: FAIL — `test_begin_prepare_mints_32bit_seed` (playSeed stays 0), and the payload tests (`KeyError: 'seed'`).

- [ ] **Step 3a: Add the mint helper + import**

In `mosaicmesh/render.py`, add at the top with the other stdlib imports (near `import uuid`):

```python
import random
```

Add a module-level helper (near the other small helpers, e.g. after `render_token`):

```python
def _mint_play_seed():
    """A fresh 32-bit run seed for coordinated animation randomness. Wrapped so
    tests can monkeypatch it. Avoids 0 so MM_RNG never hits its default branch."""
    return random.getrandbits(32) or 0x1
```

- [ ] **Step 3b: Mint in `_begin_prepare`**

In `_begin_prepare` (render.py ~1328), right after `display.prepareId = uuid.uuid4().hex`:

```python
        display.playSeed = _mint_play_seed()
```

- [ ] **Step 3c: Add `seed` to all 5 payload sites**

Site 1 — `_begin_prepare` inline PREPARE (~1344):

```python
        broadcast_to_client(key, {
            "REQUEST": "PREPARE",
            "PAYLOAD": {"prepareId": display.prepareId,
                        "items": _per_client_items(display, key, c), "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0)}})
```

Site 2 — `_prepare_unsynced_clients` late PREPARE (~1386):

```python
            broadcast_to_client(key, {
                "REQUEST": "PREPARE",
                "PAYLOAD": {"prepareId": prepare_id,
                            "items": _per_client_items(display, key, c),
                            "loop": display.loop,
                            "seed": getattr(display, "playSeed", 0)}})
```

Site 3 — `_broadcast_per_client_play` (~1209):

```python
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch,
                        "items": _per_client_items(display, key, c), "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0)}})
```

Site 4 — `_start_group_playback` group-wide PLAY (~1272):

```python
        broadcast_to_display_group(display_id, {
            "REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0)}})
```

Site 5 — `mosaicmesh/api/discovery.py` `sync_new_client_to_group` (~235):

```python
    broadcast_to_client(client_key, {
        "REQUEST": "PLAY",
        "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop,
                    "seed": getattr(display, "playSeed", 0)}
    })
```

(`_start_group_playback` already only reads `display.playSeed` — no mint added there. Good.)

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/unit/test_animation_seed.py -c tests/pytest.ini -v`
Expected: PASS (5 tests).

Then regression: `python -m pytest tests/unit/test_coordinated_start.py tests/unit/test_playback.py -c tests/pytest.ini -q`
Expected: no NEW failures vs `main` (these files carry pre-existing Py3.14 reds unrelated to this change — compare the failure set if unsure).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py mosaicmesh/api/discovery.py tests/unit/test_animation_seed.py
git commit -m "feat(render): mint per-run playSeed in _begin_prepare; deliver in PREPARE/PLAY/late-join"
```

---

### Task 5: Client plumbing (`index.html`)

**Files:**
- Modify: `index.html` (`playback` init ~90; PREPARE handler ~949; PLAY handler ~963; `runScriptLoop` draw call ~485)

No node unit test (inline ES5); verified by the e2e SCRIPT smoke (Task 6) + the determinism already proven in Tasks 1-2. The Node tests pass `seed` explicitly, so they don't depend on this wiring.

- [ ] **Step 1: Add `seed` to the `playback` object**

In `index.html` (~line 90), the `var playback = { items: [], startEpoch: 0, ... }` object — add `seed: 0,` to the literal (anywhere in it).

- [ ] **Step 2: Set `playback.seed` in the PREPARE handler**

In the `REQUEST == "PREPARE"` branch (~951), alongside `playback.items = ...; playback.loop = ...`:

```js
					playback.seed = data_obj.PAYLOAD.seed || 0;
```

- [ ] **Step 3: Set `playback.seed` in the PLAY handler**

In the `REQUEST == "PLAY"` branch (~963), alongside `playback.items = ...; playback.startEpoch = ...`:

```js
					playback.seed = data_obj.PAYLOAD.seed || 0;
```

- [ ] **Step 4: Pass the per-item seed as the 6th draw arg**

In `runScriptLoop` (~485), replace the draw call:

```js
			if (animations[name]) {
				var itemSeed = (typeof mmDeriveSeed === 'function') ? mmDeriveSeed(playback.seed, pos.index) : 0;
				animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
			}
```

(The `typeof` guard keeps the client robust if `js/animations.js` somehow didn't load — animations just get `seed=0`.)

- [ ] **Step 5: Syntax-check the inline block**

Run: `node --check <(sed -n '25,1010p' index.html)` is not reliable for HTML; instead verify the main inline `<script>` parses by extracting it. Minimum: `grep -n "playback.seed" index.html` shows 3 hits (init + 2 handlers) and `grep -n "mmDeriveSeed(playback.seed" index.html` shows 1. Visually confirm no `let`/`const`/arrow were introduced.

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(client): thread playback.seed into SCRIPT draw (6th arg via mmDeriveSeed)"
```

---

### Task 6: Full suite, e2e, review, PR

- [ ] **Step 1: Full JS + unit suites**

Run: `python pytest_runner.py --js`
Expected: all pass — `test_animations_rng.js` (5) + the extended `plasma` file + every pre-existing JS test.

Run: `python pytest_runner.py --unit`
Expected: `test_animation_seed.py` (5) + `test_render_registry.py` playseed tests pass; the only failures are the pre-existing Py3.14 `asyncio`/calibration reds that also fail on `main` (confirm the set is unchanged — `git stash`/compare if unsure).

- [ ] **Step 2: e2e SCRIPT smoke (if a dev server is available)**

The existing `tests/e2e/test-script-animations.spec.js` already drives `plasma` through the real PLAY path. Confirm it still passes (the synthetic PLAY has no `seed` → `playback.seed=0` fallback → plasma renders one fixed colorway, non-blank). Optionally add a second `plasma` PLAY with a `seed` in the payload and assert the canvas differs — but the synthetic-PLAY harness already covers "renders non-blank + tears down." Run: `node tests/e2e/run.js script-animations` (needs dev server). If no server/Playwright env, note it; the Node determinism tests cover the seed behavior.

- [ ] **Step 3: Final code review**

Use superpowers:requesting-code-review over the branch. Focus: ES5/portability in `js/animations.js` (no `Math.imul`/`let`/`const`/arrow), the 5 payload sites all carry `seed`, mint-only-in-`_begin_prepare` (resume reuses), the `getattr(display,"playSeed",0)` defensive reads, and that `plasma`'s seed perturbs only color/phase (grid unchanged).

- [ ] **Step 4: Finish the branch**

Use superpowers:finishing-a-development-branch. PR summary: server-minted per-run `Display.playSeed` (start-gate state, reconnect-safe via all 5 payload sites), `MM_RNG`/`mmDeriveSeed` (xorshift32, portable), `plasma` as first consumer; `gameOfLife` seeded-init + `sunMoonTransit` LCG cleanup are noted follow-ups.

---

## Self-Review

**1. Spec coverage:**
| Spec item | Task |
|---|---|
| `Display.playSeed` field + migration | Task 3 |
| Mint in `_begin_prepare` (once/run), reuse on resume | Task 4 (3b mint; `_start_group_playback` unchanged-mint verified by `test_start_group_playback_does_not_remint`) |
| Deliver in PREPARE + PLAY (+ reconnect) | Task 4 — all 5 payload sites |
| Client `playback.seed` + 6th draw arg + per-item derive | Task 5 |
| `MM_RNG` xorshift32 + `mmDeriveSeed`, bitwise-only | Task 1 |
| `plasma` retrofit (hue rotation + phase offsets) | Task 2 |
| No-seed fallback (0) | Task 2 Step 4 note + Task 5 `|| 0` + `MM_RNG` default |
| Tests: node determinism + portability guard + plasma seeded | Tasks 1, 2 |
| Tests: python mint/payload/resume/migration | Tasks 3, 4 |
| `gameOfLife` / `sunMoonTransit` cleanup = non-goals | Not implemented (correctly out of scope) |

**2. Placeholder scan:** No TBD/"handle X"/"similar to". Every code step shows full code. Task 5's syntax-check step is grep-based (honest: inline HTML ES5 isn't node-unit-testable; e2e covers runtime).

**3. Type/name consistency:** `MM_RNG`, `mmDeriveSeed`, `playSeed`, `_mint_play_seed`, `playback.seed` spelled identically across tasks. Draw signature `(ctx, tMs, w, h, nowMs, seed)` matches Task 2's plasma + Task 5's call (`mmDeriveSeed(playback.seed, pos.index)` → 6th arg). Payload key `"seed"` consistent across all 5 sites + both client handlers. `getattr(display, "playSeed", 0)` used uniformly at read sites.
