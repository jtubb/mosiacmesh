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


def test_calibration_order_points_behavior():
    """Pure-math behavior check: order_points returns [TL, TR, BR, BL] for a
    rectangle whose vertices are given in arbitrary order. Catches a future
    regression where the function gets accidentally reordered or its sort
    logic changes — easy mistake to make during a refactor and hard to
    notice from import-only tests."""
    import numpy as np
    from mosaicmesh.calibration import order_points
    # Rectangle vertices given out-of-order
    pts = np.array([[3, 2], [1, 1], [3, 1], [1, 2]], dtype="float32")
    result = order_points(pts)
    # Expected order: top-left, top-right, bottom-right, bottom-left
    assert result[0].tolist() == [1.0, 1.0], f"TL wrong: {result[0]}"
    assert result[1].tolist() == [3.0, 1.0], f"TR wrong: {result[1]}"
    assert result[2].tolist() == [3.0, 2.0], f"BR wrong: {result[2]}"
    assert result[3].tolist() == [1.0, 2.0], f"BL wrong: {result[3]}"


def test_render_pipeline_importable():
    from mosaicmesh.render import (
        render_group_async, compute_render_token,
        build_ffmpeg_perspective_cmd, build_ffmpeg_individual_cmd,
        get_video_dimensions, resolve_media_path,
        _apply_playlist, _start_group_playback, _stop_group_playback,
        _broadcast_per_client_play, _broadcast_per_client_preload,
        isVideoItem,
    )
    # Assert every imported callable. A future task that accidentally
    # shadows or deletes one would still allow the import (the name is
    # bound), so verify each is actually a function.
    assert callable(render_group_async)
    assert callable(compute_render_token)
    assert callable(build_ffmpeg_perspective_cmd)
    assert callable(build_ffmpeg_individual_cmd)
    assert callable(get_video_dimensions)
    assert callable(resolve_media_path)
    assert callable(_apply_playlist)
    assert callable(_start_group_playback)
    assert callable(_stop_group_playback)
    assert callable(_broadcast_per_client_play)
    assert callable(_broadcast_per_client_preload)
    assert callable(isVideoItem)


def test_render_keyframe_grid_args_behavior():
    """Pure-function behavior check: _keyframe_grid_args returns the
    canonical 0.25s ffmpeg keyframe-grid args. iPad-1 seeks to grid
    keyframes, so a future commit accidentally changing the grid step
    would silently de-sync multi-client mosaic playback. The expression
    'expr:gte(t,n_forced*0.25)' embeds KEYFRAME_GRID_SEC, so this also
    catches an accidental change to that constant."""
    from mosaicmesh.render import _keyframe_grid_args
    result = _keyframe_grid_args()
    assert result == ["-force_key_frames", "expr:gte(t,n_forced*0.25)"], result


def test_device_scripts_importable():
    from mosaicmesh.device_scripts import (
        DEFAULT_DEVICE_SCRIPTS, WEBCLIP_BUNDLE_ID,
        WEBAPP_ICON_FBX, WEBAPP_ICON_FBY,
        SSH_LEGACY_OPTS,
        _run_device_script, _launch_webapp_via_vnc, _drop_pooled_vnc,
    )
    assert isinstance(DEFAULT_DEVICE_SCRIPTS, dict)
    assert 'loginScript' in DEFAULT_DEVICE_SCRIPTS
    assert 'startScript' in DEFAULT_DEVICE_SCRIPTS
    assert 'stopScript' in DEFAULT_DEVICE_SCRIPTS
    assert isinstance(WEBAPP_ICON_FBX, int)
    assert isinstance(WEBAPP_ICON_FBY, int)
    assert callable(_run_device_script)
    assert callable(_launch_webapp_via_vnc)
    assert callable(_drop_pooled_vnc)
    # Constant composition check: the startScript embeds WEBCLIP_BUNDLE_ID.
    # If either constant silently changes, the webclip-launch shell command
    # breaks fleet-wide. Cheap regression bait that will be retired when
    # PR-3 replaces this module with the ScriptingProfile dispatcher.
    assert WEBCLIP_BUNDLE_ID in DEFAULT_DEVICE_SCRIPTS['startScript']
    # SSH options must keep IdentitiesOnly=yes — required for the iPad-1 fleet
    # (low MaxAuthTries; dropping this flag causes auth lockouts). Documented
    # in onboard_devices.ps1's sshLegacy. Catches a silent regression that
    # would break SSH-driven script execution on the production fleet.
    assert 'IdentitiesOnly=yes' in SSH_LEGACY_OPTS

def test_scheduling_helpers_importable():
    from mosaicmesh.scheduling import (
        playlist_index, _parse_date, _hhmm_to_min, schedule_active_at,
        _FREQ_MAP,
    )
    assert callable(playlist_index)
    assert callable(_parse_date)
    assert callable(_hhmm_to_min)
    assert callable(schedule_active_at)
    # _FREQ_MAP is the recurrence-frequency table referenced by
    # msg_response's schedule-CRUD validation. It was almost forgotten
    # during the Task 8 move (the spec listed only function definitions),
    # so explicitly assert its presence here to catch a future recurrence.
    assert isinstance(_FREQ_MAP, dict)
    assert {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}.issubset(_FREQ_MAP.keys())
    # Smoke checks on the trivial pure helpers
    assert _hhmm_to_min("09:30") == 9 * 60 + 30
    assert _hhmm_to_min("00:00") == 0
    assert _hhmm_to_min("23:59") == 23 * 60 + 59

def test_api_discovery_importable():
    from mosaicmesh.api.discovery import (
        auto_configure_client, get_discovered_devices,
        sync_new_client_to_group,
        _expected_seg_keys_for_display, _expected_segments_for_client,
        _propagation_percent_for_client,
        api_discovery_devices, api_discovery_stats, api_discovery_configure,
    )
    assert callable(auto_configure_client)
    assert callable(get_discovered_devices)
    assert callable(sync_new_client_to_group)
    assert callable(_expected_seg_keys_for_display)
    assert callable(_expected_segments_for_client)
    assert callable(_propagation_percent_for_client)
    assert callable(api_discovery_devices)
    assert callable(api_discovery_stats)
    assert callable(api_discovery_configure)


def test_propagation_percent_short_circuits():
    """Behavior smoke check for the two early-exit paths in
    _propagation_percent_for_client:
      - cacheMode != 'lighttpd-localhost' -> 100.0 (vacuously caught up)
      - displayID is None / empty       -> 100.0 (no group, nothing expected)
    Catches a future regression that would silently drag aggregate
    propagation stats down when non-caching clients are present."""
    from types import SimpleNamespace
    from mosaicmesh.api.discovery import _propagation_percent_for_client
    # Non-caching client (default cacheMode 'none')
    c1 = SimpleNamespace(cacheMode="none", displayID="Group1")
    assert _propagation_percent_for_client(c1) == 100.0
    # Caching client but no displayID
    c2 = SimpleNamespace(cacheMode="lighttpd-localhost", displayID=None)
    assert _propagation_percent_for_client(c2) == 100.0
    c3 = SimpleNamespace(cacheMode="lighttpd-localhost", displayID="")
    assert _propagation_percent_for_client(c3) == 100.0

def test_websocket_legacy_importable():
    from mosaicmesh.websocket.legacy import msg_response
    assert callable(msg_response)

def test_websocket_typed_importable():
    from mosaicmesh.websocket.typed import handle_websocket_message
    assert callable(handle_websocket_message)
