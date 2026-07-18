# Phase 3 — Retire lighttpd from the mmvideo cache path

**Date:** 2026-07-18
**Status:** design, corrected after code exploration; pending review
**Context:** Phase 3 of the "better playback coordination" track (Phase 1 = ws transport fix, merged;
Phase 2 = frequency-disciplined GoTime, merged). The original Phase-3 framing was "play `file://`
directly with mmvideo," but exploration showed that goal was a *means* to the real driver —
**simplify by dropping the lighttpd dependency** — and that dropping lighttpd requires neither
`file://` nor an SSH-probe change.

> **Correction (2026-07-18):** the first draft planned to rewrite an SSH cache probe
> (`_probe_cache_capability`). That probe **no longer exists** — it was deleted and replaced by
> client-reported capability on REGISTER (`mosaicmesh.websocket.legacy.apply_cache_capability`,
> documented in-code as "replaces the deleted SSH probe"). So there is nothing server-side left that
> checks lighttpd. This spec is corrected accordingly: the work is a `cacheMode` **rename** + a
> vestigial-confirmation + the operational daemon removal — no probe, no `file://`, no tweak rebuild.

## Problem

The iPad-1 video cache mode is named `"lighttpd-localhost"`, implying a local lighttpd HTTP server on
`:8080` serves cached video. That name is now a **misnomer** and lighttpd is **fully vestigial
server-side**:

- **Capability detection** is client-reported. On REGISTER the client sends `cacheBackend` /
  `cacheCapable` (set by whether its mmvideo cache backend registered), and
  `apply_cache_capability` maps it to a `cacheMode` (`"service-worker"` for the modern backend,
  `"lighttpd-localhost"` for the mmvideo tweak). **No server-side SSH probe of `:8080` exists** — it
  was deleted.
- **Download:** the `mmcache` tweak fetches segments to `/var/mobile/Media/MosaicMeshCache/<token>.mp4`
  from the central server (`:3000`) — not lighttpd.
- **Playback:** the client sets `<video src="http://127.0.0.1:8080/<name>.mp4">`; mmvideo's `h_load`
  hook intercepts that string, `mm_url_to_path` maps it to
  `file:///var/mobile/Media/MosaicMeshCache/<name>.mp4`, and it plays via a **native AVPlayer file
  load** — lighttpd is bypassed.

So lighttpd carries no traffic and gates nothing. Its only residue is (a) the misleading mode-string
`"lighttpd-localhost"`, and (b) a running daemon on each device that nothing depends on (a dormant
fallback for an mmvideo-interception miss). The `http://127.0.0.1:8080/…` string is an **origin-safe
token** WebKit accepts on an http page (a raw `file://` src is cross-origin-blocked — see
[[ios5-cached-video-url-convention]]); mmvideo pattern-matches the token and loads natively. The token
works **with or without lighttpd running**.

## Goal / non-goals

- **Goal:** remove the lighttpd dependency from the fleet — no daemon to install/run — and rename the
  now-misleading `cacheMode` `"lighttpd-localhost"` → `"mmvideo"`, while keeping cached-video playback
  byte-for-byte the same (mmvideo still intercepts the localhost token).
- **Non-goal:** `file://` as the client-facing `<video>` src (would require defeating WebKit's
  cross-origin block, for no functional gain — the token works fine). Explicitly out of scope.
- **Non-goal:** any change to capability *detection* — it is already client-reported and already
  reflects mmvideo presence. No SSH probe exists to add or rewrite.
- **Non-goal:** changing the download path (`mmcache`), the render/encode pipeline, the client
  (`index.html`/`mmCache*`/mmvideo tweak), or the `service-worker` (modern) backend. No tweak rebuild,
  no iPad-1 client redeploy.

## Design

### Capability detection is unchanged (no probe to touch)

`apply_cache_capability` (legacy.py) already sets the mmvideo `cacheMode` from the client's
self-report, which is driven by whether the mmvideo cache backend registered on the device. A device
where mmvideo is installed-but-not-injecting (the screen15 case) therefore does **not** report
`cacheCapable`, so it is correctly left at `"none"` — the accuracy the earlier "dylib-on-disk probe"
idea was chasing is already provided, client-side, for free. This spec changes only the mode **string**
that mechanism assigns, not the mechanism.

### `cacheMode` rename: `"lighttpd-localhost"` → `"mmvideo"`

Rename the mode string everywhere it is assigned, compared, or validated. Functional sites (exact,
verified against current code):

- `mosaicmesh/websocket/legacy.py:162` — `apply_cache_capability` assigns the mmvideo mode.
- `mosaicmesh/websocket/legacy.py:254` — `ANNOUNCE_CACHE_MODE` accept-list.
- `mosaicmesh/api/discovery.py:454` — `set_cache_mode` (`configure`) accept-list.
- `mosaicmesh/api/discovery.py:155, 287, 410` — propagation-count / capability guards.
- `server.py:423, 2166` — push-eligibility / reconcile guards.
- `mosaicmesh/render.py:635, 1334, 1399` — cache-serve URL-resolution guards.

**Migration:** `mosaicmesh.state.migrate_client_objects` (runs at boot) rewrites any
`Client.cacheMode == "lighttpd-localhost"` to `"mmvideo"`, so devices loaded from an older
`settings.dat` are not misclassified. (This sits alongside the existing `cacheMode` backfill at
state.py:307-308.)

