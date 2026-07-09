# Self-healing cache reconciliation — design

**Date:** 2026-07-09
**Status:** approved (brainstorming)

## Problem

A cache-capable display client only receives a playlist's segments via the
PRECACHE that fires **once**, when that playlist's render reaches READY
(`notify_precache_on_ready`). A client that is offline, reconnecting, or
mid-SockJS-drop at that instant **never gets re-sent** the PRECACHE — so its
`cachedSegments` stay stale, `_per_client_items` can't hand it a local
`127.0.0.1:8080` URL, and it streams every frame from the central server
"forever."

Observed live 2026-07-09: after a ~5-minute fleet SockJS transient, all 24
OEB Sign 1 iPads were left holding an **old** render token
(`4b47e31ce445`, from PullTest3) while the current playlist "Demo" rendered to
`5ad20e2c98c3`. Result: the whole wall streamed Demo's segments centrally
(`GET /media/.../seg_5ad20e2c98c3_1.mp4` from every screen). The same class of
bug hit the modern desktop client (a page reload fixed that one by chance).

There is a *prune* reconcile (`_reconcile_ipad_cache`, SSH-`rm`s stale device
files) but **no fill reconcile** — nothing re-sends missing segments.

## Goal

A cache-capable client missing its group's current-render segments is
automatically re-PRECACHEd by a periodic reconciliation pass, so it self-heals
to local playback — regardless of *why* it missed the original PRECACHE.

## Architecture

Add a **fill-reconcile** step to the existing `process()` loop (runs ~every 5s),
alongside the existing precache-window sweep (`precache_windows[...].sweep_timeouts`)
and the prune-reconcile (`_reconcile_ipad_cache`). It reuses existing machinery:

- `_expected_seg_keys_for_display(display)` — the seg keys a client SHOULD have
  for the group's current rendered token (already used by the propagation UI).
- `start_precache(group, token, client_urls, n=3)` — throttled PRECACHE send
  backed by the per-group `PrecacheWindow` (paces at `n=3` concurrent, drains on
  `CACHED` ack / `PRECACHE_ACK_TIMEOUT`).

No new throttle mechanism, no new client code, no change to how PRECACHE is
sent or acked — only a new *trigger* for the existing send path.

## The reconcile step (per group, each process() cycle)

For each display group whose **current render is READY**:

1. Resolve the group's current rendered token + expected seg keys via
   `_expected_seg_keys_for_display(display)`. If none (no renderable current
   playlist), skip the group.
2. **Throttle guard:** if `precache_windows` already has an *active* window for
   this group, **skip this cycle** — let the in-flight window drain. This
   prevents clobbering a live window and is what makes a mass reconnect safe.
3. Select the group's **online, cache-capable, has-IP** clients whose
   `cachedSegments` are missing ≥1 expected seg key
   (`expected − client.cachedSegments`).
4. If any are missing, build each client's missing-segment pull URLs and call
   `start_precache(group, token, urls, n=3)`. The `PrecacheWindow` paces the
   sends at 3 concurrent; subsequent cycles (once the window drains) pick up any
   still-missing clients.

Because only ONE window exists per group and step 2 skips while it's active, a
24-way simultaneous reconnect drains **3-at-a-time per group** across successive
cycles — never a simultaneous blast. This honors the AP-saturation constraint
(a fleet-wide burst trips the WiFi flood-protection; see
`fleet-ssh-no-burst` / `full-video-wifi-bound` memories).

## URL construction

`_expected_seg_keys_for_display` returns keys that already encode the kind, in
the SAME format stored in `client.cachedSegments` (so `expected −
cachedSegments` works directly — the exact comparison `_propagation_percent_for_client`
already uses):

- SEGMENT key `"<token>_<i>"`  → pull URL `/media/<clientKey>/videos/seg_<token>_<i>.mp4` (per-client warp)
- FULL key `"full_<token>_<i>"` → pull URL `/media/server/videos/full_<token>_<i>.mp4` (shared asset)

So `kind` is derived from the key prefix (`full_` → shared; else → per-client).
The render-time builder that produces these URLs currently lives inline in
`_encode_group`'s pull block (`/media/%s/videos/seg_%s_%d.mp4` and
`/media/server/videos/full_%s_%d.mp4`). Extract a small shared helper
(e.g. `pull_url_for_seg_key(client_key, seg_key)`) so the render-time path and
the reconcile path construct identical URLs (DRY), and unit-test it against both
prefixes.

## Guards / correctness

- Only groups whose current render entry is `READY` (the assets exist on disk).
- Only clients with `cacheMode in ("lighttpd-localhost", "service-worker")`,
  `isOnline`, and an IP — the same eligibility `_client_is_push_eligible` uses.
- Skip a group whose `precache_windows[group]` is active (no clobber, no
  duplicate in-flight sends).
- A client already holding all expected segs is never re-sent.
- The reconcile never *encodes* — it only re-sends PRECACHE for assets that
  already exist (READY render).

## What is explicitly unchanged

- Render-time `notify_precache_on_ready` still fires on READY (the primary path;
  the reconcile is the safety net).
- The `PrecacheWindow` throttle, `PRECACHE_ACK_TIMEOUT`, and the process()-cycle
  window sweep.
- The prune-reconcile `_reconcile_ipad_cache`.
- `_per_client_items` serve logic (local vs central decision).
- All client-side code (no iPad-1 / modern client change).

## Testing

Pure/near-pure unit tests (no ffmpeg, no browser):

1. **Missing-client selector:** given a group's expected seg keys and a set of
   clients with varying `cachedSegments`/`cacheMode`/`isOnline`, returns exactly
   the online cache-capable clients missing ≥1 seg; excludes up-to-date clients,
   offline clients, and `cacheMode=none` clients.
2. **Skip-if-window-active:** with an active `precache_windows[group]`, the
   reconcile makes no `start_precache` call; with no active window and a missing
   client, it makes exactly one `start_precache(group, token, urls)` call.
3. **URL construction helper:** `pull_url_for_seg` returns the per-client
   `seg_` path for SEGMENT and the shared `full_` path for FULL, byte-identical
   to the render-time builder (guard against drift).
4. **READY gate:** a group whose current render is not READY is skipped.

## Out of scope

- Event-driven re-PRECACHE on REGISTER (the periodic reconcile subsumes it).
- Re-encoding / re-rendering (reconcile only re-sends existing assets).
- Any change to the pacing constant `n=3` (reuse the existing default).
- Pruning behavior (`_reconcile_ipad_cache` unchanged).
