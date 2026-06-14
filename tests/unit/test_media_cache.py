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


def test_client_has_cacheProbedMs_default():
    from mosaicmesh.state import Client
    c = Client()
    assert c.cacheProbedMs is None


def test_migrate_backfills_cacheProbedMs():
    from mosaicmesh.state import Client, migrate_client_objects
    import server
    server.settings = server.Settings()
    c = Client()
    del c.cacheProbedMs            # simulate an older pickled client
    server.settings.clients = {"old": c}
    migrate_client_objects()
    assert server.settings.clients["old"].cacheProbedMs is None


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


def test_reconcile_removes_orphan_hashes_from_cachedSegments():
    """A hash in cachedSegments that isn't referenced by any current
    playlist media element on this iPad's display group should be
    swept out (and a delete-ssh fires)."""
    server.settings = server.Settings()
    c = server.Client(); c.clientKey="ipad1"; c.ip="192.168.1.50"
    c.cacheMode = "lighttpd-localhost"; c.displayID="G1"
    c.cachedSegments = {"keep_1", "orphan_3"}
    server.settings.clients["ipad1"] = c
    d = server.Display(); d.displayID="G1"
    # Build a fake media element list referencing only "keep_1"
    class _It:
        def __init__(self, h, n):
            self.playmode=server.PlayMode.SEGMENT; self.seg_hash=h; self.seg_n=n
            self.file = f"/media/ipad1/seg_{h}_{n}.mp4"
    d.mediaElements = [_It("keep", 1)]
    server.settings.displays["G1"] = d

    fake_proc = MagicMock(); fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    async def fake_subproc(*a, **k): return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subproc):
        _run(server._reconcile_ipad_cache(c))

    assert c.cachedSegments == {"keep_1"}


def test_reconcile_preserves_cached_segment_when_token_matches():
    """Regression for the 2026-06-03 bug: with real MediaElement objects
    whose .file is the SOURCE path (not the rendered seg URL), the
    janitor was building an empty in_use set and deleting EVERY
    cached segment immediately after push. The fix derives in_use
    from display.renderedToken + item index, not from parsing
    item.file. This test uses the production shape (real
    MediaElement, source-path .file, renderedToken set on display)
    and asserts the just-pushed hash stays cached."""
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"; c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"; c.displayID = "Test Group"
    c.cachedSegments = {"9a27f533acb6_1"}  # just-pushed segment
    server.settings.clients["ipad1"] = c

    d = server.Display(); d.displayID = "Test Group"
    d.renderedToken = "9a27f533acb6"
    bouncing = server.MediaElement()
    bouncing.playmode = server.PlayMode.SCRIPT
    bouncing.file = "bouncingBalls"
    bunny = server.MediaElement()
    bunny.playmode = server.PlayMode.SEGMENT
    bunny.file = "/media/server/videos/big_buck_bunny_1080p_h264.mov"  # SOURCE, not seg
    d.mediaElements = [bouncing, bunny]
    server.settings.displays["Test Group"] = d

    fake = AsyncMock()
    with patch("asyncio.create_subprocess_exec", fake):
        _run(server._reconcile_ipad_cache(c))
    assert c.cachedSegments == {"9a27f533acb6_1"}, \
        f"cachedSegments was wrongly pruned: {c.cachedSegments}"
    fake.assert_not_called()


def test_reconcile_evicts_when_token_changes():
    """After encode_ver bumps -> new renderedToken -> old cached
    hashes become orphan -> should be swept."""
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"; c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"; c.displayID = "G"
    c.cachedSegments = {"OLDhash_1", "NEWhash_1"}
    server.settings.clients["ipad1"] = c

    d = server.Display(); d.displayID = "G"; d.renderedToken = "NEWhash"
    # bouncing balls at index 0 (SCRIPT) + bunny at index 1 (SEGMENT)
    # mirrors the production Test playlist shape. Push uses the
    # enumerated index from display.mediaElements, so the bunny
    # segment's cache key is <token>_1.
    bouncing = server.MediaElement()
    bouncing.playmode = server.PlayMode.SCRIPT
    bouncing.file = "bouncingBalls"
    bunny = server.MediaElement()
    bunny.playmode = server.PlayMode.SEGMENT
    bunny.file = "/media/server/videos/source.mov"
    d.mediaElements = [bouncing, bunny]
    server.settings.displays["G"] = d

    fake_proc = MagicMock(); fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    async def fake_subproc(*a, **k): return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subproc):
        _run(server._reconcile_ipad_cache(c))
    assert c.cachedSegments == {"NEWhash_1"}, \
        f"expected only the current-token hash, got {c.cachedSegments}"


# --- _per_client_items production-path tests (the actual PLAY/PRELOAD
#     URL routing). These cover the real-world architecture where
#     URLs are derived from display.renderedToken + item index, not
#     from item.seg_hash/seg_n attributes.

