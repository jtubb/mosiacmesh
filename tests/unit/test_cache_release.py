# tests/unit/test_cache_release.py
"""release_file must close a pooled handle so a served media file can be
deleted on Windows (WinError 32 when a handle is still open). The crux is the
PATH-FORM mismatch: media_handler pools under a forward-slash relative path,
but the DELETE handler passes an os.path.join (backslash on Windows) path —
release_file matches by normalized path so they line up."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mosaicmesh import cache


def test_release_file_closes_pooled_handle_across_path_forms(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache.file_handle_pool.clear()
    os.makedirs(os.path.join("media", "server", "videos"))
    with open(os.path.join("media", "server", "videos", "clip.mp4"), "wb") as f:
        f.write(b"x" * 16)

    # media_handler pools under a forward-slash relative path.
    h = cache.get_pooled_file_handle("media/server/videos/clip.mp4", "rb")
    assert any("clip.mp4" in k for k in cache.file_handle_pool)

    # DELETE handler passes an os.path.join path (backslashes on Windows).
    cache.release_file(os.path.join("media", "server", "videos", "clip.mp4"))

    assert h.closed, "pooled handle should be closed after release_file"
    assert not any("clip.mp4" in k for k in cache.file_handle_pool), \
        "handle should be evicted from the pool"
    # And the file is now deletable (no open handle holding it).
    os.remove(os.path.join("media", "server", "videos", "clip.mp4"))


def test_release_file_noop_when_not_pooled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache.file_handle_pool.clear()
    # No handle pooled for this path — release_file must not raise.
    cache.release_file(os.path.join("media", "server", "videos", "absent.mp4"))
