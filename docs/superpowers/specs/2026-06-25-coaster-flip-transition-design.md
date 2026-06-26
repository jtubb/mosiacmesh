# Coaster Flip Transition ("coasterflip") — Design

**Date:** 2026-06-25
**Status:** Implemented (v2 — tumbling two-faced coaster); on-wall verified
**Area:** `effects.py` (catalog), `js/transitions.js` (pure tumble/disc math + `mmTransitionState` branch), `index.html` (mesh in-canvas apply + element apply). Editor is data-driven and needs no change.

> **Revision history.** v1 (the original approved design, preserved at the bottom under *v1 — simple edge-on fold*) was a single edge-on fold with a cardboard sliver. On-wall it read as "just a flip." The user asked for a **coaster** that **rounds in first**, then **tumbles multiple times** about an **offset, wobbling axis**, showing **content on the front face and a coaster PNG on the back**. v2 below is that redesign. The **element/media path keeps the v1 simple fold** (the richer tumble needs in-canvas global drawing); the **mesh SCRIPT path** is the full v2.

## Overview

A **transform-family** transition, `coasterflip` — the screen content is treated as printed on a beer coaster that is tossed and **tumbles**. As an `endEffect` the content first **rounds into a circular coaster disc**, then **tumbles** (multiple half-turns) about an off-center, slightly-wobbling axis, flashing **front (the content) ↔ back (a cork coaster PNG)** on each turn, and ends **edge-on**. As a `startEffect` the incoming content tumbles the reverse way and **opens back out** of the disc. Placed as item A's `endEffect` and item B's `startEffect`, the two edge-on ends meet → one continuous coaster tumble across the handoff.

Runs on the 1st-gen iPad-1 (iOS 5.1 / Safari 5.1): client code is **ES5 only**, and the flip is faked in **2D** — `ctx.scale(|cos θ|, 1)` for the turn, with the back face shown when `cos θ < 0`. No CSS 3D, no `matrix3d`, no WebGL, no `clip()`.

## Architecture — transform family, raw-progress front + phase

`coasterflip` rides the **same descriptor path** slide/zoom use, but with its **own `mmTransitionState` branch** (before slide/zoom) because it carries a `phase`:

- `mmTransitionState` returns, for `name === 'coasterflip'`: `effect: { name:'coasterflip', family:'transform', front: p, scope, params, phase }` (`wipe:null`). `phase` = `mmCoasterPhase(role)` → `'cover'` for the out role, `'reveal'` for the in role.
- **Raw progress `front: p`** (NOT local-progress inverted — like slide/zoom): out role `p` 1→0, in role `p` 0→1.
- All the tumble math is derived from `(front, phase, flips)` in the pure helper `mmCoasterTumble` — there is **no per-frame state** in the apply.

## Pure helpers (testable core), in `js/transitions.js`

- **`mmCoasterPhase(role)`** → `'cover'` (out) | `'reveal'` (in). The whole effect is sequenced from this + `front`, so no extra state is threaded through `runScriptLoop`.

- **`mmCoasterTumble(front, phase, flips, roundFrac)`** → `{ scale, round, showFront, wobble, theta }`:
  - Splits the local progress into a **round-in/out fraction** (`roundFrac`, default 0.25) and a **tumble fraction** (`tp`):
    - *reveal:* tumble first (`tp` 1→0), then round open (`round` 1→0) at the end.
    - *cover:* round in (`round` 0→1) first, then tumble (`tp` 0→1).
  - `theta = tp * (flips - 0.5) * π` — `flips - 0.5` half-turns gives `flips` edge-crossings, ending **edge-on** at `front=0` for a continuous A→B handoff.
  - `scale = |cos θ|` (the 2D fake of the turn — content compresses to a line at each edge-on crossing).
  - `showFront = cos θ ≥ 0` (which face is toward the viewer — alternates every crossing).
  - `wobble = sin θ * 0.1` (radians — small rotational wobble, peaks at edge-on).
  - `round` ∈ [0,1] — how far the disc has rounded in (0 = full rectangle, 1 = full coaster disc).

