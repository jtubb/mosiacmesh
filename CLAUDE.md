# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MosaicMesh is a distributed display / video-wall system. A single Python server coordinates many browser-based display clients (phones, tablets, desktops) so they can play synchronized media across a physical arrangement of screens. The server fingerprints each connecting device, auto-assigns it to a display group, and can calibrate the *physical* layout of screens by detecting ArUco markers in an uploaded photo.

There is no build step. The frontend is hand-written HTML at the repo root (`index.html`, `admin.html`, `discovery.html`) plus vendored JS in `js/`. The server serves these files directly.

## Commands

```bash
# Run the server (defaults to port 3000)
python server.py
python server.py -p 8080 -v          # custom port, verbose/DEBUG logging

# Tests — ALWAYS use a runner, never `pytest` bare (see gotcha below)
python pytest_runner.py --unit                  # unit tests (the reliably-passing set)
python pytest_runner.py --integration           # integration tests (some are WIP/failing)
python pytest_runner.py --unit --coverage        # + HTML coverage in htmlcov/
python pytest_runner.py --unit --verbose
run_tests.bat --unit                            # Windows-native equivalent

# Run a single test file / function (note the -c pointing at tests/pytest.ini)
python -m pytest tests/unit/test_client_management.py -c tests/pytest.ini -v
python -m pytest tests/unit/test_client_management.py::TestClientClass::test_client_initialization -c tests/pytest.ini

# Filter by marker: slow, integration, unit, websocket, api, discovery
python -m pytest -m "api" -c tests/pytest.ini

# Install deps
pip install -r requirements.txt           # runtime
pip install -r requirements-test.txt      # tests
```

## Importing server.py

Arg parsing lives in `parse_args()` and is called **only** under `if __name__ == '__main__'`, so `import server` has no side effects — tests can import it directly. (Historically `parse_args()` ran at import time and forced the `tests/server_test_patch.py` monkeypatch; that patch is now redundant but harmless.)

Still relevant:
- `pytest.ini` lives in `tests/`, not the root — the runners pass `-c tests/pytest.ini`. A bare `pytest` from the root won't pick up the markers/config.
- `import server` requires the runtime deps (`numpy`, `opencv`/`cv2`, `aiohttp`, `sockjs`, `jsonpickle`, `device_detector`); without them every test errors at collection.
- Keep new import-time side effects out of the module so it stays importable.

## Architecture

**Single-process async monolith.** Everything server-side is in `server.py` (~1000 lines): an `aiohttp` web app with a SockJS endpoint, plus a background loop. The `__main__` block builds the route table, starts the `TCPSite`, and runs `process()` every 5 seconds for the life of the process.

**State lives in one global `settings` object** (`Settings` → `displays`, `scripts`, `clients` dicts). Most functions mutate this global directly rather than taking it as a parameter — that's why tests assign `server.settings = mock_settings`. State is persisted to `settings.dat` (gitignored) via `jsonpickle` in `save_settings_incremental()`, which hashes the encoding and only writes on change. On startup, `migrate_client_objects()` backfills newer fields onto `Client` objects loaded from an older `settings.dat`.

**Client lifecycle & the message protocol.** Browser clients connect over SockJS (`/sockjs/`) and exchange JSON messages shaped `{"SRC", "DEST", "REQUEST", "PAYLOAD"}`. `ws_handler` dispatches connection events; `msg_response()` is a large `if/elif` switch over `REQUEST` types — the de-facto API surface. Key requests: `REGISTER` (creates/updates a `Client`, runs device detection via `device_detector`, and on first contact calls `auto_configure_client`), `SYN`/`SYNACK` (readiness handshake), `SERVERTIME` (clock sync), `UPDATEDISPLAY`/`UPDATEDISPLAYGROUP`, `GENERATEARUCO`, and the discovery requests (`DISCOVERY_STATUS`, `RECONFIGURE_CLIENT`, `BULK_CONFIGURE`). The server broadcasts to a single client or a whole display group via `broadcast_to_client` / `broadcast_to_display_group` (which set `DEST` and broadcast through the shared `socketmanager`).

