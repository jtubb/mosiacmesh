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

from mosaicmesh.api.media import api_media, upload_handler


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
