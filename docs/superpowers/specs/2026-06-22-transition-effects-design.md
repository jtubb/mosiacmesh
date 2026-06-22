# Transition Effects — Client-Side Visual + Baked Audio Design

**Date:** 2026-06-22
**Status:** Design approved, pending implementation plan
**Supersedes / extends:** `2026-05-26-effects-framework-design.md` (the baked-only framework). That slice baked `fade`/`audiofade` into rendered video via ffmpeg and deferred images, FULL video, and SCRIPT animations. This design makes **visual** transitions client-side so they work on **all** media types, while keeping **audio** fades baked (the only mechanism the iPad-1 allows).

## Context

Playlist items already carry `startEffect`/`endEffect` (`{name, params}` or `null`) and an effect catalog (`effects.py`, `GET /api/effects`) that drives editor dropdowns. Today only SEGMENT/INDIVIDUAL **video** gets a baked ffmpeg `fade`/`afade`; `wipe` is a no-op placeholder; images, FULL video, and SCRIPT animations get nothing.

The goal is a uniform transition concept — **fade** and **wipe (with direction)**, at **start and/or end**, with a **configurable duration**, on **all media types** — plus **audio fades for videos**.

### Hardware constraint that shapes the architecture

iOS Safari (including iOS 5 on the 1st-gen iPad display clients) makes `HTMLMediaElement.volume` **read-only** — JS cannot ramp it — and has **no Web Audio API** (iOS 6+), so there is no client-side way to fade a video's audio. Therefore:

- **Visual transitions (fade, wipe)** → **client-side** (uniform across images, SCRIPT, video, FULL), clock-driven, instant to edit (no re-render).
- **Audio fade (video only)** → stays **baked** (ffmpeg `afade`) on per-screen-rendered video — the only mechanism the hardware permits.

## Goals

- Fade and directional wipe on every media type, at start and/or end, with a per-item configurable duration.
- Wipe supports both **per-screen** (every panel wipes simultaneously) and **wall-spanning** (one wipe sweeps across the whole calibrated wall), selectable per item.
- Audio fade for videos (baked), toggleable per transition, default on.
- All panels transition in lockstep (driven by the shared clock; no new handshake).
- No regression to SEGMENT/INDIVIDUAL/FULL/SCRIPT render or playback; the iPad-1 ES5 constraint respected.

## Non-goals (deferred)

- Baked audio fade for **FULL** video (no per-screen render pass; FULL is one central encode — a possible later hook). FULL still gets the client-side **visual** transition.
- Cross-fade *between* items (the per-item-against-background model stands; each item transitions against its own background).
- More visual effects beyond fade + directional wipe (the catalog stays extensible; new effects are added later).
- Filesystem plugin auto-discovery (in-process registry only, as today).

## Data model

`startEffect` / `endEffect` per item — `null` or:

```json
{ "name": "fade" | "wipe",
  "params": {
    "duration":  600,
    "direction": "left",      // wipe only: left|right|up|down
    "scope":     "screen",    // wipe only: screen|wall
    "audioFade": true         // video only
  } }
```

- One transition per boundary. `fade` ignores `direction`/`scope`. `audioFade` is ignored on non-video items.
- **Normalization** (backward compatibility): a bare-string value, the old standalone `"audiofade"` name, or a missing param resolves against the effect's declared defaults; a legacy `audiofade` is treated as no visual transition + `audioFade:true`.

## `effects.py` — schema registry + audio-only baking

The plugin classes remain the catalog that drives the editor and (for visuals) describe params to the client. Their render contribution shrinks to **audio only**:

- `FadeEffect` (`name="fade"`, label "Fade"): `params = [duration, audioFade]`. `video_filters` returns `([], [afade...])` when `audioFade` is on for that role, else `([], [])`. **No baked video filter** (the visual fade is client-side).
- `WipeEffect` (`name="wipe"`, label "Wipe"): `params = [direction, duration, scope, audioFade]`. `video_filters` returns the `afade` when `audioFade` is on, else `([], [])`. No baked video filter.
- The standalone `AudioFadeEffect` is **removed** (folded into the `audioFade` toggle).

`GET /api/effects` returns the two effects with their param schemas (number `duration`; choice `direction`; choice `scope`; boolean `audioFade`). A new `"boolean"` ParamSpec type is added for `audioFade`.

## Render path (`render.py`)

- The effect hook in `render_group_async` appends **only the audio fragments** returned by `video_filters` (never a video fragment) onto SEGMENT/INDIVIDUAL **video** items. Image/FULL items: no baked effect (unchanged).
- **Render token**: drop the visual effect bits; include an **audio-fade signature** `(role, duration)` for each of start/end **only when `audioFade` is on**. Result: editing a fade/wipe or its `direction`/`scope`/visual-duration is **instant** (no re-render); only an audio-fade change (toggle, or duration while audio on) re-renders. Same hashing pattern as `backgroundColor`, just narrowed to the audio-relevant fields.

