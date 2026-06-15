# Play-Type Selection + Always-Encode-for-Device — Design

**Date:** 2026-06-14
**Status:** Design — approved in brainstorming; pending spec review → implementation plan.
**Area:** playlist editor (admin UI) + server render pipeline.

---

## Problem

Two coupled defects, surfaced by a live incident (OEB Sign 1 stuck on "Tap to Start"):

1. **The playlist editor offers no play type for media.** `contentItemToPlaylistItem` emits `playmode:'SCRIPT'` for animations and **nothing** for video/image — the server then defaults media to `FULL`. The operator cannot choose **mesh** (one video spread across the calibrated wall), **mirror** (same full video on every screen), or **per-screen warped-to-calibration**. The render-mode selector existed before the timeline rewrite and was lost.
2. **`FULL` mode serves the raw source file.** A raw 1920×1080 Main-profile `.mov` is undecodable on iPad-1 (decoder tops ~720p), so every screen hangs on "Tap to Start" — a tap fires the gesture but the element never decodes. Only `SEGMENT`/`INDIVIDUAL` currently render a device-decodable per-screen encode; `FULL` (and images) ship the original.

Net effect: a video added through the editor silently becomes `FULL` → raw source → unplayable on the hardware, with no way to pick a renderable mode.

## Goals

1. **Restore an explicit play-type selector** for media in the playlist editor: Mesh / Mirror / Per-screen. Animations remain implicitly SCRIPT.
2. **Force a choice** — a newly added media item has no play type; the editor will not save/play until one is chosen (no silent default).
3. **Always encode for the device** — *no* play mode ever serves raw source to the wall. Every video and every image is transcoded/downscaled to an iPad-1-decodable asset (≤720p Constrained Baseline for video; device-capped dimensions for images).
4. Reuse the auto-render model already built (token, queue, READY-gate, status) — making `FULL` renderable means it flows through it automatically.

## Non-goals

- **Device caching (lighttpd-localhost scp / `cacheMode`)** — verified off across OEB Sign 1, tracked separately as a follow-up ("track C"). Caching FULL/mirror shared assets is part of that follow-up; this spec serves FULL from the central server.
- Changing the coordinated-start / PREPARE / GO path.
- Changing SEGMENT/INDIVIDUAL warp math.

---

## Vocabulary → `PlayMode`

| Editor label | `PlayMode` | Output | Per-screen? |
|---|---|---|---|
| **Mesh** | `SEGMENT` | source warped to each screen's slice of the group bounding box | yes (existing) |
| **Per-screen (warped to calibration)** | `INDIVIDUAL` | full source warped to each screen's own quad | yes (existing) |
| **Mirror** | `FULL` | same full video/image, device-decodable encode, on every screen | **no — one shared encode (NEW)** |
| (animations) | `SCRIPT` | client-side canvas animation | n/a |

---

## Part A — Editor: play-type selector (force a choice)

**`js/timeline/content/content-items.js`** — `contentItemToPlaylistItem` keeps emitting `{file, playmode:'SCRIPT'}` for animations and `{file}` (no playmode) for media. The absent `playmode` is the "unchosen" signal.

**`js/timeline/modals/playlist-editor.js`** — in the selected-item sidebar (alongside the existing Duration + Background fields), for any **non-animation** item render a **Play type** `<select>`:
- First option is a disabled placeholder `— pick play type —` (selected when `it.playmode` is unset/not one of SEGMENT/FULL/INDIVIDUAL).
- Options: `Mesh` → `SEGMENT`, `Mirror` → `FULL`, `Per-screen (warped)` → `INDIVIDUAL`.
- On change → `it.playmode = <value>`; `updateRowMeta()` so the row reflects it.
- Animations: no selector (implicitly SCRIPT), as today.

**Force-a-choice enforcement (authoring-time):**
- A pure helper `mediaItemsMissingPlayType(items)` (in `content-items.js`, node-testable) → array of items where `!isAnim(it) && it.playmode not in {SEGMENT,FULL,INDIVIDUAL}`.
- The editor's **Save is disabled** (and shows a hint "pick a play type for N item(s)") while that array is non-empty; each offending row gets a `⚠` marker.
- Rationale: even though the server's FULL default is now safe (Part B transcodes it), forcing the choice captures operator *intent* (mesh vs mirror) which can't be inferred.

## Part B — Server: every mode encodes for the device

All in `mosaicmesh/render.py`.

**Renderability — `_is_renderable(me)`** becomes: `me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL, PlayMode.FULL)` — an explicit allowlist (SEGMENT, INDIVIDUAL, **and now FULL**). SCRIPT and the bare-construction DEFAULT are not renderable. A media item with an unset playmode is mapped to FULL by `_build_media_elements`, so it is renderable and safe (transcoded).

**Device-decode target.** Add a constant `DEVICE_DECODE_CAP = (1280, 720)` (W,H envelope iPad-1 can decode) and a helper `_fit_within(src_w, src_h, cap)` → scaled (even) dims preserving aspect, never upscaling. Video encode uses `_video_encoder_args()` (already iPad-1 H.264) **plus an explicit `-profile:v baseline -level 3.0`** and the existing `_keyframe_grid_args` so FULL clips match the segment profile.

