# Auto-onboard `lighttpd-localhost` cache mode — Design

**Date:** 2026-06-14
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

Display clients can play rendered segments from an **on-device lighttpd** over
`http://127.0.0.1:8080/seg_<token>_<i>.mp4` (zero LAN), instead of streaming
their per-client warped segments from the central server. The machinery for
this already exists and is correctly wired:

- `_push_segment_to_cached_clients` (server.py) scps each freshly-rendered
  `seg_<token>_<i>.mp4` to the device's lighttpd cache dir, throttled, with
  stall detection and a live `_broadcast_cache_progress`.
- It is **already called from the post-render path**: `_encode_group`
  (render.py:507) fires it per segment (lines ~660–662), and `_encode_group` is
  the shared encode body invoked by *both* `render_group_async` (legacy) and
  `render_playlist_for_group_async` (auto-render). So every render attempts the
  push.
- `_per_client_items` rewrites a client's segment URL to the `127.0.0.1:8080`
  localhost form when the client is `cacheMode == "lighttpd-localhost"` AND has
  `seg_<token>_<i>` in `cachedSegments`.
- Per-client cache status is already exposed on `/api/discovery/devices`
  (`cacheMode`, `cachedSegments`, `expectedSegments`, `propagationPercent`,
  `cachePushProgress`) and surfaced by the Fleet Devices cache chip
  (`deviceCacheStatus` / CS-T1, CS-T2).

**The dead link:** `cacheMode` defaults to `"none"` (state.py:206) and is set to
`"lighttpd-localhost"` **only** by the manual `set_cache_mode` discovery action
(discovery.py:497). The whole production fleet is `"none"` (verified live: 29/29
devices). The auto-onboarding the code comment describes — *"set to
lighttpd-localhost by onboarding … ANNOUNCE_CACHE_MODE when registration
succeeds"* (state.py:201–204) — was never built. So the push always early-returns,
nothing is cached, and every client streams from the central server (observed:
0 `127.0.0.1:8080` fetches; 24 simultaneous `GET /media/<key>/seg_...` saturating
the LAN during a play test).

## Goal

Devices that are genuinely cache-capable (lighttpd serving + cache dir present)
get `cacheMode = "lighttpd-localhost"` set **automatically**, durably, and
self-healingly — so the already-wired push and the existing cache-status UI light
up with no operator action. Devices that are not capable stay on central serving.

## Non-goals

- No change to the render pipeline, the push (`_push_segment_to_cached_clients`),
  the URL rewrite (`_per_client_items`), or the propagation math. They are
  correct; this spec only flips the `cacheMode` flag that gates them.
- No device provisioning tooling (installing lighttpd / creating the cache dir).
  This spec assumes provisioned devices and detects them; provisioning is
  separate.
- No client-side (`index.html` / `mosiacmesh.js`) change. Capability is
  determined server-side over the existing SSH channel.

## Design

### 1. Server-side capability probe

`_probe_cache_capability(client_key)` — new async coroutine in `server.py`,
beside the push helpers (reuses `SSH_KEY_PATH`, `SSH_LEGACY_OPTS`, `SSH_USER`).
Fire-and-forget; never blocks a caller.

Runs one SSH command verifying **both** real prerequisites the push needs:

```
ssh -i <SSH_KEY_PATH> <SSH_LEGACY_OPTS> <SSH_USER>@<ip> \
  'test -d /var/mobile/Media/MosaicMeshCache && kill -0 "$(cat /var/run/lighttpd.pid 2>/dev/null)" 2>/dev/null && echo MM_CACHE_OK'
```

