# Cache push progress tracking + propagation UI

**Status:** approved (2026-06-03)
**Author:** Claude (with Jonathan)
**Builds on:** `docs/superpowers/specs/2026-06-03-media-cache-design.md`

## Motivation

The media cache shipped on 2026-06-03 left two production-load gaps. We discovered the first one — 24 parallel scps saturated the same AP the cache was meant to bypass — and patched it with `Semaphore(2)` + a static 600s per-push timeout. That works, but the static timeout is the wrong tool: a healthy-but-slow transfer over contended WiFi can legitimately need >10 minutes for a single 100 MB segment, and a hard ceiling either kills good work (set too tight) or papers over genuinely-stuck pushes (set too loose).

Second gap: from the operator's seat, there is no way to know cache state without SSHing into iPads. Acceptance criterion C5 ("aggregate central-server outbound during a 24-iPad PLAY drops from 27 GB / 93 s to ≪ 1 GB") only holds when *every* iPad in a group has its segment cached. Right now an operator can't see which iPads are cached, which are still pushing, which stalled — so they can't tell if a PLAY is going to saturate the AP until it's too late.

This spec ships:

1. **Stall-based push timeout** — replace the static 600s ceiling with "abort only if no bytes have moved in the last 30s". A push that's making forward progress, however slowly, runs to completion.
2. **Per-display-group propagation bar** in `admin.html` — operator sees "Test Group: 20/24 (83%) — 2 pushing, 2 idle" in real time, refreshed via SockJS.

## Architecture

### Stall detection

Two cooperating coroutines per push:

```
                        ┌─────────────────────────┐
   scp_proc ────────────│  push coroutine          │
   (subprocess)         │  (waits on proc OR       │
                        │   stall_event)           │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────┴─────────────┐
                        │  poller coroutine        │
                        │  every 2s:               │
                        │   ssh stat -c%s on iPad  │
                        │   update progress dict   │
                        │   if size unchanged 30s: │
                        │     stall_event.set()    │
                        │   broadcast CACHE_       │
                        │     PROGRESS via SockJS  │
                        └──────────────────────────┘
```

- Push coroutine launches scp and the poller, then waits with `asyncio.wait({proc.communicate(), stall_event.wait()}, FIRST_COMPLETED)`.
- If scp wins: success path, populate `cachedSegments`, emit final `CACHE_PROGRESS status=cached`.
- If stall wins: `proc.kill()`, emit `CACHE_PROGRESS status=stalled`, clear `cachePushProgress`. The next `force_push` (manual or post-render) gets a fresh try.

The hard ceiling `_PUSH_TIMEOUT_S` from the previous fix is **removed**. Stall detection subsumes its role and is strictly better.

### Progress measurement

iPad-side file size, via SSH:

```
ssh root@<ip> "stat -c%s /var/mobile/Media/MosaicMeshCache/seg_<token>_<n>.mp4"
```

Reasons not to use the alternatives:
- **scp -v output parsing**: fragile across scp versions; OpenSSH's scp doesn't print progress to stderr in non-tty mode at all.
- **rsync with --info=progress2**: rsync isn't on the iPad-1 base; would add another dpkg install to onboarding.
- **Wireshark / pcap sniffing**: kernel-level, OS-specific, way too much machinery for one number.

Polling cadence: **2 seconds**. Lower would add SSH overhead noise to the same WiFi that's already congested; higher would delay stall detection.

### Server-side data model

New per-Client field, in-memory only (does not persist; meaningful only during a push):

```python
client.cachePushProgress = None  # idle
# or
client.cachePushProgress = {
    "token": "f586e11f8dad",
    "n": 1,
    "bytesSent": 41_943_040,
    "totalBytes": 105_000_000,        # size of local src
    "startedMs": 1717449500120,
    "lastChangeMs": 1717449612340,    # wall-clock of last bytesSent INCREASE
    "status": "pushing",              # "pushing" | "stalled" (transient)
    "mbps": 4.7,                      # rolling avg over last 10s
}
```

After scp success: set to `None`, add to `cachedSegments`. After stall: set to `None`. So an idle iPad has `cachePushProgress=None` AND either has the seg in `cachedSegments` (cached) or doesn't (uncached).

### SockJS broadcast: `CACHE_PROGRESS`

Server emits whenever a poller tick observes a change OR a push starts/ends. Frame body:

```json
{
  "REQUEST": "CACHE_PROGRESS",
  "PAYLOAD": {
    "clientKey": "ei49puuugjznz5mi",
    "ip": "192.168.1.50",
    "displayID": "Test Group",
    "token": "f586e11f8dad",
    "n": 1,
    "bytesSent": 41943040,
    "totalBytes": 105000000,
    "percent": 39.9,
    "mbps": 4.7,
    "status": "pushing"
  }
}
```

