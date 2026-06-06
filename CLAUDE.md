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
python pytest_runner.py --js                    # Node `node --test` JS unit tests under tests/unit/js/
python pytest_runner.py --e2e                   # Playwright browser tests under tests/e2e/ (needs `npm install` + dev server up)
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

**System dependency:** `ffmpeg` (with libx264) must be on PATH for video split/mosaic rendering (`SEGMENT` `.mp4` items). Image mosaic and all other features work without it.

## Importing server.py

Arg parsing lives in `parse_args()` and is called **only** under `if __name__ == '__main__'`, so `import server` has no side effects — tests can import it directly. (Historically `parse_args()` ran at import time and forced the `tests/server_test_patch.py` monkeypatch; that patch is now redundant but harmless.)

Still relevant:
- `pytest.ini` lives in `tests/`, not the root — the runners pass `-c tests/pytest.ini`. A bare `pytest` from the root won't pick up the markers/config.
- `import server` requires the runtime deps (`numpy`, `opencv`/`cv2`, `aiohttp`, `sockjs`, `jsonpickle`, `device_detector`); without them every test errors at collection.
- Keep new import-time side effects out of the module so it stays importable.

## Architecture

**Single-process async monolith.** Server-side code is split across:
- `server.py` (~2275 lines): the entry point — owns the `aiohttp` web app + route table, registers the SockJS endpoint, owns the `settings = Settings()` singleton (instantiated inside `if __name__ == '__main__':`), runs `process()` every 5 seconds for the life of the process. Also contains route handlers (index/admin/discovery HTML, media upload, `generateAruco`/`calibrate` calibration routes, `api_media`/`api_effects`/`api_discovery_*` REST handlers, `cache_stats_handler`), the ArUco detection pipeline (`find_screen_quads_bright`, `_band_from_marker_floodfill`, etc.), client identification helpers (`_client_ip`, `_DeviceDetectorWrapper`, `_engine_str`, mDNS reverse-lookup via `_mdns_reverse`), VNC push pipeline (`_push_segment_to_cached_clients`, `_poll_push_progress`, `_get_pooled_vnc`, `_do_tap`, `_auto_arm_client`), and coordinated-start helpers (`_release_group`, `_maybe_release`).
- `mosaicmesh/` package: focused modules with one responsibility each. See Layout below.

**State lives in one global `settings` object** (`Settings` → `displays`, `scripts`, `clients` dicts) — owned by `server.py` (instantiated only when running as `__main__`) so the existing test pattern `server.settings = mock_settings` continues to work unchanged. Sub-modules reference it lazily via `import server; server.settings.X` inside function bodies (call-time lookup avoids circular-import issues at import time). State is persisted to `settings.dat` (gitignored) via `jsonpickle` in `mosaicmesh.persistence.save_settings_incremental()`, which hashes the encoding and only writes on change. On startup, `mosaicmesh.state.migrate_client_objects()` backfills newer fields onto `Client` objects loaded from an older `settings.dat`.

**Client lifecycle & the message protocol.** Browser clients connect over SockJS (`/sockjs/`) and exchange JSON messages shaped `{"SRC", "DEST", "REQUEST", "PAYLOAD"}`. `ws_handler` dispatches connection events; `msg_response()` is a large `if/elif` switch over `REQUEST` types — the de-facto API surface. Key requests: `REGISTER` (creates/updates a `Client`, runs device detection via `device_detector`, and on first contact calls `auto_configure_client`), `SYN`/`SYNACK` (readiness handshake), `SERVERTIME` (clock sync), `UPDATEDISPLAY`/`UPDATEDISPLAYGROUP`, `GENERATEARUCO`, and the discovery requests (`DISCOVERY_STATUS`, `RECONFIGURE_CLIENT`, `BULK_CONFIGURE`). The server broadcasts to a single client or a whole display group via `broadcast_to_client` / `broadcast_to_display_group` (which set `DEST` and broadcast through the shared `socketmanager`).

**Device discovery & auto-config.** `mosaicmesh.api.discovery.auto_configure_client` maps `deviceType` → display group ("Mobile"/"Tablet"/"Desktop"/"Default"), derives a friendly name, and tags capabilities (HD/touch/keyboard). A parallel **REST** surface (`/api/discovery/devices`, `/stats`, `/configure`) is handled by the `api_discovery_devices` / `api_discovery_stats` / `api_discovery_configure` handlers in `mosaicmesh.api.discovery` and returns plain JSON for the admin/discovery HTML pages. The background `process()` loop (in `server.py`) marks clients stale after 60s of silence and emits `DISCOVERY_HEARTBEAT` / `CLIENTS_WENT_OFFLINE` broadcasts.

