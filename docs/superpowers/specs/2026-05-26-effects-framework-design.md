# Transition Effects — Plugin Framework Design

**Date:** 2026-05-26
**Status:** Design approved, pending implementation plan
**Builds on:** the per-screen render pipeline (`render_group_async`, ffmpeg `pad`/`perspective`/`scale`, OpenCV warp) and the playlist editor (which already carries `startEffect`/`endEffect` as fields and shows them as disabled dropdowns).

## Context

Playlist items have carried inert `startEffect`/`endEffect` fields since the playlist-editor slice. This slice gives them meaning via an **extensible effect plugin framework**: each effect is a self-describing plugin that declares its parameters and contributes ffmpeg/OpenCV operations during render, **baking the transition into each screen's per-screen media**. Because the effect is baked into the rendered file the clients already play, synchronization is free and the ES5 display client (`index.html`) is untouched.

This slice ships the **framework** plus **one working effect** (`fade`, video — visual + audio) and a registered **`wipe` placeholder** (param schema, no-op render) that marks the extension point. Real wipe/other effects come in later slices.

## Goals

- A plugin framework where adding an effect is: define a class, register it — and it automatically appears in the editor (with its parameter controls) and is honored at render.
- Effects are self-describing: each declares a parameter schema the editor renders dynamically.
- One real baked effect end-to-end: `fade` on rendered video (ffmpeg `fade` + `afade`), proving the pipeline.
- A `wipe` placeholder demonstrating extensibility without implementing the geometry yet.
- No regression to existing SEGMENT/INDIVIDUAL/FULL/SCRIPT render or playback.

## Non-goals (deferred to later slices)

- Real `wipe` rendering geometry (placeholder only this slice).
- Effects on items that do **not** already render — FULL video and all images. A temporal effect can't bake into a static PNG (no time axis), and FULL video has no per-screen render pass to ride. Effects on these are stored and shown in the editor but not baked yet.
- Cross-fade between items (the per-item-against-background model stands).
- Filesystem auto-discovery of plugins (in-process registry only).
- Client-side (non-baked) effects.

## Architecture

### `effects.py` (new module)

```python
class ParamSpec:
    """One declared effect parameter."""
    # key, type ("number" | "choice"), default, and optional min/max or choices

class Effect:
    name = ""          # registry key; matches the effect field's "name"
    label = ""         # human label for the editor
    params = []        # list[ParamSpec]
    def video_filters(self, role, params, ctx):
        """role = 'start' | 'end'; params = resolved dict; ctx = {'duration_ms', 'out_w', 'out_h'}.
        Returns (video_fragments, audio_fragments): lists of ffmpeg filter strings to append."""
        return ([], [])
    def image_ops(self, role, params, ctx):
        """Deferred this slice: temporal effects can't bake into a static image. Returns None."""
        return None
```

- **Registry:** module-level `EFFECTS = {}` populated by a `register(cls)` helper/decorator; `get_effect(name)` looks up by name; `effect_catalog()` returns the serializable schema list. No filesystem discovery.
- **Resolved params:** an effect field carries only the params the user set; missing params fall back to the `ParamSpec` defaults when resolved (so the wire payload may be sparse).

### Registered plugins this slice

- **`FadeEffect`** (`name="fade"`, working). `params = [duration (number, default 600 ms)]`. `video_filters`:
  - `role="start"` → video `["fade=t=in:st=0:d=<d>"]`, audio `["afade=t=in:st=0:d=<d>"]`
  - `role="end"` → video `["fade=t=out:st=<(duration_ms-d)/1000>:d=<d>"]`, audio `["afade=t=out:st=<...>:d=<d>"]`
  (`d` in seconds; `st` start times derived from `ctx['duration_ms']`.)
- **`WipeEffect`** (`name="wipe"`, placeholder). `params = [direction (choice: left/right/up/down, default left), duration (number, default 600 ms)]`. `video_filters` returns `([], [])` for now (no-op). It registers and appears in the catalog/editor; baking is a later slice.

### Catalog API

`GET /api/effects` → `{"effects": [ {"name","label","params":[{"key","type","default","choices?","min?","max?"}]} ]}`. Drives the editor's dropdowns and parameter inputs dynamically.

## Model & render hook (`server.py`)

### Item model

`startEffect`/`endEffect` change from a bare name string to an object or `null`:

```
startEffect: { "name": "fade", "params": { "duration": 600 } }   # or null
endEffect:   { "name": "fade", "params": { "duration": 600 } }   # or null
```

