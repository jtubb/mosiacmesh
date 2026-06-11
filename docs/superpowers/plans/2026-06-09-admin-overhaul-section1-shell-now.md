# Admin Overhaul Section 1 (App Shell + Now) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single crammed admin screen with a responsive four-destination shell (Now · Content · Schedule · Fleet) and a live "what's playing where" Now landing, backed by a new read-only playback-state surface, while removing the dead legacy jQuery.

**Architecture:** A read-only server playback surface (`Display.currentPlaylistName` + `GET /api/playback` + a `PLAYBACK_CHANGED` SockJS broadcast) exposes real per-group playback. On the client, the nav and Now cards are **declarative Alpine markup** bound to new store state (`activeTab`, `connection`, `playback`, a `nowCards` getter), with `js/timeline/shell/router.js` syncing the URL hash ⇄ `store.activeTab` and `js/timeline/now-summary.js` deriving cards as a pure function. `modal-shell.js` gains a CSS-only modal⇄sheet swap. Content/Schedule/Fleet are placeholder tabs.

**Tech Stack:** Alpine 3.x + native ES modules (no build step), aiohttp + SockJS + jsonpickle server, `node --test` JS units, pytest (`-c tests/pytest.ini`), Playwright via `tests/e2e/run.js`.

---

## Conventions the implementer must know

- **Tests run via runners.** Python: `python -m pytest <path> -c tests/pytest.ini -v` (never bare `pytest`). JS units: `node --test tests/unit/js/<file>.js`. Full JS suite: `python pytest_runner.py --js`. E2E: `node tests/e2e/run.js <substr>` (dev server must be on `http://localhost:3000`).
- **No build step.** ES modules are loaded directly by the browser. `admin.html` is modern JS (Alpine 3.x) — `const`/`let`/arrow/template-literals are fine. The **ES5 constraint applies only to `index.html`** (the iPad-1 display client), which this section does NOT touch.
- **Store mutation pattern.** Components mutate through the Alpine proxy `Alpine.store('mm')`, never the raw object. New store methods follow the existing `setStatus`/`setRenderInProgress` style.
- **SockJS broadcast pattern.** Server broadcasts via `server.socketmanager.broadcast(jsonpickle.encode({...}))`, wrapped in `try/except` so a broadcast failure never breaks playback (see the PR-27 `CLIENTS_CAME_ONLINE` precedent in `mosaicmesh/websocket/legacy.py`).
- **Lazy `import server`.** Sub-modules (`render.py`) reach the singleton + helpers via `import server; server.X` *inside function bodies* (call-time lookup avoids import cycles).
- **Commit cadence:** one commit per task, message ending with the Co-Authored-By trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

## File structure

| File | Responsibility | Create/Modify |
|------|----------------|---------------|
| `mosaicmesh/state.py` | `Display.currentPlaylistName` field | Modify |
| `server.py` | `_playback_state` / `_playback_row` (pure mapping), `_broadcast_playback_state`, `api_playback` handler + route | Modify |
| `mosaicmesh/render.py` | set/clear `currentPlaylistName` + broadcast in `_apply_playlist` / `_start_group_playback` / `_stop_group_playback` / `_begin_prepare` | Modify |
| `mosaicmesh/websocket/legacy.py` | broadcast `PLAYBACK_CHANGED` on `PAUSE` | Modify |
| `js/timeline/api.js` | `getPlayback()` | Modify |
| `js/timeline/store.js` | `activeTab`, `connection`, `playback`, `goTo`, setters, hydrate playback, `nowCards` getter | Modify |
| `js/timeline/now-summary.js` | `buildNowSummary(...)` pure derivation | Create |
| `js/timeline/shell/router.js` | hash ⇄ `store.activeTab`; `parseHash` | Create |
| `js/timeline/timeline/sockjs-status.js` | handle `PLAYBACK_CHANGED` + connection up/down | Modify |
| `js/timeline/modals/modal-shell.js` | modal⇄sheet responsive class | Modify |
| `js/timeline/index.js` | start the router | Modify |
| `admin.html` | shell restructure (statusbar + nav + 4 sections), Now markup, placeholders, connection binding, token consolidation, dead-jQuery removal, nav+sheet CSS | Modify |
| `tests/unit/test_playback_surface.py` | `/api/playback` shape, state mapping, set/clear, broadcast payload | Create |
| `tests/unit/js/test_now_summary.js` | `buildNowSummary` | Create |
| `tests/unit/js/test_router.js` | `parseHash` / `goTo` | Create |
| `tests/unit/js/test_timeline_smoke.js` | add `now-summary.js`, `shell/router.js` to MODULES | Modify |
| `tests/e2e/test-shell-nav.spec.js` | nav, hash, responsive, modal→sheet, Now | Create |

---

## Phase A — Playback-state surface (server)

### Task 1: `Display.currentPlaylistName` field

**Files:**
- Modify: `mosaicmesh/state.py` (the `Display.__init__`)
- Test: `tests/unit/test_playback_surface.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_playback_surface.py`:

```python
"""Playback-state surface: Display.currentPlaylistName, /api/playback, mapping, broadcast."""
import json
import jsonpickle
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import server
from mosaicmesh.state import Display, PlayState


class TestCurrentPlaylistNameField:
    def test_fresh_display_has_none(self):
        d = Display()
        assert d.currentPlaylistName is None

    def test_survives_jsonpickle_roundtrip(self):
        d = Display()
        d.currentPlaylistName = "Lunch Menu"
        d2 = jsonpickle.decode(jsonpickle.encode(d))
        assert d2.currentPlaylistName == "Lunch Menu"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestCurrentPlaylistNameField -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: 'Display' object has no attribute 'currentPlaylistName'`.

- [ ] **Step 3: Add the field**

In `mosaicmesh/state.py`, in `Display.__init__`, immediately after the line `self.action = PlayState.NOACTION` (the playback-state block), add:

```python
        self.currentPlaylistName = None   # name of the playlist whose items are currently applied (None = idle)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestCurrentPlaylistNameField -c tests/pytest.ini -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/state.py tests/unit/test_playback_surface.py
git commit -m "feat(playback): add Display.currentPlaylistName field

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pure playback-state mapping (`_playback_state` / `_playback_row`)

**Files:**
- Modify: `server.py` (add two module-level helpers near the other `/api` handlers, e.g. just above `api_effects`)
- Test: `tests/unit/test_playback_surface.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playback_surface.py`:

```python
class TestPlaybackStateMapping:
    def _disp(self, action, playlist="P", media=True):
        d = Display()
        d.action = action
        d.currentPlaylistName = playlist
        d.mediaElements = [object()] if media else []
        d.playStartEpoch = 123
        d.renderStatus = "ready"
        return d

    def test_play_is_playing(self):
        assert server._playback_state(self._disp(PlayState.PLAY)) == "playing"

    def test_preparing_is_playing(self):
        assert server._playback_state(self._disp(PlayState.PREPARING)) == "playing"

    def test_pause_is_paused(self):
        assert server._playback_state(self._disp(PlayState.PAUSE)) == "paused"

    def test_stop_with_playlist_is_stopped(self):
        assert server._playback_state(self._disp(PlayState.STOP)) == "stopped"

    def test_noaction_without_playlist_is_idle(self):
        assert server._playback_state(self._disp(PlayState.NOACTION, playlist=None)) == "idle"

    def test_row_shape(self):
        row = server._playback_row("Lobby", self._disp(PlayState.PLAY))
        assert row == {
            "displayID": "Lobby", "state": "playing",
            "currentPlaylist": "P", "startedEpoch": 123, "renderStatus": "ready",
        }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestPlaybackStateMapping -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_playback_state'`.

- [ ] **Step 3: Implement the helpers**

In `server.py`, add two module-level functions (place them just above the `api_effects` handler). `PlayState` is already importable in `server.py` via the `from mosaicmesh.state import ...` block — confirm `PlayState` is in that import list and add it if missing.

```python
def _playback_state(display):
    """Map a Display's playback fields to a coarse state string for the admin
    Now landing. PLAY/PREPARING -> playing, PAUSE -> paused, STOP/NOACTION ->
    'stopped' if a playlist is applied else 'idle'."""
    action = getattr(display, "action", None)
    if action in (PlayState.PLAY, PlayState.PREPARING):
        return "playing"
    if action == PlayState.PAUSE:
        return "paused"
    has_playlist = bool(getattr(display, "currentPlaylistName", None)) and bool(getattr(display, "mediaElements", None))
    return "stopped" if has_playlist else "idle"


def _playback_row(display_id, display):
    """The per-group row exposed by /api/playback and the PLAYBACK_CHANGED broadcast."""
    return {
        "displayID": display_id,
        "state": _playback_state(display),
        "currentPlaylist": getattr(display, "currentPlaylistName", None),
        "startedEpoch": getattr(display, "playStartEpoch", 0),
        "renderStatus": getattr(display, "renderStatus", ""),
    }
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestPlaybackStateMapping -c tests/pytest.ini -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playback_surface.py
git commit -m "feat(playback): pure _playback_state/_playback_row mapping helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Set/clear `currentPlaylistName` in the playback paths

**Files:**
- Modify: `mosaicmesh/render.py` (`_apply_playlist`, `_stop_group_playback`)
- Test: `tests/unit/test_playback_surface.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playback_surface.py`:

```python
class TestSetClearPlaylistName:
    def _settings_with_group(self, display_id="Lobby"):
        s = server.Settings()
        s.displays[display_id] = Display()
        return s

    def test_apply_playlist_sets_name(self):
        from mosaicmesh.render import _apply_playlist
        from mosaicmesh.state import Playlist
        server.settings = self._settings_with_group()
        pl = Playlist()
        pl.name = "Lunch Menu"
        pl.items = []
        pl.loop = True
        _apply_playlist("Lobby", pl)
        assert server.settings.displays["Lobby"].currentPlaylistName == "Lunch Menu"

    def test_stop_clears_name(self):
        from mosaicmesh.render import _stop_group_playback
        server.settings = self._settings_with_group()
        server.settings.displays["Lobby"].currentPlaylistName = "Lunch Menu"
        _stop_group_playback("Lobby")
        assert server.settings.displays["Lobby"].currentPlaylistName is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestSetClearPlaylistName -c tests/pytest.ini -v`
Expected: FAIL — `_apply_playlist` doesn't set the name (assert None != "Lunch Menu").

- [ ] **Step 3: Implement set/clear**

In `mosaicmesh/render.py`, inside `_apply_playlist(display_id, pl)`, immediately after the line `display.mediaElements = _build_media_elements(pl.items)`, add:

```python
    display.currentPlaylistName = getattr(pl, "name", None)
```

In `mosaicmesh/render.py`, inside `_stop_group_playback(display_id)`, after it resolves `display` (the function looks up `server.settings.displays.get(display_id)`), add a guarded clear at the point it mutates playback state (alongside setting `action`/clearing playback). If the function early-returns when `display is None`, place this after that guard:

```python
    display.currentPlaylistName = None
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestSetClearPlaylistName -c tests/pytest.ini -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_playback_surface.py
git commit -m "feat(playback): track currentPlaylistName in apply/stop paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `GET /api/playback` endpoint + route

**Files:**
- Modify: `server.py` (`api_playback` handler + route registration)
- Test: `tests/unit/test_playback_surface.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playback_surface.py`:

```python
import pytest
from aiohttp.test_utils import make_mocked_request


class TestApiPlayback:
    @pytest.mark.asyncio
    async def test_returns_group_rows(self):
        s = server.Settings()
        d = Display()
        d.action = PlayState.PLAY
        d.currentPlaylistName = "Lunch Menu"
        d.playStartEpoch = 999
        d.mediaElements = [object()]
        s.displays["Lobby"] = d
        s.displays["Idle Group"] = Display()  # NOACTION + no playlist -> idle
        server.settings = s

        req = make_mocked_request("GET", "/api/playback")
        resp = await server.api_playback(req)
        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["success"] is True
        rows = {r["displayID"]: r for r in body["groups"]}
        assert rows["Lobby"]["state"] == "playing"
        assert rows["Lobby"]["currentPlaylist"] == "Lunch Menu"
        assert rows["Lobby"]["startedEpoch"] == 999
        assert rows["Idle Group"]["state"] == "idle"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestApiPlayback -c tests/pytest.ini -v`
