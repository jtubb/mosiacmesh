# Playlist Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A three-pane playlist editor in `admin.html` for authoring named, reusable playlists, backed by a server-side playlist store + CRUD/assign requests, a media-listing API with upload, and per-item `backgroundColor`/effect fields.

**Architecture:** Add a `settings.playlists` dict (`Playlist` objects) persisted via the existing jsonpickle path. New `msg_response` requests (`LIST_PLAYLISTS`/`GET_PLAYLIST`/`SAVE_PLAYLIST`/`DELETE_PLAYLIST`/`ASSIGN_PLAYLIST`) manage them; `ASSIGN_PLAYLIST` reuses `SETPLAYLIST` semantics and reports render readiness. A `GET /api/media` REST handler lists the shared library; `/upload/{dest}` gains a `video` branch. The editor UI is modern JS in `admin.html` (desktop console — exempt from the ES5 constraint); the only display-client change is `index.html` applying `backgroundColor` as the container background (ES5). `INDIVIDUAL` playmode and start/end effects are modeled now but their behavior ships in later slices.

**Tech Stack:** Python 3 / aiohttp / sockjs / jsonpickle (server); jQuery 1.x + SockJS (client console); pytest (`tests/pytest.ini`, `asyncio_mode=auto`).

---

## Conventions for every task

- **Run tests** with the project runner config: `python -m pytest <path> -c tests/pytest.ini -v` (a bare `pytest` from the repo root will NOT pick up markers/asyncio config). The full unit suite is `python pytest_runner.py --unit`.
- **Branch:** stay on `feature/discovery-completion-legacy-compat` (do NOT switch to `main`).
- All new server tests go in **`tests/unit/test_playlists.py`** unless stated otherwise. It uses the same patterns as `tests/unit/test_playback.py`: import `server`, the `mock_settings` fixture (from `tests/conftest.py`), `_make_session()` for the session arg, and `server.socketmanager = MagicMock()` to observe broadcasts.
- The websocket dispatch is the `if/elif` chain in `msg_response(msg, session)` (server.py ~line 601+). Responses are `{"DEST", "REQUEST": msg["REQUEST"], "PAYLOAD": ...}` — the response echoes the request name, which is how the client routes replies in `mosiacMeshCallback`.
- **Reference for `_make_session` helper** (copy into the new test file):

```python
from unittest.mock import MagicMock

def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s
```

---

## Task 1: Data model — `INDIVIDUAL` mode, per-item fields, `Playlist`, `settings.playlists`

**Files:**
- Modify: `server.py` — `PlayMode` enum (~532), `MediaElement.__init__` (~525), add `Playlist` class, `Settings.__init__` (~493), `migrate_client_objects` (~1372)
- Test: `tests/unit/test_playlists.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_playlists.py`:

```python
"""Unit tests for the named-playlist store, CRUD, assign, and media API."""
import sys, json
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import server
import jsonpickle


def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


class TestDataModel:
    def test_playmode_has_individual(self):
        assert server.PlayMode.INDIVIDUAL.name == "INDIVIDUAL"

    def test_media_element_defaults(self):
        me = server.MediaElement()
        assert me.backgroundColor == "#000000"
        assert me.startEffect is None
        assert me.endEffect is None

    def test_playlist_round_trips_jsonpickle(self):
        pl = server.Playlist()
        pl.name = "Lobby"
        pl.items = [{"id": "a", "file": "/media/server/images/x.jpg",
                     "duration": 5, "playmode": "FULL",
                     "backgroundColor": "#222222", "startEffect": None, "endEffect": None}]
        pl.loop = True
        decoded = jsonpickle.decode(jsonpickle.encode(pl))
        assert decoded.name == "Lobby"
        assert decoded.loop is True
        assert decoded.items[0]["backgroundColor"] == "#222222"

    def test_settings_has_playlists(self):
        assert isinstance(server.Settings().playlists, dict)

    def test_migrate_backfills_playlists(self, mock_settings):
        del mock_settings.playlists          # simulate an older settings.dat
        server.settings = mock_settings
        server.migrate_client_objects()
        assert mock_settings.playlists == {}
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/unit/test_playlists.py -c tests/pytest.ini -v`
Expected: FAIL (`AttributeError: INDIVIDUAL` / no `Playlist` / no `playlists`).

- [ ] **Step 3: Implement**

In `server.py`, extend `PlayMode` (append — keep existing values stable for any persisted data):

```python
class PlayMode(Enum):
    DEFAULT = 0
    FULL = 1
    SEGMENT = 2
    SCRIPT = 3
    INDIVIDUAL = 4
```

Extend `MediaElement.__init__`:

```python
class MediaElement():
    def __init__(self):
        self.id = None
        self.file = None
        self.duration = None
        self.playmode = PlayMode.DEFAULT
        self.backgroundColor = "#000000"
        self.startEffect = None
        self.endEffect = None
```

Add a `Playlist` class next to `MediaElement`:

```python
class Playlist():
    def __init__(self):
        self.name = ""
        self.items = []      # list of item dicts: id, file, duration, playmode, backgroundColor, startEffect, endEffect
        self.loop = False
```

Add `self.playlists = {}` to `Settings.__init__`:

