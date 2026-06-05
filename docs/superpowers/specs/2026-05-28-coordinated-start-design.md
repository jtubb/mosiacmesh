# Coordinated Start Release — Design

**Goal:** Make every display in a group begin playback on the *same* shared-clock
instant from a *pre-buffered, frame-0 hold*, instead of each client jumping in at
"now" and independently catching up by seeking. This removes the startup
catch-up that left the iPad ~467 ms behind and makes heterogeneous devices
(iPad 1 vs desktop) start frame-locked.

## Problem (current behavior)

On a PLAY request the server immediately sets `display.playStartEpoch` (= now, or
a resume epoch) and broadcasts `PLAY` with that epoch. Each client computes
`elapsed = GoTime.now() - startEpoch` and *starts wherever the clock already is*,
then corrects with `driftTick` seeks.

Consequences:
- **Slow devices start behind.** An iPad 1 can't seek+decode to the target offset
  instantly; by the time its first frame shows, the clock has moved on, so it
  sits ~467 ms behind and the seek-to-catch-up is fragile (it was the source of
  the thrash we just fixed with the cooldown). The −467 ms is a *seek/decode
  latency artifact of starting late*, not an inherent `play()` delay.
- **Heterogeneous devices drift apart.** A desktop starts ~immediately; an iPad
  starts late — so the wall tears across device types even when each is
  internally stable.
- **iOS needs a gesture to play.** "Start now" is meaningless for an un-armed
  iPad; the existing flow has no notion of "hold until the device can actually
  play."

## Core idea: two-phase start (PREPARE → READY → GO)

Split "play" into **prepare** (buffer + hold frame 0) and **release** (start at a
shared future epoch).

```
admin PLAY ─▶ server: PREPARE(prepareId, items) ─▶ all clients in group
                         │
clients: preload, seek persistent <video> to item0/offset0, PAUSE there,
         buffer to a playable readyState, confirm armed ─▶ READY(prepareId)
                         │
server: when ALL online clients READY (or timeout) ─▶ pick startEpoch = now + LEAD
        ─▶ PLAY(startEpoch in the FUTURE) to the group
                         │
clients: hold frame 0 until GoTime.now() == startEpoch, then play. Everyone
         releases the first frame on the same tick. driftTick = gentle
         maintenance only.
```

Because each client releases from a *paused, pre-buffered frame 0* (no seek, no
buffer wait at release), `play()` latency is minimal and uniform — which is what
actually closes the −467 ms gap, with no per-device compensation needed in v1.

## Protocol

Three message types (the legacy `REQUEST`-based protocol, ES5-client-safe):

- **`PREPARE`** (server → group): `{prepareId, items, loop}`. "Get ready to play
  this from the start; do not start the clock yet."
