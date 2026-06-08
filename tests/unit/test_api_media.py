"""Unit tests for /api/media + /api/upload (relocated from server.py).

Relocation tests — assert handlers are importable from the new module and
that the existing api_media response shape is preserved. The full upload
path is exercised by the existing test_api_endpoints.py suite (which
calls server.upload_handler that resolves through the re-import).
"""
import json
import os
import tempfile
import pytest
from aiohttp.test_utils import make_mocked_request

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args

class _MockArgs:
    Port = 3000
    Verbose = False

argparse.ArgumentParser.parse_args = lambda self, args=None, namespace=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.api.media import api_media, api_media_delete, upload_handler
from mosaicmesh.state import Settings, Playlist


def test_media_handlers_importable():
    """Smoke check: both handlers are callable from mosaicmesh.api.media."""
    assert callable(api_media)
    assert callable(upload_handler)


def test_server_reexports_media_handlers():
    """server.py still exposes api_media + upload_handler so existing
    route bindings + tests calling server.X continue to work."""
    assert server.api_media is api_media
    assert server.upload_handler is upload_handler


class TestApiMediaResponseShape:
    @pytest.mark.asyncio
    async def test_lists_empty_directories(self, tmp_path, monkeypatch):
        """Empty media/server/{images,videos} returns empty lists."""
        # Run from a tmp_path so media/server doesn't exist
        monkeypatch.chdir(tmp_path)
        resp = await api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert data['images'] == []
        assert data['videos'] == []
        assert data['videoDurations'] == {}

    @pytest.mark.asyncio
    async def test_lists_image_files(self, tmp_path, monkeypatch):
        d = tmp_path / "media" / "server" / "images"
        d.mkdir(parents=True)
        (d / "a.png").write_bytes(b"\x89PNG")
        (d / "b.jpg").write_bytes(b"\xff\xd8\xff")
        monkeypatch.chdir(tmp_path)
        resp = await api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert "/media/server/images/a.png" in data['images']
        assert "/media/server/images/b.jpg" in data['images']
        assert data['videos'] == []

    @pytest.mark.asyncio
    async def test_lists_videos_with_durations(self, tmp_path, monkeypatch):
        """Populated videos dir + stubbed get_video_duration → videoDurations
        is keyed by URL and rounded to 1 decimal."""
        d = tmp_path / "media" / "server" / "videos"
        d.mkdir(parents=True)
        (d / "intro.mp4").write_bytes(b"\x00\x00\x00\x20ftyp")
        (d / "outro.mp4").write_bytes(b"\x00\x00\x00\x20ftyp")
        monkeypatch.chdir(tmp_path)

        async def fake_duration(disk_path):
            return 12.345 if disk_path.endswith("intro.mp4") else 8.0
        monkeypatch.setattr(server, "get_video_duration", fake_duration)

        resp = await api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert "/media/server/videos/intro.mp4" in data['videos']
        assert "/media/server/videos/outro.mp4" in data['videos']
        assert data['videoDurations']["/media/server/videos/intro.mp4"] == 12.3
        assert data['videoDurations']["/media/server/videos/outro.mp4"] == 8.0

    @pytest.mark.asyncio
    async def test_skips_videos_with_unknown_duration(self, tmp_path, monkeypatch):
        """get_video_duration returning None (e.g. ffprobe missing) → that
        video appears in `videos` but not in `videoDurations`."""
        d = tmp_path / "media" / "server" / "videos"
        d.mkdir(parents=True)
        (d / "bad.mp4").write_bytes(b"not-a-video")
        monkeypatch.chdir(tmp_path)

        async def fake_duration(disk_path):
            return None
        monkeypatch.setattr(server, "get_video_duration", fake_duration)

        resp = await api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert "/media/server/videos/bad.mp4" in data['videos']
        assert data['videoDurations'] == {}


# ---- PR-16: DELETE /api/media ----

def _delete_request(body):
    from unittest.mock import AsyncMock
    req = make_mocked_request('DELETE', '/api/media')
    req.json = AsyncMock(return_value=body)
    return req


def _fresh_settings():
    """Fresh Settings() + drop it on server.settings; restored by the
    fixture caller via the `monkeypatch` they pass in here."""
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    return server.settings, prev


