# Play-Type Selection + Always-Encode-for-Device Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Restore an explicit Mesh/Mirror/Per-screen play-type selector in the playlist editor (force a choice, no silent default), and make every media play mode encode for the device — FULL video transcodes to ≤720p Constrained Baseline and FULL images downscale (one shared per-group encode served centrally), so raw source is never sent to iPad-1.

**Architecture:** Server-side, `FULL` joins `SEGMENT`/`INDIVIDUAL` as renderable; `_encode_group` produces a single shared `full_<token>_<i>` asset per (item, group) under `media/server/`, and `_per_client_items` serves it instead of the raw file. Editor-side, a play-type `<select>` writes `it.playmode`, and Save is blocked until every media item has a chosen type. Reuses the auto-render model already shipped (token/queue/READY-gate/status).

**Tech Stack:** Python 3 / aiohttp / OpenCV / ffmpeg (server); Alpine.js 3 + ES modules (admin, no build); pytest (`tests/pytest.ini`), node `--test`, Playwright.

**Spec:** `docs/superpowers/specs/2026-06-14-play-type-and-encode-for-device-design.md`

**Test reminders:** Python unit `python -m pytest tests/unit/<f> -c tests/pytest.ini -v` (never bare pytest); JS `node --test tests/unit/js/<f>.js`; e2e `node tests/e2e/run.js <substr>`. Full-suite baseline is **15 pre-existing failures** (legacy `_begin_prepare`/`_start_group_playback` event-loop-in-sync-test + ReconcileQuad) — do not exceed. Python 3.14: async tests use `asyncio.run(...)`, not `get_event_loop()`.

---

## File Structure

**Modified**
- `mosaicmesh/render.py` — `DEVICE_DECODE_CAP` + `_fit_within`; `build_ffmpeg_transcode_cmd`; `_is_renderable` (allowlist incl. FULL); `_encode_group` FULL branch (shared asset); `_per_client_items` FULL → shared URL.
- `js/timeline/content/content-items.js` — `mediaItemsMissingPlayType(items)` + `playTypeLabel(mode)` pure helpers.
- `js/timeline/modals/playlist-editor.js` — play-type `<select>` for media; Save-disable + row `⚠` when unchosen.
- `CLAUDE.md`, `js/timeline/README.md` — docs.

**New tests**
- `tests/unit/test_encode_for_device.py` (py) — `_fit_within`, `build_ffmpeg_transcode_cmd`, `_is_renderable` FULL, `_encode_group` FULL branch, `_per_client_items` FULL URL.
- `tests/unit/js/play-type.test.js` — editor pure helpers.
- `tests/e2e/test-play-type.spec.js` — selector + force-a-choice.

---

## Phase A — Server: always-encode-for-device

### Task 1: `DEVICE_DECODE_CAP` + `_fit_within`

**Files:** Modify `mosaicmesh/render.py`; Test `tests/unit/test_encode_for_device.py` (new).

- [ ] **Step 1: Write the failing test** (`tests/unit/test_encode_for_device.py`):

```python
"""Always-encode-for-device: fit helper, transcode cmd, FULL render path."""
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
from mosaicmesh import render as R


def test_fit_within_downscales_keeping_aspect():
    # 1920x1080 into 1280x720 cap → 1280x720
    assert R._fit_within(1920, 1080, (1280, 720)) == (1280, 720)

def test_fit_within_portrait():
    # 1080x1920 into 1280x720 → limited by height: w = 720*1080/1920=405 → even 404/406? keep even
    w, h = R._fit_within(1080, 1920, (1280, 720))
    assert h == 720 and w % 2 == 0 and w <= 1280 and abs(w/h - 1080/1920) < 0.02

def test_fit_within_no_upscale():
    assert R._fit_within(640, 480, (1280, 720)) == (640, 480)

def test_fit_within_even_dims():
    w, h = R._fit_within(1001, 333, (1280, 720))
    assert w % 2 == 0 and h % 2 == 0
```

- [ ] **Step 2: Run to fail** — `python -m pytest tests/unit/test_encode_for_device.py -c tests/pytest.ini -v` → FAIL (`_fit_within` / `DEVICE_DECODE_CAP` missing).

