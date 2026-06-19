# Coordinated Animation Seed — Design

**Date:** 2026-06-18
**Status:** Approved — ready for implementation plan.
**Builds on:** the SCRIPT animation model (`js/animations.js`, `MM_ANIMATIONS`), the coordinated-start payload (`_begin_prepare` → PREPARE → READY → GO/PLAY), and the wall-clock 5th-arg precedent (`nowMs`).

## Problem

SCRIPT animations are pure functions of elapsed time — `draw(ctx, tMs, w, h, nowMs)` — and that purity is the synchronization mechanism: same inputs → identical frame on every screen in a group. The cost is that animations **cannot be randomized**: `Math.random()` is banned because each screen would diverge, breaking the wall.

We want **randomized but coordinated** animations: every screen runs the *same* "random" sequence (so the wall stays in lockstep), and each playback run looks *different* from the last. The lever is a shared **seed**: a value the server mints at the start of a coordinated run and delivers to every screen, so all screens drive an identical seeded PRNG.

## Decisions (settled during brainstorming)

- **Seed lifetime:** the server mints a fresh **run-seed** at each fresh coordinated-start; it is **stable for the whole run** (all loops within a run reuse it).
- **Granularity:** **per item** — each playlist item's effective seed = `mmDeriveSeed(runSeed, itemIndex)`, so different items in one playlist get different "random" layouts.
- **Run-to-run:** **different each run** — a fresh run-seed per playback means the same item looks different every time the playlist runs, while remaining identical across all screens at every instant.
- **Source (Approach A):** server-minted run-seed stored on the `Display` (not derived client-side from `startEpoch`), for entropy + clean reconnect behavior.

## Architecture / data flow

```
fresh PLAY (display.action != PAUSE)
  -> _begin_prepare(display_id):
       display.playSeed = <random 32-bit uint>     # minted ONCE per run
       PREPARE payload {prepareId, items, loop, seed: display.playSeed}  (per client)
  -> clients READY -> _release_group GO:
       PLAY payload {startEpoch, items, loop, seed: display.playSeed}     (per client)

resume (display.action == PAUSE) -> _start_group_playback:
       reuse existing display.playSeed (NO re-mint)  -> PLAY payload carries it

client (index.html):
  PREPARE/PLAY handler: playback.seed = PAYLOAD.seed
  runScriptLoop frame:  itemSeed = mmDeriveSeed(playback.seed, pos.index)
                        animations[name](ctx, pos.offsetMs, w, h, GoTime.now(), itemSeed)

animation:
  draw(ctx, tMs, w, h, nowMs, seed):
     var rng = MM_RNG(seed); ... rng()  // [0,1), identical on every screen
```

**Why the seed lives on the `Display`, not regenerated per broadcast.** Same invariant as `startEpoch`: when a screen reconnects mid-run, the server re-sends PLAY. If that re-send minted a *new* seed, the reconnecting screen would render a different random layout than the screens already running. So `playSeed` is set once per run (in `_begin_prepare`) and included verbatim on every (re)broadcast for that run. The seed is "start-gate state."

## Components

### Server (`server.py` / `mosaicmesh/render.py`)

- **`Display.playSeed`** — new field (int, default `0`). Migrated/backfilled like other newer fields.
- **Mint at fresh start.** In `_begin_prepare(display_id)` (the fresh coordinated-start path), set `display.playSeed = <random 32-bit uint>` once, alongside the existing `prepareId`/`prepareDeadline` setup. Use Python `random.getrandbits(32)`.
- **Deliver in PREPARE and PLAY.** The per-client PREPARE payload (`_broadcast_per_client_preload` / the prepare broadcast in `_begin_prepare`) and the per-client PLAY payload (`_broadcast_per_client_play`) add `"seed": display.playSeed`. The seed is group-level (same for every client in the group), but rides the existing per-client payloads.
- **Resume reuses.** `_start_group_playback` (the resume path) must NOT re-mint; it reads the existing `display.playSeed`. Only `_begin_prepare` mints. (A fresh PLAY always goes through `_begin_prepare`; resume goes directly to `_start_group_playback`.)
- **Default/back-compat:** a `Display` with `playSeed == 0` (e.g. legacy `settings.dat`, or a render-only display that never started a coordinated run) simply delivers `seed: 0`; the client treats 0 as a valid (non-run-varying) seed.

