# Stale Render-Asset GC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reclaim server-side render assets (`seg_/ind_/full_<token>_<i>` files) that no live render references — both as a (playlist×group) re-renders (lifecycle delete-after-READY) and as a one-time boot sweep of accumulated orphans.

**Architecture:** A single shared guard `_token_is_live(token)` decides whether any registry entry or `renderedToken` still references a token. Two callers use it: (1) `render_playlist_for_group_async` deletes the *previous* token's files once the new token reaches READY; (2) a boot-only `sweep_orphan_render_assets()` walks `media/` and deletes rendered-asset files whose token is referenced nowhere. The existing `_delete_render_assets(playlist, group)` is generalized into a token-scoped `_delete_token_assets(token, group)` so no glob logic is duplicated.

**Tech Stack:** Python 3, pytest (`python -m pytest ... -c tests/pytest.ini`), `os`/`glob`/`re`. No ffmpeg, no SSH — all tests are unit-level with `monkeypatch.chdir(tmp_path)` + a hand-built `media/` tree.

**Spec:** `docs/superpowers/specs/2026-06-15-stale-render-asset-gc-design.md`

**Key facts from the codebase (read before starting):**
- Render assets are named `media/<clientKey>/{videos,images}/{seg_,ind_}<token>_<i>.{mp4,png}` (per-client SEGMENT/INDIVIDUAL) and `media/server/{videos,images}/full_<token>_<i>.{mp4,png}` (shared FULL). `<token>` is `hashlib.sha1(...).hexdigest()[:12]` — 12 lowercase hex chars (`render.py:371`).
- `render_token(media_elements, display_id)` hashes `(items, boundingBox, clients, encode_ver)` — **not** the playlist name. Identical-item playlists on one group share a token AND its files. This is why every delete must be guarded by `_token_is_live`.
- `render_playlist_for_group_async` (`render.py:726`) overwrites `entry["token"]` with the *new* token at the RENDERING transition (`render.py:742`), so the previous token must be captured at the **top** of the function.
- The existing `_delete_render_assets` lives at `render.py:897`. `_group_clients(display_id)` yields `(clientKey, client)` for a group.
- Test fixture pattern (see `tests/unit/test_render_registry.py:21-26`): a `fresh_settings` fixture swaps `server.settings = Settings()`. Filesystem tests `monkeypatch.chdir(tmp_path)` then create `media/...` subdirs (see `tests/unit/test_mosaic.py:161-163`).
- Run tests with: `python -m pytest tests/unit/<file> -c tests/pytest.ini -v` (a bare `pytest` from the root won't pick up config — see CLAUDE.md).

---

## File Structure

- **Modify** `mosaicmesh/render.py`:
  - Add `_token_is_live(token)` (new, near the other registry helpers, e.g. after `is_playlist_ready`).
  - Refactor `_delete_render_assets(playlist_name, display_id)` → add `_delete_token_assets(token, display_id)`; make `_delete_render_assets` a thin wrapper.
  - Modify `render_playlist_for_group_async` to capture `prev_token` and delete-after-READY.
  - Add `sweep_orphan_render_assets()` (new) + a module-level compiled regex `_RENDER_ASSET_RE`.
- **Modify** `server.py`: call `sweep_orphan_render_assets()` in the `__main__` boot block, right after `revalidate_renders_on_boot()`; add it to the re-export import from `mosaicmesh.render`.
- **Create** `tests/unit/test_render_gc.py`: all unit tests for the four pieces.

---

### Task 1: `_token_is_live(token)` shared guard

**Files:**
- Modify: `mosaicmesh/render.py` (add function after `is_playlist_ready`, ~line 443)
- Test: `tests/unit/test_render_gc.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_render_gc.py`:

```python
# tests/unit/test_render_gc.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import pytest
from mosaicmesh.state import Settings, Display
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def test_token_is_live_true_for_registry_token(fresh_settings):
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_READY, token="abc123abc123")
    assert R._token_is_live("abc123abc123") is True


def test_token_is_live_true_for_rendered_token(fresh_settings):
    d = Display()
    d.renderedToken = "deadbeef0000"
    fresh_settings.displays["G1"] = d
    assert R._token_is_live("deadbeef0000") is True


def test_token_is_live_false_for_unreferenced(fresh_settings):
    d = Display()
    R._set_render_state(d, "P", R.RENDER_READY, token="abc123abc123")
    fresh_settings.displays["G1"] = d
    assert R._token_is_live("999999999999") is False


def test_token_is_live_false_for_empty(fresh_settings):
    assert R._token_is_live("") is False
    assert R._token_is_live(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: module 'mosaicmesh.render' has no attribute '_token_is_live'`

- [ ] **Step 3: Write minimal implementation**

In `mosaicmesh/render.py`, add immediately after `is_playlist_ready` (after line 442):

```python
def _token_is_live(token):
    """True if `token` is still referenced anywhere: any group's render-registry
    entry token, or any group's live renderedToken. The single guard that makes
    asset deletion safe against the shared-token case (identical-item playlists
    on one group hash to the same token and share files)."""
    import server
    if not token:
        return False
    for display in server.settings.displays.values():
        for e in (getattr(display, "renders", {}) or {}).values():
            if e.get("token") == token:
                return True
        if getattr(display, "renderedToken", "") == token:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_gc.py
git commit -m "feat(render): _token_is_live guard for safe asset GC"
```

---

### Task 2: `_delete_token_assets(token, display_id)` refactor

**Files:**
- Modify: `mosaicmesh/render.py:897-920` (refactor `_delete_render_assets`)
- Test: `tests/unit/test_render_gc.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render_gc.py`:

```python
def _seed_asset(tmp_path, key, sub, name):
    d = tmp_path / "media" / key / sub
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("x")
    return f


def test_delete_token_assets_removes_per_client_and_full(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Display, Client
    monkeypatch.chdir(tmp_path)
    d = Display(); fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"
    fresh_settings.clients["c1"] = c
    tok = "aaaaaaaaaaaa"
    seg = _seed_asset(tmp_path, "c1", "videos", f"seg_{tok}_0.mp4")
    ind = _seed_asset(tmp_path, "c1", "images", f"ind_{tok}_1.png")
    full = _seed_asset(tmp_path, "server", "videos", f"full_{tok}_0.mp4")
    R._delete_token_assets(tok, "G1")
    assert not seg.exists()
    assert not ind.exists()
    assert not full.exists()


def test_delete_token_assets_leaves_other_token(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Display, Client
    monkeypatch.chdir(tmp_path)
    d = Display(); fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"
    fresh_settings.clients["c1"] = c
    keep = _seed_asset(tmp_path, "c1", "videos", "seg_bbbbbbbbbbbb_0.mp4")
    R._delete_token_assets("aaaaaaaaaaaa", "G1")
    assert keep.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k delete_token`
Expected: FAIL — `AttributeError: ... has no attribute '_delete_token_assets'`

- [ ] **Step 3: Write minimal implementation**

Replace `_delete_render_assets` (`mosaicmesh/render.py:897-920`) with the generalized helper + wrapper:

```python
def _delete_token_assets(token, display_id):
    """Delete on-disk seg_/ind_/full_ assets for a group at a SPECIFIC token.
    Best-effort; missing files are fine. Caller is responsible for confirming the
    token is no longer live (see _token_is_live)."""
    import server, glob
    if not token:
        return
    for key, _c in _group_clients(display_id):
        for sub in ("videos", "images"):
            for prefix in ("seg_", "ind_"):
                for path in glob.glob(os.path.join("media", key, sub, prefix + token + "_*")):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
    for sub in ("videos", "images"):
        for path in glob.glob(os.path.join("media", "server", sub, "full_" + token + "_*")):
            try:
                os.remove(path)
            except OSError:
                pass


def _delete_render_assets(playlist_name, display_id):
    """Delete the assets for a (playlist, group) at its CURRENT registry token.
    Thin wrapper over _delete_token_assets — used by playlist/group delete."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return
    token = (display.renders.get(playlist_name) or {}).get("token", "")
    _delete_token_assets(token, display_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k delete_token`
Expected: PASS (2 passed)

Then verify the existing delete-path tests still pass (the wrapper preserves behavior):

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_gc.py
git commit -m "refactor(render): extract _delete_token_assets from _delete_render_assets"
```

---

### Task 3: Lifecycle delete-after-READY in `render_playlist_for_group_async`

**Files:**
- Modify: `mosaicmesh/render.py:726-769` (`render_playlist_for_group_async`)
- Test: `tests/unit/test_render_gc.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render_gc.py`:

```python
import asyncio


def _calibrated_group(fresh_settings, did, ckey):
    from mosaicmesh.state import Display, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays[did] = d
    c = Client(); c.displayID = did; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients[ckey] = c
    return d


def test_rerender_deletes_previous_token_assets(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Playlist
    monkeypatch.chdir(tmp_path)
    d = _calibrated_group(fresh_settings, "G1", "c1")
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    # Pretend a previous render produced files under an OLD token (12 hex chars).
    old_tok = "0123456789ab"
    old = _seed_asset(tmp_path, "c1", "videos", f"seg_{old_tok}_0.mp4")
    R._set_render_state(d, "P", R.RENDER_READY, token=old_tok)

    async def _fake_encode(elements, did, token, progress_cb=None):
        if progress_cb: progress_cb(1, 1)
    monkeypatch.setattr(R, "_encode_group", _fake_encode)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    new_tok = R.render_token(R._build_media_elements(pl.items), "G1")
    assert d.renders["P"]["state"] == R.RENDER_READY
    assert d.renders["P"]["token"] == new_tok
    assert new_tok != old_tok
    assert not old.exists()                     # previous token's files reclaimed


def test_rerender_keeps_shared_old_token(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Playlist
    monkeypatch.chdir(tmp_path)
    d = _calibrated_group(fresh_settings, "G1", "c1")
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    old_tok = "abcdefabcdef"
    old = _seed_asset(tmp_path, "c1", "videos", f"seg_{old_tok}_0.mp4")
    R._set_render_state(d, "P", R.RENDER_READY, token=old_tok)
    # A SECOND playlist entry on the same group still references the old token.
    R._set_render_state(d, "Q", R.RENDER_READY, token=old_tok)

    async def _fake_encode(elements, did, token, progress_cb=None):
        if progress_cb: progress_cb(1, 1)
    monkeypatch.setattr(R, "_encode_group", _fake_encode)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    assert old.exists()                          # shared token still live -> NOT deleted


def test_failed_rerender_keeps_previous_assets(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Playlist
    monkeypatch.chdir(tmp_path)
    d = _calibrated_group(fresh_settings, "G1", "c1")
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    old_tok = "0123456789ab"
    old = _seed_asset(tmp_path, "c1", "videos", f"seg_{old_tok}_0.mp4")
    R._set_render_state(d, "P", R.RENDER_READY, token=old_tok)

    async def _boom(elements, did, token, progress_cb=None):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(R, "_encode_group", _boom)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    assert d.renders["P"]["state"] == R.RENDER_FAILED
    assert old.exists()                          # failed re-render must not delete old
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k rerender`
Expected: FAIL — `test_rerender_deletes_previous_token_assets` fails (`old.exists()` is still True; nothing deletes it yet). The shared-token and failed tests may pass incidentally (no deletion happens at all yet) — that's fine; they lock in behavior once Step 3 lands.

- [ ] **Step 3: Write minimal implementation**

In `mosaicmesh/render.py`, modify `render_playlist_for_group_async`. Capture the previous token at the top (after the N/A early-return, before the RENDERING `_set_render_state` at line 742):

```python
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        display.renders.pop(playlist_name, None)   # became N/A
        _broadcast_renders_changed(force=True)
        return
    prev_token = (display.renders.get(playlist_name) or {}).get("token")   # <-- ADD
    token = render_token(elements, display_id)
    _set_render_state(display, playlist_name, RENDER_RENDERING, token=token,
                      percent=0, started=time.time())
    _broadcast_renders_changed(force=True)
```

Then in the success branch (after the READY `_set_render_state` and the `renderedToken` sync, before the `except`), add the guarded delete:

```python
    try:
        await _encode_group(elements, display_id, token, progress_cb=_progress)
        _set_render_state(display, playlist_name, RENDER_READY, token=token,
                          percent=100, eta=0, error=None)
        # If this playlist is the one applied to the group, sync the live token
        # so the per-client PLAY URLs resolve the freshly-rendered assets.
        if getattr(display, "currentPlaylistName", None) == playlist_name:
            display.renderedToken = token
        # Reclaim the superseded token's assets — but only if nothing else
        # references it (shared-token safety).
        if prev_token and prev_token != token and not _token_is_live(prev_token):
            _delete_token_assets(prev_token, display_id)
    except Exception as e:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k rerender`
Expected: PASS (3 passed)

Then the full registry suite for no regressions:

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_gc.py
git commit -m "feat(render): delete superseded token assets after READY re-render"
```

---

### Task 4: `sweep_orphan_render_assets()` boot sweep

**Files:**
- Modify: `mosaicmesh/render.py` (add `_RENDER_ASSET_RE` module-level + `sweep_orphan_render_assets`)
- Test: `tests/unit/test_render_gc.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render_gc.py`:

```python
def test_sweep_removes_only_orphan_tokens(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Display, Client
    monkeypatch.chdir(tmp_path)
    d = Display(); fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"
    fresh_settings.clients["c1"] = c
    live_tok = "fedcbafedcba"          # 12 hex chars
    R._set_render_state(d, "P", R.RENDER_READY, token=live_tok)

    live = _seed_asset(tmp_path, "c1", "videos", f"seg_{live_tok}_0.mp4")
    orphan = _seed_asset(tmp_path, "c1", "videos", "seg_111111111111_0.mp4")
    orphan_full = _seed_asset(tmp_path, "server", "images", "full_111111111111_2.png")
    # Non-matching files must be untouched.
    upload = _seed_asset(tmp_path, "server", "videos", "myvideo.mp4")
    aruco = _seed_asset(tmp_path, "c1", "images", "aruco.png")

    removed = R.sweep_orphan_render_assets()

    assert live.exists()
    assert upload.exists()
    assert aruco.exists()
    assert not orphan.exists()
    assert not orphan_full.exists()
    assert removed == 2


def test_sweep_empty_media_is_noop(fresh_settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert R.sweep_orphan_render_assets() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k sweep`
Expected: FAIL — `AttributeError: ... has no attribute 'sweep_orphan_render_assets'`

- [ ] **Step 3: Write minimal implementation**

In `mosaicmesh/render.py`, add a module-level compiled regex near the top of the file with the other constants (after the imports, e.g. alongside `encode_ver`-adjacent constants — anywhere at module scope works; put it just above `_token_is_live`):

```python
import re
# Strict match for a rendered asset filename: seg_/ind_/full_ + 12 hex token +
# _<index> + .mp4/.png. Deliberately strict so the sweep can NEVER match uploaded
# source media (arbitrary names) or aruco.png — only rendered assets.
_RENDER_ASSET_RE = re.compile(r"^(?:seg|ind|full)_([0-9a-f]{12})_\d+\.(?:mp4|png)$")
```

(If `import re` already exists at the top of the module, don't duplicate it — just add the compiled pattern.)

Then add the sweep function (place it after `_delete_token_assets`):

```python
def sweep_orphan_render_assets():
    """One-time boot sweep: delete rendered-asset files under media/ whose token
    is referenced by no live render (registry entry or renderedToken). Best-effort.
    Returns the count of files removed. Walks media/<key>/{videos,images}/ and
    media/server/{videos,images}/; the strict _RENDER_ASSET_RE guards the blast
    radius so uploaded source media and aruco markers are never touched."""
    import server, glob
    live = set()
    for display in server.settings.displays.values():
        for e in (getattr(display, "renders", {}) or {}).values():
            t = e.get("token")
            if t:
                live.add(t)
        rt = getattr(display, "renderedToken", "")
        if rt:
            live.add(rt)
    removed = 0
    for sub in ("videos", "images"):
        for path in glob.glob(os.path.join("media", "*", sub, "*")):
            fname = os.path.basename(path)
            m = _RENDER_ASSET_RE.match(fname)
            if m and m.group(1) not in live:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    return removed
```

Note: `glob.glob("media/*/...")` covers both `media/<clientKey>/...` and `media/server/...` in one pattern — `server` is just another first-level directory.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k sweep`
Expected: PASS (2 passed)

Then the whole new file:

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_gc.py
git commit -m "feat(render): sweep_orphan_render_assets boot GC of unreferenced assets"
```

---

### Task 5: Wire the sweep into boot + re-export

**Files:**
- Modify: `server.py` (re-export import block ~line 73; `__main__` boot block ~line 2363)
- Test: `tests/unit/test_render_gc.py` (importability assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_render_gc.py`:

```python
def test_sweep_reexported_from_server():
    # server.py re-exports render helpers for backward-compat call sites.
    assert hasattr(server, "sweep_orphan_render_assets")
    assert server.sweep_orphan_render_assets is R.sweep_orphan_render_assets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k reexported`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'sweep_orphan_render_assets'`

- [ ] **Step 3: Write minimal implementation**

In `server.py`, find the import from `mosaicmesh.render` that includes `revalidate_renders_on_boot` (~line 73) and add `sweep_orphan_render_assets` to it:

```python
    revalidate_renders_on_boot,
    sweep_orphan_render_assets,
```

Then in the `__main__` boot block, find the `revalidate_renders_on_boot()` call (~line 2362-2365) and add the sweep right after its try/except:

```python
            try:
                revalidate_renders_on_boot()
            except Exception as e:
                logging.error("render revalidation on boot failed: %s", e)
            try:
                _swept = sweep_orphan_render_assets()
                if _swept:
                    logging.info("swept %d orphaned render asset(s) at boot", _swept)
            except Exception as e:
                logging.error("orphan-asset sweep on boot failed: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v -k reexported`
Expected: PASS

Then confirm `server` still imports cleanly and the full GC file passes:

Run: `python -m pytest tests/unit/test_render_gc.py -c tests/pytest.ini -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_render_gc.py
git commit -m "feat(render): run orphan-asset sweep once at server boot"
```

---

### Task 6: Full-suite regression + docs

**Files:**
- Modify: `CLAUDE.md` (one-line note in the render-cleanup description)

- [ ] **Step 1: Run the unit suite**

Run: `python pytest_runner.py --unit`
Expected: PASS (the pre-existing reliably-passing unit set, plus the new `test_render_gc.py`). If any pre-existing failure appears, confirm it also fails on `origin/main` before treating it as a regression.

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, find the auto-render model bullet that documents `revalidate_renders_on_boot()` and `Display.renders` cleanup. Add one sentence:

> Superseded render assets are reclaimed two ways: `render_playlist_for_group_async` deletes the previous token's files once the new token reaches READY (guarded by `_token_is_live` against the shared-token case), and `sweep_orphan_render_assets()` runs once at boot to remove any `seg_/ind_/full_<token>` file whose token is referenced by no live render. Uploaded source media is never swept (operator-driven `DELETE /api/media` only).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document server-side stale render-asset GC"
```

---

## Self-Review

**1. Spec coverage:**
- `_token_is_live` guard → Task 1. ✓
- `_delete_token_assets` refactor of `_delete_render_assets` → Task 2. ✓
- Lifecycle delete-after-READY, capture-prev-token-early, failed-keeps-old → Task 3. ✓
- Boot sweep + strict regex blast-radius guard + boot-only wiring → Tasks 4 & 5. ✓
- Shared-token "must NOT delete" test → Task 3 (`test_rerender_keeps_shared_old_token`). ✓
- Non-matching-file safety (upload, aruco) test → Task 4 (`test_sweep_removes_only_orphan_tokens`). ✓
- Error handling (best-effort `os.remove` in `try/except OSError`) → present in Tasks 2 & 4. ✓
- All tests unit-level, no ffmpeg/SSH (encode monkeypatched) → ✓.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows full code. ✓

**3. Type/name consistency:** `_token_is_live`, `_delete_token_assets`, `sweep_orphan_render_assets`, `_RENDER_ASSET_RE`, `prev_token` used identically across tasks. All token literals in test code are exactly 12 lowercase-hex characters so they satisfy `_RENDER_ASSET_RE`'s `[0-9a-f]{12}` group: `abc123abc123`, `deadbeef0000`, `999999999999`, `aaaaaaaaaaaa`, `bbbbbbbbbbbb`, `0123456789ab`, `abcdefabcdef`, `fedcbafedcba`, `111111111111`. (The lifecycle delete in Tasks 2-3 uses `glob` and would tolerate any string, but the sweep in Task 4 is regex-gated — keeping every token hex avoids a test that passes for the wrong reason.) ✓