- [ ] **Step 3: Implement** in `mosaicmesh/render.py` (after `_video_encoder_args`, near line 185):

```python
# Largest frame iPad-1 (iOS 5 / WebKit 534) reliably decodes: ~720p H.264
# Constrained Baseline. FULL-mode media is transcoded/downscaled to fit within
# this so raw source is never served to the wall.
DEVICE_DECODE_CAP = (1280, 720)


def _fit_within(src_w, src_h, cap):
    """Scale (src_w, src_h) to fit within cap=(W,H) preserving aspect, never
    upscaling. Returns even integer dims (H.264 requires even W/H)."""
    cw, ch = cap
    sw, sh = int(src_w or 0), int(src_h or 0)
    if sw <= 0 or sh <= 0:
        return (cw, ch)
    scale = min(cw / sw, ch / sh, 1.0)   # 1.0 cap → never upscale
    w = max(2, int(sw * scale)); h = max(2, int(sh * scale))
    if w % 2: w -= 1
    if h % 2: h -= 1
    return (w, h)
```

- [ ] **Step 4: Run to pass** — `python -m pytest tests/unit/test_encode_for_device.py -c tests/pytest.ini -v` → PASS (4).

- [ ] **Step 5: Commit**
```bash
git add mosaicmesh/render.py tests/unit/test_encode_for_device.py
git commit -m "feat(render): DEVICE_DECODE_CAP + _fit_within (device-fit dims)"
```

---

### Task 2: `build_ffmpeg_transcode_cmd`

**Files:** Modify `mosaicmesh/render.py`; Test append to `tests/unit/test_encode_for_device.py`.

**Context:** Mirrors `build_ffmpeg_perspective_cmd` (render.py:218) but scales+pads (letterbox) instead of perspective-warping. Reuses `_video_input_args`, `_video_encoder_args`, `_keyframe_grid_args` (all exist).

- [ ] **Step 1: Append the failing test:**

```python
def test_build_transcode_cmd_shape():
    cmd = R.build_ffmpeg_transcode_cmd("/src/a.mp4", "/out/full_tok_0.mp4", 1280, 720)
    assert cmd[0] == "ffmpeg"
    assert "/src/a.mp4" in cmd and "/out/full_tok_0.mp4" == cmd[-1]
    j = " ".join(cmd)
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in j
    assert "pad=1280:720" in j
    assert "-profile:v baseline" in j

def test_build_transcode_cmd_extra_filters():
    cmd = R.build_ffmpeg_transcode_cmd("/s.mp4", "/o.mp4", 640, 480,
                                       extra_video_filters=["fade=in:0:30"])
    assert "fade=in:0:30" in " ".join(cmd)
```

- [ ] **Step 2: Run to fail** — `python -m pytest tests/unit/test_encode_for_device.py::test_build_transcode_cmd_shape -c tests/pytest.ini -v` → FAIL.

- [ ] **Step 3: Implement** in `mosaicmesh/render.py` (after `build_ffmpeg_individual_cmd`, near line 271):

```python
def build_ffmpeg_transcode_cmd(src_path, out_path, out_w, out_h,
                               extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg args for FULL (mirror): scale the source to fit out_w x out_h
    preserving aspect, letterbox-pad to exactly out_w x out_h, encode iPad-1
    Constrained Baseline H.264 + AAC. No perspective warp. Mirrors the encode
    conventions of build_ffmpeg_perspective_cmd."""
    vf = ("scale=" + str(out_w) + ":" + str(out_h) +
          ":force_original_aspect_ratio=decrease," +
          "pad=" + str(out_w) + ":" + str(out_h) + ":(ow-iw)/2:(oh-ih)/2:color=0x000000")
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y"] + _video_input_args() + ["-i", src_path, "-vf", vf]
    cmd += _video_encoder_args()
    cmd += ["-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += _keyframe_grid_args()
    cmd += ["-movflags", "+faststart", out_path]
    return cmd
```