### Client (`index.html`, ES5)

- **`playback.seed`** — new field on the `playback` object (default `0`).
- **PREPARE handler** (~line 949) and **PLAY handler** (~line 963): `playback.seed = data_obj.PAYLOAD.seed || 0;`.
- **`runScriptLoop`** (~line 485 draw call): compute the per-item seed and pass it as the 6th arg:
  ```js
  var itemSeed = mmDeriveSeed(playback.seed, pos.index);
  animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
  ```
  (`mmDeriveSeed` is exposed by `js/animations.js`, loaded before the inline script.)

### `js/animations.js` — the seeded-PRNG helpers (ES5, portable)

Two pure functions, exposed on `root` next to `MM_ANIMATIONS` (so `index.html`, the admin, and Node tests share one implementation):

- **`MM_RNG(seed)`** → returns a zero-arg function yielding floats in `[0, 1)`. Implemented as **xorshift32** — **bitwise operators only** (`^`, `<<`, `>>>`), normalized with `>>> 0`. Example shape:
  ```js
  function MM_RNG(seed) {
    var s = (seed >>> 0) || 0x9E3779B9;   // 0 -> non-degenerate default
    return function () {
      s ^= s << 13; s >>>= 0;
      s ^= s >>> 17;
      s ^= s << 5;  s >>>= 0;
      return (s >>> 0) / 4294967296;
    };
  }
  ```
- **`mmDeriveSeed(runSeed, itemIndex)`** → a uint mixing the run-seed with a small playlist index, bitwise/small-multiply only (item indices are tiny, so any multiply stays far under 2^53):
  ```js
  function mmDeriveSeed(runSeed, idx) {
    var s = ((runSeed >>> 0) ^ (((idx >>> 0) + 1) * 0x9E3779B1)) >>> 0;
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;  s >>>= 0;
    return s >>> 0;
  }
  ```

**Portability is the load-bearing constraint** (the reason for xorshift, documented in the module): `Math.imul` does not exist on iOS 5 / Safari 5.1, and a multiplication-based LCG whose product exceeds 2^53 (e.g. `s * 1103515245`) loses exactly the low bits a mask would keep, so its output can differ between Safari 5.1, Node, and modern V8. JS bitwise ops are spec-defined as exact 32-bit (`ToUint32`/`ToInt32`) on every engine, so xorshift32 is bit-identical everywhere — which is precisely what "coordinated across screens" requires. `mmDeriveSeed`'s single `(idx+1) * 0x9E3779B1` is safe because `idx` is a small playlist position (< ~10⁴ ⇒ product < ~2.7×10¹³ ≪ 2^53).

### First consumer / showcase: `plasma`

`plasma` is the cleanest built showcase because it is a **generative** animation — its appearance is a color field with a configuration space (palette + phase) that a seed can sample — rather than a **time-driven** one (`analogClock`/`wordClock`/`sunMoonTransit`) whose entire state comes from `nowMs` and has nothing meaningful to randomize. Retrofit `plasma` to vary its colorway per run:

- Signature becomes `draw(ctx, tMs, w, h, nowMs, seed)`.
- At the top: `var rng = MM_RNG(seed);` then derive a per-run **hue rotation** and **phase offsets**:
  ```js
  var hueShift = rng() * 360;                 // whole field rotates to a new colorway each run
  var ph1 = rng() * 6.283, ph2 = rng() * 6.283,
      ph3 = rng() * 6.283, ph4 = rng() * 6.283;
  ```
- Add each `phN` inside the corresponding `Math.sin(... + tMs/TN + phN)` term, and rotate the hue: `hue = (((c + 4) / 8) * 360 + hueShift) % 360`.
- Effect: each playback run renders a **different plasma colorway/pattern, identical on every screen at every instant** — the feature's purpose made visible. The motion stays time-driven (`tMs`), so it animates as before; only the per-run *configuration* is seeded.

