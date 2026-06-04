"""Smoke tests confirming the mosaicmesh module split landed.
Each module's existence is verified by importing one canonical symbol from it."""
import pytest

def test_state_classes_importable():
    from mosaicmesh.state import (
        Settings, Scripts, Display, PlayState, MediaElement,
        Playlist, Schedule, PlayMode, Client,
        migrate_client_objects, _apply_default_scripts,
    )
    s = Settings()
    assert hasattr(s, 'clients')
    assert hasattr(s, 'displays')
    assert hasattr(s, 'playlists')
    assert hasattr(s, 'schedules')
    assert hasattr(s, 'scripts')

def test_persistence_helpers_importable():
    from mosaicmesh.persistence import (
        save_settings_incremental, saveSettings, cleanup_old_clients,
    )
    assert callable(save_settings_incremental)
    assert callable(saveSettings)
    assert callable(cleanup_old_clients)
    # cleanup_old_clients has a default max-age — confirm it's the canonical
    # 24-hour value so a future commit accidentally shortening it would be caught.
    assert cleanup_old_clients.__defaults__ == (24 * 3600,)

def test_cache_helpers_importable():
    from mosaicmesh.cache import (
        get_pooled_file_handle, close_file_pool,
        prewarm_static_cache, get_cached_file,
    )
    assert callable(get_cached_file)
    assert callable(get_pooled_file_handle)
    assert callable(close_file_pool)
    assert callable(prewarm_static_cache)


def test_cache_behavior_smoke():
    """Real behavior check: cache miss → hit, cache_stats updates correctly.
    This catches a future regression where the re-import in server.py shadows
    a broken implementation in mosaicmesh.cache."""
    import os
    import tempfile
    from mosaicmesh.cache import get_cached_file, cache_stats, file_cache
    # Reset state for a clean reading
    baseline_misses = cache_stats['misses']
    baseline_hits = cache_stats['hits']
    fd, path = tempfile.mkstemp(suffix='.tmp')
    try:
        os.write(fd, b"hello")
        os.close(fd)
        # First call: miss
        data1 = get_cached_file(path)
        assert data1 == b"hello"
        assert cache_stats['misses'] == baseline_misses + 1
        # Second call: hit
        data2 = get_cached_file(path)
        assert data2 == b"hello"
        assert cache_stats['hits'] == baseline_hits + 1
    finally:
        # Evict from cache + remove temp file
        if path in file_cache:
            del file_cache[path]
        try:
            os.unlink(path)
        except OSError:
            pass

def test_broadcast_helpers_importable():
    from mosaicmesh.broadcast import (
        _send_to_session, _deliver,
        broadcast_to_client, broadcast_to_display_group,
    )
    assert callable(broadcast_to_client)
    assert callable(broadcast_to_display_group)
    assert callable(_send_to_session)
    assert callable(_deliver)


def test_send_to_session_targeted():
    """Steady-state targeted path: _send_to_session looks up the session
    via socketmanager.get(sid), calls sess.send(msg) directly, and returns
    True. socketmanager.broadcast() is NOT called — that's the fallback
    path covered by test_broadcast_to_display_group in test_websocket_handlers.py.

    This is the optimization path that makes broadcast O(1) per recipient
    instead of O(N) over all clients. A future regression breaking
    socketmanager.get() lookup would otherwise be invisible to the suite."""
    import server
    from unittest.mock import MagicMock
    from mosaicmesh.broadcast import _send_to_session

    prev_mgr = getattr(server, 'socketmanager', None)
    try:
        mock_sess = MagicMock()
        server.socketmanager = MagicMock()
        server.socketmanager.get.return_value = mock_sess

        result = _send_to_session("sess-abc", "encoded-msg")

        assert result is True
        mock_sess.send.assert_called_once_with("encoded-msg")
        server.socketmanager.broadcast.assert_not_called()
    finally:
        server.socketmanager = prev_mgr

def test_server_reexports_state_classes():
    """server.py still exposes the classes for backward compat with tests
    that do `from server import Client, Settings, etc.`

    Covers all nine classes (not just the five commonly-imported ones) so
    a future commit that accidentally re-introduces a local definition of
    Scripts / Display / PlayState / MediaElement in server.py would be
    caught by this test failing the `is` identity check."""
    import mosaicmesh.state as _state
    import server
    for name in ('Settings', 'Scripts', 'Display', 'PlayState', 'MediaElement',
                 'Playlist', 'Schedule', 'PlayMode', 'Client'):
        assert getattr(server, name) is getattr(_state, name), (
            f"server.{name} is not the same object as mosaicmesh.state.{name} "
            "— a duplicate local definition may have crept back into server.py"
        )

def test_calibration_helpers_importable():
    from mosaicmesh.calibration import (
        order_points, reconstruct_screen_quad, reconcile_screen_quad,
        warp_image_for_screen, assign_group_bounding_boxes,
        group_bounding_box, letterbox_to_aspect,
    )
    assert callable(order_points)
    assert callable(reconstruct_screen_quad)
    assert callable(reconcile_screen_quad)
    assert callable(warp_image_for_screen)
    assert callable(assign_group_bounding_boxes)
    assert callable(group_bounding_box)
    assert callable(letterbox_to_aspect)