- [ ] **Step 4: Run to pass** — `python -m pytest tests/unit/test_encode_for_device.py -c tests/pytest.ini -v` → PASS (6).

- [ ] **Step 5: Commit**
```bash
git add mosaicmesh/render.py tests/unit/test_encode_for_device.py
git commit -m "feat(render): build_ffmpeg_transcode_cmd for FULL device encode"
```

---

### Task 3: `_is_renderable` allowlist includes FULL

**Files:** Modify `mosaicmesh/render.py:333` (`_is_renderable`); Test append.

**Context:** Currently `return me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL)`. Adding FULL makes mirror content render. This automatically enrolls FULL in the auto-render model (triggers/gates already call `_is_renderable`). `PlayMode` is imported in render.py.

- [ ] **Step 1: Append the failing test:**

```python
def test_is_renderable_includes_full():
    from mosaicmesh.state import MediaElement, PlayMode
    def me(pm):
        m = MediaElement(); m.playmode = pm; m.file = "/media/server/videos/a.mp4"; return m
    assert R._is_renderable(me(PlayMode.SEGMENT)) is True
    assert R._is_renderable(me(PlayMode.INDIVIDUAL)) is True
    assert R._is_renderable(me(PlayMode.FULL)) is True
    assert R._is_renderable(me(PlayMode.SCRIPT)) is False
    assert R._is_renderable(me(PlayMode.DEFAULT)) is False
```

- [ ] **Step 2: Run to fail** — `python -m pytest tests/unit/test_encode_for_device.py::test_is_renderable_includes_full -c tests/pytest.ini -v` → FAIL (FULL returns False).

- [ ] **Step 3: Implement** — replace `_is_renderable` body in `mosaicmesh/render.py`:

```python
def _is_renderable(me):
    """SEGMENT, INDIVIDUAL, and FULL all require a server-side encode for the
    device (per-screen warp for SEGMENT/INDIVIDUAL; a shared device transcode/
    downscale for FULL). SCRIPT (animations) and bare DEFAULT do not render."""
    return me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL, PlayMode.FULL)
```

- [ ] **Step 4: Run to pass** — `python -m pytest tests/unit/test_encode_for_device.py -c tests/pytest.ini -v` → PASS. Then regression: `python -m pytest tests/unit -c tests/pytest.ini -k "render or playlist or mosaic or schedul" -q 2>&1 | tail -4` — note the count; **FULL is now renderable, which changes some existing assertions** (e.g. tests that assumed a FULL item is "nothing to render"). If a test breaks because it encoded the OLD assumption (FULL not renderable), update it to the new contract and report it. Do NOT exceed the established failure baseline beyond such legitimate contract updates.

- [ ] **Step 5: Commit**
```bash
git add mosaicmesh/render.py tests/unit/test_encode_for_device.py <any updated tests>
git commit -m "feat(render): _is_renderable allowlist now includes FULL (device encode)"
```

---

### Task 4: `_encode_group` FULL branch (shared device asset)

**Files:** Modify `mosaicmesh/render.py` (`_encode_group`, ~line 410); Test append.

**Context:** `_encode_group(media_elements, display_id, token, progress_cb)` loops renderable items, producing per-client `seg_`/`ind_` files. FULL must produce ONE shared asset under `media/server/` (not per-client). Read the current `_encode_group` first. Add a FULL branch at the TOP of the per-item loop that `continue`s after producing the shared asset, leaving the existing SEGMENT/INDIVIDUAL per-client logic untouched. Helpers in scope: `isVideoItem`, `get_video_dimensions`, `resolve_media_path`, `_resolve_effect_filters`, `cv`, `os`, `Path`, `_fit_within`, `DEVICE_DECODE_CAP`, `build_ffmpeg_transcode_cmd`.

- [ ] **Step 1: Append the failing test** (mocks ffmpeg + cv so no real encode):

