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

def test_server_reexports_state_classes():
    """server.py still exposes the classes for backward compat with tests
    that do `from server import Client, Settings, etc.`"""
    import server
    assert server.Settings is __import__('mosaicmesh.state', fromlist=['Settings']).Settings
    assert server.Client is __import__('mosaicmesh.state', fromlist=['Client']).Client
    assert server.Playlist is __import__('mosaicmesh.state', fromlist=['Playlist']).Playlist
    assert server.Schedule is __import__('mosaicmesh.state', fromlist=['Schedule']).Schedule
    assert server.PlayMode is __import__('mosaicmesh.state', fromlist=['PlayMode']).PlayMode