- **`READY`** (client → server): `{prepareId}`. "I am buffered and holding
  frame 0 (and armed, if I'm a gesture device)."
- **`NEEDS_ARM`** (client → server): `{prepareId}`. "I am buffered and holding
  frame 0, but I am a gesture device that is not yet armed — I cannot `play()` at
  GO until something delivers a touch." Triggers server-side auto-arming (below).
- **`PLAY`** (server → group) — *reused* as the GO: `{startEpoch, items, loop}`
  where **`startEpoch` is a near-future server time**. Clients already prepared
  hold until `startEpoch`, then begin. (A `PLAY` whose `startEpoch` is in the
  *past* keeps today's meaning — immediate jump-in + catch-up — which is exactly
  what a late joiner / reconnect needs.)

`prepareId` is a monotonic/uuid token per PREPARE so stale `READY`s (from a
superseded prepare) are ignored.

## Server components

**State (on `Display`):**
- `action` enum gains `PREPARING`.
- `prepareId` — current prepare token (or None).
- `readyClients` — set of client keys that reported READY for `prepareId`.
- `prepareDeadline` — server-time ms after which we release regardless.

**Flow (in the PLAY request handler + the background `process()` loop):**
1. **PLAY request** → if render-gating passes (today's checks): set
   `action = PREPARING`, new `prepareId`, `readyClients = {}`,
   `prepareDeadline = now + PREPARE_TIMEOUT_MS`; broadcast `PREPARE`.
   (Renderable playlists still render first, as today; PREPARE comes after
   render readiness.)
2. **READY handler** (`msg_response`): if `msg.prepareId == display.prepareId`,
   add the client to `readyClients`. If `readyClients` now covers all *online*
   clients in the group → **release**.
3. **`process()` tick**: for any display in `PREPARING` past `prepareDeadline`,
   **release anyway** (log the laggards) so one stuck/asleep device can't freeze
   the wall.
4. **Release** = set `playStartEpoch = now + RELEASE_LEAD_MS`, `action = PLAY`,
   `prepareId = None`, broadcast `PLAY` (per-client for renderable items, as
   today) with that future epoch.

Reuse `_broadcast_per_client_play` / `broadcast_to_display_group` for both
PREPARE and the GO so per-client renderable URLs keep working.

## Client behavior (`index.html`)

- **On `PREPARE`**: run the existing PRELOAD path; then for the *first* item:
  - video → set the persistent `<video>` src, `load()`, seek to offset 0, and
    **pause** it; mark "armed" when the gesture/`activated` flag is set (iPad: the
    VNC arming tap arrives during this window). Send `READY` once
    `readyState >= HAVE_FUTURE_DATA` **and** armed.
  - non-video (SCRIPT/image) → no buffering needed; send `READY` immediately.
  - Do **not** start `renderPlayback` / the clock yet.
- **On `PLAY`**:
  - `startEpoch` in the **future** → `playback.startEpoch = startEpoch`,
    `active = true`, keep frame 0 paused, and
    `setTimeout(renderPlayback, startEpoch - GoTime.now())`. At fire time the
    video is already buffered at 0, so it plays with minimal latency.
  - `startEpoch` in the **past/now** → `renderPlayback()` immediately (today's
    behavior; late-join / resume / reconnect).
- Existing `driftTick` (asymmetric, cooldown-limited) is unchanged and now only
  does small maintenance corrections.

### Arm-then-hold (gesture devices)

A tap normally *starts* playback (the `#tapstart` overlay → `activatePlayback`
→ `play()`). During PREPARE we must instead **consume the gesture to arm the
element, then hold frame 0**. So when a gesture device receives the arming
touch during PREPARE, it: lets `play()` fire (which sets `activated` on the
`'playing'` event), then immediately **pauses and seeks back to 0**, and sends
`READY`. The element is now armed *and* held at frame 0, so the GO releases it
like any other client. (A device already armed from a prior session skips
straight to `READY`.)

## Auto-arming (server-orchestrated VNC tap)

Gesture devices (iOS) can preload/buffer without a gesture but cannot `play()`
without one. To make the start fully unattended, the **server** delivers the
arming touch during PREPARE:

1. On PREPARE, a gesture device that is buffered but not armed sends
   `NEEDS_ARM {prepareId}` (instead of `READY`).
2. The server, for a `NEEDS_ARM` client that has a known LAN IP, fires a single
   VNC tap at the screen centre via Veency (the device is configured headless,
   password `mosaic`, port 5900 — see `tools/veency`). This is the existing
   `vnc_tap` capability, invoked from the server as an **asyncio subprocess**
   (`vncdo -s <ip>::5900 -p <pw> move <cx> <cy> click 1`) so it never blocks the
   event loop. Tap centre = `deviceWidth/2 × deviceHeight/2` (the overlay is
   full-screen).
3. The tap arms the element (arm-then-hold, above); the client then sends
   `READY`, and release proceeds normally.

Notes:
- **Server, not agent.** The running server process shells out to `vncdo`; this
  is the server's own action at runtime, independent of the harness gating that
  applies to the *agent*.
- **Best-effort.** If `vncdo` is missing or the tap fails, the client stays
  un-ready and the `PREPARE_TIMEOUT_MS` release covers it (degrades to "needs a
  manual tap", today's behaviour) — auto-arming never blocks the wall.
- **Config:** `VEENCY_PASSWORD` (default `mosaic`), `VEENCY_PORT` (5900), and a
  toggle `AUTO_ARM` (default on) so it can be disabled per deployment.
- A device only gets tapped when it reports `NEEDS_ARM`, so already-armed devices
  are never touched.

## Timing constants

- `RELEASE_LEAD_MS` (~750 ms): GO must reach every client and let their
  `setTimeout` fire before the start instant. Must exceed worst-case
  message-delivery + scheduling jitter; GoTime keeps the clocks aligned so the
  absolute instant is shared.
- `PREPARE_TIMEOUT_MS` (~5 s): max wait for all READYs before releasing without
  the laggards.

## Edge cases

- **Late join / reconnect:** unchanged — `sync_new_client_to_group` sends `PLAY`
  with the *existing* (past) `playStartEpoch`; the client jumps to the current
  offset and uses the gentle drift path. Late joiners don't re-coordinate the
  whole group.
- **A device never reports READY** (asleep, un-armed, offline): the
  `PREPARE_TIMEOUT_MS` release fires without it; it re-syncs via the late-join
  path when it returns.
- **Un-armed iPad:** it can buffer (preload) without a gesture but cannot `play()`
  at GO until armed, so it sends `NEEDS_ARM` and the server auto-arms it via VNC
  (see "Auto-arming"). If auto-arming fails, the timeout release covers it.
- **New PLAY supersedes an in-flight PREPARE:** bump `prepareId`; stale `READY`s
  are dropped by the id check.
- **Single-client group:** releases as soon as that one client is READY (or
  timeout) — same path, no special case.

## Interaction with existing work

- Builds directly on the now-stable engine (Range/206 fix, keep-alive,
  cooldown'd asymmetric drift). Drift correction stays as the *maintenance* layer
  under the coordinated start.
- The `REPORT_CANVAS` and calibration paths are untouched.

## Testing

**Server (unit, `msg_response` + helpers):**
- PLAY on a ready (rendered) group → broadcasts `PREPARE` with a `prepareId`,
  sets `action = PREPARING`, does **not** set `playStartEpoch` yet.
- `READY` from all online clients → releases: `action = PLAY`, `playStartEpoch`
  set to a *future* epoch, `PLAY` broadcast.
- `READY` from a subset → no release; partial `readyClients`.
- `NEEDS_ARM` from a touch client with an IP → server invokes the VNC-tap
  subprocess for that client (mock the subprocess; assert it's called with the
  client's IP + centre coords); client not counted ready until its later `READY`.
- `NEEDS_ARM` with auto-arm disabled / no IP / vncdo missing → no crash; client
  covered by timeout release.
- `prepareDeadline` elapsed in `process()` → release despite missing clients.
- Stale `READY` (wrong `prepareId`) → ignored.
- Late joiner (`sync_new_client_to_group`) → still gets `PLAY` with the existing
  past epoch (no PREPARE).

**Client (extracted-logic / behavioral):**
- `PREPARE` → emits `READY` after buffering (mock the video element's
  readyState/armed).
- `PLAY` with a future `startEpoch` → defers start (no `renderPlayback` until the
  scheduled instant); video stays paused at 0 until then.
- `PLAY` with a past `startEpoch` → starts immediately.
- Gesture device on `PREPARE`, not yet armed → emits `NEEDS_ARM` (not `READY`);
  after a simulated arming touch, arm-then-hold pauses at 0 and emits `READY`.

## Out of scope (v1)

- **Per-device-class latency compensation** (firing GO earlier for slower
  devices). Start from a pre-buffered frame should make `play()` latency small
  and uniform; revisit only if measurement shows a residual cross-class offset.
- **Mid-playlist re-coordination** (re-syncing the whole group at each item
  boundary). Item transitions stay clock-driven via `renderPlayback`.