```python
import asyncio

def test_encode_group_full_writes_shared_asset(tmp_path, monkeypatch):
    from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    try:
        d = Display(); d.boundingBox = [0, 0, 10, 10]
        server.settings.displays["G1"] = d
        c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
        c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
        server.settings.clients["c1"] = c
        me = MediaElement(); me.id = 0; me.file = "/media/server/videos/a.mp4"
        me.playmode = PlayMode.FULL; me.duration = 5

        captured = {}
        monkeypatch.setattr(R, "resolve_media_path", lambda f: "/abs/a.mp4")
        monkeypatch.setattr(R, "get_video_dimensions", lambda p: (1920, 1080))
        def _fake_cmd(src, out, w, h, **kw):
            captured["out"] = out; captured["wh"] = (w, h); return ["ffmpeg", out]
        monkeypatch.setattr(R, "build_ffmpeg_transcode_cmd", _fake_cmd)
        async def _fake_run(cmd, label, sem): captured["ran"] = True
        monkeypatch.setattr(R, "_run_ffmpeg", _fake_run)

        asyncio.run(R._encode_group([me], "G1", "tok123"))
        assert captured.get("ran") is True
        # shared, central, token-keyed path — NOT a per-client dir
        assert captured["out"].replace("\\", "/").endswith("media/server/videos/full_tok123_0.mp4")
        assert captured["wh"] == (1280, 720)   # _fit_within(1920,1080,cap)
    finally:
        server.settings = prev
```

- [ ] **Step 2: Run to fail** — `python -m pytest tests/unit/test_encode_for_device.py::test_encode_group_full_writes_shared_asset -c tests/pytest.ini -v` → FAIL (FULL currently goes through the per-client SEGMENT path / wrong path).

- [ ] **Step 3: Implement** — in `_encode_group`, at the very start of the `for i, me in seg_items:` loop body, add the FULL branch (before the existing `src_path = resolve_media_path(...)` per-client logic):

```python
        if me.playmode == PlayMode.FULL:
            # Mirror: ONE shared device-decodable asset for the whole group.
            src_path = resolve_media_path(me.file)
            if isVideoItem(me.file):
                dims = get_video_dimensions(src_path) if src_path else None
                if not dims:
                    raise RuntimeError("cannot read source video: " + str(me.file))
                tw, th = _fit_within(dims[0], dims[1], DEVICE_DECODE_CAP)
                out_dir = os.path.join("media", "server", "videos")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_path = os.path.join(out_dir, "full_" + token + "_" + str(i) + ".mp4")
                evf, eaf = _resolve_effect_filters(me, me.duration, tw, th)
                cmd = build_ffmpeg_transcode_cmd(src_path, out_path, tw, th,
                                                 extra_video_filters=evf, extra_audio_filters=eaf)
                video_jobs.append((cmd, "server/full_" + str(i)))
            else:
                img = cv.imread(src_path) if src_path else None
                if img is None:
                    raise RuntimeError("cannot read source image: " + str(me.file))
                sh, sw = img.shape[:2]
                tw, th = _fit_within(sw, sh, DEVICE_DECODE_CAP)
                out_dir = os.path.join("media", "server", "images")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_path = os.path.join(out_dir, "full_" + token + "_" + str(i) + ".png")
                if (tw, th) != (sw, sh):
                    img = cv.resize(img, (tw, th), interpolation=cv.INTER_AREA)
                cv.imwrite(out_path, img)
            continue
```

Note: `video_jobs` is the existing batch list `_encode_group` gathers under the concurrency semaphore — appending the FULL job means it runs + reports progress with the others. The `seg_push_targets` cache-push list is intentionally NOT touched for FULL (served centrally; caching is track C).

- [ ] **Step 4: Run to pass** — `python -m pytest tests/unit/test_encode_for_device.py -c tests/pytest.ini -v` → PASS. Regression: `python -m pytest tests/unit -c tests/pytest.ini -k "render" -q 2>&1 | tail -4`.

- [ ] **Step 5: Commit**
```bash
git add mosaicmesh/render.py tests/unit/test_encode_for_device.py
git commit -m "feat(render): _encode_group renders shared FULL device asset under media/server"
```

---

### Task 5: `_per_client_items` serves the shared FULL asset

**Files:** Modify `mosaicmesh/render.py` (`_per_client_items`, ~line 1000); Test append.