def _restore_settings(prev):
    server.settings = prev


class TestApiMediaDelete:

    @pytest.mark.asyncio
    async def test_delete_image_happy(self, tmp_path, monkeypatch):
        d = tmp_path / "media" / "server" / "images"
        d.mkdir(parents=True)
        (d / "logo.png").write_bytes(b"\x89PNG")
        monkeypatch.chdir(tmp_path)
        fresh, prev = _fresh_settings()
        try:
            resp = await api_media_delete(_delete_request({"url": "/media/server/images/logo.png"}))
            assert resp.status == 204
            assert not (d / "logo.png").exists()
        finally:
            _restore_settings(prev)

    @pytest.mark.asyncio
    async def test_delete_video_happy(self, tmp_path, monkeypatch):
        d = tmp_path / "media" / "server" / "videos"
        d.mkdir(parents=True)
        (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x20ftyp")
        monkeypatch.chdir(tmp_path)
        fresh, prev = _fresh_settings()
        try:
            resp = await api_media_delete(_delete_request({"url": "/media/server/videos/clip.mp4"}))
            assert resp.status == 204
        finally:
            _restore_settings(prev)

    @pytest.mark.asyncio
    async def test_delete_missing_file_404(self, tmp_path, monkeypatch):
        (tmp_path / "media" / "server" / "images").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        fresh, prev = _fresh_settings()
        try:
            resp = await api_media_delete(_delete_request({"url": "/media/server/images/ghost.png"}))
            assert resp.status == 404
        finally:
            _restore_settings(prev)

    @pytest.mark.asyncio
    async def test_delete_referenced_409_with_refs(self, tmp_path, monkeypatch):
        d = tmp_path / "media" / "server" / "videos"
        d.mkdir(parents=True)
        (d / "intro.mp4").write_bytes(b"\x00\x00\x00\x20ftyp")
        monkeypatch.chdir(tmp_path)
        fresh, prev = _fresh_settings()
        try:
            # Two playlists reference the file; one doesn't.
            p1 = Playlist(); p1.name = "MorningLoop"
            p1.items = [{"file": "/media/server/videos/intro.mp4", "duration": 10}]
            p2 = Playlist(); p2.name = "EveningLoop"
            p2.items = [{"file": "/media/server/videos/intro.mp4", "duration": 5},
                        {"file": "/media/server/images/logo.png", "duration": 2}]
            p3 = Playlist(); p3.name = "Other"
            p3.items = [{"file": "/media/server/images/logo.png", "duration": 2}]
            fresh.playlists["MorningLoop"] = p1
            fresh.playlists["EveningLoop"] = p2
            fresh.playlists["Other"] = p3

            resp = await api_media_delete(_delete_request({"url": "/media/server/videos/intro.mp4"}))
            assert resp.status == 409
            data = json.loads(resp.text)
            assert set(data['refs']) == {"MorningLoop", "EveningLoop"}
            assert "Other" not in data['refs']
            # File still present
            assert (d / "intro.mp4").exists()
        finally:
            _restore_settings(prev)

    @pytest.mark.asyncio
    async def test_delete_rejects_bad_url_shape(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fresh, prev = _fresh_settings()
        try:
            for bad in [
                None,
                "",
                "/etc/passwd",                          # outside /media/server
                "/media/server/videos/../../etc/passwd",  # traversal
                "/media/server/audio/foo.mp3",          # unknown subdir
                "/media/server/videos/",                # empty filename
                "/media/server/videos/.hidden",         # dotfile
                "/media/server/videos/sub/foo.mp4",    # nested
                "/media/server/videos/foo\\bar.mp4",   # backslash
            ]:
                resp = await api_media_delete(_delete_request({"url": bad}))
                assert resp.status == 400, f"expected 400 for url={bad!r}, got {resp.status}"
        finally:
            _restore_settings(prev)

    @pytest.mark.asyncio
    async def test_delete_invalid_json_400(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock
        monkeypatch.chdir(tmp_path)
        fresh, prev = _fresh_settings()
        try:
            req = make_mocked_request('DELETE', '/api/media')
            req.json = AsyncMock(side_effect=ValueError("nope"))
            resp = await api_media_delete(req)
            assert resp.status == 400
        finally:
            _restore_settings(prev)
