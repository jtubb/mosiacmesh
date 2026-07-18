# Phase 3 — Retire lighttpd from the mmvideo cache path

**Date:** 2026-07-18
**Status:** design, pending review
**Context:** Phase 3 of the "better playback coordination" track (Phase 1 = ws transport fix, merged;
Phase 2 = frequency-disciplined GoTime, merged). The original Phase-3 framing was "play `file://`
directly with mmvideo," but exploration showed that goal was a *means* to the real driver —
**simplify by dropping the lighttpd dependency** — and that dropping lighttpd does **not** require
`file://` at all.

## Problem

The iPad-1 video cache is described as `cacheMode = "lighttpd-localhost"`, implying a local lighttpd
HTTP server on `:8080` serves cached video. In reality lighttpd is on **neither hot path**:

- **Download:** the `mmcache` tweak fetches segments to `/var/mobile/Media/MosaicMeshCache/<token>.mp4`
  from the central server (`:3000`) — not lighttpd.
- **Playback:** the client sets `<video src="http://127.0.0.1:8080/<name>.mp4">`; mmvideo's `h_load`
  hook intercepts that string, `mm_url_to_path` maps it to
  `file:///var/mobile/Media/MosaicMeshCache/<name>.mp4`, and it plays via a **native AVPlayer file
  load** — lighttpd is bypassed.

lighttpd's only real roles are: (a) the **SSH probe target** — `_probe_cache_capability` curls
`:8080` to decide a device is cache-capable and flip its `cacheMode`; and (b) a **dormant fallback**
if mmvideo ever fails to intercept the localhost URL. It is an extra moving part (a daemon to install,
run, and keep alive) that carries no playback traffic.

The `http://127.0.0.1:8080/…` string is an **origin-safe token** WebKit accepts on an http page
(a raw `file://` src is cross-origin-blocked from an http origin — see
[[ios5-cached-video-url-convention]]); mmvideo pattern-matches the token and loads natively. The token
works **with or without lighttpd running**.

## Goal / non-goals

- **Goal:** remove the lighttpd dependency from the fleet — no daemon to install/run — while keeping
  cached-video playback byte-for-byte the same (mmvideo still intercepts the localhost token).
- **Non-goal:** `file://` as the client-facing `<video>` src. That would require defeating WebKit's
  cross-origin block on `file://` (unproven WebKit-internals hooking) for **no functional gain** over
  keeping the token. Explicitly out of scope (recorded as a possible future spike if the token ever
  becomes a real problem).
- **Non-goal:** changing the download path (`mmcache` tweak), the render/encode pipeline, or the
  `service-worker` (modern) cache backend used by non-iPad-1 clients.
- **Non-goal:** a tweak rebuild / fleet redeploy. The chosen readiness signal is checkable from the
  existing on-device state, so `mmvideo.dylib` is untouched.

## Design

### Readiness signal — dylib-on-disk (chosen: 1(i), no tweak rebuild)

The probe's job is to decide "is this device cache-capable." Today that's "is lighttpd answering on
`:8080`." The replacement, requiring **no tweak change**, is a single SSH check that mmvideo is
installed:

- `test -f /Library/MobileSubstrate/DynamicLibraries/mmvideo.dylib` (mmvideo present).

**Do NOT also require the cache dir to exist.** `/var/mobile/Media/MosaicMeshCache/` is created lazily
by the `mmcache` tweak on the first segment download — a fresh device has no dir yet. Gating on the dir
would deadlock (no dir → probed incapable → no cache push → no download → dir never created). mmvideo
presence alone is the capability signal; a device that then fails to write/serve the cached file
self-corrects via the client-`<video>`-error downgrade below.

Semantics match today's lighttpd check: **presence/capability, not live-injection**. It does NOT catch
a "dylib present but not injecting into the current webclip" device (the screen15 case) — but neither
does today's lighttpd probe, so this is no regression. (A tweak-written liveness marker — option
1(ii) — would catch that, but costs a rebuild + fleet redeploy; deferred as separate hardening.)

The authoritative **downgrade** signal is unchanged: a client `<video>` error still flips the device
off cache mode (per [[cache-capability-is-client-local]] — the SSH probe is upgrade-only; never
downgrade on a probe/WiFi blip).

### Probe rewrite

`_probe_cache_capability` (server.py) keeps its structure — `_is_probe_eligible` gating,
`_PROBE_CONCURRENCY` bound, the in-flight guard, and the `client.cacheProbedMs` timestamp — and only
its verification body changes: swap the `curl http://127.0.0.1:8080/...` for the dylib-present SSH
check above. On success set `cacheMode = "mmvideo"`; leave `none` otherwise.

### cacheMode rename: `"lighttpd-localhost"` → `"mmvideo"`

The mode string is renamed everywhere it is compared or emitted (~10 sites across `server.py`,
`mosaicmesh/api/discovery.py`, `mosaicmesh/render.py`): the push-eligibility guards, the propagation
counters, and the `api_discovery_configure` validator's allowed set.