**Context:** Read the current `_per_client_items`. It currently does: if `_is_renderable(me) and c.measuredPerimeter is not None` → per-client `seg_`/`ind_` URL (with lighttpd-localhost rewrite); else `f = me.file`. Now FULL is `_is_renderable` but must resolve to the shared central asset, never the raw file or a per-client path.

- [ ] **Step 1: Append the failing test:**

```python
def test_per_client_items_full_uses_shared_central_asset():
    from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    try:
        d = Display(); d.boundingBox = [0, 0, 10, 10]; d.renderedToken = "tok9"; d.loop = False
        server.settings.displays["G1"] = d
        c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
        c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]; c.cacheMode = "none"
        server.settings.clients["c1"] = c
        me = MediaElement(); me.id = 0; me.file = "/media/server/videos/big.mov"
        me.playmode = PlayMode.FULL; me.duration = 5
        d.mediaElements = [me]
        items = R._per_client_items(d, "c1", c)
        assert items[0]["file"] == "/media/server/videos/full_tok9_0.mp4"
        assert items[0]["file"] != me.file        # never the raw source
    finally:
        server.settings = prev
```

- [ ] **Step 2: Run to fail** — `python -m pytest tests/unit/test_encode_for_device.py::test_per_client_items_full_uses_shared_central_asset -c tests/pytest.ini -v` → FAIL (currently returns raw `me.file`, since FULL+calibrated would try the seg_ per-client path or fall to raw).

- [ ] **Step 3: Implement** — in `_per_client_items`, restructure the per-item branch so FULL is handled first:

```python
    for i, me in enumerate(display.mediaElements):
        if me.playmode == PlayMode.FULL:
            # Mirror: shared central device asset (Task 4 wrote it).
            ext = ".mp4" if isVideoItem(me.file) else ".png"
            sub = "videos" if ext == ".mp4" else "images"
            f = "/media/server/" + sub + "/full_" + token + "_" + str(i) + ext
        elif _is_renderable(me) and c.measuredPerimeter is not None:
            prefix = "ind_" if me.playmode == PlayMode.INDIVIDUAL else "seg_"
            ext = ".mp4" if isVideoItem(me.file) else ".png"
            seg_key = "%s_%d" % (token, i)
            if (prefix == "seg_" and cache_on and seg_key in cached):
                f = "http://127.0.0.1:8080/seg_" + seg_key + ".mp4"
            else:
                f = "/media/" + key + "/" + prefix + token + "_" + str(i) + ext
        else:
            f = me.file  # SCRIPT animation ref, or uncalibrated SEGMENT/INDIVIDUAL fallback
        item = _media_item_payload(me)
        item["file"] = f
        items.append(item)
    return items
```

(Read the current loop and preserve the surrounding `token = display.renderedToken`, `cache_on`, `cached` setup above it. Only the per-item branch changes — add the FULL case first.)

- [ ] **Step 4: Run to pass** — `python -m pytest tests/unit/test_encode_for_device.py -c tests/pytest.ini -v` → PASS. Regression: `python -m pytest tests/unit -c tests/pytest.ini -k "render or mosaic or playlist" -q 2>&1 | tail -4` — stay at baseline (+ any legitimate FULL-contract test updates from Task 3).

- [ ] **Step 5: Commit**
```bash
git add mosaicmesh/render.py tests/unit/test_encode_for_device.py
git commit -m "feat(render): _per_client_items serves shared FULL device asset, never raw"
```

---

## Phase B — Editor: play-type selector + force-a-choice

### Task 6: editor pure helpers (`mediaItemsMissingPlayType`, `playTypeLabel`)

**Files:** Modify `js/timeline/content/content-items.js`; Test `tests/unit/js/play-type.test.js` (new).

**Context:** `content-items.js` exports `buildContentItems` + `contentItemToPlaylistItem`. `isAnim` lives in playlist-editor.js as `it.playmode === 'SCRIPT'`. Add node-testable helpers here.

- [ ] **Step 1: Write the failing test** (`tests/unit/js/play-type.test.js`):

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
import { mediaItemsMissingPlayType, playTypeLabel } from '../../../js/timeline/content/content-items.js';