**FULL is a single shared encode (not per-client).** Because the same content shows on every screen in a group and the whole fleet is one device class, FULL renders **one** asset per (playlist-item, group) — shared across that group's screens — into the central media dir. It's keyed by the group's `render_token`, so two groups produce two files (identical content, different token); within a group all clients fetch the one file:
- video → `media/server/videos/full_<token>_<i>.mp4` — `build_ffmpeg_transcode_cmd(src, out, tw, th)` scales+letterboxes the source into `_fit_within(src, CAP)` at Constrained Baseline.
- image → `media/server/images/full_<token>_<i>.png` — downscaled (OpenCV) to `_fit_within(src, CAP)` (no upscale).

`SEGMENT`/`INDIVIDUAL` continue to write per-client `seg_<token>_<i>` / `ind_<token>_<i>` exactly as today.

**`_encode_group`** gains a FULL branch: for each FULL item, produce the one shared `full_<token>_<i>` asset (skip the per-client loop). Video FULL jobs join the same `asyncio.gather` ffmpeg batch + `progress_cb`. (No cache-push for FULL in this spec — served centrally; caching is track C.)

**`_per_client_items`** URL resolution:
- `SEGMENT`/`INDIVIDUAL` (calibrated): per-client `/media/<key>/{videos|images}/{seg_|ind_}<token>_<i>.{mp4|png}` (existing, incl. the lighttpd-localhost rewrite for cached clients).
- **`FULL` (NEW): `/media/server/{videos|images}/full_<token>_<i>.{mp4|png}`** — the shared device encode, **never** `me.file` (raw) again.
- `SCRIPT`: animation ref (existing).

**`build_ffmpeg_transcode_cmd(src_path, out_path, out_w, out_h, extra_video_filters=None, extra_audio_filters=None)`** (new): `-vf scale=w:h:force_original_aspect_ratio=decrease,pad=w:h:(ow-iw)/2:(oh-ih)/2[,extra]` + `_video_encoder_args()` + `-profile:v baseline -level 3.0` + `_keyframe_grid_args` + AAC audio — mirrors `build_ffmpeg_perspective_cmd`'s output conventions minus the perspective warp.

**Token.** `render_token` is unchanged (items + bbox + per-client quads). A FULL-only playlist's token still varies with per-client quads, so a recalibration will re-encode the shared FULL asset unnecessarily — a minor, bounded over-render; acceptable, noted here rather than special-cased.

## Data flow / auto-render tie-in

With `FULL` renderable, the auto-render model already built handles it end-to-end: saving a playlist debounces → enqueues a render for each calibrated group → `_encode_group` now also produces `full_` assets → registry goes READY → PLAY/ASSIGN/schedule gates pass → `_per_client_items` serves the device encode. "Force a choice" guarantees every media item has an explicit, encodable mode before it can be played.

**Operational note:** the live server is still pre-auto-render (no `/api/renders`); to exercise auto-render on the wall it must be restarted onto this branch. Under the old server, the operator renders via Fleet; the FULL-encode change still applies once the new code runs.

---

## Components to change

**Client**
- `js/timeline/content/content-items.js` — `mediaItemsMissingPlayType(items)` helper; `contentItemToPlaylistItem` unchanged (media stays playmode-less = unchosen).
- `js/timeline/modals/playlist-editor.js` — play-type `<select>` for media; Save-disabled + row `⚠` while any media item is unchosen.
- (optional) a tiny pure `playTypeLabel(mode)` map for display.

**Server**
- `mosaicmesh/render.py` — `_is_renderable` (FULL renderable); `DEVICE_DECODE_CAP` + `_fit_within`; `build_ffmpeg_transcode_cmd`; `_encode_group` FULL branch (shared `full_` asset); `_per_client_items` FULL → shared URL.

**Docs**
- `CLAUDE.md` Conventions: note play types + "always encode for device" (FULL now renders a shared device encode; no raw source served).
- `js/timeline/README.md`: editor play-type selector + helper.

## Test plan

- **Unit (py):** `_is_renderable` returns True for FULL video/image; `_fit_within` (aspect, no-upscale, even dims); `build_ffmpeg_transcode_cmd` arg shape; `_encode_group` FULL branch writes one shared asset (mock ffmpeg / OpenCV); `_per_client_items` FULL → `/media/server/.../full_<token>_<i>` (not raw).
- **Unit (js):** `mediaItemsMissingPlayType` (animation excluded; SEGMENT/FULL/INDIVIDUAL satisfy; unset flags); `playTypeLabel`.
- **E2e:** add a media item, assert Save disabled until a play type is picked; pick Mesh → item shows mesh; pick Mirror → item shows mirror.
- **Regression:** full unit suite stays at the established baseline (15 pre-existing); JS green.

## Resolved decisions (from brainstorming)

1. **Default = force a choice** — no silent default; editor blocks save until every media item has a play type.
2. **Always encode for device** — FULL **video transcodes** (≤720p Constrained Baseline) **and FULL images downscale** to the device cap; raw source is never served.
3. **FULL is one shared encode** served centrally (per-client only for SEGMENT/INDIVIDUAL).
4. **Device caching is out of scope** here — separate follow-up (track C: verify lighttpd on the fleet, re-enable `cacheMode='lighttpd-localhost'`); caching the shared FULL asset is part of that.
