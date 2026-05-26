# INDIVIDUAL Render Path — Design

**Date:** 2026-05-26
**Status:** Design approved, pending implementation plan
**Builds on:** the SEGMENT mosaic render (image warp + ffmpeg perspective, async `render_group_async`, per-client PLAY) and the playlist editor (which already models `PlayMode.INDIVIDUAL` and shows it disabled in the picker).

## Context

`PlayMode.INDIVIDUAL` exists in the model but has no render path — the editor shows it disabled ("render coming soon"). This slice implements it: an INDIVIDUAL playlist item shows the **whole** media on **every** screen in the group, pre-warped per screen so that, viewed from the calibration camera, each physically tilted/rotated screen displays the media as an upright, undistorted rectangle. Where the media's aspect ratio doesn't match the screen, the item's `backgroundColor` fills the letterbox margins. An uncalibrated screen falls back to a plain fit (identical to FULL).

This contrasts with SEGMENT (mesh), where the source is stretched across the **group** bounding box and each screen renders only its **crop** of one large image. INDIVIDUAL is the same warp machinery driven by each screen's **own** quad bounding box instead.

## Goals

- A calibrated screen renders the full media rectified to its measured quad (perspective + rotation corrected), aspect-fit with `backgroundColor` letterbox.
- Both images (OpenCV warp) and video (ffmpeg `pad`+`perspective`+`scale`, audio retained) are supported.
- Uncalibrated screens fall back to the plain source (FULL-equivalent).
- INDIVIDUAL participates in the existing render gate: assigning/playing an INDIVIDUAL-containing playlist requires a render and reports `RENDER_REQUIRED` / `NOT_CALIBRATED` like SEGMENT.
- The editor's INDIVIDUAL picker option becomes enabled.
- No regression to SEGMENT, FULL, SCRIPT, or PLAY/PAUSE/STOP.

## Non-goals (deferred)

- Start/end effects (still fields-only, a later slice).
- Choosing fill-vs-fit per item (this slice is always aspect-FIT with letterbox).
- Re-deriving corner labels for extreme rotations (uses the existing `order_points` heuristic; very-rotated screens may mislabel corners — same limitation SEGMENT has).

## Geometry

For a calibrated screen with measured quad `Q` (photo coords) and device resolution `out_w × out_h`:

1. `bbox = cv.boundingRect(Q)` → `[bx, by, bw, bh]`, the upright rectangle the viewer should perceive.
2. **Aspect-fit letterbox:** scale the full media to fit within a `bw × bh` canvas preserving aspect, centered, with `backgroundColor` filling the margins.
3. **Warp:** `warp_image_for_screen(canvas, bbox, Q, out_w, out_h)` (unchanged math). `Q` expressed relative to `bbox` maps onto the device buffer; composed with the physical panel→`Q` view, the camera sees the media as the upright `bbox` rectangle with bg margins. `bbox` corners outside the physical screen quad are clipped by the screen edge (expected).

Mirrors SEGMENT exactly except SEGMENT uses the group bbox and the screen's region of the source; INDIVIDUAL uses the screen's own bbox and the whole (letterboxed) source.

**Video** (ffmpeg): `pad` the source to the `bbox` aspect ratio with `backgroundColor`, then the existing `perspective` filter with source points from `quad_to_source_points(bbox, Q, padded_w, padded_h)`, then `scale` to `out_w × out_h`. Audio retained on every screen (consistent with the video-split slice).

