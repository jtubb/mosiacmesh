"""Tests for the media-cache URL routing logic. See
docs/superpowers/plans/2026-06-03-media-cache.md Task 4."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import server


class _Item:
    """Minimal MediaElement stand-in for unit tests."""
    def __init__(self, file, playmode="SEGMENT", seg_hash=None, seg_n=None):
        self.file = file
        self.playmode = playmode
        self.seg_hash = seg_hash
        self.seg_n = seg_n


def test_resolve_media_url_returns_localhost_for_cached_ipad1():
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = {"f00d_1"}
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    assert url == "http://127.0.0.1:8080/seg_f00d_1.mp4"


def test_resolve_media_url_falls_back_to_central_for_uncached_ipad1():
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = set()  # not yet pushed
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    # Central URL pattern matches /media/<key>/seg_<hash>_<n>.mp4
    assert "/media/abc/seg_f00d_1.mp4" in url
    assert "127.0.0.1" not in url


def test_resolve_media_url_central_for_service_worker_mode():
    """Modern devices: server emits central URL; SW intercepts transparently."""
    client = server.Client()
    client.clientKey = "modern"
    client.cacheMode = "service-worker"
    client.cachedSegments = {"f00d_1"}  # doesn't matter for SW mode
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    assert "/media/modern/seg_f00d_1.mp4" in url
    assert "127.0.0.1" not in url


def test_resolve_media_url_passthrough_for_non_segment_items():
    """SCRIPT, IMAGE, etc. items pass their .file through unchanged."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    item = _Item(file="bouncingBalls", playmode="SCRIPT")
    assert server._resolve_media_url(client, item) == "bouncingBalls"


def test_resolve_media_url_central_for_cachemode_none():
    """Devices that haven't announced cache support get central URLs."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "none"
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    assert "/media/abc/seg_f00d_1.mp4" in url


# --- Realistic-MediaElement tests: enum PlayMode + parse seg_hash/n
#     from item.file (the path/URL set by the render pipeline at
#     server.py:1159). These cover the actual production code path
#     since real broadcast sites pass MediaElement instances.

def test_resolve_media_url_parses_seg_from_file_with_enum_playmode():
    """Real MediaElement: playmode is PlayMode.SEGMENT, file is the
    /media/<key>/seg_<hash>_<n>.mp4 path the render pipeline set.
    _resolve_media_url should parse hash+n from the file path and
    emit a localhost URL when the segment is cached."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = {"9a27f533acb6_1"}
    me = server.MediaElement()
    me.playmode = server.PlayMode.SEGMENT
    me.file = "/media/abc/seg_9a27f533acb6_1.mp4"
    url = server._resolve_media_url(client, me)
    assert url == "http://127.0.0.1:8080/seg_9a27f533acb6_1.mp4"


def test_resolve_media_url_enum_segment_uncached_returns_central():
    """Same realistic shape but cachedSegments doesn't include the
    hash yet -> central-server URL."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = set()
    me = server.MediaElement()
    me.playmode = server.PlayMode.SEGMENT
    me.file = "/media/abc/seg_9a27f533acb6_1.mp4"
    url = server._resolve_media_url(client, me)
    assert "/media/abc/seg_9a27f533acb6_1.mp4" in url
    assert "127.0.0.1" not in url


def test_resolve_media_url_enum_non_segment_passthrough():
    """Real MediaElement with PlayMode.SCRIPT passes through .file."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    me = server.MediaElement()
    me.playmode = server.PlayMode.SCRIPT
    me.file = "bouncingBalls"
    assert server._resolve_media_url(client, me) == "bouncingBalls"


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_push_segment_adds_hash_to_cachedSegments_on_success():
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"
    c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"
    server.settings.clients["ipad1"] = c

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        _run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))

    assert "f00d_1" in server.settings.clients["ipad1"].cachedSegments


def test_push_segment_does_not_update_on_scp_failure():
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"; c.ip = "192.168.1.50"; c.cacheMode = "lighttpd-localhost"
    server.settings.clients["ipad1"] = c

    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b"scp: connection refused\n"))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        _run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))

    assert "f00d_1" not in server.settings.clients["ipad1"].cachedSegments


def test_push_segment_skips_clients_not_in_lighttpd_mode():
    """A client whose cacheMode is service-worker or none must NOT
    have an scp attempted (we'd waste bandwidth and time)."""
    server.settings = server.Settings()
    c = server.Client(); c.clientKey="modern"; c.ip="192.168.1.100"; c.cacheMode="service-worker"
    server.settings.clients["modern"] = c

    fake_create = AsyncMock()
    with patch("asyncio.create_subprocess_exec", fake_create):
        _run(server._push_segment_to_cached_clients("modern", "f00d", 1))

    fake_create.assert_not_called()