Expected: FAIL — `module 'server' has no attribute 'api_playback'`.

- [ ] **Step 3: Implement the handler + register the route**

In `server.py`, add the handler next to `_playback_row`:

```python
async def api_playback(request):
    """Read-only per-group playback snapshot for the admin Now landing."""
    rows = [_playback_row(did, d) for did, d in settings.displays.items()]
    return web.json_response({"success": True, "groups": rows})
```

Then register the route where the other `/api/*` GET routes are added (search `server.py` for `add_get('/api/displays'` or `add_get('/api/effects'` and add alongside):

```python
    app.router.add_get('/api/playback', api_playback)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestApiPlayback -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playback_surface.py
git commit -m "feat(playback): GET /api/playback read-only snapshot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `PLAYBACK_CHANGED` broadcast on transitions

**Files:**
- Modify: `server.py` (`_broadcast_playback_state` helper)
- Modify: `mosaicmesh/render.py` (call it from `_start_group_playback`, `_stop_group_playback`, `_begin_prepare`)
- Modify: `mosaicmesh/websocket/legacy.py` (call it on `PAUSE`)
- Test: `tests/unit/test_playback_surface.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playback_surface.py`:

```python
class TestBroadcast:
    def test_broadcast_payload_shape(self):
        s = server.Settings()
        d = Display()
        d.action = PlayState.PLAY
        d.currentPlaylistName = "Lunch Menu"
        d.mediaElements = [object()]
        s.displays["Lobby"] = d
        server.settings = s

        sent = []
        fake_mgr = MagicMock()
        fake_mgr.broadcast = lambda payload: sent.append(json.loads(payload))
        with patch.object(server, "socketmanager", fake_mgr):
            server._broadcast_playback_state("Lobby")

        assert len(sent) == 1
        msg = sent[0]
        assert msg["REQUEST"] == "PLAYBACK_CHANGED"
        assert msg["PAYLOAD"]["groups"][0]["displayID"] == "Lobby"
        assert msg["PAYLOAD"]["groups"][0]["state"] == "playing"

    def test_broadcast_unknown_group_is_noop(self):
        server.settings = server.Settings()
        fake_mgr = MagicMock()
        with patch.object(server, "socketmanager", fake_mgr):
            server._broadcast_playback_state("Nope")
        fake_mgr.broadcast.assert_not_called()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestBroadcast -c tests/pytest.ini -v`
Expected: FAIL — `module 'server' has no attribute '_broadcast_playback_state'`.

- [ ] **Step 3: Implement the helper**

In `server.py`, next to `_playback_row`, add:

```python
def _broadcast_playback_state(display_id):
    """Broadcast one group's playback row to all admins. Best-effort: a broadcast
    failure must never break playback (matches the PR-27 CLIENTS_CAME_ONLINE pattern)."""
    try:
        display = settings.displays.get(display_id)
        if display is None:
            return
        socketmanager.broadcast(jsonpickle.encode({
            "REQUEST": "PLAYBACK_CHANGED",
            "PAYLOAD": {"groups": [_playback_row(display_id, display)]},
        }))
    except Exception:
        pass
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `python -m pytest tests/unit/test_playback_surface.py::TestBroadcast -c tests/pytest.ini -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Emit from transition points**

In `mosaicmesh/render.py`, add `import server` (lazy, inside each function if not already present) and call `server._broadcast_playback_state(display_id)` as the **last** statement of each of:
- `_start_group_playback(display_id, ...)` — after playback has started.
- `_stop_group_playback(display_id)` — after the `currentPlaylistName = None` clear from Task 3.
- `_begin_prepare(display_id)` — after it sets the group into the preparing/PREPARING flow.

In `mosaicmesh/websocket/legacy.py`, in the `PAUSE` request handler (the `elif(msg["REQUEST"] == "PAUSE")` block), after it sets `display.action = PlayState.PAUSE` and broadcasts the existing `PAUSE` message to the group, add:

```python
        server._broadcast_playback_state(display_id)
```

(`server` is already imported in `legacy.py`.)

- [ ] **Step 6: Verify nothing regressed**

Run: `python -m pytest tests/unit/test_playback_surface.py -c tests/pytest.ini -v`
Expected: all playback-surface tests PASS. (The transition-point wiring is exercised end-to-end by the e2e in Task 15; here we confirm no import/typo breakage.)

Also run the existing playback tests to confirm the new broadcast calls don't break them:
Run: `python -m pytest tests/unit/test_playback.py tests/unit/test_coordinated_start.py -c tests/pytest.ini -v`
Expected: no NEW failures vs. baseline. (Note: these suites have pre-existing Python-3.14 `asyncio.get_event_loop` failures unrelated to this change — compare against a clean checkout if unsure; your change must not ADD failures.)

- [ ] **Step 7: Commit**

```bash
git add server.py mosaicmesh/render.py mosaicmesh/websocket/legacy.py tests/unit/test_playback_surface.py
git commit -m "feat(playback): PLAYBACK_CHANGED broadcast on play/stop/pause/prepare

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — Client data layer

### Task 6: `api.getPlayback()`

**Files:**
- Modify: `js/timeline/api.js`
- Test: `tests/unit/js/test_api_playback.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_api_playback.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';

test('getPlayback returns the groups array', async () => {
  global.fetch = async (url) => {
    assert.equal(url, '/api/playback');
    return { ok: true, json: async () => ({ success: true, groups: [{ displayID: 'Lobby', state: 'playing' }] }) };
  };
  const { api } = await import('../../../js/timeline/api.js?cache=' + Date.now());
  const groups = await api.getPlayback();
  assert.deepEqual(groups, [{ displayID: 'Lobby', state: 'playing' }]);
});

test('getPlayback returns [] when body has no groups', async () => {
  global.fetch = async () => ({ ok: true, json: async () => ({ success: true }) });
  const { api } = await import('../../../js/timeline/api.js?cache=' + (Date.now() + 1));
  assert.deepEqual(await api.getPlayback(), []);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_api_playback.js`