- **`mmDrawCoasterDisc(ctx, reg, round, bg)`** — masks the content down to a **centered circle** (a round coaster) by filling everything *outside* a shrinking circle with `bg`. No `clip()`: 4 outer strips (region beyond the circle's bbox) + the bbox corners rounded by `R` (which turns the bbox square into the circle). `round` 0 = no mask (circle ≥ region), 1 = full disc (`R ≈ 0.48·min(w,h)`). Lerps `R` from the region half-diagonal down to the disc radius. Drawn under the caller's fold transform, so it compresses with the turn.

- **`mmDrawCoasterCorners(ctx, reg, radius, bg)`** — fills the four corner cutouts of `reg` with `bg` (quarter-disc each). Used by `mmDrawCoasterDisc` to round the bbox into a circle; also independently testable.

- **`mmCoasterColor(name)`** + `_COASTER = { kraft:'#b9935f', cork:'#c8a06a', slate:'#5a5e63' }` — the cork tone for the edge band (default kraft).

- **`mmFlipFactor(front, axis)`** → `{ sx, sy, alpha, edge }` — the **v1** helper, retained for the **element/media path** (simple edge-on fold, see v1 section).

Every choice that could be wrong (when each face shows, how rounded, how much wobble, the disc mask geometry) lives in node-tested helpers; the apply glue only consumes the numbers.

## `effects.py` — `Effect` subclass

`CoasterFlipEffect(Effect)`, single-`duration` (a coasterflip instance only folds (endEffect) or opens (startEffect), never both):

- `name = "coasterflip"`, label `"Coaster Flip"`.
- `params`:
  - `axis` — choice `[horizontal, vertical]`, default `"horizontal"`.
  - `coaster` — choice `[kraft, cork, slate]`, default `"kraft"` (the edge-band cork tone).
  - `sprite` — string, default `"coaster"` — the **back-face PNG** (any transparent PNG; `""` = blank back). Resolved via `mmScatterSpriteUrl` (shared with scatter/kegroll/frostcreep).
  - `flips` — number, default `5`, min `1`, max `12` — half-turns in the tumble.
  - `scope` — choice `[screen, wall]`, default `"wall"`.
  - `duration` — number, default `1800`, min `0`.
  - `audioFade` — boolean, default `True`.
- `video_filters(role, params, ctx)` returns audio-only (`_afade`) when `audioFade` on, else `([], [])`. No baked video filter.

## Apply — mesh in-canvas (`index.html`, `runScriptLoop` mesh SCRIPT)

Per-frame, when `stc.effect.name === 'coasterflip'`:

1. **Pre-content (fold transform + face select):** compute `_cfT = mmCoasterTumble(front, phase, flips, 0.25)` and `_cfImg = mmSprite(mmScatterSpriteUrl(sprite))`. Under the mesh affine, fold about an **offset pivot** (`pivot = wall-center + 0.05·GW` on the fold axis) with the **wobble rotate**, then `ctx.scale` on the chosen axis by `_cfT.scale` (other axis = 1); dim `globalAlpha = 0.4 + 0.6·scale`.
2. **Gated content draw:** if `!_cfT.showFront` → fill `bg` + stamp the **back coaster PNG** centered (`mmStampSprite`, size `0.92·min(GW,GH)`); else draw the normal animation (the **front** content).
3. **Disc mask (post-content, opaque):** force `globalAlpha = 1`, then `mmDrawCoasterDisc(ctx, {0,0,GW,GH}, _cfT.round, bg)` — rounds whichever face is showing into the circular coaster. (Opaque so dimmed content never bleeds outside the disc.)
4. **Edge-band thickness (only near edge-on, `scale < 0.2`):** drop to the pre-scale transform (mesh affine → translate to the fold pivot → rotate by wobble), and draw a **cork capsule** (rounded-end band, `mmCoasterColor(coaster)`) along the fold — length `0.9·min(GW,GH)`, thickness `0.025·min(GW,GH)`, opacity ramping `(0.2 - scale)/0.2`. Reads as the coaster's edge at each turn-through; gone when open. (This replaced v1's full-wall, always-on sliver, which read as a persistent center bar.)

