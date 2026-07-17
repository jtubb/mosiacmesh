# Phase 1 — Restore the real-websocket transport: de-duplicate `meshCells`

**Date:** 2026-07-17
**Status:** design, pending review
**Context:** Phase 1 of the "better playback coordination" track (Phase 2 = frequency-disciplined
GoTime; Phase 3 = mmvideo `file:///` playback). The clean `SERVERTIME`-over-ws clock samples that
Phase 2 needs are only available when the fleet holds a real websocket — which it currently does
NOT. This phase restores that.

## Problem

The MosaicMesh iPad-1 fleet reached a real RFC-6455 websocket transport (native `mmws` transplant)
fleet-wide on 2026-07-08 (`xhr_send`=0, `/websocket`=23). It has since **regressed to SockJS-over-XHR
polling** (last 2 h: 136 `xhr_send` vs 1 `websocket`), which:

- makes clock samples ride jittery HTTP `/time` polls instead of `SERVERTIME` over a persistent ws
  (phase std-dev ~356 ms on XHR vs ~9 ms on ws), directly worsening playback drift;
- causes an open/overflow **reconnect loop** on every device (mmws.log: `OPEN=201 overflow=125`).

### Root cause (pinned)

The device `mmws.log` shows the failure signature:

```
[mmws] ERROR sid=35 msg=rx overflow rxn=130082 len=4096 cap=131072
```

mmws reassembles inbound ws frames into a fixed **128 KB** buffer (`MMWS_RXCAP` in
`tweak/mmws/mmws_sm.h`). A frame larger than that can never complete → overflow → ws torn down →
SockJS falls back to XHR → reconnect storm.

The oversized frame is the **per-client PLAY/PRELOAD** message for the mesh group. `_per_client_items`
(`mosaicmesh/render.py:1437–1447`) attaches the display's **entire mesh warp grid**,
`item["meshCells"]`, to **every mesh SCRIPT item**. Measured on the live wall:

| Quantity | Size |
|---|---|
| `meshCells` (meshGrid `[6,4]` = 24 cells, 4 pts × 2 floats) | ~4 KB |
| × 21 items in "All Effects Demo (Mesh)" | **~83 KB app message** |
| SockJS double-encoding (`a["…escaped…"]`) | **~166 KB ws frame** |
| mmws rx buffer | 128 KB → **overflow** |

`meshCells` is **display-level** geometry — identical for all items — so 20 of the 21 copies are pure
waste. The other mesh fields (`meshGlobal` = 2 floats, `meshGrid` = 2 ints, `meshQuad` = 4 pts) are
small and stay per-item; **only `meshCells` causes the bloat.**

This is a data-duplication bug, not a fundamentally-too-small buffer. The 07-08 fix (16 KB→128 KB)
treated the symptom; a fixed buffer regressed the moment the mesh payload grew.

## Goals / non-goals

- **Goal:** shrink the per-client PLAY/PRELOAD payload below the mmws buffer so the fleet holds the
  real ws again; keep the mesh render visually identical.
- **Goal (defensive):** make mmws tolerant of future payload growth so this class of regression
  can't silently recur.
- **Non-goal:** any change to the mesh render math, calibration, or the effect rotation. `meshCells`
  content is unchanged — only *where in the message* it travels.
- **Non-goal:** Phase 2 (GoTime frequency discipline). This phase only unblocks it.

## Design

### Part A — De-duplicate `meshCells` (primary fix; server + client, NO tweak)

**Server (`mosaicmesh/render.py`).** Stop attaching `meshCells` per item. Instead attach it **once**
at the message PAYLOAD level, in the two per-client senders that carry mesh SCRIPT items:

