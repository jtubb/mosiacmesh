# Beer-Fill Transition ("beerfill") — Design

**Date:** 2026-06-23
**Status:** Approved (pending spec review)

## Overview

A new transition effect, **`beerfill`**, that joins the existing transition family
(`fade`, `wipe`, `slide`, `zoom`, `iris`, `dissolve`). It is a **mask/cover**
effect: beer fills the display from the bottom up to cover outgoing content, then
drains back down to reveal incoming content — a two-phase "pour-and-empty"
between playlist items. It is designed to span the calibrated mesh wall as one
giant glass (`scope: wall`) or fill each screen independently (`scope: screen`).

It must run on the 1st-gen iPad-1 display clients (iOS 5.1 / Safari 5.1), so all
client code is **ES5 only** and uses only canvas primitives those devices handle
well (`fillRect`, `drawImage`, simple polyline paths). It is lighter than the
existing `plasma` SCRIPT animation.

## Role-aware two-phase behavior

The transition engine applies an item's `startEffect` when it begins and its
`endEffect` when it ends. `beerfill` reads which role it is playing and behaves
accordingly:

- **`endEffect` role → FILL (cover):** a wide pour stream falls from the top and
  the beer level rises from the bottom (rising level, wavy foam head, bubbles in
  the beer, scattered foam bubbles) until the screen is fully covered. Phase
  duration = `fillMs`. Progress `p∈[0,1]` maps beer coverage `0 → full` (beer
  occupies the bottom fraction `p`; outgoing content shows in the top `1−p`).
- **`startEffect` role → DRAIN (reveal):** the screen starts fully covered in beer
  and the level recedes downward, revealing the incoming content top-first as the
  surface drops. No pour stream during drain. Phase duration = `drainMs`. Progress
  `p∈[0,1]` maps beer coverage `1 → 0` (beer occupies the bottom fraction `1−p`).

Placing `beerfill` as item A's `endEffect` and item B's `startEffect` produces the
full pour-then-empty handoff. Each placement carries the same params object, so a
single `{beerType, scope, fillMs, drainMs}` fully describes the transition; the
effect selects `fillMs` vs `drainMs` from its role.

## Parameters

Exposed via `ParamSpec` so they appear in `/api/effects` and the playlist editor,
matching the other effects:

| key        | type    | default  | choices / range            | meaning |
|------------|---------|----------|----------------------------|---------|
| `beerType` | choice  | `pale`   | `pale`, `amber`, `stout`   | preset bundle: beer gradient (top/bottom), foam color, head thickness, bubble density |
| `scope`    | choice  | `wall`   | `screen`, `wall`           | one glass across the whole sign vs per-screen |
| `fillMs`   | number  | `2500`   | min `0`                    | fill (cover) phase duration |
| `drainMs`  | number  | `2500`   | min `0`                    | drain (reveal) phase duration |
| `audioFade`| boolean | `true`   | —                          | fade audio on video items (server-side `_afade`) |

### `beerType` presets (the three validated in the visual companion)

| beerType | beerTop  | beerBot  | foam     | headH (frac of fill height) | bubbleDensity | foamBubbles |
|----------|----------|----------|----------|------------------------------|---------------|-------------|
| `pale`   | `#F6C744`| `#E0A21A`| `#FFF8E7`| 0.11                         | high (≈34)    | ≈30         |
| `amber`  | `#C9791C`| `#8A4A0E`| `#F3E0C0`| 0.14                         | medium (≈22)  | ≈26         |
| `stout`  | `#3A241A`| `#160C07`| `#E8C9A0`| 0.20                         | low (≈12)     | ≈34         |

(Counts are tuned against the wall/screen pixel area at implementation time; the
ratios above are the validated look. The pour stream is wide — ≈10% of screen
width — with a lighter center column.)

## Architecture

### Server — `effects.py`

Add a `BeerFillEffect(Effect)` subclass with `name = "beerfill"`, `label = "Beer
Fill"`, the `params` list above, and `@register` so it auto-populates
`EFFECTS` / `effect_catalog()` / `/api/effects`. Like the other visual effects,
its `video_filters(self, role, params, ctx)` returns `([], _afade(role, params,
ctx))` — **no server-side video frame manipulation; the visual is entirely
client-side.** `_afade` already keys off `role`/`duration`; pass the role-selected
duration (`fillMs` for the cover/out role, `drainMs` for the reveal/in role) so the
audio fade length matches the visual phase.

No render-pipeline changes: `beerfill` is a transition on top of items, and SCRIPT
items don't render. Catalog test added to `tests/unit/test_effects.py`.

### Client — pure helpers in `js/transitions.js` (ES5, exported on `root`/`globalThis`)