## Client-side transition engine (`index.html`, ES5 / Safari 5.1)

A small engine applies the visual transition to the element currently mounted by `showItem` (`<canvas>` for SCRIPT, `<img>` for image, `<video>` for video), timed from the shared clock so all panels move together.

**Timing (pure function, unit-tested):** `mmTransitionState(startEffect, endEffect, offsetMs, durationMs, screenRect)` → `{ role: 'in'|'out'|'none', type, opacity, wipeReveal, direction }`:
- `offsetMs < startDur` → role `in`, progress `p = offsetMs/startDur` (0→1).
- `offsetMs > durationMs − endDur` → role `out`, progress `p = (durationMs − offsetMs)/endDur` (1→0).
- otherwise → `none` (fully visible).

**Apply:**
- **fade** → element opacity = `p` via `-webkit`/standard CSS opacity (compositor-cheap; Safari-5.1-safe). Fade-in starts at mount; fade-out is scheduled at clock offset `durationMs − endDur`.
- **wipe** → an opaque cover `<div>` (the item's `backgroundColor`) over the element, slid off in `direction` via `-webkit-transform: translate(...)`. (Avoids `clip-path`, which WebKit 534 lacks; transform is GPU-composited.) Reveal fraction = `p`.

No `video.volume` is touched (impossible on iOS). The engine adds no per-frame work to the SCRIPT RAF loop beyond setting a style; for video/image it relies on CSS transitions + a single scheduled out-timer.

### Wipe scope

- **`screen`**: identical wipe on every panel; cover slides off over `durationMs`.
- **`wall`**: each panel uses its normalized global rect (from `meshCells` bbox, or `meshGlobal` position) to compute when the wall-wide front crosses it. For a left→right wipe over `D`, a panel spanning global x `[a,b]` reveals from `a·D` to `b·D` (delay `a·D`, sub-duration `(b−a)·D`); up/down use the y-range; right/left and down/up invert. The reveal sweeps across the wall as one motion. **Falls back to `screen`** when no geometry is present (mirror or uncalibrated). Each panel's per-segment start is clock-aligned, so the fleet stays in lockstep.

## Editor (`admin.html` / `js/timeline/modals/playlist-editor.js`)

The existing catalog-driven Start effect / End effect controls render the new params dynamically: `duration` (number) always; `direction` (choice) + `scope` (choice) for `wipe`; `audioFade` (checkbox) shown **only when the item is a video**. Selecting an effect writes `{name, params}`; "None" writes `null`. These flow through SAVE_PLAYLIST/ASSIGN_PLAYLIST as today.

## Sync

- **Visual**: each panel derives role + progress from the shared-clock `offsetMs`; out-transitions and wall-wipe per-segment starts are scheduled at clock offsets. Cross-panel skew is bounded by the clock residual (tens of ms) — imperceptible for a fade/wipe.
- **Audio**: baked into the rendered file, so it plays in lockstep with the video frames inherently.

## Error handling

- Unknown effect `name` (stale data) → treated as no transition (visual skipped; no afade baked); logged, render/playback never fails.
- Effect on a media type that can't honor part of it (audioFade on image/SCRIPT, baked audio on FULL) → that part is silently a no-op; the visual transition still applies.
- Malformed/missing params → resolved against `ParamSpec` defaults.
- `scope:"wall"` without geometry → falls back to per-screen wipe.

## Testing

- **Pure client helper** `mmTransitionState(...)` (node `--test`, no DOM): role selection across the start/middle/end windows; opacity ramp endpoints; wall-spanning per-screen reveal fraction from a global rect (front crosses `[a,b]` at `a·D`..`b·D`); direction inversion; `none` outside the windows; determinism.
- **`effects.py`**: catalog includes `fade` + `wipe` with correct param schemas (incl. boolean `audioFade`, choice `scope`); `video_filters` returns `afade` only when `audioFade` on and never a video fragment; legacy normalization.
- **`render.py`**: a SEGMENT/INDIVIDUAL video with `fade`+`audioFade` bakes `afade` (in/out, duration honored) and no video filter; `audioFade:false` bakes nothing; the render token includes the audio signature and is unchanged by visual-only param edits (regression guard for instant edits).
- **Playwright (`admin.html`)**: effect dropdowns show `fade`/`wipe`; `wipe` reveals `direction` + `scope`; `audioFade` checkbox appears only on video items; selections write the expected `{name, params}`.

## Legacy / ES5

- `index.html` (ES5) gains the transition engine: CSS opacity + `-webkit-transform` cover slide + clock-offset timers. All Safari-5.1-safe and compositor-cheap; no `clip-path`, no `video.volume`, no Web Audio.
- Server-side Python (`effects.py`, `render.py`) + the desktop `admin.html` editor change. The baked audio path is unchanged in mechanism (ffmpeg `afade`); only its triggering moves under the `audioFade` toggle.