- `_broadcast_per_client_play` (PLAY) and `_broadcast_per_client_preload` (PRELOAD).
- Payload gains a sibling of `items`: `"meshCells": <grid or null>`, computed once per client from
  `display.meshCells` (same fresh-list construction as today, to avoid jsonpickle `{"py/id"}`
  back-refs the iPad JSON.parse can't read).
- `_per_client_items` keeps attaching `meshGlobal`/`meshGrid`/`meshQuad` per item (small), and STOPS
  attaching `meshCells`.
- Rationale for payload-level (not a top-level `display` object): minimal blast radius, and the
  value is per-client-message anyway (PLAY and PRELOAD each already build a per-client payload).

**Client (`index.html`).** On receiving a PLAY/PRELOAD message, if `PAYLOAD.meshCells` is present,
hydrate it back onto each item **before** the render loop runs:

```
for (var i = 0; i < items.length; i++) { items[i].meshCells = payload.meshCells; }
```

Then the existing consumer at `index.html:863–864`
(`{ ..., cells: (it.meshCells || null) }`) is **unchanged** — it still reads `it.meshCells`. This
keeps the change to a single hydration loop at message intake and touches none of the ~15 mesh
render sites. (Sharing one array reference across items is safe on the client: it is read-only in the
render path.)

**Result:** per-client PLAY/PRELOAD drops from ~83 KB to ~4 KB (`meshCells` once) + small per-item
fields ≈ ~10–12 KB → ~20–24 KB framed → well under 128 KB. The ws stops overflowing.

### Part B — Grow the mmws rx buffer dynamically (defensive backstop; tweak) — DEFERRED

> **DEFERRED (2026-07-17):** the implementation plan covers **Part A only**. Part A fixes the live
> regression with no tweak risk; Part B is tracked as a separate future hardening task (rebuild +
> fleet redeploy of `mmws.dylib`) and is not in scope for this plan.


So a future payload can't silently regress the transport again, replace the fixed
`uint8_t rx[MMWS_RXCAP]` reassembly buffer with a **heap buffer that grows on demand** (`malloc` at
connect, `realloc` when an incoming frame needs more) up to a high safety cap (e.g. 1 MB), then a
hard error only past that. One buffer per `MMWSConn` (already `calloc`'d). This is a
`tweak/mmws/mmws_sm.{h,c}` change → rebuild `mmws.dylib` → fleet redeploy (Windows-OpenSSH push +
reload). It is **secondary** — Part A fixes the live regression with no tweak risk; Part B is the
durability guard and can ship separately.

## Data flow (after)

```
PLAY/PRELOAD build (server, per client):
  payload = { startEpoch, loop, seed,
              items: [ {file, playmode, meshGlobal, meshGrid, meshQuad, …} × N ],   # NO meshCells
              meshCells: <grid once, or null> }
  -> SockJS frame ~20–24 KB  (< 128 KB)  -> mmws holds the ws

client intake:
  if payload.meshCells: for each item: item.meshCells = payload.meshCells
  -> existing render reads it.meshCells unchanged
```

## Testing / verification

- **Node unit test** (`tests/unit/js/`): a pure helper `mmHydrateMeshCells(items, payloadMeshCells)`
  that sets `meshCells` on each item; assert it hydrates and is a no-op when the field is absent
  (keeps the intake logic testable without a DOM).
- **Payload-size assertion** (Python unit): build a per-client PLAY for a mesh group with N items and
  assert the encoded size is O(one grid), not O(N × grid) — a regression guard so `meshCells` can't
  drift back into `_per_client_items`.
- **On-wall verification:** after server restart + RELOAD, confirm `xhr_send`→~0 and `/websocket`
  count climbs (access log), and device `mmws.log` shows `overflow=0`, `OPEN` stops looping. Confirm
  the mesh animation still renders correctly (meshCells hydrated).

## Deploy

- **Part A:** `render.py` + `index.html` — server restart + group RELOAD (no per-device SSH, no
  tweak). Note: a server restart re-loads `settings.dat`; verify OEB Sign 1 group membership + mesh
  calibration survive the restart before RELOAD.
- **Part B (if shipped):** rebuild `mmws.dylib` (WSL theos), push to the fleet via Windows OpenSSH,
  fleet reload.

## Risks

- **Reference sharing on the client:** hydrating the same `meshCells` array onto every item is safe
  only if the render path never mutates it. It doesn't today (read-only), but the test/no-mutation
  assumption should be noted so a future edit doesn't alias-corrupt it.
- **Server restart clobber:** restarts have previously appeared to reset OEB group config; verify
  membership + calibration post-restart (this is a known operational gotcha, not caused by this fix).