- **Migration:** `mosaicmesh.state.migrate_client_objects` (runs at boot) rewrites any
  `Client.cacheMode == "lighttpd-localhost"` to `"mmvideo"`, so devices loaded from an older
  `settings.dat` are not re-probed or downgraded.
- **Back-compat:** for one release the `configure` validator accepts BOTH `"lighttpd-localhost"` and
  `"mmvideo"` (the allowed set is `{"none", "mmvideo", "lighttpd-localhost", "service-worker"}`), so an
  in-flight admin request with the old string doesn't 400.

### Client

**Unchanged behavior.** `mmCacheBackendMmvideo.js` keeps `CACHE_DIR = 'http://127.0.0.1:8080/'` and its
`localSrc`; the `<video>` src is identical; mmvideo intercepts it. The recache-poll parser in
`index.html` (which keys off the `127.0.0.1` substring) is untouched. Only comments that reference
"lighttpd" are reworded. **No iPad-1 client redeploy.**

### lighttpd removal (operational rollout, sequenced last)

The actual daemon removal is a **fleet operation done after the code is deployed and caching is
verified still working**, not a code change:

1. Deploy the server change (restart the `:3000` server — a client-only-invisible change).
2. Confirm a device re-probes to `cacheMode = "mmvideo"` and still caches + plays cached video.
3. On **one canary screen**, stop lighttpd and verify cached playback still works (proves the bypass).
4. Then stop + disable lighttpd fleet-wide via paced SSH (per [[fleet-ssh-no-burst]] /
   [[fleet-tweak-deploy-ssh-doze]] — Windows OpenSSH, sequential, no burst).

Reversible at every step (re-enabling lighttpd is a per-device start command).

## Data flow (after)

```
mmcache tweak: download seg -> /var/mobile/Media/MosaicMeshCache/<token>.mp4   (from :3000, unchanged)
probe (SSH):   mmvideo.dylib present -> cacheMode="mmvideo"   (was: curl :8080; dir NOT gated on)
playback:      <video src="http://127.0.0.1:8080/seg_<key>.mp4">   (unchanged token)
                 -> mmvideo h_load intercepts -> mm_url_to_path -> file:///.../<name> -> AVPlayer
                 (lighttpd never involved; can be absent)
```

## Testing / verification

- **Unit (Python):**
  - Probe readiness: with the SSH check mocked to "dylib present", assert the probe sets
    `client.cacheMode == "mmvideo"`; with "dylib absent", assert it stays `"none"`. Assert the probe
    does NOT gate on the cache dir (a mocked missing dir still yields `"mmvideo"` when the dylib is
    present — the no-deadlock guarantee).
  - Boot migration: a `Client` loaded with `cacheMode == "lighttpd-localhost"` becomes `"mmvideo"`
    after `migrate_client_objects`.
  - `configure` validator: accepts `"mmvideo"` and (transitional) `"lighttpd-localhost"`; rejects an
    unknown mode.
  - Regression: the push-gating / propagation-count helpers behave identically for `"mmvideo"` as they
    did for `"lighttpd-localhost"` (parametrize the existing tests on the new string).
- **On-wall:** restart the server; confirm an eligible device re-probes to `"mmvideo"`; on a canary
  screen stop lighttpd and confirm cached video still plays (the bypass), then roll the daemon removal
  fleet-wide.

## Success criteria

The fleet caches and plays cached video with **lighttpd stopped/removed** on every device; devices
report `cacheMode = "mmvideo"`; no change to playback behavior, download, or the client. One fewer
daemon to install and keep alive.

## Constraints

- Server-side Python only for the code change (`server.py`, `mosaicmesh/`); no iPad-1 client change
  (ES5/mmvideo untouched), no tweak rebuild.
- Preserve the upgrade-only probe / client-error-downgrade invariant
  ([[cache-capability-is-client-local]]).
- Fleet SSH is paced, Windows OpenSSH, never bursted ([[fleet-ssh-no-burst]]).

## Risks

- **A device where `mmvideo.dylib` is present but not injecting** (screen15-style) probes as capable
  but can't actually play the cached token → its `<video>` errors → the existing client-error
  downgrade flips it off cache mode. So the accuracy gap is self-correcting via the authoritative
  signal; the marker (1(ii)) would only make the *first* classification tighter. Acceptable.
- **lighttpd was a real fallback for an mmvideo-interception miss.** After removal, such a miss hits a
  refused `:8080` connection instead of a lighttpd file serve → a `<video>` error → client downgrade
  (same end state as an mmvideo-absent device). Interception misses are rare; the canary step (stop
  lighttpd on one screen first) de-risks this before fleet-wide removal.
- **Rename drift:** miss one `"lighttpd-localhost"` compare site and that device silently stops being
  treated as cache-capable. Mitigated by the transitional dual-accept + a grep sweep for the literal in
  the test plan.