```python
class Settings():
    def __init__(self):
        self.displays = {}
        self.scripts = {}
        self.clients = {}
        self.playlists = {}
```

Add a backfill guard at the top of `migrate_client_objects` (older `settings.dat` predates the field):

```python
def migrate_client_objects():
    """Migrate old client objects to include new discovery fields"""
    if not hasattr(settings, 'playlists'):
        settings.playlists = {}
    current_time = time.time()
    ...  # existing body unchanged
```

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/unit/test_playlists.py -c tests/pytest.ini -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playlists.py
git commit -m "feat(playlist): data model — INDIVIDUAL mode, item fields, Playlist store"
```

---

## Task 2: `SETPLAYLIST` stores new item fields; `PLAY`/`PRELOAD` carry them

**Files:**
- Modify: `server.py` — `SETPLAYLIST` handler (~757), the group `PLAY` items comprehension (~799), `_broadcast_segment_play` (~393), `sync_new_client_to_group` (~235)
- Test: `tests/unit/test_playlists.py`

Background: three code paths build the per-item PLAY payload. They currently emit `{id, file, duration, playmode}`. They must also emit `backgroundColor`/`startEffect`/`endEffect`. Old `MediaElement`s loaded from disk may lack the attributes, so read them with `getattr(me, "backgroundColor", "#000000")` etc. in every builder.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playlists.py`:

```python
class TestSetPlaylistFields:
    def test_setplaylist_stores_new_fields_with_defaults(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
               "PAYLOAD": {"displayID": "Default", "loop": False, "items": [
                   {"id": "a", "file": "/media/server/images/x.jpg", "duration": 5},
                   {"id": "b", "file": "/media/server/images/y.jpg", "duration": 5,
                    "playmode": "FULL", "backgroundColor": "#abcdef",
                    "startEffect": "wipe", "endEffect": "fade"},
               ]}}
        server.msg_response(msg, _make_session())
        me0, me1 = mock_settings.displays["Default"].mediaElements
        assert me0.backgroundColor == "#000000"   # default applied
        assert me0.startEffect is None
        assert me1.backgroundColor == "#abcdef"
        assert me1.startEffect == "wipe"
        assert me1.endEffect == "fade"

    def test_play_payload_carries_new_fields(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement()
        me.id = "a"; me.file = "/media/server/images/x.jpg"; me.duration = 1000
        me.playmode = server.PlayMode.FULL; me.backgroundColor = "#123456"
        disp.mediaElements = [me]
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        with patch("time.time", return_value=1000.0):
            server.msg_response({"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
                                 "PAYLOAD": {"displayID": "Default"}}, _make_session())
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args[0][0])
        item = sent["PAYLOAD"]["items"][0]
        assert item["backgroundColor"] == "#123456"
        assert item["startEffect"] is None and item["endEffect"] is None
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/unit/test_playlists.py::TestSetPlaylistFields -c tests/pytest.ini -v`
Expected: FAIL (`backgroundColor` not stored / not in payload).

- [ ] **Step 3: Implement**

In the `SETPLAYLIST` handler, after the existing `me.playmode = ...` assignment and before `display.mediaElements.append(me)`:

```python
            me.backgroundColor = item.get("backgroundColor", "#000000")
            me.startEffect = item.get("startEffect")
            me.endEffect = item.get("endEffect")
```

Define a single helper above `msg_response` (DRY — used by all three builders):

```python
def _media_item_payload(me):
    """Per-item dict sent to clients in PLAY/PRELOAD. getattr guards items
    loaded from an older settings.dat that predate the newer fields."""
    return {"id": me.id, "file": me.file, "duration": me.duration,
            "playmode": me.playmode.name,
            "backgroundColor": getattr(me, "backgroundColor", "#000000"),
            "startEffect": getattr(me, "startEffect", None),
            "endEffect": getattr(me, "endEffect", None)}
```

Replace the three builder expressions with calls to it:

- Group `PLAY` (~799): `items = [_media_item_payload(me) for me in display.mediaElements]`
- `sync_new_client_to_group` (~235): `[_media_item_payload(me) for me in display.mediaElements]`
- `_broadcast_segment_play` (~393): the appended per-item dict becomes a `_media_item_payload(me)` merged with the segment-specific keys it already adds (keep the existing `.update(...)`/extra keys; start from `_media_item_payload(me)` instead of the literal `{id,file,duration,playmode}`). Preserve every segment-only field already present.

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/unit/test_playlists.py::TestSetPlaylistFields -c tests/pytest.ini -v`
Then the existing playback suite to confirm no regression:
`python -m pytest tests/unit/test_playback.py tests/unit/test_mosaic.py -c tests/pytest.ini -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playlists.py
git commit -m "feat(playlist): carry backgroundColor/effects through SETPLAYLIST and PLAY payloads"
```

---

## Task 3: Playlist CRUD requests (`LIST`/`GET`/`SAVE`/`DELETE`)

**Files:**
- Modify: `server.py` — add four `elif` branches in `msg_response` (before the final `else`)
- Test: `tests/unit/test_playlists.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
class TestPlaylistCRUD:
    def _save(self, mock_settings, name="Lobby", loop=True):
        items = [{"id": "a", "file": "/media/server/images/x.jpg", "duration": 5,
                  "playmode": "SEGMENT", "backgroundColor": "#000000",
                  "startEffect": None, "endEffect": None}]
        server.msg_response({"SRC": "admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
            "PAYLOAD": {"name": name, "items": items, "loop": loop}}, _make_session())
        return items

    def test_save_then_get(self, mock_settings):
        server.settings = mock_settings
        items = self._save(mock_settings)
        assert "Lobby" in mock_settings.playlists
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_PLAYLIST",
             "PAYLOAD": {"name": "Lobby"}}, _make_session()))
        assert resp["PAYLOAD"]["name"] == "Lobby"
        assert resp["PAYLOAD"]["loop"] is True
        assert resp["PAYLOAD"]["items"] == items

    def test_save_upserts(self, mock_settings):
        server.settings = mock_settings
        self._save(mock_settings, loop=True)
        self._save(mock_settings, loop=False)
        assert len(mock_settings.playlists) == 1
        assert mock_settings.playlists["Lobby"].loop is False

    def test_list_returns_summaries(self, mock_settings):
        server.settings = mock_settings
        self._save(mock_settings)
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "LIST_PLAYLISTS",
             "PAYLOAD": {}}, _make_session()))
        row = resp["PAYLOAD"][0]
        assert row == {"name": "Lobby", "itemCount": 1, "hasSegment": True}

    def test_get_unknown_returns_error(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_PLAYLIST",
             "PAYLOAD": {"name": "nope"}}, _make_session()))
        assert resp["PAYLOAD"] == {"error": "not found"}

    def test_save_requires_name(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
             "PAYLOAD": {"name": "", "items": [], "loop": False}}, _make_session()))
        assert resp["PAYLOAD"] == {"error": "name required"}
        assert mock_settings.playlists == {}

    def test_delete(self, mock_settings):
        server.settings = mock_settings
        self._save(mock_settings)
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "DELETE_PLAYLIST",
            "PAYLOAD": {"name": "Lobby"}}, _make_session())
        assert "Lobby" not in mock_settings.playlists
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/unit/test_playlists.py::TestPlaylistCRUD -c tests/pytest.ini -v`
Expected: FAIL (requests fall through to the echo `else`).

- [ ] **Step 3: Implement**

Add these branches in `msg_response`, immediately before the final `else:`:

```python
    elif(msg["REQUEST"] == "LIST_PLAYLISTS"):
        rows = []
        for name, pl in settings.playlists.items():
            has_segment = any(it.get("playmode") == "SEGMENT" for it in pl.items)
            rows.append({"name": name, "itemCount": len(pl.items),
                         "hasSegment": has_segment})
        response["PAYLOAD"] = rows

    elif(msg["REQUEST"] == "GET_PLAYLIST"):
        pl = settings.playlists.get(msg["PAYLOAD"].get("name"))
        if pl is None:
            response["PAYLOAD"] = {"error": "not found"}
        else:
            response["PAYLOAD"] = {"name": pl.name, "items": pl.items, "loop": pl.loop}

    elif(msg["REQUEST"] == "SAVE_PLAYLIST"):
        payload = msg["PAYLOAD"]
        name = (payload.get("name") or "").strip()
        if not name:
            response["PAYLOAD"] = {"error": "name required"}
        else:
            pl = settings.playlists.setdefault(name, Playlist())
            pl.name = name
            pl.items = payload.get("items", [])
            pl.loop = bool(payload.get("loop", False))
            response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "DELETE_PLAYLIST"):
        settings.playlists.pop(msg["PAYLOAD"].get("name"), None)
        response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/unit/test_playlists.py::TestPlaylistCRUD -c tests/pytest.ini -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playlists.py