- `_build_media_elements` stores these objects on `MediaElement` (default `None`); `_media_item_payload` carries them with `getattr(..., None)` guards.
- `compute_render_token` includes `startEffect`/`endEffect` in its hashed per-item tuple, so changing an effect or any param invalidates the cached render (same pattern as `backgroundColor`).
- Backward compatibility: a field that is still a bare string (older data) or `None` is treated as "no resolved effect" — the render hook tolerates both shapes via a small normalizer.

### Render hook

`render_group_async`'s **video** branch composes effect filters onto the existing chain:
- After building the per-screen `pad`/`perspective`/`scale` video chain and the audio handling, it resolves each of the item's `startEffect`/`endEffect`, looks up the plugin via `get_effect(name)`, and calls `video_filters("start"/"end", params, ctx)` with `ctx = {duration_ms: me.duration, out_w, out_h}`.
- The returned video/audio fragments are **appended** to that screen's filter chain (fade applied last, over the warped frame). The ffmpeg command builders (`build_ffmpeg_perspective_cmd`, `build_ffmpeg_individual_cmd`) gain optional `extra_video_filters=[]` / `extra_audio_filters=[]` parameters that thread the plugin output into the `-vf` / `-af` (or `-filter:a`) chain.
- Applies only where a per-screen video render already happens: **SEGMENT and INDIVIDUAL video items**. Image items and FULL items: effects are stored/shown but `video_filters` is not invoked (deferred). `_is_renderable` is unchanged.

## Editor (`admin.html`, modern JS)

- On entry, fetch `GET /api/effects` once and cache the catalog.
- The inspector's **Start effect** / **End effect** controls become enabled, data-driven dropdowns: `None` + one option per registered effect (by `label`).
- Selecting an effect renders its parameter inputs beneath the dropdown from the effect's `params` schema (number input for `duration`; a `<select>` of `choices` for `wipe`'s `direction`), seeded with defaults.
- Edits write the field back as `{name, params}` (or `null` for "None"). These flow through SAVE_PLAYLIST / ASSIGN_PLAYLIST → render unchanged.
- `wipe` is selectable and stores its params, but renders nothing yet — the visible proof of extensibility.
- `index.html` untouched.

## Error handling

- Unknown effect `name` at render (stale data, unregistered plugin) → the hook skips it (treated as no effect), logged; render does not fail.
- Effect set on a non-rendered item (FULL/image) → silently not baked this slice (no error).
- Malformed/missing params → resolved against `ParamSpec` defaults.
- `GET /api/effects` is read-only and cannot fail on empty state (returns whatever is registered).

## Testing

### pytest
- Registry: `fade` and `wipe` register; `get_effect` resolves them; `effect_catalog()` / `GET /api/effects` returns both with param schemas (types, defaults, `wipe` direction choices).
- `FadeEffect.video_filters`: start → `fade=t=in:st=0:d=…` + `afade=t=in…`; end → `fade=t=out:st=<duration-d>…` + `afade=t=out…`; `duration` param respected; seconds conversion correct.
- `WipeEffect.video_filters` → `([], [])`; still present in the catalog with `direction` choices.
- Model: `_build_media_elements` / `_media_item_payload` round-trip `{name, params}` (and tolerate a bare-string or `None` legacy value); `compute_render_token` changes when an effect or a param changes.
- Render hook: a SEGMENT (and an INDIVIDUAL) video item with a `fade` start+end yields an ffmpeg `-vf` containing the warp chain **and** the fade fragments in the right order, plus the `afade` audio fragments; a `wipe` adds nothing; an effect on a FULL/image item bakes nothing.
- Builders: `build_ffmpeg_perspective_cmd` / `build_ffmpeg_individual_cmd` thread `extra_video_filters`/`extra_audio_filters` into the command; with none supplied the output is byte-identical to today (regression guard).
- Opt-in real-ffmpeg integration: a faded SEGMENT or INDIVIDUAL video actually encodes to a non-empty valid file (reuse the existing opt-in skip gate).

### Playwright (light, `admin.html`)
- Effect dropdowns populate from `/api/effects`; selecting `fade` shows a `duration` input and writes `{name:"fade", params:{duration:…}}` to the item; selecting `wipe` shows a `direction` `<select>`; "None" writes `null`.

## Legacy / ES5

Server-side Python (new `effects.py` + render hook) plus a desktop-console (`admin.html`) change. `index.html` is untouched — effects are baked into the rendered media the ES5 client already plays — so the 1st-gen iPad constraint is unaffected.