Expected: FAIL — `api.getPlayback is not a function`.

- [ ] **Step 3: Implement**

In `js/timeline/api.js`, add a method to the exported `api` object, following the existing `list*` style (match the file's exact GET/error idiom; the read-only shape below is the intent):

```js
  async getPlayback() {
    const r = await fetch('/api/playback');
    if (!r.ok) throw new ApiError({ status: r.status, body: await r.json().catch(() => ({})) });
    const body = await r.json();
    return body.groups || [];
  },
```

(If `api.js` uses a shared `getJSON` helper for its list endpoints, use that helper instead and return `(body.groups || [])`.)

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_api_playback.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/api.js tests/unit/js/test_api_playback.js
git commit -m "feat(admin): api.getPlayback() for the playback surface

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `buildNowSummary` pure derivation

**Files:**
- Create: `js/timeline/now-summary.js`
- Test: `tests/unit/js/test_now_summary.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_now_summary.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { buildNowSummary } from '../../../js/timeline/now-summary.js';

const groups = [{ displayID: 'Lobby', clientCount: 4, onlineCount: 2 }];

test('playing group reflects playback row', () => {
  const cards = buildNowSummary({
    displayGroups: groups,
    playback: { Lobby: { state: 'playing', currentPlaylist: 'Lunch Menu', renderStatus: '' } },
  });
  assert.equal(cards.length, 1);
  assert.deepEqual(cards[0], {
    displayID: 'Lobby', screenCount: 4, onlineCount: 2,
    state: 'playing', currentPlaylist: 'Lunch Menu', renderStatus: '',
  });
});

test('group with no playback entry is idle', () => {
  const cards = buildNowSummary({ displayGroups: groups, playback: {} });
  assert.equal(cards[0].state, 'idle');
  assert.equal(cards[0].currentPlaylist, null);
});

test('renderInProgress fallback sets renderStatus', () => {
  const cards = buildNowSummary({ displayGroups: groups, playback: {}, renderInProgress: { Lobby: true } });
  assert.equal(cards[0].renderStatus, 'rendering');
});

test('counts fall back to displays when group lacks them', () => {
  const cards = buildNowSummary({
    displayGroups: [{ displayID: 'Lobby' }],
    displays: [
      { displayID: 'Lobby', isOnline: true },
      { displayID: 'Lobby', isOnline: false },
      { displayID: 'Other', isOnline: true },
    ],
    playback: {},
  });
  assert.equal(cards[0].screenCount, 2);
  assert.equal(cards[0].onlineCount, 1);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_now_summary.js`
Expected: FAIL — cannot find module `now-summary.js`.

- [ ] **Step 3: Implement**

Create `js/timeline/now-summary.js`:

```js
/**
 * Pure derivation of the Now-landing cards from store slices. One card per
 * display group: screen/online counts (from the group summary, falling back
 * to counting `displays`), the playback state + current playlist (from the
 * /api/playback surface), and a render indicator.
 *
 * Kept pure + DOM-free so it's unit-testable; the Now markup is a declarative
 * x-for over store.nowCards (which calls this).
 */
export function buildNowSummary({ displayGroups = [], displays = [], playback = {}, renderInProgress = {} } = {}) {
  return displayGroups.map((g) => {
    const pb = playback[g.displayID] || {};
    let screenCount = g.clientCount;
    let onlineCount = g.onlineCount;
    if (screenCount == null || onlineCount == null) {
      let s = 0, o = 0;
      for (const c of displays) {
        if (c.displayID !== g.displayID) continue;
        s += 1;
        if (c.isOnline) o += 1;
      }
      if (screenCount == null) screenCount = s;
      if (onlineCount == null) onlineCount = o;
    }
    const renderStatus = pb.renderStatus || (renderInProgress[g.displayID] ? 'rendering' : '');
    return {
      displayID: g.displayID,
      screenCount,
      onlineCount,
      state: pb.state || 'idle',
      currentPlaylist: pb.currentPlaylist || null,
      renderStatus,
    };
  });
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_now_summary.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/now-summary.js tests/unit/js/test_now_summary.js
git commit -m "feat(admin): buildNowSummary pure derivation for Now cards

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Store additions (`activeTab`, `connection`, `playback`, `goTo`, `nowCards`)

**Files:**
- Modify: `js/timeline/store.js`
- Test: `tests/unit/js/test_store_now.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_store_now.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('store defaults: activeTab now, empty connection/playback', () => {
  const s = makeStore();
  assert.equal(s.activeTab, 'now');
  assert.deepEqual(s.playback, {});
  assert.equal(s.connection.connected, false);
});

test('setActiveTab + setConnection + setPlayback mutate state', () => {
  const s = makeStore();
  s.setActiveTab('fleet');
  assert.equal(s.activeTab, 'fleet');
  s.setConnection({ connected: true, onlineClients: 5 });
  assert.equal(s.connection.connected, true);
  assert.equal(s.connection.onlineClients, 5);
  s.setPlayback({ displayID: 'Lobby', state: 'playing', currentPlaylist: 'P' });
  assert.equal(s.playback.Lobby.state, 'playing');
});

test('nowCards getter derives cards from slices', () => {
  const s = makeStore();
  s.displayGroups = [{ displayID: 'Lobby', clientCount: 3, onlineCount: 1 }];
  s.setPlayback({ displayID: 'Lobby', state: 'playing', currentPlaylist: 'P', renderStatus: '' });
  const cards = s.nowCards;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].state, 'playing');
  assert.equal(cards[0].screenCount, 3);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_store_now.js`
Expected: FAIL — `activeTab` undefined / `setPlayback` not a function.

- [ ] **Step 3: Implement**

In `js/timeline/store.js`, add to the object returned by `makeStore()`:

- After the existing UI-state fields (near `viewMode`), add:
```js
    activeTab: 'now',                 // 'now' | 'content' | 'schedule' | 'fleet'
    connection: { connected: false, onlineClients: 0 },
    playback: {},                     // displayID -> {state, currentPlaylist, startedEpoch, renderStatus}
```

- Add an import at the top of `store.js` (next to the existing imports):
```js
import { buildNowSummary } from './now-summary.js';
```

- Add the methods + getter (next to the other mutators like `setStatus`):
```js
    setActiveTab(tab) { this.activeTab = tab; },
    goTo(tab) { if (typeof location !== 'undefined') location.hash = '#' + tab; },
    setConnection(patch) { this.connection = { ...this.connection, ...patch }; },
    setPlayback(row) { if (row && row.displayID) this.playback[row.displayID] = row; },
    get nowCards() {
      return buildNowSummary({
        displayGroups: this.displayGroups,
        displays: this.displays,
        playback: this.playback,
        renderInProgress: this.renderInProgress,
      });
    },
```

- In `hydrate()`, add `api.getPlayback()` to the `Promise.all([...])` and destructure it, then seed `playback`. Concretely, change the array + destructuring to include a `pb` result and add:
```js
      this.playback = Object.fromEntries((pb || []).map((r) => [r.displayID, r]));
```
(Place `api.getPlayback()` last in the `Promise.all` and add `pb` as the matching last destructured name, so the existing order is undisturbed.)

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_store_now.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full JS suite to confirm no regression**

Run: `python pytest_runner.py --js`
Expected: all pass (store changes don't break existing store tests).

- [ ] **Step 6: Commit**

```bash
git add js/timeline/store.js tests/unit/js/test_store_now.js
git commit -m "feat(admin): store activeTab/connection/playback + nowCards getter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Hash router (`shell/router.js`)

**Files:**
- Create: `js/timeline/shell/router.js`
- Test: `tests/unit/js/test_router.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_router.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { parseHash } from '../../../js/timeline/shell/router.js';

test('parseHash maps valid hashes', () => {
  assert.equal(parseHash('#schedule'), 'schedule');
  assert.equal(parseHash('content'), 'content');
  assert.equal(parseHash('#fleet'), 'fleet');
  assert.equal(parseHash('#now'), 'now');
});

test('parseHash falls back to now for unknown/empty', () => {
  assert.equal(parseHash(''), 'now');
  assert.equal(parseHash('#bogus'), 'now');
  assert.equal(parseHash(undefined), 'now');
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_router.js`
Expected: FAIL — cannot find module `shell/router.js`.

- [ ] **Step 3: Implement**

Create `js/timeline/shell/router.js`:

```js
/**
 * URL-hash ⇄ store.activeTab. The hash is the single source of route truth
 * (bookmarkable, back-button friendly); the store mirrors it. store.goTo(tab)
 * sets the hash, this listener reflects it back into activeTab.
 */
export const ROUTES = ['now', 'content', 'schedule', 'fleet'];

export function parseHash(hash) {
  const t = String(hash || '').replace(/^#/, '');
  return ROUTES.includes(t) ? t : 'now';
}

export function startRouter(store) {
  const sync = () => store.setActiveTab(parseHash(location.hash));
  window.addEventListener('hashchange', sync);
  sync();   // initial route on load
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_router.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Add both new modules to the load smoke**

In `tests/unit/js/test_timeline_smoke.js`, add to the `MODULES` array:
```js
  'js/timeline/now-summary.js',
  'js/timeline/shell/router.js',
```
Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS (new modules load).

- [ ] **Step 6: Commit**

```bash
git add js/timeline/shell/router.js tests/unit/js/test_router.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(admin): hash router for the shell (router.js)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: SockJS — `PLAYBACK_CHANGED` + connection

**Files:**
- Modify: `js/timeline/timeline/sockjs-status.js`

This module's behavior depends on `window.sock` and is covered end-to-end by the e2e in Task 15; there is no isolated unit test (it mirrors the existing `DISCOVERY_HEARTBEAT`/`CLIENTS_*` handling which is also e2e-covered).

- [ ] **Step 1: Add the `PLAYBACK_CHANGED` branch**

In `sockjs-status.js`, inside the `handle(msg)` switch, add a branch alongside the existing `CLIENTS_CAME_ONLINE`/`RENDER_IN_PROGRESS` branches:

```js
    } else if (req === 'PLAYBACK_CHANGED') {
      // payload: {groups: [{displayID, state, currentPlaylist, startedEpoch, renderStatus}, ...]}
      const rows = payload?.groups ?? [];
      if (rows.length > 0) applyMutation(() => rows.forEach((r) => store.setPlayback(r)));
```

- [ ] **Step 2: Set connection state on socket open + status frames**

Where the subscriber hooks `window.sock.onmessage` (the `if (window.sock ...)` block near the bottom), also reflect connection state into the store. Add, right after the existing `onmessage` wrapper is installed:

```js
  // Connection indicator (replaces the legacy jQuery #connDot/#connText poking).
  if (window.sock) {
    if (window.sock.readyState === 1 /* OPEN */) store.setConnection({ connected: true });
    const prevOpen = window.sock.onopen;
    window.sock.onopen = function (e) { store.setConnection({ connected: true }); if (prevOpen) prevOpen.call(this, e); };
    const prevClose = window.sock.onclose;
    window.sock.onclose = function (e) { store.setConnection({ connected: false }); if (prevClose) prevClose.call(this, e); };
  }
```

And in the `DISCOVERY_HEARTBEAT` branch (which carries an aggregate online count in `payload.onlineClients` per the legacy `mosiacMeshCallback`), set the count:

```js
      if (payload && typeof payload.onlineClients === 'number') {
        applyMutation(() => store.setConnection({ onlineClients: payload.onlineClients }));
      }
```

- [ ] **Step 3: Verify the module still loads**

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS (sockjs-status.js still imports cleanly).

- [ ] **Step 4: Commit**

```bash
git add js/timeline/timeline/sockjs-status.js
git commit -m "feat(admin): route PLAYBACK_CHANGED + connection into the store

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Shell + Now UI

### Task 11: Modal⇄sheet responsive behavior

**Files:**
- Modify: `js/timeline/modals/modal-shell.js`
- Modify: `admin.html` (CSS only)

Verified by the e2e in Task 15; no isolated unit (DOM/viewport behavior).

- [ ] **Step 1: Tag the overlay for responsive styling**

In `modal-shell.js`, where the overlay element is created (`overlay.className = 'mm-modal-overlay'`), the class is sufficient — the swap is pure CSS. No JS change is required *if* the existing markup uses `.mm-modal-overlay` + `.mm-modal`. Confirm those class names; if the dialog lacks a stable class, add `dialog.classList.add('mm-modal')` so the CSS below can target it. (No behavioral JS change — the API stays identical.)

- [ ] **Step 2: Add the sheet CSS**

In `admin.html`'s `<style>`, near the existing `.mm-modal` rules, add a mobile breakpoint:

```css
@media (max-width: 760px) {
  .mm-modal-overlay { align-items: flex-end; }      /* dock to bottom */
  .mm-modal {
    width: 100%;
    max-width: 100%;
    max-height: 92vh;
    border-radius: 14px 14px 0 0;                    /* sheet: rounded top only */
    animation: mm-sheet-up 180ms ease-out;
  }
  .mm-modal-body { overflow-y: auto; }
}
@keyframes mm-sheet-up { from { transform: translateY(100%); } to { transform: translateY(0); } }
```

- [ ] **Step 3: Manual verification (dev server running)**

Open `http://localhost:3000/admin.html`, open any modal (e.g. right-click a clip → Edit), and resize the window below 760px: the modal should dock to the bottom as a full-width sheet; above 760px it stays a centered box. (Automated coverage lands in Task 15.)

- [ ] **Step 4: Commit**

```bash
git add js/timeline/modals/modal-shell.js admin.html
git commit -m "feat(admin): modals become bottom sheets on mobile (CSS swap)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: `admin.html` shell restructure (nav + 4 sections + Now + connection binding)

**Files:**
- Modify: `admin.html`
- Modify: `js/timeline/index.js`

Verified by Task 15 (and the existing JS suite must stay green).

- [ ] **Step 1: Replace the sidebar nav with the four destinations**

In `admin.html`, replace the `<nav class="sidebar">` contents (currently `Timeline` / `Console` buttons) with four destinations bound to the store. Use `x-data="{}"` is not needed — bind directly to `$store.mm`:

```html
<nav class="sidebar" id="sidebar" role="navigation" aria-label="Sections">
  <button class="navitem" :class="{ on: $store.mm.activeTab==='now' }"
          :aria-current="$store.mm.activeTab==='now' ? 'page' : false"
          @click="$store.mm.goTo('now')"><span class="ni-ic">⚡</span><span class="ni-lbl">Now</span></button>
  <button class="navitem" :class="{ on: $store.mm.activeTab==='content' }"
          :aria-current="$store.mm.activeTab==='content' ? 'page' : false"
          @click="$store.mm.goTo('content')"><span class="ni-ic">▦</span><span class="ni-lbl">Content</span></button>
  <button class="navitem" :class="{ on: $store.mm.activeTab==='schedule' }"
          :aria-current="$store.mm.activeTab==='schedule' ? 'page' : false"
          @click="$store.mm.goTo('schedule')"><span class="ni-ic">📅</span><span class="ni-lbl">Schedule</span></button>
  <button class="navitem" :class="{ on: $store.mm.activeTab==='fleet' }"
          :aria-current="$store.mm.activeTab==='fleet' ? 'page' : false"
          @click="$store.mm.goTo('fleet')"><span class="ni-ic">📡</span><span class="ni-lbl">Fleet</span></button>
</nav>
```

- [ ] **Step 2: Replace the sections (Now real + three placeholders)**

In `admin.html`'s `<main>`, replace the existing `.section` blocks. Drive visibility with `x-show` off `$store.mm.activeTab` (replacing the jQuery `.active` toggle):

```html
<main class="main">
  <section class="section" data-route="now" x-show="$store.mm.activeTab==='now'">
    <div class="now-glance" x-text="`${$store.mm.connection.onlineClients} online · ${$store.mm.nowCards.filter(c=>c.state==='playing').length} playing`"></div>
    <div class="now-cards">
      <template x-for="card in $store.mm.nowCards" :key="card.displayID">
        <div class="now-card" :class="{ playing: card.state==='playing' }">
          <div class="nc-head">
            <span class="nc-name" x-text="card.displayID"></span>
            <span class="nc-pill" :class="card.state" x-text="card.state==='playing' ? '▶ playing' : card.state"></span>
          </div>
          <div class="nc-meta">
            <span x-text="card.currentPlaylist || 'Idle'"></span>
            <span x-text="`${card.onlineCount}/${card.screenCount} online`"></span>
            <span class="nc-render" x-show="card.renderStatus==='rendering'">· rendering…</span>
          </div>
        </div>
      </template>
      <p class="now-empty" x-show="$store.mm.nowCards.length===0">No display groups yet.</p>
    </div>
  </section>

  <section class="section" data-route="content" x-show="$store.mm.activeTab==='content'">
    <div class="placeholder-tab">Content — coming soon.</div>
  </section>
  <section class="section" data-route="schedule" x-show="$store.mm.activeTab==='schedule'">
    <div class="placeholder-tab">Schedule — coming soon.</div>
  </section>
  <section class="section" data-route="fleet" x-show="$store.mm.activeTab==='fleet'">
    <div class="placeholder-tab">Fleet — coming soon.</div>
  </section>
</main>
```

- [ ] **Step 3: Bind the connection indicator in the statusbar**

In the statusbar, replace the jQuery-driven `#connDot`/`#connText` with Alpine bindings:

```html
<span class="dot" id="connDot" :class="{ on: $store.mm.connection.connected }"></span>
<span id="connText" class="size" x-text="$store.mm.connection.connected ? ('connected · ' + $store.mm.connection.onlineClients + ' online') : 'connecting…'"></span>
```

- [ ] **Step 4: Add nav + Now + placeholder CSS (responsive)**

In `admin.html`'s `<style>`, add:

```css
/* Sidebar (desktop) */
.navitem .ni-ic { font-size: 16px; margin-right: 8px; }
.navitem.on { color: var(--text); background: var(--surface-2); border-left: 2px solid var(--accent); }
.now-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.now-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: 9px; padding: 12px; }
.now-card.playing { border-color: var(--accent); }
.nc-head { display: flex; justify-content: space-between; align-items: center; }
.nc-name { font-weight: 600; }
.nc-pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: var(--surface); color: var(--text-muted); }
.nc-pill.playing { background: rgba(74,144,217,.18); color: var(--accent); }
.nc-meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--text-muted); margin-top: 6px; }
.now-glance { font-size: 13px; color: var(--text-muted); margin-bottom: 12px; }
.placeholder-tab { color: var(--text-muted); padding: 40px; text-align: center; }

/* Mobile: sidebar -> bottom tab-bar */
@media (max-width: 760px) {
  .shell { display: block; }
  .sidebar {
    position: fixed; left: 0; right: 0; bottom: 0; top: auto;
    display: flex; flex-direction: row; border-top: 1px solid var(--border);
    background: var(--surface); z-index: 1200;
  }
  .sidebar .navitem { flex: 1; flex-direction: column; gap: 2px; font-size: 10px; text-align: center; border-left: none; }
  .sidebar .navitem.on { border-left: none; border-top: 2px solid var(--accent); background: transparent; }
  .navitem .ni-ic { margin-right: 0; display: block; font-size: 18px; }
  .main { padding-bottom: 64px; }   /* clear the tab-bar */
}
```

- [ ] **Step 5: Start the router in `index.js`**

In `js/timeline/index.js`, inside `bootstrap()` (after the store is created + hydrated), add:

```js
import { startRouter } from './shell/router.js';
// ... inside bootstrap(), after `const store = Alpine.store('mm'); store.hydrate();`:
startRouter(store);
```

(Place the `import` with the other top-of-file imports.)

- [ ] **Step 6: Verify**

Run: `python pytest_runner.py --js` → all pass (smoke includes the new modules).
Then open `http://localhost:3000/admin.html`: the four tabs switch the visible section, the hash updates (`#now` etc.), deep-linking `#schedule` lands there, the connection dot/text reflect the socket, and the Now tab shows group cards. (Automated in Task 15.)

- [ ] **Step 7: Commit**

```bash
git add admin.html js/timeline/index.js
git commit -m "feat(admin): four-destination shell + live Now landing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Consolidate design tokens

**Files:**
- Modify: `admin.html` (CSS only)

- [ ] **Step 1: Gather the token block**

In `admin.html`'s `<style>`, ensure ALL design tokens live in a single, commented `:root` block at the top of the stylesheet (colors, surfaces, text, the `--s1..--s5` spacing scale, `--radius`, `--shadow`, `--font`, and the `--grid-*` values), with the `[data-theme="light"]` overrides and the `@media (prefers-color-scheme: light)` fallback grouped immediately after. Move any stray `--var` definitions that are currently scattered into this block. Do NOT rename tokens (later sections depend on the names) — this is a relocation + commenting pass only.

- [ ] **Step 2: Verify nothing visually broke**

Open `http://localhost:3000/admin.html`, toggle the theme button (dark⇄light), and confirm colors still apply on both themes and the timeline grid lines render. (No automated assertion — it's a pure CSS relocation; the e2e in Task 15 still passing confirms no structural breakage.)

- [ ] **Step 3: Commit**

```bash
git add admin.html
git commit -m "refactor(admin): consolidate design tokens into one :root block

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Remove dead legacy jQuery

**Files:**
- Modify: `admin.html`

- [ ] **Step 1: Delete the dead code**

In `admin.html`, remove:
- The `toast()` function definition (Alpine `store.toast` replaces it) and any remaining callers (the device-join/leave toasts now flow through `sockjs-status.js` → `store.toast` if desired; if a caller remains, route it to `Alpine.store('mm').toast(...)` instead of deleting the notification).
- The `ProgrammableTimer` setup block (the admin page doesn't tick).
- The Console `.section` (already removed in Task 12's section replacement — confirm no orphan markup) and any debug `<form>` submit handler that sent raw SockJS messages.
- The body of `mosiacMeshCallback` that pokes `#connDot`/`#connText` (now Alpine-bound) — keep only any branch still needed; if the function body becomes empty, delete the function and the line that registers it as the SockJS message callback (the `sockjs-status.js` subscriber is the live message path now).

**Keep:** `window.sock`, `generateMessage()`, the `<script src>` tags for jQuery/SockJS/GoTime/`mosiacmesh.js` (these still provide the socket + message packing used by `play-now.js`, `fleet-confirm.js`, `calibration.js`, `track-header-context-menu.js`).

- [ ] **Step 2: Grep to confirm nothing live was removed**

Run:
```bash
grep -rn "window.sock\|generateMessage" js/timeline/ | head
```
Expected: the four consumer modules still reference `window.sock`/`generateMessage` — confirm those globals are still defined (via `mosiacmesh.js` + the kept `<script>` tags). Then load the page and confirm a fleet action (e.g. track-header → Reload group) still sends over the socket.

- [ ] **Step 3: Verify suites + page**

Run: `python pytest_runner.py --js` → pass.
Open `http://localhost:3000/admin.html` → no console errors; connection indicator works; a Play-now still functions.

- [ ] **Step 4: Commit**

```bash
git add admin.html
git commit -m "refactor(admin): remove dead legacy jQuery (keep sock/generateMessage)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — End-to-end verification

### Task 15: Playwright e2e — shell + Now

**Files:**
- Create: `tests/e2e/test-shell-nav.spec.js`

- [ ] **Step 1: Read the harness conventions**

Inspect `tests/e2e/run.js` and an existing spec (`tests/e2e/test-shell-nav.spec.js` doesn't exist yet — read `tests/e2e/test-fleet-scope.spec.js` and `tests/e2e/helpers.js`) to match the export signature, `baseURL`, and `chromium.launch()` usage. Match whatever shape the existing specs use.

- [ ] **Step 2: Write the spec**

Create `tests/e2e/test-shell-nav.spec.js` matching the existing export shape. It must:

1. Navigate to `admin.html` on `baseURL`; wait for Alpine to hydrate (the nav buttons exist).
2. **Tab switching + hash:** click each of Now/Content/Schedule/Fleet; assert (a) the matching `.section[data-route=...]` is visible and the others hidden, (b) `location.hash` equals `#<tab>`, (c) the clicked `.navitem` has `aria-current="page"`.
3. **Deep-link:** `page.goto(baseURL + '/admin.html#schedule')`; assert the Schedule section is the visible one on load.
4. **Responsive nav:** set viewport to 800×900 (desktop) → assert the sidebar is laid out vertically (e.g. `.sidebar` width is the narrow rail / `flex-direction: column`); set viewport to 380×800 (mobile) → assert `.sidebar` is the bottom bar (`position: fixed`, near `bottom: 0`). Use `getComputedStyle` via `page.evaluate`.
5. **Modal→sheet:** open a modal (navigate to a context that opens one, or call the global that opens Play-now); at 380px width assert the `.mm-modal` is full-width docked to the bottom; at 900px assert it's centered. (If opening a modal from a placeholder-only shell is awkward, drive `openModal` directly via a tiny `page.evaluate` that imports `modal-shell.js` — or assert the CSS media query effect on a synthetically inserted `.mm-modal-overlay`.)
6. **Now cards + live update:** seed a playback state — POST a `__e2e_`-prefixed playlist + assign/play via the SockJS path *or* inject a `PLAYBACK_CHANGED` frame through the page's `window.sock.onmessage` — then assert a `.now-card` for that group shows `▶ playing` and the playlist name. Clean up any `__e2e_` fixtures.

Keep it light but cover those six assertions.

- [ ] **Step 3: Run it**

Run: `node tests/e2e/run.js shell-nav`
Expected: PASS. (If the e2e environment isn't provisioned — no `node_modules`/chromium — write the spec cleanly, commit it, and report that it needs a Playwright-capable environment to execute; the manual checks in Tasks 11–14 cover the gap in the interim.)

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test-shell-nav.spec.js
git commit -m "test(e2e): shell nav, hash sync, responsive nav, modal→sheet, Now

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Final review + PR

- [ ] **Step 1: Run the full suites**

Run:
```bash
python pytest_runner.py --js
python -m pytest tests/unit/test_playback_surface.py -c tests/pytest.ini -v
node tests/e2e/run.js shell-nav
```
Expected: JS suite green; playback-surface tests green; e2e green (or noted as needing the e2e env). Pre-existing Python `--unit` asyncio failures are unrelated — confirm you ADDED none.

- [ ] **Step 2: Code review**

Use superpowers:requesting-code-review over the section's commits. Focus: the playback-surface read-only-ness (no write paths touched), the `_apply_playlist` single-choke-point coverage of both ad-hoc + scheduled, that `window.sock`/`generateMessage` are intact, and that the four consumer modals still work.

- [ ] **Step 3: Manual smoke**

On `http://localhost:3000/admin.html`: switch tabs (hash + back-button), confirm Now reflects a Play-now you trigger (live `PLAYBACK_CHANGED`), confirm a modal is a bottom sheet at phone width, confirm dark/light toggle.

- [ ] **Step 4: Finish the branch**

Use superpowers:finishing-a-development-branch. PR summary notes: the responsive shell + Now landing, the read-only playback surface (one new `Display` field + `/api/playback` + `PLAYBACK_CHANGED`), placeholder Content/Schedule/Fleet, dead-jQuery removal, and that Sections 2–4 fill in the placeholder tabs.

---

## Self-Review

**1. Spec coverage** (against `2026-06-09-admin-overhaul-section1-shell-now-design.md`):

| Spec requirement | Task(s) |
|---|---|
| Responsive 4-destination nav (tab-bar ⇄ sidebar) | 12 (markup+CSS) |
| Hash routing into store.activeTab | 9 (router), 12 (wiring) |
| Modal⇄sheet system | 11 |
| Consolidated design tokens | 13 |
| Statusbar + Alpine connection binding | 10 (set), 12 (bind) |
| Now landing (cards + glance + play-now) | 7 (derivation), 8 (store getter), 12 (markup) |
| Playback surface: Display.currentPlaylistName | 1 |
| set in _apply_playlist / clear in _stop_group_playback | 3 |
| state mapping | 2 |
| GET /api/playback | 4 |
| PLAYBACK_CHANGED broadcast | 5 |
| client api.getPlayback + store.playback hydrate | 6, 8 |
| sockjs PLAYBACK_CHANGED handling | 10 |
| Remove dead jQuery, keep sock/generateMessage | 14 |
| Placeholder Content/Schedule/Fleet | 12 |
| Tests: pytest / node / playwright | 1–10 (units), 15 (e2e) |

No gaps.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Tasks 11/12/14 reference existing code by class/function name + describe the exact edit with complete new code; where an existing function body isn't quoted verbatim (e.g. `_stop_group_playback`), the step states the exact line to add and its anchor — acceptable for in-place edits. Task 15 gives two concrete fallbacks for the modal-open probe rather than a vague instruction.

**3. Type/name consistency:**
- `buildNowSummary` signature + card shape `{displayID, screenCount, onlineCount, state, currentPlaylist, renderStatus}` consistent across Tasks 7, 8, 12.
- `_playback_row` shape `{displayID, state, currentPlaylist, startedEpoch, renderStatus}` consistent across Tasks 2, 4, 5; the client card uses `screenCount/onlineCount` (from `buildNowSummary`), distinct from the server row by design.
- `store.setPlayback(row)` keyed by `row.displayID` — consistent in Tasks 8, 10.
- `PLAYBACK_CHANGED` payload `{groups: [...]}` consistent server (5) ↔ client (10).
- `parseHash`/`startRouter`/`ROUTES` consistent in Task 9 and consumed in 12.
- `store.goTo`/`store.activeTab`/`store.setActiveTab` consistent across 8, 9, 12.

No inconsistencies found.