test('mediaItemsMissingPlayType: media without playmode is flagged', () => {
  const items = [{ file: 'a.mp4' }, { file: 'b.png', playmode: 'SEGMENT' }];
  const missing = mediaItemsMissingPlayType(items);
  assert.equal(missing.length, 1);
  assert.equal(missing[0].file, 'a.mp4');
});

test('mediaItemsMissingPlayType: animations are exempt', () => {
  assert.equal(mediaItemsMissingPlayType([{ file: 'x', playmode: 'SCRIPT' }]).length, 0);
});

test('mediaItemsMissingPlayType: all valid modes satisfy', () => {
  const items = [{file:'a',playmode:'SEGMENT'},{file:'b',playmode:'FULL'},{file:'c',playmode:'INDIVIDUAL'}];
  assert.equal(mediaItemsMissingPlayType(items).length, 0);
});

test('playTypeLabel maps modes to labels', () => {
  assert.equal(playTypeLabel('SEGMENT'), 'Mesh');
  assert.equal(playTypeLabel('FULL'), 'Mirror');
  assert.equal(playTypeLabel('INDIVIDUAL'), 'Per-screen');
  assert.equal(playTypeLabel('SCRIPT'), 'Animation');
  assert.equal(playTypeLabel(undefined), '— pick play type —');
});
```

- [ ] **Step 2: Run to fail** — `node --test tests/unit/js/play-type.test.js` → FAIL (exports missing).

- [ ] **Step 3: Implement** — append to `js/timeline/content/content-items.js`:

```javascript
// Play-type vocabulary shared by the editor. Maps PlayMode → operator label.
const PLAY_TYPE_LABELS = { SEGMENT: 'Mesh', FULL: 'Mirror', INDIVIDUAL: 'Per-screen', SCRIPT: 'Animation' };
const MEDIA_PLAY_TYPES = ['SEGMENT', 'FULL', 'INDIVIDUAL'];

export function playTypeLabel(mode) {
  return PLAY_TYPE_LABELS[mode] || '— pick play type —';
}