The other 7 animations ignore the new 6th arg (unchanged). `seed == 0` (no-seed fallback) yields one fixed-but-valid colorway, so `plasma` still renders without a server seed.

## Error handling / edge cases

- **No seed in payload** (old server, direct `mosiacMeshCallback` test, or `B`-style absence): `playback.seed` falls back to `0`; `MM_RNG(0)` uses its non-degenerate default. Animations remain deterministic (just not run-varying). Fully backward-compatible — no animation breaks for lack of a seed.
- **Reconnect mid-run:** server re-sends the stored `playSeed`; the late-joiner matches the running screens.
- **Resume (pause→play):** same seed; no re-randomize.
- **Seed over JSON:** a 32-bit unsigned integer; survives `jsonpickle`/JSON round-trips and `settings.dat`.
- **`MM_RNG` degenerate seed (0):** mapped to a fixed non-zero constant so the stream never collapses to all-zeros.

## Testing

All unit-level; no ffmpeg/SSH.

### Node (`tests/unit/js/`)
- `MM_RNG`: same seed → identical stream (first N values deep-equal); different seeds → different streams; every value in `[0, 1)`; seed `0` yields a valid non-constant stream.
- `mmDeriveSeed`: deterministic; distinct for distinct indices (no collision for indices 0..N); same `(runSeed, idx)` → same uint.
- **Portability guard:** a test asserting the `js/animations.js` source for `MM_RNG`/`mmDeriveSeed` contains no `Math.imul` and no large-constant multiply in the RNG step (a cheap regression guard for the cross-engine invariant).
- `plasma`: same `(tMs, seed)` → identical op log (determinism/sync); different `seed` → different op log (the per-run hue rotation + phase offsets change the `fillStyle` sequence); the existing 1200-cell `fillRect` count still holds (the seed perturbs colors/phase, not the grid).

### Python (`tests/unit/`)
- `_begin_prepare` sets `display.playSeed` to a 32-bit int (`0 <= seed < 2**32`) on a fresh start.
- The PREPARE and PLAY per-client payloads include `seed` equal to `display.playSeed`.
- Resume (`_start_group_playback` on a PAUSE→PLAY) does **not** change `display.playSeed`.
- A second fresh start re-mints (new seed differs from the old with overwhelming probability — assert it's re-assigned, e.g. by monkeypatching the RNG to return a known value).
- `Display` migration backfills `playSeed = 0` on objects loaded from a pre-feature `settings.dat`.

## Non-goals (follow-ups)

- **`gameOfLife` seeded initial state — the marquee future consumer.** `gameOfLife` is the canonical use of a coordinated seed: the precomputed-cycle's initial grid (today seeded from a *fixed* LFSR so every screen matches) becomes `MM_RNG(seed)`-seeded, so each run starts from a different-but-identical-across-screens board. It is NOT in this spec because `gameOfLife` itself is still deferred (it needs the precomputed-cycle infrastructure); when that lands, the seed is already waiting for it. This is the strongest motivation for building the seed infra now.
- **New seeded animations** (randomized particle bursts, shuffled palettes, per-run generative layouts). Separate leaf additions on top of `MM_RNG`; this spec ships the infra + `plasma` as the one showcase.
- **`sunMoonTransit` star-LCG portability cleanup.** Its night-sky stars use a hand-rolled LCG (`s * 1103515245 + ...`) with the 2^53 overflow hazard described above — a latent *cross-screen* divergence bug (independent of this feature). The fix is to swap it to `MM_RNG(12345)` (a **fixed** seed — a portability cleanup, NOT randomization; the time-of-day animation has nothing to randomize). Logged as a separate small follow-up so this spec stays focused on the generative use case.
- **Per-loop re-seeding.** Run + item granularity only; a looping single item repeats within a run (its motion is still time-driven). Per-loop variation (`mmDeriveSeed(runSeed, itemIndex, loopIndex)`) is a future option.
- **Mosaic-spanning seeded animations.** Out of scope (depends on the separate per-client mosaic payload work).
- **Operator-visible seed control** (pinning a seed, re-roll button). Not needed for v1.