git commit -m "feat(playlist): LIST/GET/SAVE/DELETE playlist websocket requests"
```

---

## Task 4: `ASSIGN_PLAYLIST` — apply to a group + report render readiness

**Files:**
- Modify: `server.py` — add one `elif` branch in `msg_response`
- Test: `tests/unit/test_playlists.py`

Background: `ASSIGN_PLAYLIST` copies a named playlist's items into the target group's `mediaElements` (exactly as `SETPLAYLIST` does — build `MediaElement`s, reset `renderedToken=""`, broadcast `PRELOAD`), then classifies render readiness. Reuse the existing helpers `compute_render_token(display_id)` and the `PlayMode.SEGMENT` check. A renderable item is one with `playmode == PlayMode.SEGMENT` (INDIVIDUAL render is a later slice; the editor cannot save INDIVIDUAL items yet).

- [ ] **Step 1: Write the failing test**

Append:

```python
class TestAssignPlaylist:
    def _save(self, mock_settings, name, items, loop=False):
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
            "PAYLOAD": {"name": name, "items": items, "loop": loop}}, _make_session())

    def _assign(self, name, display_id="Default"):
        return jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "ASSIGN_PLAYLIST",
             "PAYLOAD": {"name": name, "displayID": display_id}}, _make_session()))

    def test_assign_ok_no_segment(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        self._save(mock_settings, "Imgs", [
            {"id": "a", "file": "/media/server/images/x.jpg", "duration": 5,
             "playmode": "FULL", "backgroundColor": "#000000",
             "startEffect": None, "endEffect": None}])
        resp = self._assign("Imgs")
        assert resp["PAYLOAD"]["status"] == "ok"
        assert len(mock_settings.displays["Default"].mediaElements) == 1

    def test_assign_segment_not_calibrated(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        mock_settings.displays["Default"].boundingBox = None
        self._save(mock_settings, "Seg", [
            {"id": "a", "file": "/media/server/videos/v.mp4", "duration": 5,
             "playmode": "SEGMENT", "backgroundColor": "#000000",
             "startEffect": None, "endEffect": None}])
        resp = self._assign("Seg")
        assert resp["PAYLOAD"]["status"] == "NOT_CALIBRATED"

    def test_assign_segment_render_required(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        disp.boundingBox = [[0, 0], [10, 0], [10, 10], [0, 10]]
        disp.renderedToken = "stale"
        self._save(mock_settings, "Seg", [
            {"id": "a", "file": "/media/server/videos/v.mp4", "duration": 5,
             "playmode": "SEGMENT", "backgroundColor": "#000000",
             "startEffect": None, "endEffect": None}])
        resp = self._assign("Seg")
        assert resp["PAYLOAD"]["status"] == "RENDER_REQUIRED"

    def test_assign_unknown_playlist(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        resp = self._assign("ghost")
        assert resp["PAYLOAD"]["status"] == "error"
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/unit/test_playlists.py::TestAssignPlaylist -c tests/pytest.ini -v`
Expected: FAIL (request echoes; no `status`).

- [ ] **Step 3: Implement**

Add this branch in `msg_response` (next to the other playlist branches):

```python
    elif(msg["REQUEST"] == "ASSIGN_PLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload.get("displayID")
        pl = settings.playlists.get(payload.get("name"))
        if pl is None or display_id is None:
            response["PAYLOAD"] = {"status": "error", "displayID": display_id}
        else:
            display = settings.displays.setdefault(display_id, Display())
            display.mediaElements = []
            for item in pl.items:
                me = MediaElement()
                me.id = item.get("id")
                me.file = item.get("file")
                me.duration = item.get("duration")
                _pm = item.get("playmode")
                me.playmode = (PlayMode.SEGMENT if _pm == "SEGMENT"
                               else PlayMode.SCRIPT if _pm == "SCRIPT"
                               else PlayMode.INDIVIDUAL if _pm == "INDIVIDUAL"
                               else PlayMode.FULL)
                me.backgroundColor = item.get("backgroundColor", "#000000")
                me.startEffect = item.get("startEffect")
                me.endEffect = item.get("endEffect")
                display.mediaElements.append(me)
            display.loop = bool(pl.loop)
            display.renderedToken = ""
            broadcast_to_display_group(display_id, {
                "REQUEST": "PRELOAD", "PAYLOAD": {"items": pl.items}})
            has_segment = any(me.playmode == PlayMode.SEGMENT
                              for me in display.mediaElements)
            if has_segment and not display.boundingBox:
                status = "NOT_CALIBRATED"
            elif has_segment and compute_render_token(display_id) != display.renderedToken:
                status = "RENDER_REQUIRED"
            else:
                status = "ok"
            response["PAYLOAD"] = {"status": status, "displayID": display_id}
```

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/unit/test_playlists.py::TestAssignPlaylist -c tests/pytest.ini -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playlists.py
git commit -m "feat(playlist): ASSIGN_PLAYLIST applies to group + reports render readiness"
```

---

## Task 5: `GET /api/media` + `video` upload destination

**Files:**
- Modify: `server.py` — add `api_media` handler, register route (~1522 route block), extend `upload_handler` (~1109) + add `processVideo`
- Test: `tests/unit/test_playlists.py`

- [ ] **Step 1: Write the failing test**

Append (uses `make_mocked_request`, matching `tests/unit/test_api_endpoints.py`, and `tmp_path` so the real filesystem is untouched):

```python
import os
from aiohttp.test_utils import make_mocked_request
import pytest

class TestMediaApi:
    @pytest.mark.asyncio
    async def test_api_media_lists_images_and_videos(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("media/server/images"); os.makedirs("media/server/videos")
        open("media/server/images/a.jpg", "w").close()
        open("media/server/videos/b.mp4", "w").close()
        resp = await server.api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert "/media/server/images/a.jpg" in data["images"]
        assert "/media/server/videos/b.mp4" in data["videos"]

    @pytest.mark.asyncio
    async def test_api_media_missing_dirs_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resp = await server.api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert data == {"images": [], "videos": []}

    def test_process_video_moves_into_library(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("cache")
        open("cache/clip.mp4", "w").close()
        server.processVideo("cache", "clip.mp4")
        assert os.path.exists("media/server/videos/clip.mp4")
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/unit/test_playlists.py -k "Media or video" -c tests/pytest.ini -v`
Expected: FAIL (`api_media`/`processVideo` not defined).

- [ ] **Step 3: Implement**

Add the handler (near `api_discovery_devices`):

```python
async def api_media(request):
    """List the shared media library under media/server/{images,videos}."""
    def _list(sub):
        d = os.path.join("media", "server", sub)
        if not os.path.isdir(d):
            return []
        return ["/media/server/" + sub + "/" + f
                for f in sorted(os.listdir(d))
                if os.path.isfile(os.path.join(d, f))]
    body = json.dumps({"images": _list("images"), "videos": _list("videos")})
    return web.Response(text=body, content_type="application/json")
```

Add `processVideo` next to `processImage`:

```python
def processVideo(path, filename):
    logging.debug("processVideo")
    vidDir = "media/server/videos"
    Path(vidDir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(path, filename)).rename(os.path.join(vidDir, filename))
    return "success", "text/html"
```

Extend `upload_handler`'s dest dispatch (after the `image` branch):

```python
    elif(uploadDest == "video"):
        response, ct = processVideo(path, filename)
```

Register the route in the `__main__` route block (next to the discovery routes):

```python
        app.router.add_route('GET', '/api/media', api_media)
```

NOTE on route ordering: `/api/media` must be added **before** the catch-all `app.router.add_route('GET', '/{page:[^{}/]+}', index_handler)` only if that pattern could match `api`; it cannot (the pattern excludes `/`, and `/api/media` has slashes), but keep `/api/media` grouped with the other `/api/...` routes which already sit after the catch-all and work fine. Place it beside them.

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/unit/test_playlists.py -k "Media or video" -c tests/pytest.ini -v`
Expected: 3 passed. Then full unit suite: `python pytest_runner.py --unit` → all green.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playlists.py
git commit -m "feat(playlist): /api/media listing + video upload destination"
```

---

## Task 6: `index.html` — apply `backgroundColor` letterbox (ES5)

**Files:**
- Modify: `index.html` — `showItem` (the FULL/else image branch)
- Verify: Playwright (controller-run)

Background: do the small display-client change early so the editor (Task 7–8) can be visually verified against a real client. **ES5 ONLY** (`var`/`function`/string concat — 1st-gen iPad). `showItem` currently sets `#canvas` HTML for the image case; add a per-item background color on the container so a FULL image that doesn't fill the viewport letterboxes against `item.backgroundColor`.

- [ ] **Step 1: Implement**

In `index.html` `showItem(i, offsetMs)`, immediately after `var item = playback.items[i]; if (!item) { return; }`, set the container background (applies to every mode; default black preserves today's look):

```javascript
		$('#canvas').css('background-color', item.backgroundColor || '#000000');
```

(Place it before the `if (item.playmode === 'SCRIPT')` branch so all modes share it.)

- [ ] **Step 2: Verify ES5 + behavior (controller, Playwright)**

- Grep `index.html` for `=>`, backticks, `let `, `const ` → expect zero in the inline script.
- Start the server: `python server.py -p 3000` (background). Navigate Playwright to `http://localhost:3000/`, then drive:

```javascript
() => {
  playback.items = [{ id:'a', file:'/none.jpg', duration:60000, playmode:'FULL', backgroundColor:'#3344ff' }];
  playback.startEpoch = GoTime.now(); playback.active = true;
  showItem(0, 0);
  return $('#canvas').css('background-color'); // expect rgb(51, 68, 255)
}
```

Expected: `rgb(51, 68, 255)`.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat(playlist): apply per-item backgroundColor as display letterbox (ES5)"
```

---

## Task 7: `admin.html` — editor scaffold, media library, playlist list

**Files:**
- Modify: `admin.html` — add a `#playlistEditor` section (HTML), CSS, and a `<script>` with editor state + library/list logic; add response handling in `mosiacMeshCallback`
- Verify: Playwright (controller-run)

Background: `admin.html` is a desktop console (modern JS allowed). It already has `sock`, `generateMessage('SRV', REQUEST, PAYLOAD)`, `sock.send(...)`, and a `mosiacMeshCallback(data_obj)` that switches on `data_obj.REQUEST`. Replies echo the request name. This task builds everything EXCEPT the inspector and transport bar (Task 8): the section shell, the media library (fed by `GET /api/media` + the hardcoded SCRIPT list), the playlist rows (add/reorder/remove), and New/Save/Delete/Load wired to the CRUD requests.

- [ ] **Step 1: Add the HTML section**

Insert before the `<div id="log" ...>` block in `admin.html`:

```html
<div id="playlistEditor" style="border:1px solid black; padding:8px; width:62em; margin-bottom:1em;">
  <div style="margin-bottom:6px;">
    <b>Playlists:</b>
    <select id="plSelect"></select>
    <button id="plNew" type="button">+ New</button>
    <button id="plSave" type="button">Save</button>
    <button id="plDelete" type="button">Delete</button>
    <label style="margin-left:8px;"><input type="checkbox" id="plLoop"> Loop</label>
    <span id="plName" style="margin-left:8px; color:#555;"></span>
  </div>
  <div style="display:flex; gap:10px;">
    <div style="flex:0 0 12em;">
      <div class="size"><b>Media Library</b></div>
      <div id="plLibrary" style="height:14em; overflow:auto; border:1px solid #ccc;"></div>
      <input id="plUpload" type="file" style="margin-top:4px;">
    </div>
    <div style="flex:1;">
      <div class="size"><b>Playlist</b> (drag to reorder)</div>
      <ul id="plItems" style="list-style:none; padding:0; margin:0; height:14em; overflow:auto; border:1px solid #ccc;"></ul>
    </div>
    <div id="plInspectorHost" style="flex:0 0 15em;">
      <!-- Task 8 injects the inspector here -->
    </div>
  </div>
  <div id="plTransport" style="margin-top:6px;">
    <!-- Task 8 injects assign + render + transport here -->
  </div>
</div>
```

- [ ] **Step 2: Add the editor script**

Add a new `<script>` block before `</body>`:

```html
<script>
var SCRIPT_ANIMATIONS = ["bouncingBalls"];
var plEditor = { name: "", items: [], loop: false, selected: -1 };

function plDefaultItem(file, isScript) {
  return { id: "i" + Date.now() + Math.floor(Math.random()*1000),
           file: file, duration: 5,
           playmode: isScript ? "SCRIPT" : "FULL",
           backgroundColor: "#000000", startEffect: null, endEffect: null };
}

function plRenderLibrary(images, videos) {
  var $lib = $('#plLibrary').empty();
  function row(label, file, isScript) {
    $('<div>').text(label).css({padding:'3px', cursor:'pointer'})
      .on('click', function(){ plAddItem(file, isScript); }).appendTo($lib);
  }
  $.each(images, function(_, f){ row("🖼 " + f.split('/').pop(), f, false); });
  $.each(videos, function(_, f){ row("🎞 " + f.split('/').pop(), f, false); });
  $.each(SCRIPT_ANIMATIONS, function(_, n){ row("⚡ " + n, n, true); });
}

function plLoadLibrary() {
  $.getJSON('/api/media', function(data){
    plRenderLibrary(data.images || [], data.videos || []);
  });
}

function plAddItem(file, isScript) {
  plEditor.items.push(plDefaultItem(file, isScript));
  plRenderItems();
}

function plRenderItems() {
  var $list = $('#plItems').empty();
  $.each(plEditor.items, function(i, it){
    var $li = $('<li>').attr('draggable', true)
      .css({padding:'5px', border:'1px solid #ddd', margin:'2px', cursor:'pointer',
            background: i === plEditor.selected ? '#d6e6fb' : '#fff'})
      .text("⠿ " + (i+1) + "  " + it.file.split('/').pop() + " · " +
            it.duration + "s · " + it.playmode);
    $('<span>').text(" ✕").css({float:'right', color:'#a00'})
      .on('click', function(e){ e.stopPropagation(); plEditor.items.splice(i,1);
            if (plEditor.selected >= plEditor.items.length) plEditor.selected = -1;
            plRenderItems(); plRenderInspector(); }).appendTo($li);
    $li.on('click', function(){ plEditor.selected = i; plRenderItems(); plRenderInspector(); });
    $li.on('dragstart', function(e){ e.originalEvent.dataTransfer.setData('text/plain', i); });
    $li.on('dragover', function(e){ e.preventDefault(); });
    $li.on('drop', function(e){
      e.preventDefault();
      var from = parseInt(e.originalEvent.dataTransfer.getData('text/plain'), 10);
      var moved = plEditor.items.splice(from, 1)[0];
      plEditor.items.splice(i, 0, moved);
      plEditor.selected = i; plRenderItems(); plRenderInspector();
    });
    $list.append($li);
  });
  if (typeof plRenderInspector === 'function') { /* defined in Task 8 */ }
}

// plRenderInspector is defined in Task 8; stub keeps Task 7 standalone.
if (typeof plRenderInspector !== 'function') { window.plRenderInspector = function(){}; }

function plRefreshList() { sock.send(generateMessage('SRV', 'LIST_PLAYLISTS', {})); }

function plLoad(name) {
  if (!name) return;
  sock.send(generateMessage('SRV', 'GET_PLAYLIST', { name: name }));
}

function plNew() {
  plEditor = { name: "", items: [], loop: false, selected: -1 };
  $('#plLoop').prop('checked', false);
  $('#plName').text("(new, unsaved)");
  plRenderItems(); plRenderInspector();
}

function plSave() {
  var name = (plEditor.name || prompt("Playlist name:") || "").trim();
  if (!name) { alert("Name required"); return; }
  plEditor.name = name;
  plEditor.loop = $('#plLoop').is(':checked');
  sock.send(generateMessage('SRV', 'SAVE_PLAYLIST',
    { name: name, items: plEditor.items, loop: plEditor.loop }));
  $('#plName').text(name);
  setTimeout(plRefreshList, 100);
}

function plDelete() {
  if (!plEditor.name) return;
  sock.send(generateMessage('SRV', 'DELETE_PLAYLIST', { name: plEditor.name }));
  plNew(); setTimeout(plRefreshList, 100);
}

function plUploadFile(file) {
  var fd = new FormData(); fd.append('file', file);
  var dest = /\.mp4$/i.test(file.name) ? 'video' : 'image';
  $.ajax({ url: '/upload/' + dest, type: 'POST', data: fd,
           processData: false, contentType: false,
           success: function(){ plLoadLibrary(); } });
}

$(function(){
  $('#plNew').on('click', plNew);
  $('#plSave').on('click', plSave);
  $('#plDelete').on('click', plDelete);
  $('#plSelect').on('change', function(){ plLoad($(this).val()); });
  $('#plUpload').on('change', function(){ if (this.files[0]) plUploadFile(this.files[0]); });
  plLoadLibrary();
  setTimeout(plRefreshList, 500);  // after sock connects
});
</script>
```

- [ ] **Step 3: Wire responses into `mosiacMeshCallback`**

Inside `mosiacMeshCallback(data_obj)`, add handling (as `else if` branches):

```javascript
		else if(data_obj.REQUEST == "LIST_PLAYLISTS")
		{
			var $sel = $('#plSelect').empty();
			$('<option>').val("").text("-- select --").appendTo($sel);
			$.each(data_obj.PAYLOAD, function(_, row){
				$('<option>').val(row.name)
					.text(row.name + " (" + row.itemCount + ")").appendTo($sel);
			});
			if (plEditor.name) { $sel.val(plEditor.name); }
		}
		else if(data_obj.REQUEST == "GET_PLAYLIST")
		{
			if (data_obj.PAYLOAD && !data_obj.PAYLOAD.error) {
				plEditor = { name: data_obj.PAYLOAD.name,
					items: data_obj.PAYLOAD.items || [],
					loop: !!data_obj.PAYLOAD.loop, selected: -1 };
				$('#plLoop').prop('checked', plEditor.loop);
				$('#plName').text(plEditor.name);
				plRenderItems(); plRenderInspector();
			}
		}
```

- [ ] **Step 4: Verify (controller, Playwright)**

Start `python server.py -p 3000` (background). Navigate Playwright to `http://localhost:3000/admin.html`. Then:

```javascript
() => {
  plAddItem('/media/server/images/x.jpg', false);
  plAddItem('bouncingBalls', true);
  return { count: plEditor.items.length,
           rows: $('#plItems li').length,
           modes: plEditor.items.map(function(i){ return i.playmode; }) };
}
```

Expected: `{count:2, rows:2, modes:["FULL","SCRIPT"]}`. Then click row 1 and confirm `plEditor.selected === 0`. Confirm `$('#plLibrary div').length >= 1` (at least the SCRIPT entry shows even with empty media).

- [ ] **Step 5: Commit**

```bash
git add admin.html
git commit -m "feat(playlist): editor scaffold — media library + playlist rows + CRUD wiring"
```

---

## Task 8: `admin.html` — inspector, assign, transport, render status

**Files:**
- Modify: `admin.html` — define `plRenderInspector`, the assign/transport bar, and `RENDER_STATUS` handling
- Verify: Playwright (controller-run)

Background: completes the editor. The inspector edits the selected item; the transport bar assigns to a group and drives Render/Play/Pause/Stop. `INDIVIDUAL` and both effect dropdowns are present but disabled. The group dropdown is populated from the displays tree the console already loads (`settings.displays` via the `DISPLAYS` request); read the group ids from `$('#displays')` jstree, or fall back to a text input.

- [ ] **Step 1: Build the inspector + transport (replace the Task 7 stub)**

Replace the `plRenderInspector` stub with the real implementation and add the transport, in the editor `<script>`:

```javascript
function plRenderInspector() {
  var $host = $('#plInspectorHost').empty();
  var it = plEditor.items[plEditor.selected];
  if (!it) { $host.html('<div class="size" style="color:#888;">Select an item</div>'); return; }
  $('<div class="size"><b>Inspector — item ' + (plEditor.selected+1) + '</b></div>').appendTo($host);

  $('<div class="size">Duration (s)</div>').appendTo($host);
  $('<input>').val(it.duration).attr('type','number').css('width','5em')
    .on('change', function(){ it.duration = parseFloat(this.value)||0; plRenderItems(); })
    .appendTo($host);

  $('<div class="size">Play mode</div>').appendTo($host);
  var $pm = $('<select>').appendTo($host);
  $.each([["FULL","FULL"],["SEGMENT","SEGMENT (mesh)"],
          ["INDIVIDUAL","INDIVIDUAL — soon"],["SCRIPT","SCRIPT"]], function(_, o){
    var $o = $('<option>').val(o[0]).text(o[1]);
    if (o[0] === "INDIVIDUAL") $o.prop('disabled', true);
    $pm.append($o);
  });
  $pm.val(it.playmode).on('change', function(){ it.playmode = this.value; plRenderItems(); });

  $('<div class="size">Background color</div>').appendTo($host);
  $('<input>').attr('type','color').val(it.backgroundColor || '#000000')
    .on('change', function(){ it.backgroundColor = this.value; }).appendTo($host);

  $('<div class="size" style="opacity:.5;">Start effect</div>').appendTo($host);
  $('<select disabled><option>None (coming soon)</option></select>').appendTo($host);
  $('<div class="size" style="opacity:.5;">End effect</div>').appendTo($host);
  $('<select disabled><option>None (coming soon)</option></select>').appendTo($host);
}

function plGroupOptions() {
  // Group ids = children of the 'displays' root in the jstree, minus the tree root.
  var ids = [];
  try {
    var data = $('#displays').jstree(true).settings.core.data[0].children;
    $.each(data, function(_, g){ ids.push(g.id); });
  } catch (e) {}
  if (!ids.length) ids = ["Default", "Mobile", "Tablet", "Desktop"];
  return ids;
}

function plBuildTransport() {
  var $t = $('#plTransport').empty();
  $('<span>Assign → </span>').appendTo($t);
  var $g = $('<select id="plGroup">').appendTo($t);
  $.each(plGroupOptions(), function(_, id){ $('<option>').val(id).text(id).appendTo($g); });
  $('<button type="button">Assign</button>').on('click', plAssign).appendTo($t);
  $('<button type="button">⚙ Render</button>').on('click', plRender).appendTo($t);
  $('<span id="plRenderBadge" style="margin:0 8px; color:#c80;"></span>').appendTo($t);
  $('<button type="button">▶ Play</button>').on('click', function(){ plTransport('PLAY'); }).appendTo($t);
  $('<button type="button">⏸ Pause</button>').on('click', function(){ plTransport('PAUSE'); }).appendTo($t);
  $('<button type="button">⏹ Stop</button>').on('click', function(){ plTransport('STOP'); }).appendTo($t);
}

function plAssignedGroup() { return $('#plGroup').val(); }

function plAssign() {
  if (!plEditor.name) { alert("Save the playlist first"); return; }
  sock.send(generateMessage('SRV', 'ASSIGN_PLAYLIST',
    { name: plEditor.name, displayID: plAssignedGroup() }));
}
function plRender() {
  sock.send(generateMessage('SRV', 'RENDER', { displayID: plAssignedGroup() }));
}
function plTransport(req) {
  sock.send(generateMessage('SRV', req, { displayID: plAssignedGroup() }));
}
function plSetBadge(text) { $('#plRenderBadge').text(text); }
```

Call `plBuildTransport()` once the socket is up — add to the existing `$(function(){ ... })` in Task 7 (append): `setTimeout(plBuildTransport, 600);`.

- [ ] **Step 2: Handle `ASSIGN_PLAYLIST` + `RENDER_STATUS` replies**

Add to `mosiacMeshCallback`:

```javascript
		else if(data_obj.REQUEST == "ASSIGN_PLAYLIST")
		{
			var s = data_obj.PAYLOAD.status;
			plSetBadge(s === "RENDER_REQUIRED" ? "⚠ needs render"
				: s === "NOT_CALIBRATED" ? "⚠ not calibrated"
				: s === "error" ? "⚠ assign failed" : "✓ assigned");
		}
		else if(data_obj.REQUEST == "RENDER_STATUS")
		{
			var st = data_obj.PAYLOAD.renderStatus || data_obj.PAYLOAD.status;
			plSetBadge(st === "rendering" ? "⏳ rendering…"
				: st === "ready" ? "✓ render ready"
				: st === "error" ? "⚠ render error" : st || "");
		}
```

(Confirm the broadcast field name by checking `_broadcast_render_status` in `server.py` — use whichever key it sets; the fallback above reads either `renderStatus` or `status`.)

- [ ] **Step 3: Verify (controller, Playwright)**

Start `python server.py -p 3000` (background). Navigate to `http://localhost:3000/admin.html`. Build a playlist, then:

```javascript
() => {
  plAddItem('/media/server/images/x.jpg', false);
  plEditor.selected = 0; plRenderInspector();
  var modes = $('#plInspectorHost select').first().find('option')
                .map(function(){ return this.disabled ? this.value + ':disabled' : this.value; }).get();
  return { inspectorShown: $('#plInspectorHost select').length >= 1,
           individualDisabled: modes.indexOf('INDIVIDUAL:disabled') >= 0,
           transportButtons: $('#plTransport button').length };
}
```

Expected: `inspectorShown:true`, `individualDisabled:true`, `transportButtons:5` (Assign/Render/Play/Pause/Stop). Then exercise a full round-trip if media exists: New → add item → Save (name "PWTest") → confirm it appears in `#plSelect`; Assign to "Default" → badge updates.

- [ ] **Step 4: Commit**

```bash
git add admin.html
git commit -m "feat(playlist): inspector, assign + transport bar, render-status badge"
```

---

## Final verification (after all tasks)

- [ ] `python pytest_runner.py --unit` → all green (no regressions in playback/mosaic suites).
- [ ] Playwright end-to-end in `admin.html`: New → add image + script items → set duration/playmode/bg in inspector → Save → reload section → playlist reloads → Assign to a group → badge reflects status. Confirm a FULL item's `backgroundColor` shows as the letterbox on a connected `index.html` client.
- [ ] Push branch and update PR #1.

## Notes for the implementer

- **DRY:** `_media_item_payload` (Task 2) is the single source of the per-item PLAY/PRELOAD shape — do not re-inline the dict in the three builders.
- **YAGNI:** Do NOT implement INDIVIDUAL rendering or effects here — fields/enum only. INDIVIDUAL stays disabled in the picker.
- **No ES5 in `admin.html`** — it's a desktop console; jQuery/modern JS is fine. The ONLY ES5-constrained edit is Task 6 in `index.html`.
- If a referenced helper (`compute_render_token`, `broadcast_to_display_group`, `_broadcast_render_status`, `Display`, the displays jstree shape) does not match what's in the code, STOP and report NEEDS_CONTEXT rather than guessing.
```