**REST API surface for the admin UI.** The new admin timeline view (PR-4) hydrates from REST endpoints, one module per resource under `mosaicmesh/api/`:

  - `/api/playlists` (GET / POST / PUT / DELETE) — `mosaicmesh/api/playlists.py`
  - `/api/schedules` (GET / POST / PUT / DELETE) — `mosaicmesh/api/schedules.py`
  - `/api/profiles`  (GET / POST / PUT / DELETE) + `POST /api/clients/{key}/profile` — `mosaicmesh/api/profiles.py`
  - `/api/media`     (GET) + `/upload/{dest}` (POST) — `mosaicmesh/api/media.py`

All mutating endpoints use `If-Match: <_serverVersion>` for optimistic concurrency. The helper module `mosaicmesh/api/_concurrency.py` centralizes the header parsing + 412/428 response shapes. Successful PUTs bump the target's `_serverVersion` by 1; the response always echoes the new version on the returned resource. DELETE on `Playlist` or `ScriptingProfile` returns 409 + a `refs` list when the resource is referenced by a Schedule or Client respectively.

Response convention (matches `/api/discovery/configure`): `{success: true, ...}` on success, `{success: false, error: "..."}` on error. HTTP status per resource: 201 create, 204 delete, 400 validation, 404 missing, 409 conflict, 412 stale If-Match, 428 missing If-Match.

**Lifecycle scripts via ScriptingProfile (PR-3).** Each `Client` carries a `profileName` pointing into `settings.profiles[name]`. The profile holds five script templates (login/start/stop/test/reboot), a launch method (`shell` / `vnc-tap` / `ssh-then-vnc`), webclip metadata, and SSH options. `mosaicmesh.device_scripts._run_device_script(client_key, which)` resolves the profile, substitutes template variables (`{webclipBundleId}`, `{displayUrl}`, `{ip}`, etc. via `mosaicmesh.template_vars.SafeDict`), and routes through the dispatcher. The default `ipad1-ios5` profile is seeded at first boot with content byte-identical to the pre-PR-3 hardcoded scripts. Auto-match on REGISTER assigns `profileName` from the first profile whose `matchDeviceType` matches `client.deviceType` (case-insensitive); operator overrides via `POST /api/clients/{key}/profile` always win.

**Physical-layout calibration (OpenCV/ArUco).** This is the distinctive part. `generateAruco()` writes a unique DICT_6X6_50 marker per client to `media/<clientID>/images/aruco.png` and tells each client to display it. A user photographs the wall of screens and POSTs it to `/upload/calibrate`; `calibrate()` detects the markers, maps each marker ID back to a client, and records `measuredCenter`/`measuredPerimeter` so the server knows where each physical screen sits. `find_squares`/`angle_cos` are contour helpers for this.

**Clock synchronization.** Synchronized playback relies on `js/GoTime.js`, which estimates client-vs-server offset by polling `/time` (and over the websocket via the `SERVERTIME` request). Media playback targets are aligned to this shared clock so frames advance together across displays.

**File serving & caching.** Static handlers (`index_handler`, `javascript_handler`, `image_handler`, `media_handler`) read from disk through `get_cached_file()` (mtime-keyed, FIFO-capped at 100 entries). Large media uses a file-handle pool and HTTP range requests (206 responses) for video streaming; files under 10MB are cached, larger ones streamed. `/debug/cache` exposes hit/miss stats.

## Layout

