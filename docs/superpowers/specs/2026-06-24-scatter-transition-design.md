# Scatter Transition ("scatter") — Design

**Date:** 2026-06-24
**Status:** Approved (pending spec review)

## Overview

A new **PNG-driven sprite-burst transition**, `scatter`, joining the transition
family (`fade`, `wipe`, `slide`, `zoom`, `iris`, `dissolve`, `beerfill`). A giant
sprite grows at the center as the cover while many smaller copies erupt outward and
tumble; the outgoing item is hidden, then the burst clears to reveal the incoming
item. The sprite is any **transparent PNG in the uploaded media library**, so the
same effect serves hops, bottlecaps, darts, confetti, etc. — only the chosen image
differs, with zero new code or art per variant.

It must run on the 1st-gen iPad-1 display clients (iOS 5.1 / Safari 5.1): client
code is **ES5 only** and the per-frame work is `drawImage` of a once-decoded PNG +
`fillRect` (no `clip()`, no `destination-*` compositing).

## Role-aware behavior (mask/cover family)

Like `beerfill`, `scatter` reads its transition role:

- **`endEffect` (COVER):** a backing disc (the item's `backgroundColor`) grows
  radially from center to guarantee a clean, gap-free cover; a **giant sprite**
  grows at center (to ~1.43× the region's height) doing one full 360° rotation;
  **`count`** smaller sprite copies erupt outward from center on **continuous**
  outward trajectories (seeded angle/speed), tumbling as they fly. Phase duration =
  `fillMs`.
- **`startEffect` (REVEAL):** the reverse — disc shrinks, giant shrinks/rotates
  out, copies continue outward off the edges, revealing the incoming item. Phase
  duration = `drainMs`.

Progress `p∈[0,1]` is the clamped phase progress. The **eruption distance is a
monotonic function of `p`** (e.g. `pow(p,0.72)·reach`) — it must never plateau,
so the copies never freeze while the giant holds at full size (the "pause"
artifact found and fixed during the mockup). Placing `scatter` as item A's
`endEffect` and item B's `startEffect` produces the full burst-and-clear handoff;
both carry the same params object, and the effect selects `fillMs`/`drainMs` by role.

## Parameters

Exposed via `ParamSpec` (appear in `/api/effects` + the editor):

| key        | type    | default  | range / source                                  | meaning |
|------------|---------|----------|-------------------------------------------------|---------|
| `sprite`   | string  | `hop`    | a transparent PNG in `media/server/images/`     | the burst sprite; editor offers a dropdown of transparent PNGs |
| `scope`    | choice  | `wall`   | `screen`, `wall`                                | one burst from the sign center across all screens, or per-screen |
| `count`    | number  | `40`     | min `1`, max `120`                              | number of erupting copies |
| `fillMs`   | number  | `2500`   | min `0`                                         | cover phase duration |
| `drainMs`  | number  | `2500`   | min `0`                                         | reveal phase duration |
| `audioFade`| boolean | `true`   | —                                               | fade audio on video items (server `_afade`) |

`sprite` is stored as the image basename or URL (e.g. `hop` → `/media/server/images/hop.png`).
**Locked constants** (validated in the mockup; not exposed — YAGNI): giant peak
≈1.43× region height; giant one full 360° rotation over the phase; eruption
distance curve `pow(p,0.72)`; copy spin from a seeded per-copy rate; backing =
item `backgroundColor`.

## Sprite source — transparent PNGs in the media library

No bespoke sprites folder. The sprite list is the set of **transparent PNGs already
in `media/server/images/`** (where the normal image uploader, `POST /upload/image`,
drops files). To get a new sprite, an operator just uploads a transparent PNG the
usual way.

- **Transparency detection (cheap):** read the PNG IHDR **color-type byte** (offset
  25 in the file: `6` = RGBA, `4` = gray+alpha → has alpha) without decoding
  pixels. Cache the result keyed by `(path, mtime)` — same pattern as
  `server._video_duration_cache` / `_duration_ms`. A helper
  `mosaicmesh/api/media.py:_png_has_alpha(path)` + a small mtime cache.
- **Surfaced to the editor:** `GET /api/media` (in `mosaicmesh/api/media.py`)
  already lists `media/server/images`; add a per-image boolean field
  `transparent` (true only for alpha PNGs). The scatter editor's `sprite` dropdown
  filters to `transparent === true`.
- **Validation:** the server resolves `sprite` to a path under
  `media/server/images/`, rejects traversal, and confirms the file exists and is a
  transparent PNG (same spirit as the `DELETE /api/media` path validation). An
  invalid/missing sprite falls back to the default (`hop`) rather than erroring the
  transition.
- **Seed asset:** `hop.png` is placed in `media/server/images/` so the default
  works out of the box; it can be replaced/re-uploaded via the UI.

## Architecture (mirrors beerfill)

### Server — `effects.py`
`ScatterEffect(Effect)`, `name="scatter"`, `label="Scatter"`, the params above,
`@register`. `video_filters(role, params, ctx)` returns `([], _afade(...))` with the
role-selected duration injected (`fillMs` for `end`, `drainMs` for `start`) — **no
server-side video frames; visuals are client-side.** Catalog test in
`tests/unit/test_effects.py`; full-catalog assertions updated to include `scatter`.

### Client — pure helpers in `js/transitions.js` (ES5, exported on `root`)
- `mmScatterPhase(role)` → `'cover'` (end) / `'reveal'` (start).
- `mmScatterDuration(params, role)` → `fillMs`/`drainMs` (default 2500).
- `mmScatterProgress(phase, p)` → cover progress (cover: `p`; reveal: `1−p`), clamped.
- `mmScatterDist(p)` → monotonic eruption distance factor (`pow(clamp(p),0.72)`) — unit-tested to be monotonic non-decreasing (guards the no-pause fix).
- `mmScatterParticles(seed, count)` → seeded `[{ang, speed, rot0, rps}]` (deterministic from `seed` via the existing `_mmLcg`; identical across screens sharing a wall).
- `mmScatterGiant(p)` → `{scale, angle}` (scale ramps to the locked peak; angle = `p·2π`).

### Client — draw + integration in `index.html`
- `mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, spriteImg)` (in `transitions.js`): via `_mmMaskRegion(scope, quad, GW, GH)` get the region; draw the backing disc (item bg) sized by cover; stamp `count` erupting copies (`drawImage` rotated/scaled along seeded radial trajectories at `mmScatterDist(p)`); stamp the giant at center (`mmScatterGiant`). `drawImage`/`fillRect` only — no clip/composite. No-op until `spriteImg` is loaded.
- `mmTransitionState` gains a `scatter` branch → mask-family descriptor `{name:'scatter', family:'mask', front:p, scope, params, phase}`; `_dur` returns `mmScatterDuration` for `scatter`.
- `index.html` wiring (same three touch-points as beerfill): `_cvEff` cover gate gains `'scatter'`; the mesh in-canvas mask branch and the `applyTransitionNow` overlay branch call `mmDrawScatter` (passing the preloaded sprite image) before the generic mask fallback.
- **Sprite preload/cache:** a small `mmSpriteCache` keyed by URL holds `Image` objects; the client kicks off `new Image(); img.src = '/media/server/images/<sprite>.png'` when it first sees a `scatter` item (PRELOAD/PLAY). Until decoded, the transition shows the backing disc only (clean cover) and starts stamping once `img.complete`.
- **Wall coordination:** for `scope:wall`, the burst is drawn in **global wall coords** on the mesh canvas and warped per-screen by `mmMeshTransform` (free wall-spanning, like the wall-scope wipe/beerfill); `scope:screen` uses each screen's region.

### iPad-1 performance
Per frame: one backing `fillRect` + ~`count`+1 `drawImage` stamps of a **once-decoded** PNG. `drawImage` is the cheapest path on iPad-1, but ~41 stamps/frame at the default is the cost driver, so **`count` is the tuning lever** and the on-wall sign-off verifies it at 40 (drop it if a given wall struggles). No `clip()`, no `destination-*`.

## Testing
- **Node** (`tests/unit/js/`): `mmScatterPhase`, `mmScatterDuration`, `mmScatterProgress` (cover/reveal endpoints + clamp), `mmScatterDist` (monotonic non-decreasing across `0..1` — the no-pause guard), `mmScatterParticles` (same seed → identical; wall-shared seed → identical across screens; ranges), `mmScatterGiant` (angle = 2π at p=1), and `mmTransitionState` scatter branch (mask family, phase, duration via fillMs/drainMs).
- **Python** (`tests/unit/test_effects.py`): `scatter` in `effect_catalog()` with the params/defaults; `video_filters` audio-only + role→fillMs/drainMs; `_png_has_alpha` true for an RGBA PNG with alpha, false for an opaque/RGB PNG (build tiny fixtures in a tmp dir); `/api/media` returns `transparent` flag.
- **On-wall iPad-1 sign-off** (acceptance): on the calibrated mesh, `scatter` (sprite=hop, scope=wall) as `endEffect`→`startEffect` bursts from the sign center, covers cleanly, reveals, with smooth motion (no pause) at `count` 40.

## Demo / delivery
A **Scatter Demo** playlist (two plasma mesh items handing off via `scatter`, sprite=hop, scope=wall) so it can be run immediately, alongside the existing Beer Demo / Transition Demo.

## Out of scope (YAGNI)
- Exposing giant size / spin / eruption-curve / per-copy size as params (locked constants).
- Non-PNG or non-transparent sprites; per-sprite tint/recolor.
- Server-side video compositing of the burst (visuals stay client-side).
- The other queued brewery effects (bottlecap is just a different transparent PNG into this same effect; frost creep / coaster flip / keg roll / splash crown / wheat part are separate specs).