def _build_test_display():
    """Test fixture: Test playlist shape (bouncing balls SCRIPT at index 0,
    big_buck_bunny SEGMENT at index 1)."""
    d = server.Display()
    d.displayID = "Test Group"
    d.renderedToken = "9a27f533acb6"
    bouncing = server.MediaElement()
    bouncing.id = "b1"
    bouncing.playmode = server.PlayMode.SCRIPT
    bouncing.file = "bouncingBalls"
    bouncing.duration = 10
    bunny = server.MediaElement()
    bunny.id = "b2"
    bunny.playmode = server.PlayMode.SEGMENT
    bunny.file = "/media/server/videos/big_buck_bunny_1080p_h264.mov"
    bunny.duration = 597
    d.mediaElements = [bouncing, bunny]
    return d


def test_per_client_items_emits_localhost_url_for_cached_lighttpd_client():
    """The integration test: a real MediaElement playlist + a cached
    iPad-1 produces a localhost URL for the SEGMENT item."""
    d = _build_test_display()
    c = server.Client()
    c.clientKey = "ipad1"
    c.cacheMode = "lighttpd-localhost"
    c.cachedSegments = {"9a27f533acb6_1"}
    c.measuredPerimeter = [[0, 0], [100, 0], [100, 100], [0, 100]]  # any non-None
    items = server._per_client_items(d, "ipad1", c)
    # Item 0 (SCRIPT bouncingBalls) passes through .file unchanged
    assert items[0]["file"] == "bouncingBalls"
    # Item 1 (SEGMENT) gets localhost URL because it's cached
    assert items[1]["file"] == "http://127.0.0.1:8080/seg_9a27f533acb6_1.mp4"


def test_per_client_items_emits_central_url_for_uncached_segment():
    """Same playlist, but the iPad's cachedSegments is empty -> falls
    back to the central-server per-client URL."""
    d = _build_test_display()
    c = server.Client()
    c.clientKey = "ipad1"
    c.cacheMode = "lighttpd-localhost"
    c.cachedSegments = set()  # not yet pushed
    c.measuredPerimeter = [[0, 0], [100, 0], [100, 100], [0, 100]]
    items = server._per_client_items(d, "ipad1", c)
    assert items[1]["file"] == "/media/ipad1/seg_9a27f533acb6_1.mp4"
    assert "127.0.0.1" not in items[1]["file"]


def test_per_client_items_emits_central_url_for_service_worker_client():
    """Modern devices (cacheMode=service-worker) always get central
    URLs; their SW intercepts transparently. cachedSegments is ignored."""
    d = _build_test_display()
    c = server.Client()
    c.clientKey = "modern"
    c.cacheMode = "service-worker"
    c.cachedSegments = {"9a27f533acb6_1"}  # doesn't matter
    c.measuredPerimeter = [[0, 0], [100, 0], [100, 100], [0, 100]]
    items = server._per_client_items(d, "modern", c)
    assert items[1]["file"] == "/media/modern/seg_9a27f533acb6_1.mp4"


def test_per_client_items_emits_central_url_for_cacheMode_none():
    """Default-mode iPads (cacheMode=none) keep the legacy central URL."""
    d = _build_test_display()
    c = server.Client()
    c.clientKey = "ipad2"
    c.cacheMode = "none"
    c.measuredPerimeter = [[0, 0], [100, 0], [100, 100], [0, 100]]
    items = server._per_client_items(d, "ipad2", c)
    assert items[1]["file"] == "/media/ipad2/seg_9a27f533acb6_1.mp4"


def test_reconcile_noop_for_non_lighttpd_clients():
    """Service-worker / none clients have no on-device cache to clean;
    janitor should skip them entirely (no ssh attempts)."""
    server.settings = server.Settings()
    c = server.Client(); c.clientKey="m"; c.ip="1.1.1.1"
    c.cacheMode = "service-worker"; c.cachedSegments = {"x_1"}
    server.settings.clients["m"] = c

    fake = AsyncMock()
    with patch("asyncio.create_subprocess_exec", fake):
        _run(server._reconcile_ipad_cache(c))
    fake.assert_not_called()
    assert c.cachedSegments == {"x_1"}  # unchanged


# --- Stall-detection tests (2026-06-03 second cache iteration).
#     Covers the stall_event path in _push_segment_to_cached_clients:
#     when the sibling poller signals stall (size unchanged for
#     _PUSH_STALL_WINDOW_S), the push must kill scp and exit with
#     status=stalled, leaving cachedSegments unchanged.

def test_push_segment_aborts_on_stall_signal(monkeypatch):
    """Mock the poller to immediately set stall_event. The push
    must kill the (otherwise-blocking) scp process and clear
    cachePushProgress without populating cachedSegments."""
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"; c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"
    server.settings.clients["ipad1"] = c

    # FakeProc.communicate blocks until kill() releases it -- mimics
    # an scp that's making no progress.
    forever = asyncio.Event()

    class FakeProc:
        def __init__(self):
            self.returncode = None
            self.killed = False
        async def communicate(self):
            await forever.wait()
            return (b"", b"")
        def kill(self):
            self.killed = True
            self.returncode = -9
            forever.set()

    fake_proc = FakeProc()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    async def immediate_stall_poller(client_key, client, stall_event, proc):
        stall_event.set()

    monkeypatch.setattr(server, "_poll_push_progress",
                        immediate_stall_poller)

    with patch("asyncio.create_subprocess_exec",
               side_effect=fake_create_subprocess_exec):
        _run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))

    assert c.cachedSegments == set(), \
        "stalled push must NOT add segment to cachedSegments"
    assert c.cachePushProgress is None, \
        "cachePushProgress must be cleared in the finally block"
    assert fake_proc.killed, "scp.kill() must be called on stall"