**Uncalibrated screen** (`measuredPerimeter is None`): no render; the client plays the plain source URL (today's FULL fallback in the per-client PLAY builder).

## Render path changes (`server.py`)

A new predicate centralizes the mode test:

```python
def _is_renderable(me):
    """SEGMENT and INDIVIDUAL items require a per-screen server render."""
    return me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL)
```

It replaces the scattered `me.playmode == PlayMode.SEGMENT` checks at every gate.

- **`render_group_async`**: iterate items where `_is_renderable(me)`. Per item, branch on `playmode`:
  - SEGMENT → group bbox + region crop (unchanged).
  - INDIVIDUAL → per-screen `boundingRect(Q)` + aspect-fit letterbox (above), output `ind_<token>_<i>.{png,mp4}`.
  Both the image (OpenCV) and video (ffmpeg) branches handle both modes.
- **`_broadcast_segment_play`** → renamed `_broadcast_per_client_play` (internal only; no protocol change). File selection per item for a client `c`:
  - `_is_renderable(me)` **and** `c.measuredPerimeter is not None` → that client's warped file (`seg_…` for SEGMENT, `ind_…` for INDIVIDUAL).
  - otherwise (FULL item, or uncalibrated client) → the plain source `me.file`.
- **PLAY handler**: `has_segment` → `has_renderable = any(_is_renderable(me) for me in display.mediaElements)`. The render-gate logic (`RENDER_IN_PROGRESS`, `RENDER_REQUIRED`, else per-client play) is otherwise unchanged.
- **RENDER request**: the SEGMENT precondition becomes `_is_renderable`; the `"no SEGMENT items"` error becomes `"no renderable items"`.
- **`ASSIGN_PLAYLIST`**: the `has_segment` classification check becomes `_is_renderable`, so INDIVIDUAL-containing playlists return `NOT_CALIBRATED` / `RENDER_REQUIRED` appropriately.
- **`compute_render_token`**: unchanged — it already hashes `playmode`, so toggling INDIVIDUAL invalidates the token.

A small geometry helper may be extracted for the aspect-fit letterbox (e.g. `letterbox_to_aspect(img, w, h, bg)`), used by the image render; the ffmpeg path expresses the same via `pad`.

## Client (`admin.html`)

Enable the INDIVIDUAL option in the inspector playmode picker: remove `disabled`, relabel `"INDIVIDUAL — soon"` → `"INDIVIDUAL"`. No other client change. `index.html` is untouched — it already plays whatever per-client URL it receives (`ind_…` exactly like `seg_…`).

## Error handling

- Source media unreadable (image or video) → `render_group_async` raises, sets `renderStatus = "error"`, broadcasts `RENDER_STATUS error` (existing behavior).
- INDIVIDUAL item on an uncalibrated group → `NOT_CALIBRATED` at assign/play; if rendered anyway, calibrated screens render and uncalibrated ones fall back to source.
- `backgroundColor` missing on an item → defaults to `#000000` (already guaranteed by `_build_media_elements` / `_media_item_payload`).

## Testing

### pytest (`tests/unit/test_mosaic.py` + playlist tests)
- `_is_renderable`: True for SEGMENT/INDIVIDUAL, False for FULL/SCRIPT/DEFAULT.
- INDIVIDUAL geometry: a known image rectified onto an axis-aligned quad lands upright in `boundingRect(Q)` — assert a sentinel pixel (left-half-red → output left half red).
- Aspect-fit letterbox: wide media on a tall bbox leaves `backgroundColor` margins — assert border pixels equal the item's `backgroundColor`.
- `render_group_async`: an INDIVIDUAL image item writes `ind_<token>_<i>.png` per calibrated client; an INDIVIDUAL video item builds an ffmpeg command containing `pad`, `perspective`, and `scale`; uncalibrated client renders nothing.
- Per-client PLAY: calibrated client → `ind_…` for an INDIVIDUAL item, plain source for a FULL item; uncalibrated client → plain source.
- Gates: INDIVIDUAL-only playlist → PLAY returns `RENDER_REQUIRED` (stale token); `ASSIGN_PLAYLIST` with INDIVIDUAL + no bbox → `NOT_CALIBRATED`; `RENDER` accepts an INDIVIDUAL-only playlist.
- Regression: the full existing SEGMENT/mosaic suite stays green through the `_is_renderable` refactor.
- Opt-in real-ffmpeg integration test for an INDIVIDUAL video, behind the existing opt-in marker, verifying the pad+perspective chain encodes a non-empty valid output.

### Playwright (light, `admin.html`)
- The inspector's INDIVIDUAL option is enabled (not `disabled`), and selecting it writes `playmode: "INDIVIDUAL"` to the selected item.

## Legacy / ES5

Server-side only plus a one-line `admin.html` (desktop console) change. `index.html` is untouched, so the 1st-gen iPad constraint is unaffected.