**Device discovery & auto-config.** `auto_configure_client` maps `deviceType` → display group ("Mobile"/"Tablet"/"Desktop"/"Default"), derives a friendly name, and tags capabilities (HD/touch/keyboard). A parallel **REST** surface (`/api/discovery/devices`, `/stats`, `/configure`) is handled by `discovery_api_handler` and returns plain JSON for the admin/discovery HTML pages. The background `process()` loop marks clients stale after 60s of silence and emits `DISCOVERY_HEARTBEAT` / `CLIENTS_WENT_OFFLINE` broadcasts.

**Physical-layout calibration (OpenCV/ArUco).** This is the distinctive part. `generateAruco()` writes a unique DICT_6X6_50 marker per client to `media/<clientID>/images/aruco.png` and tells each client to display it. A user photographs the wall of screens and POSTs it to `/upload/calibrate`; `calibrate()` detects the markers, maps each marker ID back to a client, and records `measuredCenter`/`measuredPerimeter` so the server knows where each physical screen sits. `find_squares`/`angle_cos` are contour helpers for this.

**Clock synchronization.** Synchronized playback relies on `js/GoTime.js`, which estimates client-vs-server offset by polling `/time` (and over the websocket via the `SERVERTIME` request). Media playback targets are aligned to this shared clock so frames advance together across displays.

**File serving & caching.** Static handlers (`index_handler`, `javascript_handler`, `image_handler`, `media_handler`) read from disk through `get_cached_file()` (mtime-keyed, FIFO-capped at 100 entries). Large media uses a file-handle pool and HTTP range requests (206 responses) for video streaming; files under 10MB are cached, larger ones streamed. `/debug/cache` exposes hit/miss stats.

## Layout

- `server.py` — entire backend (routes, websocket dispatch, discovery, calibration, caching).
- `js/mosiacmesh.js` — client connection logic, UDID cookie, SockJS wiring, message construction (`generateMessage`).
- `js/GoTime.js` — clock-sync library used for synchronized playback.
- `*.html` (root) — `index` (display client), `admin` (control), `discovery` (device management).
- `media/<client>/{images,videos}/` — per-client media and generated ArUco markers (created at runtime).
- `tests/unit` reliably pass; `tests/integration` and several unit suites are partially implemented (see `tests/README.md` "Test Status").

## Conventions

- **Legacy device compatibility is a hard requirement.** Display clients must run on **1st-gen iPads (iOS 5.1 / Safari 5.1)**. Client JS (`js/mosiacmesh.js`, `js/GoTime.js`, inline `<script>` in `index.html`) must be **ES5 only** — no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. Keep **jQuery 1.x** and **SockJS** (with its polling fallbacks). `admin.html`/`discovery.html` are desktop control consoles and may use modern JS. Server-side Python is invisible to the device.
- There are **two websocket message protocols**: the legacy `REQUEST`-based one (`msg_response`, used by current JS clients — do not remove) and a newer async `type`-based one (`handle_websocket_message`, the intended replacement). Per-client delivery uses the central `socketmanager` + a `DEST` field, not per-client sockets.
- The discovery REST API is served by granular handlers: `api_discovery_devices` / `api_discovery_stats` / `api_discovery_configure`. `configure` accepts both field-update (`{clientKey, displayID, friendlyName}`) and action (`{action: "reconfigure"|"bulk_reconfigure"}`) payloads.
- Note the spelling: the project, files, and SockJS manager name are all **`mosiacmesh`** (transposed "ai"). Match it exactly — the websocket manager is registered under `name='mosiacmesh'`.
- Python dict insertion order is relied upon for ArUco↔client mapping in `calibrate()` (marker ID indexes into `list(settings.clients.keys())`). Don't reorder client registration logic without accounting for this.
