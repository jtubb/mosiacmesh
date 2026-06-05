# SCRIPT Synced Animations (identical) — Design (playback engine slice 6)

**Date:** 2026-05-26
**Status:** Design approved, pending implementation plan
**Builds on:** the synchronized playback engine (MVP, video, PAUSE, image/video split).

## Context

`PlayMode.SCRIPT` has existed unused in the model. This slice gives it meaning: a `SCRIPT` playlist item runs a **built-in, named JavaScript animation** full-screen on every display in the group, in lockstep — driven by the shared GoTime clock exactly like media playback (same clock-derived principle, no per-frame server traffic). Mosaic-spanning SCRIPT (each screen renders its region of one large animation) is a deferred follow-up; this slice is **identical on every screen**.

## Goals

- A `SCRIPT` playlist item plays its named animation for its `duration`, synchronized across the group.
- The animation is a **pure function of elapsed time**, which is what makes it synchronized (no shared mutable state to drift).
- No server-side render needed (client-computed); SCRIPT items don't hit the `RENDER_REQUIRED` gate.
- No regression to image/video/SEGMENT playback or PLAY/PAUSE/STOP.

## Non-goals (deferred)

Mosaic-spanning SCRIPT (per-screen viewport) · configurable animation params (count/colors/speed) · user-uploaded animation code · more than one built-in animation. This slice ships exactly one animation, `bouncingBalls`, with defaults.

## Item model

Reuses `{id, file, duration, playmode}`: `playmode = SCRIPT`, and `file` = the **animation name** (e.g. `"bouncingBalls"`, no leading slash — distinguishable from a media URL). No params. `SETPLAYLIST` already stores per-item `playmode` (`"SCRIPT"` → `PlayMode.SCRIPT`); no SETPLAYLIST change needed.

## Server change (small)

The PLAY payload items currently carry `{id, file, duration}`. **Add `playmode`** (the enum name string, e.g. `"SCRIPT"`/`"FULL"`/`"SEGMENT"`) to the items in every path that builds them: the FULL/group PLAY path, `_broadcast_segment_play` (per-client), and `sync_new_client_to_group`. This is the only signal the client needs to recognize a SCRIPT item.

SCRIPT items have `playmode == SCRIPT` (not `SEGMENT`), so a SCRIPT-only playlist's `any(... == SEGMENT)` is false → it plays via the normal group broadcast and never emits `RENDER_REQUIRED`. A SCRIPT item inside a per-client (SEGMENT-containing) playlist is delivered with `playmode "SCRIPT"` and runs the same animation on every screen — fine.

## Client (`index.html`, ES5)

- **Registry:** `var animations = { bouncingBalls: function(ctx, tMs, w, h){ … } };`. Each animation is a **pure function of `tMs`** (the elapsed ms into the SCRIPT item) and the canvas size — no internal state across frames. `bouncingBalls`: a few balls whose `(x,y)` are deterministic functions of `tMs` (e.g. sinusoidal bounce off the edges), drawn as filled circles on a cleared canvas. Same `tMs` ⇒ same frame on every display.
- **rAF shim (iOS 5):** `var _raf = window.requestAnimationFrame || window.webkitRequestAnimationFrame || function(cb){ return setTimeout(cb, 16); };` and a matching `_caf`. (iOS 5 Safari only has the `webkit`-prefixed form.)
- **`showItem` branch:** at the top, tear down any prior animation and video (`clearScript()`, `clearVideo()`). If `item.playmode === 'SCRIPT'`: build a full-viewport `<canvas>` in `#canvas`, record `playback.scriptIndex = i`, and start the loop. Else: existing media path (video / img by extension).
- **Clock-driven loop:** each frame, if `!playback.active` return; compute `pos = playlistIndex(GoTime.now() - playback.startEpoch, durations, loop)`; if `pos === null` → `stopPlayback()`; if `pos.index !== playback.scriptIndex` → stop the loop (a transition is underway; `renderPlayback`'s scheduled `setTimeout` will `showItem` the next item, whose `clearScript` already ran); else clear the canvas and call `animations[file](ctx, pos.offsetMs, w, h)`, then request the next frame (handle stored in `playback.scriptRaf`).
- **`clearScript()`** (mirrors `clearVideo`): cancel `playback.scriptRaf`, remove the canvas, reset `scriptIndex`. Called from `showItem` (top) and `stopPlayback`.
- **PRELOAD:** skip `SCRIPT` items (nothing to fetch) — count them immediately settled so they don't fire a doomed media request. (PRELOAD items already carry `playmode` from the SETPLAYLIST payload.)

Boundary advance is unchanged: `renderPlayback`'s `setTimeout(duration - offset)` still fires at the SCRIPT item's end and advances to the next item (tearing the animation down via `showItem`/`clearScript`).

## Edge cases

- SCRIPT item with an unknown animation name → `animations[file]` is undefined → render nothing (blank canvas), no crash (guard `if (animations[file])`).
- SCRIPT mixed with images/video in one playlist → handled per item; transitions tear down the canvas.
- A SCRIPT-only playlist needs no calibration and no RENDER.

## Testing

- **pytest:** PLAY items include `playmode` (assert the field is present in the broadcast payload for a FULL and a SCRIPT item); a SCRIPT-only playlist `PLAY` returns `SUCCESS` (not `RENDER_REQUIRED`) and broadcasts via the group path.
- **Playwright (light, client is the substance):** a `SCRIPT` item builds a `<canvas>` and starts the loop (`playback.scriptRaf` set); **determinism check** — `animations.bouncingBalls` called twice at the same `tMs` produces identical output (sample a couple of computed ball positions, or compare canvas pixel data) — this is the synchronization guarantee; a transition to a non-SCRIPT item and `STOP` both tear the canvas down (`playback.scriptRaf` cleared, no `#canvas canvas`).

## ES5 / legacy

Canvas 2D and the `webkit`-prefixed rAF are available on iOS 5 Safari. All additions are `var`/`function`, no ES6. The animation runs on the A4's GPU/CPU — `bouncingBalls` is a handful of circles, well within budget.