`axis === 'vertical'` swaps the scaled axis, the pivot axis, and the capsule orientation.

## Apply — element (media / mirror, `applyTransitionNow`)

Keeps the **v1 simple fold**: `mmFlipFactor(front, axis)` → `-webkit-transform: scaleX/scaleY` + `opacity`. No round-in, no back face, no tumble — the rich v2 needs in-canvas global drawing. Documented limitation, consistent with slide/zoom on media.

ES5 / Safari-5.1 safe throughout: `ctx.scale`, `ctx.rotate`, `ctx.translate`, `ctx.setTransform`, `globalAlpha`, `fillRect`, `arc`, `drawImage`, `-webkit-transform`. No `clip()`, `matrix3d`, 3D, or filters.

## Render-token / server impact

**None beyond the catalog.** Visual is client-side; only the audio-fade signature (`role`, `duration`, when `audioFade` on) enters the render token — `_audio_fade_sig` reads only `audioFade`. Editing `axis`/`coaster`/`sprite`/`flips`/`scope` (or `duration` while `audioFade` off) is instant, no re-render. A regression-guard test confirms the new visual params (`sprite`, `flips`) never change the token.

## Back-face sprite asset

`media/server/images/coaster.png` — a 1000×1000 (1 MP) cork disc placeholder (`tools/_make_coaster_sprite.py`), generated at 1 MP to stay under the iPad-1 ~3 MP image-decode cap. Operator-selectable via the `sprite` param.

## Testing

- **Node (`tests/unit/js/test_coasterflip.js`):** `mmCoasterPhase` role mapping; `mmCoasterTumble` cover/reveal sequencing, face alternation across crossings, edge-on at `front=0`, clamps; `mmDrawCoasterCorners` corner cutouts; `mmDrawCoasterDisc` (no draw at `round=0`, strips+cutouts at `round=1`); `mmFlipFactor` (v1, retained); `mmTransitionState` coasterflip branch (`family==='transform'`, `front===p`, `phase` per role). 10 cases; full JS suite green.
- **Python (`tests/unit/test_effects.py`):** `coasterflip` in `effect_catalog()` with v2 params/defaults (`sprite="coaster"`, `flips=5`, `duration=1800`); `video_filters` audio-only + single-duration role-aware; `audioFade:false` bakes nothing. **`tests/unit/test_mosaic.py`:** render-token regression guard varying `sprite`/`flips` (token-neutral).
- **On-wall iPad-1 sign-off (acceptance):** content rounds into a circular coaster, tumbles ~`flips` times about an offset/wobbling axis showing the cork back face on the turns, edge-on at the handoff, cork edge band visible only at each turn-through, no content bleed outside the disc, no stray bar; smooth at wall scale.

## Demo / delivery

A **Coaster Flip Demo** playlist (two plasma mesh items handing off via `coasterflip`, axis=horizontal, coaster=kraft, sprite=coaster, flips=5, duration=1800), alongside the existing Beer / Scatter / Keg Roll / Frost Creep / Transition demos.

## Out of scope (YAGNI)

- Real 3D / perspective flip (impossible on iPad-1 — 2D scale only).
- The tumble / back face / round-in on the element/media path (mesh-only; element keeps the v1 fold).
- The remaining brewery effects (splash crown / wheat part — separate specs).

---

## v1 — simple edge-on fold (superseded, retained for the element/media path)

The original approved v1: a single edge-on fold via **`mmFlipFactor(front, axis)`** → `{ sx, sy, alpha, edge }` (`sx`/`sy` = chosen-axis scale = `front`, other axis 1; `alpha = 0.35 + 0.65·front`; `edge = 1 - front`), content scaled about the wall center with a thin cardboard sliver at the fold, `duration` default 700, no `sprite`/`flips`. The mesh path replaced this with the v2 tumble; the **element/media path still uses `mmFlipFactor`** unchanged.