(The cache dir path matches `_push_segment_to_cached_clients`'s scp destination;
the pid-file path matches `lighttpd.conf`'s `server.pid-file`.)

**Liveness uses shell builtins only.** The original design used `curl
localhost:8080`, but live verification (2026-06-14) found the iPad-1 userland has
**no curl/wget/nc/ps** — any external-binary check returns 127 ("command not
found") and would wrongly report a serving device as not-capable. `kill -0` on
lighttpd's pid file confirms the daemon is alive (hence bound to its configured
:8080) using only the `kill` builtin + `cat`.

Outcome handling (mutates `settings.clients[client_key]`, then persists +
broadcasts only on a state change):

- **`MM_CACHE_OK` in stdout, rc 0** → if `cacheMode != "lighttpd-localhost"`, set
  it. Log. (cachedSegments left as-is; the next render's push fills them.)
- **Anything else** (rc != 0, timeout, missing `MM_CACHE_OK`) → if `cacheMode ==
  "lighttpd-localhost"`, **downgrade to `"none"` and clear `cachedSegments`**
  (lighttpd/dir is gone; cached entries are unreachable and would otherwise keep
  handing out dead localhost URLs). Log. If already `"none"`, no-op.
- Always set `client.cacheProbedMs = int(time.time()*1000)` (observability).

Bounding (the 24-device mass-reconnect concern):

- A module-level `asyncio.Semaphore` (`_get_probe_sem()`, cap ~4) wraps the SSH
  call so a mass reconnect can't open 24 SSH connections at once.
- A module-level in-flight `set` of `client_key`s: if a probe for this key is
  already running, the new request returns immediately (no result caching — we
  still probe on the *next* register per the chosen cadence; this only prevents
  duplicate *concurrent* probes for the same device).
- Short SSH `ConnectTimeout` (10s) + an overall `asyncio.wait_for` ceiling (e.g.
  20s) so a dead device can't pin a semaphore slot.

Guards: skip if no `client.ip`. Skip non-display devices (see §2).

### 2. REGISTER wiring

In `mosaicmesh/websocket/legacy.py`, in the `REGISTER` branch of `msg_response`,
**after** the client is created/updated and `auto_configure_client` runs:

```python
if _is_probe_eligible(client):          # iPad / iOS display device with an ip
    asyncio.ensure_future(server._probe_cache_capability(key))
```

- Fire-and-forget — REGISTER handling does not await the probe.
- `_is_probe_eligible(client)` gates to the provisioned display devices
  (deviceType indicates iPad / iOS) so we do not SSH every desktop/phone that
  registers. Non-eligible devices never get `cacheMode` and always serve
  centrally — correct.
- Cadence: **every** eligible REGISTER (chosen for maximum self-healing). The
  semaphore + in-flight guard from §1 keep this bounded.

### 3. Payoff (no new code)

Once `cacheMode` flips to `"lighttpd-localhost"`, the existing post-render push
(`_encode_group`) scps that device's segments on the next render, fills
`cachedSegments`, and `_per_client_items` then emits `127.0.0.1:8080` URLs. The
LAN saturation and stall behavior disappear for cached devices.

### 4. UI: per-client cache status

`/api/discovery/devices` already returns the cache fields; add `cacheProbedMs`.
Extend the **pure** `deviceCacheStatus` helper (CS-T1, node-tested) + the Fleet
Devices cache chip (CS-T2) to render three clear states from the existing
fields:

- **network** — `cacheMode == "none"` (probed, not capable): serves centrally.
- **caching… N%** — `cacheMode == "lighttpd-localhost"` and
  `propagationPercent < 100` (uses `cachePushProgress` for the in-flight bar).
- **local ✓** — `cacheMode == "lighttpd-localhost"` and `propagationPercent ==
  100`.

Live-updates via the existing cache broadcast (`_broadcast_cache_progress`) and
the discovery devices refresh. No new endpoint.

### 5. Persistence & broadcast

`cacheMode` and `cachedSegments` persist in `settings.dat` (jsonpickle) and
already survive restart and re-registration (the `migrate_client_objects`
`hasattr` guards never overwrite an existing value). The probe calls the existing
save + broadcast path on a state change so the admin UI updates live.

## Error handling

- SSH unreachable / timeout / non-zero → treated as "not capable" → downgrade
  path (or stay `none`). Logged at WARNING. No exception escapes the coroutine.
- Source segment missing at push time is already handled by the existing push
  (best-effort; falls back to central URL).
- A device flipped capable but whose later push fails stays `lighttpd-localhost`
  with that segment absent from `cachedSegments`, so `_per_client_items` serves
  *that* segment centrally — partial-cache correctness is already the existing
  behavior.

## Testing

- **Unit (`tests/unit/test_media_cache.py`)** — `_probe_cache_capability` with a
  mocked SSH `asyncio` subprocess (the file's existing subprocess-mock pattern):
  - `MM_CACHE_OK` rc0 → sets `cacheMode = "lighttpd-localhost"`.
  - rc!=0 / no marker → leaves `none`; and from `lighttpd-localhost` →
    downgrades to `none` AND clears `cachedSegments`.
  - in-flight guard: a second concurrent call for the same key is a no-op.
  - non-eligible device / no ip → no SSH attempted.
- **Unit (node, `tests/unit/js/`)** — extend `deviceCacheStatus` for the three
  states (network / caching%/ local).
- **Manual fleet** — register an eligible provisioned sign; confirm the log shows
  the probe flip it to `lighttpd-localhost`, the next render's push populates
  `cachedSegments`, and a play test shows `127.0.0.1:8080` fetches with no LAN
  saturation. A device without lighttpd stays `none` and serves centrally.

## File map

- `server.py` — `_probe_cache_capability`, `_get_probe_sem`, in-flight set;
  `cacheProbedMs` into the device payload (or via `mosaicmesh/api/discovery.py`
  `get_discovered_devices`).
- `mosaicmesh/websocket/legacy.py` — `_is_probe_eligible` + the `ensure_future`
  call in the REGISTER branch.
- `mosaicmesh/state.py` — `cacheProbedMs` field default + migration `hasattr`
  backfill.
- `mosaicmesh/api/discovery.py` — include `cacheProbedMs` in the device payload.
- `js/timeline/fleet/fleet-status.js` — extend `deviceCacheStatus` (3 states).
- Tests as above.
