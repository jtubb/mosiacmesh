# Admin Overhaul — Section 2 addendum: Playlist-item duration model ("Auto")

**Date:** 2026-06-09
**Status:** Draft — approved in conversation
**Parent:** [Section 2 (Content)](./2026-06-09-admin-overhaul-section2-content-design.md). A small playback-semantics fix-slice that replaces the (partly non-functional) per-item play-mode with a single **Duration** control whose default is **Auto**.

**Goal:** Make duration the single per-item control. **Auto** = play the content's natural length (video) or a default (image/animation); a number = play for exactly that long. Drop the Loop / Play Once / Play Full Clip play-mode entirely, and make "Auto" actually resolve to a real length on the wall (today blank duration ships `0` and the item is skipped).

## Why (the findings that drove this)

- The per-item **Loop / Play Once** choice is **cosmetic today**: `render.py:_build_media_elements` maps both `'loop'` and `'once'` → `PlayMode.FULL`, and the display client (`index.html:568`) hard-codes `v.loop=false` and only branches on `playmode === 'SCRIPT'`. So loop/once has zero effect on the wall.
- **Blank ("Auto") duration is broken**, not "natural length": `render.py:_duration_ms` returns `0` for a missing duration, and the client builds its `durations` array straight from that (`index.html:688`, no fallback) → the item gets a **0-ms window** → skipped in a multi-item playlist (and degenerate `% 0` math in a single-item one).
- My just-shipped **"Play full clip"** *deletes* the duration → hits the same `0` path → it's broken (skips the video). This slice retires it.

## The model

Every content item has exactly one playback control: **Duration**.

- **`Auto`** (the default; blank field, shown as "Auto" in the item list): play the content's natural length.
  - **Video** → its probed natural length.
  - **Image / animation** → **20 s** (they have no intrinsic length).
- **`N seconds`**: play for exactly N (videos truncate; videos shorter than N hold the last frame — unchanged engine behavior).

No Loop / Play Once / Play Full Clip. (Animations remain `playmode:'SCRIPT'` on the wire — that's the render-mode flag, not a user-facing "play mode"; their Duration is Auto→20s or a number.)

### Correctness: why Auto resolves on the SERVER, not the client

Synchronized playback requires every screen to know each item's duration **upfront** so they all advance at the same instant (the engine precomputes boundaries from a `durations` array against the shared clock). A client-side "play until the video's `ended` event" would make each screen advance whenever *its* copy finishes → the wall desyncs. Therefore **Auto must be resolved to a concrete duration before it reaches the clients** — server-side, from the probed length. The display client is unchanged.

## Changes

1. **Server (`mosaicmesh/render.py`)** — resolve Auto when building the playback payload. In `_duration_ms` (or a helper it calls): if the item has an explicit positive duration, use it; otherwise resolve:
   - video file with a known probed length (from the `_video_duration_cache`) → that length;
   - otherwise (image, animation, or not-yet-probed video) → the **20 s** default.
   Result: the wire `duration` is always a concrete, positive ms value — never `0`. "Auto" persists in the stored playlist item (duration stays absent); only the *payload* materializes it.
   - Add a module constant `DEFAULT_ITEM_DURATION_S = 20`.
   - Best-effort probe: the admin's `/api/media` call warms `_video_duration_cache`; if a video's length isn't cached at play time it falls back to 20 s (documented; acceptable — it self-corrects once probed).

2. **`js/timeline/content/content-items.js`** — `contentItemToPlaylistItem`:
   - animation → `{ file, playmode: 'SCRIPT' }` (no duration → Auto).
   - media (image/video) → `{ file }` (no playmode, no duration → Auto; server defaults playmode to FULL and resolves the duration).
   - i.e. nothing is stamped with a duration anymore — everything defaults to Auto.

3. **`js/timeline/modals/playlist-editor.js`** — remove the play-mode `<select>` for media entirely. The settings box becomes: **Duration** (number input; **blank = Auto**, with placeholder "Auto" + helper text "blank = full length") and **Background**. The item-row label shows `"Auto"` when no duration, else `"<n>s"` (already does). Clearing the field stores Auto (no duration) — and that is now *valid* (server resolves it), so the focus-safe in-place update from the last fix stays. Drop the `isAnim`-gated play-mode block and the `'full'`/`'once'`/`'loop'` logic.

4. **`mosaicmesh/api/...` / wire** — unchanged shape. Items are still `{file, duration?, playmode?, backgroundColor?}`; "Auto" is simply the absence of `duration`. No new fields.

## Testing

- **pytest:** `_duration_ms` / the resolver — explicit duration passes through; missing duration on a video with a cached probed length → that length (ms); missing duration on an image/animation/unprobed → 20000 ms; never returns 0 for a real item.
- **node:** `contentItemToPlaylistItem` — animation → `{file, playmode:'SCRIPT'}` (no duration); media → `{file}` (no duration, no playmode). Update the existing test.
- **Playwright:** in the editor, a media item shows a Duration field (no play-mode select); leaving it blank shows "Auto" in the row; setting a number shows "<n>s"; save round-trips (Auto persists as absent duration). Optionally assert via REST that an Auto item has no `duration` key and that `/api/playback`-adjacent payload building yields a positive duration (or assert the resolver directly in pytest, which is simpler).
- **iPad-1 note:** the display client is untouched, but this changes what duration the wall receives for Auto items — re-confirm during the pending iPad-1 sign-off that an Auto video plays its full length and advances.

## Decision log

- **Duration is the only per-item control; Auto is the default.** Loop/Once/Full-clip dropped — they were cosmetic or broken. Simpler and honest.
- **Auto = natural length (video) / 20 s (image, animation).** Default 20 s chosen by the operator.
- **Resolve Auto server-side, not client-side.** Synchronized playback needs the duration upfront; per-screen "play until ended" would desync the wall.
- **No wire/model change.** "Auto" = absence of `duration`; the server materializes it in the payload only.