def test_push_segment_clears_cachePushProgress_on_success(monkeypatch):
    """Happy path: scp succeeds, cachedSegments populated, the
    transient cachePushProgress is reset to None in the finally
    block so subsequent /api/discovery/devices reads show idle."""
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"; c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"
    server.settings.clients["ipad1"] = c

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    # Poller that just sleeps so it doesn't trigger stall before
    # the scp 'completes' (asyncio.wait will return communicate
    # first because fake_proc.communicate returns immediately).
    async def quiet_poller(client_key, client, stall_event, proc):
        await asyncio.sleep(1)  # any non-instant delay

    monkeypatch.setattr(server, "_poll_push_progress", quiet_poller)

    with patch("asyncio.create_subprocess_exec",
               side_effect=fake_create_subprocess_exec):
        _run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))

    assert "f00d_1" in c.cachedSegments
    assert c.cachePushProgress is None, \
        "cachePushProgress must be reset after success"


# --- Propagation aggregation tests for the new API surface.

def test_propagation_percent_for_client_partial_cache():
    """A client whose displayID's rendered token has 2 SEGMENT items
    of which the client has 1 cached should report 50.0."""
    server.settings = server.Settings()
    d = server.Display()
    d.displayID = "G"
    d.renderedToken = "abc"
    me1 = server.MediaElement()
    me1.id = "i1"; me1.playmode = server.PlayMode.SEGMENT
    me1.file = "/media/server/videos/foo.mp4"
    me2 = server.MediaElement()
    me2.id = "i2"; me2.playmode = server.PlayMode.SEGMENT
    me2.file = "/media/server/videos/bar.mp4"
    d.mediaElements = [me1, me2]
    server.settings.displays["G"] = d

    c = server.Client()
    c.clientKey = "ipad1"
    c.cacheMode = "lighttpd-localhost"
    c.displayID = "G"
    c.cachedSegments = {"abc_0"}  # has item 0 cached, not item 1
    server.settings.clients["ipad1"] = c

    assert server._propagation_percent_for_client(c) == 50.0


def test_propagation_percent_full_cache_returns_100():
    server.settings = server.Settings()
    d = server.Display()
    d.displayID = "G"; d.renderedToken = "abc"
    me = server.MediaElement()
    me.id = "i1"; me.playmode = server.PlayMode.SEGMENT
    me.file = "/media/server/videos/foo.mp4"
    d.mediaElements = [me]
    server.settings.displays["G"] = d
    c = server.Client()
    c.clientKey = "ipad1"; c.cacheMode = "lighttpd-localhost"
    c.displayID = "G"; c.cachedSegments = {"abc_0"}
    server.settings.clients["ipad1"] = c
    assert server._propagation_percent_for_client(c) == 100.0


def test_propagation_percent_non_lighttpd_returns_100():
    """Non-iPad / non-lighttpd clients are vacuously 'cached' so
    they don't drag down group aggregates."""
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "modern"; c.cacheMode = "service-worker"
    c.displayID = "G"; c.cachedSegments = set()
    server.settings.clients["modern"] = c
    assert server._propagation_percent_for_client(c) == 100.0


def test_propagation_percent_no_renderedToken_returns_100():
    """A display group whose render hasn't run yet has no expected
    segments; bar would be meaningless, so vacuously 100%."""
    server.settings = server.Settings()
    d = server.Display(); d.displayID = "G"; d.renderedToken = None
    d.mediaElements = []
    server.settings.displays["G"] = d
    c = server.Client()
    c.clientKey = "ipad1"; c.cacheMode = "lighttpd-localhost"
    c.displayID = "G"; c.cachedSegments = set()
    server.settings.clients["ipad1"] = c
    assert server._propagation_percent_for_client(c) == 100.0


def test_expected_seg_keys_only_includes_segment_video_items():
    """Bouncing-balls SCRIPT items don't render, so the expected
    set should only contain seg keys for SEGMENT video items."""
    server.settings = server.Settings()
    d = server.Display()
    d.displayID = "G"; d.renderedToken = "abc"
    bouncing = server.MediaElement()
    bouncing.id = "b1"; bouncing.playmode = server.PlayMode.SCRIPT
    bouncing.file = "bouncingBalls"
    bunny = server.MediaElement()
    bunny.id = "b2"; bunny.playmode = server.PlayMode.SEGMENT
    bunny.file = "/media/server/videos/big_buck_bunny_1080p_h264.mov"
    d.mediaElements = [bouncing, bunny]
    expected = server._expected_seg_keys_for_display(d)
    assert expected == {"abc_1"}, \
        f"expected only the bunny SEGMENT at index 1, got {expected}"
