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