New pure functions, unit-testable in isolation exactly like `mmSlideOffset` /
`mmIrisCircle` / `mmDissolveOrder`:

- `mmBeerPalette(beerType)` → `{beerTop, beerBot, foam, headH, bubbleDensity, foamBubbles}` (the preset table; unknown type → `pale`).
- `mmBeerPhase(role)` → `'fill'` or `'drain'` (`end`→fill, `start`→drain).
- `mmBeerDuration(params, role)` → `fillMs` or `drainMs`.
- `mmBeerLevel(phase, p)` → beer coverage fraction `∈[0,1]` (fill: `p`; drain: `1−p`). `p` is clamped progress.
- `mmFoamWaveY(xFrac, t, amp, baseY)` → wavy foam-top y for a column (the two-sine sample used by the preview), pure given `t`.
- `mmBeerBubbles(seed, count, ...)` / `mmFoamBubbles(seed, count, ...)` → **seeded** position/size/alpha arrays (deterministic from `seed` so positions are stable across frames and identical across screens that share a wall — no per-frame `Math.random` in the draw path). Seed derives from the shared `playSeed` already threaded into SCRIPT items.

These return plain data; the draw step consumes them.

### Client — draw integration in `index.html` (mask family)

`beerfill` is a **mask/cover** effect. Reuse the established cover plumbing:

- **Mesh SCRIPT items** (`scriptSpan: mesh`): draw the beer cover **in-canvas** inside the same canvas `runScriptLoop` already drives (frame-locked, the approach that fixed the earlier wipe choppiness), after the item's own draw.
- **Media / element items**: draw the cover on the existing overlay cover canvas (`ensureCover` / `#mmTransCoverCanvas`). Extend the `_cvEff` gate so `beerfill` creates the cover canvas (the same fix applied for iris/dissolve).

Per frame, the cover draw:
1. Compute `level = mmBeerLevel(phase, p)`; derive the beer surface line.
2. `fillRect` the beer body (vertical gradient `beerTop→beerBot`) for the covered region.
3. Stamp pre-seeded rising bubbles within the beer (`arc`+`fill`, capped).
4. Fill the foam band with a **wavy top** built as one polyline path (`mmFoamWaveY` samples), then stamp pre-seeded scattered foam bubbles.
5. During **fill** only, `fillRect` the wide pour stream from the top to the surface, plus a lighter center column and a few splash dots at impact.

**No `clip()`, no `destination-*` compositing.**

### Mesh / scope geometry

- **`scope: wall`** — the beer level is a single line in **global wall coordinates**
  (height from `display.meshGlobal`). Each screen maps the global surface line into
  its own canvas via its `meshQuad` / `measuredPerimeter` (the same data the
  wall-scope `wipe` uses), so screens lower on the physical wall fill first and the
  foam line sweeps up through the higher screens as one. The pour stream descends
  from the top of the **sign**, so only the top-most screens render it (others see
  the stream already past them / off-canvas).
- **`scope: screen`** — `level` and pour apply to each screen's own canvas
  independently; every screen pours and fills in lockstep.

This mirrors how `mmTransitionState` already carries `{front, scope, params}` for
the wall-coordinated wipe; `beerfill` adds the level/foam/pour fields to that
descriptor.

## Testing

- **Node `--test`** (`tests/unit/js/`): `mmBeerPalette`, `mmBeerPhase`,
  `mmBeerDuration`, `mmBeerLevel` (fill/drain endpoints + clamping), `mmFoamWaveY`
  (determinism for fixed `t`), and seeded-bubble determinism (same seed → same
  positions; wall-shared seed → identical across screens).
- **Python** (`tests/unit/test_effects.py`): `beerfill` present in
  `effect_catalog()` with the five params + defaults; `video_filters` returns an
  empty video filter list and a non-empty audio fade for both roles.
- **Existing full-catalog assertions** (e.g. `test_playlists.py`'s effects-list
  test) updated to include `beerfill`.
- **iPad-1 on-wall sign-off** (acceptance): on the calibrated "OEB Sign 1" mesh,
  `beerfill` as `endEffect`→`startEffect` across two items fills bottom-up across
  the sign with a visible pour and drains to reveal — smooth, no flicker, at
  `fillMs`/`drainMs` timing.

## Demo / delivery

Wire `beerfill` into a demo playlist (two plasma mesh items with
`endEffect`/`startEffect = beerfill`, `scope: wall`, `pale`) so it can be run
immediately after implementation, alongside the existing "Transition Demo".

## Out of scope (YAGNI)

- Per-parameter foam/bubble tuning beyond the three `beerType` presets.
- A standalone looping "ambient beer" SCRIPT animation (this is a transition only;
  could be a separate future spec).
- Server-side video frame compositing of the beer (visual stays client-side).