`DEST="ALL"` — all admin clients (browsers on admin.html) see it. Each admin page filters by `displayID` to update its own group bar; if more than one admin page is open they each maintain their own state independently.

Broadcast cadence: same as poller (2s) but **debounced** — if percent hasn't changed since last broadcast, skip. Push start and push end (status `cached` / `stalled`) always broadcast unconditionally.

### Aggregate field in `/api/discovery/stats`

```json
"displayGroupPropagation": {
  "Test Group": {
    "total": 24,
    "fullyCached": 20,
    "pushing": 2,
    "stalled": 0,
    "idle": 2,
    "percent": 83.3
  }
}
```

Computed server-side at request time (cheap — O(clients) in the group). `fullyCached` counts iPads whose `cachedSegments` ⊇ the current display's renderable-segment-key set. `pushing` counts those with `cachePushProgress.status == "pushing"`. `idle` = uncached without an active push. `percent` is `fullyCached / total * 100`.

### admin.html: the propagation bar

One element per display group, rendered above the play/stop controls:

```
┌─────────────────────────────────────────────────────────┐
│ Test Group cache propagation                            │
│ ████████████████████████████████░░░░░░░░░░  83%         │
│ 20 cached · 2 pushing (avg 47%) · 2 idle                │
└─────────────────────────────────────────────────────────┘
```

- Bar width = `fullyCached / total`
- A second, lighter band overlays the bar for "in-flight" (sums each pushing iPad's percent and adds it as fractional progress beyond `fullyCached`).
- Sublabel summarises counts. When `stalled > 0`, that count appears in a yellow chip ("1 stalled — retry pending").

Updates: on initial page load, fetch `/api/discovery/stats` for the steady-state numbers. Then listen for `CACHE_PROGRESS` SockJS messages and update the bar incrementally — increment `bytesSent` for the relevant iPad, recompute aggregates client-side without re-fetching.

This page already runs modern JS (per CLAUDE.md "admin.html/discovery.html are desktop control consoles and may use modern JS") so the bar's incremental computation can use Map / Set / arrow functions freely.

### Stall window

Default `_PUSH_STALL_WINDOW_S = 30`, env-overridable via `MMPUSH_STALL_S`. Rationale: even at heavily-congested WiFi rates (~50 KB/s, the floor we've measured), a healthy scp moves at least 1.5 MB in 30s, which is many discrete `stat` size jumps. If size doesn't change at all for 30s the connection is genuinely stuck.

## API surface — new fields

`/api/discovery/devices` per-device:
- `cachePushProgress`: object or null (shape above)
- `expectedSegments`: int (count of seg_ items for current display+token)
- `propagationPercent`: float, 0-100

`/api/discovery/stats`:
- `displayGroupPropagation`: object keyed by displayID (shape above)

SockJS:
- New incoming message type `CACHE_PROGRESS` (server → admin clients only)

## Migration / rollout

1. Implement on top of `feature/discovery-completion-legacy-compat` (current working branch).
2. The 19 `tests/unit/test_media_cache.py` tests stay passing. Add new tests for:
   - `_push_segment_to_cached_clients` stall path (mock the SSH poller, advance time)
   - `_push_segment_to_cached_clients` happy path with progress callbacks
   - `/api/discovery/stats` `displayGroupPropagation` computation for mixed cached/pushing/idle group
3. Restart server. In-flight pushes from the previous-code period are aborted; their state was best-effort anyway. `force_push` re-queues whatever's still uncached.
4. Validate visually on `admin.html` with the next render → push cycle.

## Out of scope (intentionally)

- **Per-device cache column on discovery.html** — operator answered "only admin.html". Per-iPad detail is in `/api/discovery/devices` JSON if needed.
- **Persisting `cachePushProgress` across server restarts** — it's transient by design. A restart kills the scp anyway, so the progress is meaningless.
- **Bandwidth-limiting individual scps** (`scp -l`) — `Semaphore(2)` already keeps aggregate behaviour sane; per-scp throttling would slow pushes without solving any current problem.
- **Service-Worker side of propagation** — only `lighttpd-localhost` iPads have measurable "push progress". Modern devices with `cacheMode=service-worker` populate their cache via their own SW fetch, not a server push; the propagation bar can show them as "via SW (n/a)" or just exclude them from the denominator (we'll exclude — keeps the bar's denominator meaningful).

## Acceptance

1. With the new code running, a force_push of 24 segments to the Test Group fleet either completes all 24 OR shows specific stalled iPads in the admin.html bar.
2. The bar visibly increments as pushes land — it does not just jump from 0% to 100% at the end.
3. A simulated network drop (eg `iptables -A OUTPUT -d <one-ipad-ip> -j DROP` on the server, then revert) causes that iPad's push to be marked `stalled` within 30s of the drop, and the bar's "stalled" count increments.
4. After the drop is reverted, a second `force_push` re-tries the previously-stalled iPad and it completes normally.