- `server.py` (~2275 lines) — entry point: route table, SockJS endpoint registration, `settings = Settings()` singleton (in `__main__`), `process()` background loop. Hosts route handlers (index/admin/discovery HTML, media upload, `generateAruco`/`calibrate`, `api_media`/`api_effects`, `api_discovery_*`, `cache_stats_handler`), ArUco detection pipeline (~140 lines: `find_screen_quads_bright`, `_band_from_marker_floodfill`, etc.), client identification + mDNS helpers (`_client_ip`, `_DeviceDetectorWrapper`, `_engine_str`, `_mdns_reverse`, `resolve_client_hostnames`), VNC push pipeline (`_push_segment_to_cached_clients`, `_poll_push_progress`, `_get_pooled_vnc`, `_do_tap`, `_auto_arm_client`), and coordinated-start helpers (`_release_group`, `_maybe_release`). Also re-imports everything from `mosaicmesh/` for backward compatibility — tests and existing call sites using `server.X` or `from server import X` continue to work unchanged.
- `mosaicmesh/` — server-side code split by responsibility:
  - `state.py` — `Settings`, `Client`, `Playlist`, `Schedule`, `ScriptingProfile`, `PlayMode`, `PlayState`, `Display`, `MediaElement`, `Scripts`; `migrate_client_objects` (also seeds the default profile + migrates legacy Client `*Script` fields on first boot via `profile_bootstrap`). The singleton instance `settings = Settings()` stays in `server.py` (for the `server.settings = mock_settings` test pattern). Per-Client lifecycle scripts now flow through `client.profileName -> settings.profiles[name]`; the old `loginScript`/`startScript`/`stopScript`/`testScript`/`rebootScript` attributes were removed in PR-3.
  - `persistence.py` — `save_settings_incremental`, `saveSettings`, `cleanup_old_clients`; jsonpickle-based `settings.dat` IO.
  - `cache.py` — file-content cache + file-handle pool + `cache_stats` / `file_cache` accessors (`get_cached_file`, `get_pooled_file_handle`, `close_file_pool`, `prewarm_static_cache`).
  - `broadcast.py` — SockJS broadcast helpers (`broadcast_to_client`, `broadcast_to_display_group`, `_send_to_session`, `_deliver`).
  - `calibration.py` — ArUco math + perspective warps (`order_points`, `reconstruct_screen_quad`, `reconcile_screen_quad`, `warp_image_for_screen`, etc.). The route handlers (`generateAruco`, `calibrate`) stay in `server.py` and call into this module for the math.
  - `render.py` — ffmpeg pipeline (perspective + segment + mosaic encoding) + playback orchestration (`render_group_async`, `_apply_playlist`, `_start_group_playback`, `_stop_group_playback`, `_begin_prepare`, `_prepare_unsynced_clients`, etc.) plus encoding constants.
  - `device_scripts.py` — ScriptingProfile dispatcher: `_run_device_script` (alias of `run_profile_action`), three launch primitives (`_exec_ssh`, `_vnc_tap_sequence`, `_ssh_then_vnc`), `LAUNCH_METHODS` dispatch table, SSH constants (`SSH_KEY_PATH`, `SSH_LEGACY_OPTS`, `DISPLAY_URL`), `_drop_pooled_vnc`. The Veency pool itself (`_veency_pool`, `_veency_lock`, `_get_pooled_vnc`, `_do_tap`) still lives in `server.py` — moving it into the dispatcher is a follow-up cleanup.
  - `profile_bootstrap.py` — `DEFAULT_PROFILE_IPAD1_IOS5` (byte-identical-to-legacy content) + `seed_default_profile_if_empty` + `migrate_client_script_fields`. Called once at startup from `migrate_client_objects`.
  - `template_vars.py` — `SafeDict` + `build_vars(client, profile, **extra)`. Profile script strings reference `{webclipBundleId}`, `{displayUrl}`, `{ip}`, etc.; the dispatcher calls `str.format_map(SafeDict(build_vars(...)))` before SSH execution. Unknown tokens stay literal.
  - `scheduling.py` — `schedule_active_at` + recurrence helpers (`playlist_index`, `_parse_date`, `_hhmm_to_min`, `_FREQ_MAP`; DAILY/WEEKLY/MONTHLY/YEARLY iCal patterns via python-dateutil).
  - `websocket/legacy.py` — `msg_response` (the iPad-facing REQUEST-based protocol — byte-identical; per CLAUDE.md mandate: do not remove).
  - `websocket/typed.py` — `handle_websocket_message` (async typed protocol; intended replacement for `msg_response` but **NOT YET wired into `ws_handler`** — only exercised by direct test calls).
  - `websocket/dispatch.py` — `ws_handler` (SockJS connection lifecycle) + `handle_client_disconnect`. `ws_handler.MESSAGE` dispatches only to `msg_response` — wiring the typed handler in is a future task.
  - `api/discovery.py` — `auto_configure_client` (deviceType → displayID), `get_discovered_devices`, `sync_new_client_to_group`, the cache-push propagation calculators (`_expected_seg_keys_for_display`, `_expected_segments_for_client`, `_propagation_percent_for_client`), and the three `/api/discovery/*` aiohttp REST handlers (`api_discovery_devices`, `api_discovery_stats`, `api_discovery_configure`).
  - `api/playlists.py` — REST CRUD for `Playlist` (GET/POST/PUT/DELETE /api/playlists; If-Match concurrency; 409+refs on DELETE when referenced by a Schedule).
  - `api/schedules.py` — REST CRUD for `Schedule` with foreign-key validation (playlistName + displayID must exist), freq + byweekday + HH:MM time format + end-dict shape checks; id auto-generated server-side (uuid4-16hex).
  - `api/profiles.py`  — REST CRUD for `ScriptingProfile` + per-client assignment (`POST /api/clients/{key}/profile`). PR-2 only ships the CRUD shell; PR-3 wires dispatcher behavior + bootstrap default.
  - `api/media.py`     — `GET /api/media` (lists media/server/{images,videos} + per-video durations); `POST /upload/{dest}` (multipart upload routed to calibrate/image/video processors). Relocated from server.py in PR-2.
  - `api/_concurrency.py` — shared If-Match parsing + 412/428 response helpers used by playlists, schedules, profiles.