**Back-compat window:** for one release BOTH accept-lists (legacy.py:254, discovery.py:454) accept
`{"none", "mmvideo", "lighttpd-localhost", "service-worker"}`, so an in-flight client/admin message
carrying the old string does not get rejected. New code only ever *writes* `"mmvideo"`.

### Comments

Reword in-code comments/docstrings that describe the mode as "lighttpd-localhost" or imply lighttpd
serves the file (server.py, discovery.py, render.py, state.py:229, legacy.py:145-153). The
`state.py:241` `cacheProbedMs` field (already documented "SSH push retired… nothing gates on it") stays
as a harmless deserialization-compat field; only its comment is clarified.

### Confirm lighttpd is vestigial (safety gate before removal)

Before the daemon is removed, prove nothing server-side still needs it: a grep sweep must show every
remaining `:8080` / `lighttpd` reference is either the client-facing token (`render.py` URL builders,
`mmCacheBackendMmvideo.js` — all intercepted by mmvideo) or a comment — i.e. no server code path opens
a connection to `:8080` or shells lighttpd. (Confirmed during design: the only `:8080` producers are
`render.py`'s `http://127.0.0.1:8080/…` URL builders, consumed by mmvideo on-device.)

### lighttpd removal (operational rollout, sequenced last)

The daemon removal is a **fleet operation done after the code is deployed and caching is verified**,
not a code change:

1. Deploy the server change (restart the `:3000` server — a client-invisible change).
2. Confirm a device still reports/holds `cacheMode = "mmvideo"` and caches + plays cached video.
3. On **one canary screen**, stop lighttpd and verify cached playback still works (proves the bypass).
4. Then stop + disable lighttpd fleet-wide via paced SSH ([[fleet-ssh-no-burst]] /
   [[fleet-tweak-deploy-ssh-doze]] — Windows OpenSSH, sequential, no burst).

Reversible at every step (re-enabling lighttpd is a per-device start command).

## Data flow (after)

```
REGISTER:      client reports cacheCapable -> apply_cache_capability -> cacheMode="mmvideo"  (was "lighttpd-localhost")
mmcache tweak: download seg -> /var/mobile/Media/MosaicMeshCache/<token>.mp4   (from :3000, unchanged)
playback:      <video src="http://127.0.0.1:8080/seg_<key>.mp4">   (unchanged token)
                 -> mmvideo h_load intercepts -> mm_url_to_path -> file:///.../<name> -> AVPlayer
                 (lighttpd never involved; can be absent)
```

## Testing / verification

- **Unit (Python, `tests/unit/`, `server.settings = mock_settings` pattern):**
  - `apply_cache_capability`: a `cacheCapable` REGISTER payload sets `cacheMode == "mmvideo"` (was
    `"lighttpd-localhost"`); a `cacheBackend=="modern"` payload still sets `"service-worker"`;
    upgrade-only (a client already at a non-`none` mode is unchanged).
  - Boot migration: a `Client` deserialized with `cacheMode == "lighttpd-localhost"` becomes
    `"mmvideo"` after `migrate_client_objects`; a `"service-worker"` / `"none"` client is untouched.
  - Accept-lists: `set_cache_mode` and `ANNOUNCE_CACHE_MODE` accept `"mmvideo"` AND (transitional)
    `"lighttpd-localhost"`, and reject an unknown mode (400 / ignored respectively).
  - Regression: the push-gating (`server.py`) and propagation-count (`discovery.py`) helpers behave
    identically for `"mmvideo"` as they did for `"lighttpd-localhost"` — parametrize existing tests on
    the new string; assert no test still hard-codes the old literal except the transitional accept-list
    + migration cases.
- **Vestigial sweep:** `grep -rn '8080\|lighttpd' server.py mosaicmesh/` shows only client-token URL
  builders + comments — no server-side connect/shell to lighttpd.
- **On-wall:** restart the server; confirm an eligible device reports `"mmvideo"`; on a canary screen
  stop lighttpd and confirm cached video still plays (the bypass), then roll the daemon removal
  fleet-wide.

## Success criteria

The fleet caches and plays cached video with **lighttpd stopped/removed** on every device; devices
report `cacheMode = "mmvideo"`; no change to playback, download, capability detection, or the client.
One fewer daemon to install and keep alive, and a mode name that reflects reality.

## Constraints

- Server-side Python only (`server.py`, `mosaicmesh/`); no iPad-1 client change (ES5/mmvideo
  untouched), no tweak rebuild.
- Preserve the client-reported-upgrade / client-error-downgrade capability invariant
  ([[cache-capability-is-client-local]]) — untouched by this change.
- Fleet SSH is paced, Windows OpenSSH, never bursted ([[fleet-ssh-no-burst]]).

## Risks

- **Rename drift:** miss one `"lighttpd-localhost"` compare site and that device silently stops being
  treated as cache-capable (→ its video streams centrally instead of from cache — degraded, not
  broken). Mitigated by the exhaustive site list above, the transitional dual-accept, and a test-plan
  grep sweep for the literal (only the migration + accept-list may still name it).
- **lighttpd was a real fallback for an mmvideo-interception miss.** After removal, such a miss hits a
  refused `:8080` connection instead of a lighttpd file serve → a `<video>` error → the existing
  client-error downgrade flips the device off cache mode (same end state as an mmvideo-absent device).
  Interception misses are rare; the canary step (stop lighttpd on one screen first) de-risks this
  before fleet-wide removal.
- **Old `settings.dat` not migrated** would leave devices at the old string; the boot migration plus
  the one-release dual-accept cover both the persisted and in-flight cases.
