# FULL device-caching — design

**Date:** 2026-06-28
**Status:** Approved (scope); ready for implementation plan
**Goal:** Cache the shared FULL (mirror) video asset on each cache-capable iPad's
lighttpd-localhost cache, so FULL playback serves from `127.0.0.1:8080` instead
of streaming centrally over WiFi — eliminating the PLAY-time AP saturation that
desyncs FULL video by 1–2 s.

## Why

Diagnosed 2026-06-28 (see memory `wall-desync-is-video-seek`): clocks sync to
~30 ms and the video seek mechanism is fine — **cached SEGMENT video syncs to
~64 ms**. FULL video desyncs only because it is the one playmode **not**
device-cached: 24 iPads stream the shared `full_<token>_<i>.mp4` centrally at
PLAY time, saturate the single AP, and stall multi-second (a streamed client
read −4.2 s; some never buffer through). FULL caching moves that bandwidth from
PLAY (sync-critical) to render time (background push), exactly the model that
makes SEGMENT sync tight.

This is the documented follow-up in CLAUDE.md ("Device caching of the shared
FULL asset is a separate follow-up; FULL is served centrally today").

## Current state (verified in code 2026-06-28)

The cache pipeline exists end-to-end for **SEGMENT** only:
- Push targets: `render.py` `seg_push_targets` ("seg_ video jobs only", populated
  at ~line 773 in the SEGMENT branch of `_encode_group`).
- Push fn: `server.py` `_push_segment_to_cached_clients(client_key, hash, n)` —
  scp `media/<client_key>/videos/seg_<hash>_<n>.mp4` → `…/MosaicMeshCache/seg_…`,
  with stall detection (`_poll_push_progress`) + `_PUSH_CONCURRENCY`. On success:
  `client.cachedSegments.add("<hash>_<n>")`.
- URL rewrite: `render.py` `_per_client_items` rewrites a cached SEGMENT item to
  `http://127.0.0.1:8080/seg_<hash>_<n>.mp4` (~line 1371). The FULL branch
  (~1360) always emits the central `/media/server/videos/full_<token>_<i>.mp4`.
- Propagation/reconcile: `discovery.py` `_expected_seg_keys_for_display` +
  `_reconcile_ipad_cache` operate on `seg_` keys.

FULL is added to **none** of these — confirmed: `_resolve_media_url` early-returns
for non-SEGMENT, the FULL `_per_client_items` branch never checks the cache, and
`seg_push_targets` excludes FULL items.

## Cache-key namespace

FULL is ONE shared asset for the whole group (not per-client). Track FULL keys
in the existing `client.cachedSegments` set with a **`full_` prefix** to avoid
colliding with SEGMENT's bare `<hash>_<n>`:
- SEGMENT cached key: `"<hash>_<n>"` (unchanged).
- FULL cached key: `"full_<hash>_<n>"`.

No new `Client` field → no `settings.dat` migration. (`migrate_client_objects`
already backfills `cachedSegments`.)

## The five seams (each mirrors the SEGMENT path)

### 1. Push targets — `render.py _encode_group`
In the FULL branch (where `full_<token>_<i>.mp4` is written, ~line 631), for a
**video** FULL item, append a push job for every cache-eligible client:
`full_push_targets.append((key, i))` for each `key` in the group that passes
`_client_is_push_eligible` (reuse the T2.1 helper). The asset is shared, so the
SAME `full_<token>_<i>.mp4` is pushed to each. After the gather (beside the
existing `seg_push_targets` loop, ~line 843), fire-and-forget the FULL pushes.
Images are tiny → not cached (video only).

### 2. Push fn — `server.py`
Generalize `_push_segment_to_cached_clients` to a `kind` param ("seg" | "full")
OR add a thin sibling `_push_full_to_cached_clients`. The ONLY differences:
- **src**: seg = `media/<client_key>/videos/seg_<hash>_<n>.mp4` (per-client);
  full = `media/server/videos/full_<hash>_<n>.mp4` (shared).
- **dst basename** + **cache key**: `full_<hash>_<n>` vs `seg_<hash>_<n>`.
- `_poll_push_progress` polls the iPad-side dst file by name → must take the
  dst basename.
- On success: `client.cachedSegments.add("full_<hash>_<n>")`.

**Risk:** this fn carries the production-tuned stall/poll/concurrency logic
(prior push-stall incident). Prefer parameterising src/dst/key with NO change to
the asyncio.wait / stall machinery. Add a regression test asserting the SEGMENT
path's cmd/key are byte-identical before vs after the refactor.

### 3. Cache-track — covered by seam 2 (`cachedSegments.add("full_…")`).

### 4. URL rewrite — `render.py _per_client_items` FULL branch (~1360)
```
if me.playmode == PlayMode.FULL:
    ext = ".mp4" if isVideoItem(me.file) else ".png"
    sub = "videos" if ext == ".mp4" else "images"
    full_name = "full_" + token + "_" + str(i)
    if ext == ".mp4" and cache_on and full_name in cached:
        f = "http://127.0.0.1:8080/" + full_name + ".mp4"
    else:
        f = "/media/server/" + sub + "/" + full_name + ext
```

### 5. Propagation + reconcile — `discovery.py`
- `_expected_seg_keys_for_display`: also emit `"full_<token>_<i>"` for FULL video
  items so the admin propagation bar and `_propagation_percent_for_client`
  count FULL caching.
- `_reconcile_ipad_cache`: ensure it treats `full_<token>_<i>` as a live key for
  the current render (don't delete the device-side `full_` file as an orphan),
  symmetric with seg handling.

## Testing

- **Unit:** push-eligibility reuse; `_per_client_items` FULL → localhost URL when
  `full_<token>_<i>` cached, central URL when not (parallels the existing
  SEGMENT cache test); push-fn SEGMENT regression (cmd/key unchanged); push-fn
  FULL builds the shared-src cmd + adds the `full_` key; propagation includes
  full keys.
- **On-wall (acceptance):** FULL video to the OEB wall, confirm via
  `tools/_desync_watch.py` (on-device `err`, NOT `ct`) + the `drift` traces that
  cached-FULL `err` spread drops from multi-second to ~SEGMENT levels (~64 ms),
  and the propagation bar fills. Validate the push doesn't re-trigger the
  AP-saturation it's meant to avoid (it runs at render time, bounded by
  `_PUSH_CONCURRENCY`).

## Out of scope / follow-ups

- INDIVIDUAL caching (still uncached by design; rare, per-client).
- Image FULL caching (tiny; not worth the push).
- A device-cache eviction policy for old `full_` tokens beyond the existing
  reconcile sweep.

## Risk summary

The change is a faithful parallel of the proven SEGMENT path, but it touches two
incident-sensitive areas: the **async push pipeline** (stall/poll — parameterise,
don't restructure) and the **`cachedSegments`** set persisted in `settings.dat`
(prefix-namespaced, no schema change). Both are containable with the regression
test on the SEGMENT path + the on-device-`err` on-wall acceptance.