// Media items (non-animation) must have an explicit, valid play type before a
// playlist can be saved/played. Returns the items still needing a choice.
export function mediaItemsMissingPlayType(items) {
  return (items || []).filter(
    (it) => it.playmode !== 'SCRIPT' && !MEDIA_PLAY_TYPES.includes(it.playmode));
}
```

- [ ] **Step 4: Run to pass** — `node --test tests/unit/js/play-type.test.js` → PASS (4). Then `node --test tests/unit/js/*.js` → all pass.

- [ ] **Step 5: Commit**
```bash
git add js/timeline/content/content-items.js tests/unit/js/play-type.test.js
git commit -m "feat(content): mediaItemsMissingPlayType + playTypeLabel helpers"
```

---

### Task 7: play-type `<select>` + Save-disable in the editor

**Files:** Modify `js/timeline/modals/playlist-editor.js`.

**Context:** In `openPlaylistEditor`, the selected-item sidebar `box` (playlist-editor.js:146-176) currently builds Duration + Background fields. Add a Play-type `<select>` for non-animation items, BEFORE the Duration field. Then disable the modal Save while `mediaItemsMissingPlayType(draft.items)` is non-empty. READ the file to find: the `render()` function, `isAnim`, `draft`, `updateRowMeta`, and how Save is wired (the modal Save button — likely via `openModal({..., onSave})` or a footer button created in this file). `import { ..., mediaItemsMissingPlayType, playTypeLabel } from '../content/content-items.js'` — add to the existing import.

- [ ] **Step 1: Add the selector** — in the `if (selectedIdx >= 0)` block, after `const box = ...` and BEFORE the Duration `durWrap`, insert:

```javascript
      // Play type — media only (animations are implicitly SCRIPT).
      // Mesh=SEGMENT, Mirror=FULL, Per-screen=INDIVIDUAL. No silent default:
      // an unchosen media item blocks Save (mediaItemsMissingPlayType).
      if (!isAnim(it)) {
        const ptWrap = document.createElement('label'); ptWrap.textContent = 'Play type ';
        const pt = document.createElement('select');
        const opts = [['', '— pick play type —'], ['SEGMENT', 'Mesh (across the wall)'],
                      ['FULL', 'Mirror (same on every screen)'], ['INDIVIDUAL', 'Per-screen (warped to calibration)']];
        for (const [val, label] of opts) {
          const o = document.createElement('option');
          o.value = val; o.textContent = label;
          if (val === '') o.disabled = true;
          if ((it.playmode || '') === val) o.selected = true;
          pt.appendChild(o);
        }
        if (!['SEGMENT', 'FULL', 'INDIVIDUAL'].includes(it.playmode)) pt.value = '';
        pt.addEventListener('change', () => {
          if (pt.value) it.playmode = pt.value; else delete it.playmode;
          render();   // re-render to update the ⚠ marker + Save state
        });
        ptWrap.appendChild(pt); box.appendChild(ptWrap);
      }
```

- [ ] **Step 2: Save-disable wiring** — locate where the modal Save control is created/updated in this file. After `render()` builds the DOM (end of `render()`), compute and apply the disabled state. Add near the end of `render()` (before it returns / after `root` is assembled):

```javascript
      const missing = mediaItemsMissingPlayType(draft.items);
      // Find the modal's Save button (modal-shell renders it in the footer).
      const saveBtn = document.querySelector('#mmModalHost .mm-modal-save, #mmModalHost [data-role="save"]');
      if (saveBtn) {
        saveBtn.disabled = missing.length > 0;
        saveBtn.title = missing.length ? ('Pick a play type for ' + missing.length + ' item(s)') : '';
      }
```

If the Save button is created locally in this file (not via modal-shell), disable that element instead — READ the file and adapt the selector to the actual Save control. The invariant: Save is disabled iff `mediaItemsMissingPlayType(draft.items).length > 0`.

- [ ] **Step 3: Row ⚠ marker** — in the item-list row build (the `list` loop, playlist-editor.js ~line 100-140), append a warning marker when the row's item needs a type. After the row's label/meta is built, add:

```javascript
        if (!isAnim(it) && !['SEGMENT','FULL','INDIVIDUAL'].includes(it.playmode)) {
          const warn = document.createElement('span');
          warn.className = 'mm-ple-warn'; warn.textContent = '⚠'; warn.title = 'pick a play type';
          row.appendChild(warn);
        }
```

(Adapt `row` to the actual row element variable name in the list loop.)

- [ ] **Step 4: Verify** — `node --test tests/unit/js/*.js` → all pass (module-load smoke confirms playlist-editor.js still imports). DOM behavior is covered by Task 8 e2e.

- [ ] **Step 5: Commit**
```bash
git add js/timeline/modals/playlist-editor.js
git commit -m "feat(content): play-type selector (Mesh/Mirror/Per-screen) + Save-gate in editor"
```

---

### Task 8: e2e — selector + force-a-choice

**Files:** Create `tests/e2e/test-play-type.spec.js` (mirror the existing e2e harness structure).

- [ ] **Step 1: Write the spec.** READ an existing spec (e.g. `tests/e2e/test-content-tab.spec.js`) for the harness API (`cleanupE2eOrphans`, `__e2e_` prefix, page helpers, REST create/delete). Assertions:
  1. Create `__e2e_pt` playlist via REST with one video item that has **no** playmode.
  2. Open the playlist editor for it; select the video item; assert the **Save** button is **disabled** and a `⚠` marker is present.
  3. Choose **Mesh** in the play-type select; assert Save becomes **enabled** and the `⚠` clears.
  4. Cleanup: delete `__e2e_pt` via REST (in a `finally`).

- [ ] **Step 2: Run** — `node tests/e2e/run.js play-type` (server + chromium up) → iterate to green. If infra absent, `node --check tests/e2e/test-play-type.spec.js` and report DONE_WITH_CONCERNS.

- [ ] **Step 3: Commit**
```bash
git add tests/e2e/test-play-type.spec.js
git commit -m "test(e2e): play-type selector force-a-choice (Save gated until picked)"
```

---

## Phase C — Verify + docs

### Task 9: full suite + docs + final review

**Files:** `CLAUDE.md`, `js/timeline/README.md`.

- [ ] **Step 1: Full suites** — `python pytest_runner.py --unit` (expect baseline 15 failures + the new `test_encode_for_device.py` green + any legitimate FULL-contract test updates) and `node --test tests/unit/js/*.js` (all pass). Report counts.

- [ ] **Step 2: Docs** — add to `CLAUDE.md` Conventions:

```
- **Media play types + always-encode-for-device.** Playlist items carry a `playmode`: `SEGMENT` (Mesh — warped across the calibrated wall), `INDIVIDUAL` (Per-screen — warped to each screen's quad), `FULL` (Mirror — same content on every screen), or `SCRIPT` (animation). The editor forces an explicit choice for media (no silent default). All non-SCRIPT modes are renderable (`render._is_renderable`): SEGMENT/INDIVIDUAL render per-client; FULL renders ONE shared `media/server/{videos,images}/full_<token>_<i>` device encode (≤720p Constrained Baseline via `build_ffmpeg_transcode_cmd`, images downscaled via `_fit_within`/`DEVICE_DECODE_CAP`). `_per_client_items` serves the shared FULL asset — raw source is never sent to iPad-1. FULL flows through the auto-render model like any renderable item.
```

Add to `js/timeline/README.md` (content section): `content-items.js` now exports `mediaItemsMissingPlayType` + `playTypeLabel`; the playlist editor has a Mesh/Mirror/Per-screen selector that gates Save.

- [ ] **Step 3: Commit**
```bash
git add CLAUDE.md js/timeline/README.md
git commit -m "docs(content): document play types + always-encode-for-device"
```

- [ ] **Step 4: Final review + finish** — dispatch a final review across the feature, then use `superpowers:finishing-a-development-branch`.

---

## Self-Review

**Spec coverage:**
- Goal 1 (selector restore): Task 7. ✓
- Goal 2 (force a choice): Task 6 (`mediaItemsMissingPlayType`) + Task 7 (Save-gate + ⚠) + Task 8 (e2e). ✓
- Goal 3 (always encode for device): Task 1 (`_fit_within`/CAP), Task 2 (transcode cmd), Task 3 (`_is_renderable` FULL), Task 4 (`_encode_group` FULL shared asset, video transcode + image downscale), Task 5 (`_per_client_items` serves it, never raw). ✓
- Goal 4 (auto-render tie-in): automatic — Task 3 makes FULL `_is_renderable`, so the shipped triggers/gates/queue enroll it; no extra task needed. ✓
- Spec "FULL one shared per-group encode served centrally": Task 4 (`media/server/full_<token>_<i>`) + Task 5. ✓
- Spec "FULL images downscale": Task 4 image branch (`cv.resize` INTER_AREA, no upscale). ✓
- Non-goal (caching): untouched — Task 4 explicitly skips `seg_push_targets` for FULL. ✓

**Known limitation (documented, not a gap):** FULL still renders within the calibrated-group flow (auto-render triggers + the ASSIGN `NOT_CALIBRATED` gate are calibrated-only). Mirror conceptually needs no calibration; "FULL on an uncalibrated group" is out of scope here (the target wall is calibrated). Flag at review.

**Placeholder scan:** none — every code step has complete code; "adapt to actual Save control / row variable" steps are explicit instructions to match real local names, not vague TODOs.

**Type consistency:** `_fit_within(src_w, src_h, cap)→(w,h)`, `DEVICE_DECODE_CAP=(1280,720)`, `build_ffmpeg_transcode_cmd(src,out,out_w,out_h,extra_video_filters,extra_audio_filters)`, `_is_renderable` allowlist, `full_<token>_<i>.{mp4,png}` path, `media/server/{videos,images}` — all consistent across Tasks 1–5. JS `mediaItemsMissingPlayType(items)`/`playTypeLabel(mode)` consistent across Tasks 6–8. Play-type values `SEGMENT|FULL|INDIVIDUAL` consistent editor↔server.