- `js/timeline/` — admin-side ES modules (PR-4a+, modern JS). NOT loaded on the iPad-1 display clients (those load `js/mosiacmesh.js` + `js/GoTime.js` which stay ES5 + jQuery 1.x). Top-level `js/timeline/index.js` is the Alpine.js bootstrap loaded from `admin.html`. See `js/timeline/README.md` for the module map.
- `tests/unit/js/` — Node 20+ `--test` suites for the pure-function JS modules (`util/time.js`, `util/conflicts.js`) + a module-load smoke. Run via `python pytest_runner.py --js` or `node --test tests/unit/js/*.js`.
- `tests/e2e/` — Playwright browser-driven smoke for the admin timeline interactions (drag/drop, click+Delete, double-click drill-in). Catches layout + reactivity bugs that Node `--test` can't see (PR-4a learned this lesson the hard way). Uses the `playwright` npm package directly (not `@playwright/test`) inside `tests/e2e/run.js` so there's no separate test-framework config. Requires a one-time `npm install` + `npx playwright install chromium` (gitignored `node_modules/`, ~150MB chromium binary). Each spec creates + cleans up its own `__e2e_`-prefixed playlist/schedule and calls `cleanupE2eOrphans` up-front so a previous failed spec doesn't contaminate the next one. Run via `python pytest_runner.py --e2e` (server must be on `MM_BASE_URL`, default `http://localhost:3000`) or `node tests/e2e/run.js [<substr>]`.
- `js/mosiacmesh.js` — client connection logic, UDID cookie, SockJS wiring, message construction (`generateMessage`).
- `js/GoTime.js` — clock-sync library used for synchronized playback.
- `*.html` (root) — `index` (display client), `admin` (control), `discovery` (device management).
- `media/<client>/{images,videos}/` — per-client media and generated ArUco markers (created at runtime).
- `tests/unit` reliably pass; `tests/integration` and several unit suites are partially implemented (see `tests/README.md` "Test Status").

## Conventions

- **Legacy device compatibility is a hard requirement.** Display clients must run on **1st-gen iPads (iOS 5.1 / Safari 5.1)**. Client JS (`js/mosiacmesh.js`, `js/GoTime.js`, inline `<script>` in `index.html`) must be **ES5 only** — no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. Keep **jQuery 1.x** and **SockJS** (with its polling fallbacks). `admin.html`/`discovery.html` are desktop control consoles and may use modern JS. Server-side Python is invisible to the device.
- **Admin UI uses Alpine.js 3.x + native ES modules.** Loaded from CDN; no build step. Coexists with jQuery 1.x in `admin.html` (Alpine sits alongside, doesn't replace). Display clients on the iPad-1 are unaffected — they still load ES5 + jQuery 1.x.
- **Admin timeline mutations are optimistic-local + server-confirm + rollback.** Every `store.create*`/`update*`/`delete*` snapshots the relevant store slice via `util/optimistic.js`'s `withRollback`, applies the change locally, then PUTs/POSTs with an `If-Match` header (412 → rollback + toast the server's `error` string). HTML5 drag-and-drop drives cross-element drops (playlist → track, media → drilled clip); pointer events drive in-place manipulations (clip-edge resize) where the source must stay visible. Every drag handler sets/clears `document.body.classList.mm-dragging` so other clips get `pointer-events: none` for the drag duration and don't intercept dragover/drop on the underlying `.mm-track-droparea`.
- There are **two websocket message protocols**: the legacy `REQUEST`-based one (`mosaicmesh.websocket.legacy.msg_response`, used by current JS clients — do not remove) and a newer async `type`-based one (`mosaicmesh.websocket.typed.handle_websocket_message`, the intended replacement). `handle_websocket_message` is **NOT YET wired into `ws_handler`** — it is only exercised by direct test calls; wiring it in is a future task. Per-client delivery uses the central `socketmanager` + a `DEST` field, not per-client sockets.
- The discovery REST API is served by granular handlers in `mosaicmesh.api.discovery`: `api_discovery_devices` / `api_discovery_stats` / `api_discovery_configure`. `configure` accepts both field-update (`{clientKey, displayID, friendlyName}`) and action (`{action: "reconfigure"|"bulk_reconfigure"}`) payloads.
- Note the spelling: the project, files, and SockJS manager name are all **`mosiacmesh`** (transposed "ai"). Match it exactly — the websocket manager is registered under `name='mosiacmesh'`.
- Python dict insertion order is relied upon for ArUco↔client mapping in `calibrate()` (marker ID indexes into `list(settings.clients.keys())`). Don't reorder client registration logic without accounting for this.
