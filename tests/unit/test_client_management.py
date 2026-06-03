"""
Unit tests for client management functionality
"""
import pytest
import time
from unittest.mock import patch, MagicMock

# Import server with patches to avoid argparse conflicts
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Mock args before import
import argparse
original_parse_args = argparse.ArgumentParser.parse_args

class MockArgs:
    def __init__(self):
        self.Port = 3000
        self.Verbose = False

def mock_parse_args(self, args=None, namespace=None):
    return MockArgs()

argparse.ArgumentParser.parse_args = mock_parse_args

try:
    import server
finally:
    # Restore original
    argparse.ArgumentParser.parse_args = original_parse_args


class TestClientClass:
    """Test the Client data model"""
    
    def test_client_initialization(self):
        """Test client object is properly initialized"""
        client = server.Client()
        
        # Test default values
        assert client.friendlyName is None
        assert client.clientID == ""
        assert client.displayID is None
        assert client.deviceWidth == 0
        assert client.deviceHeight == 0
        assert client.ready is False
        assert client.autoConfigured is False
        assert client.capabilities == []
        assert client.connectionCount == 0
        assert client.discoverySource == "manual"
        
        # Test timestamps are set
        assert hasattr(client, 'discoveryTime')
        assert hasattr(client, 'lastSeen')


class TestAutoConfiguration:
    """Test auto-configuration functionality"""
    
    def test_auto_configure_smartphone(self, mock_settings):
        """Test smartphone auto-configuration"""
        client_key = "test_mobile"
        client = server.Client()
        client.deviceType = "smartphone"
        client.deviceWidth = 390
        client.deviceHeight = 844
        
        server.settings = mock_settings
        server.auto_configure_client(client_key, client)
        
        assert client.displayID == "Mobile"
        assert client.autoConfigured is True
        assert client.discoverySource == "websocket"
        assert "touch" in client.capabilities
        assert "mobile" in client.capabilities
        assert client.friendlyName.startswith("Unknown_")
        assert "Mobile" in server.settings.displays
    
    def test_auto_configure_desktop(self, mock_settings):
        """Test desktop auto-configuration"""
        client_key = "test_desktop"
        client = server.Client()
        client.deviceType = "desktop"
        client.deviceWidth = 1920
        client.deviceHeight = 1080
        client.deviceBrand = "TestBrand"
        client.deviceModel = "TestModel"
        
        server.settings = mock_settings
        server.auto_configure_client(client_key, client)
        
        assert client.displayID == "Desktop"
        assert client.autoConfigured is True
        assert "HD" in client.capabilities
        assert "keyboard" in client.capabilities
        assert "mouse" in client.capabilities
        assert client.friendlyName == "TestModel_test_des"
    
    def test_auto_configure_tablet(self, mock_settings):
        """Test tablet auto-configuration"""
        client_key = "test_tablet"
        client = server.Client()
        client.deviceType = "tablet"
        client.deviceWidth = 1024
        client.deviceHeight = 768
        
        server.settings = mock_settings
        server.auto_configure_client(client_key, client)
        
        assert client.displayID == "Tablet"
        assert "touch" in client.capabilities
        assert "mobile" in client.capabilities
        assert "Tablet" in server.settings.displays
    
    def test_auto_configure_unknown_device(self, mock_settings):
        """Test unknown device type defaults to Default group"""
        client_key = "test_unknown"
        client = server.Client()
        client.deviceType = None
        
        server.settings = mock_settings  
        server.auto_configure_client(client_key, client)
        
        assert client.displayID == "Default"
        assert client.autoConfigured is True
    
    def test_auto_configure_skips_already_configured(self, mock_settings):
        """Test that already configured clients are skipped"""
        client_key = "test_skip"
        client = server.Client()
        client.autoConfigured = True
        original_display = "CustomDisplay"
        client.displayID = original_display
        
        server.settings = mock_settings
        server.auto_configure_client(client_key, client)
        
        # Should remain unchanged
        assert client.displayID == original_display


class TestClientMigration:
    """Test client object migration functionality"""
    
    def test_migrate_client_objects(self, mock_settings):
        """Test migration adds missing fields to existing clients"""
        # Create client without new fields (simulating old data)
        old_client = server.Client()
        # Remove new attributes to simulate old version
        delattr(old_client, 'discoveryTime')
        delattr(old_client, 'lastSeen') 
        delattr(old_client, 'connectionCount')
        delattr(old_client, 'capabilities')
        delattr(old_client, 'autoConfigured')
        delattr(old_client, 'discoverySource')
        
        mock_settings.clients["old_client"] = old_client
        server.settings = mock_settings
        
        server.migrate_client_objects()
        
        migrated_client = mock_settings.clients["old_client"]
        assert hasattr(migrated_client, 'discoveryTime')
        assert hasattr(migrated_client, 'lastSeen')
        assert migrated_client.connectionCount == 1
        assert migrated_client.capabilities == []
        assert migrated_client.autoConfigured is False
        assert migrated_client.discoverySource == "existing"


class TestDiscoveryData:
    """Test discovery data retrieval"""
    
    def test_get_discovered_devices_empty(self, mock_settings):
        """Test get_discovered_devices with no clients"""
        server.settings = mock_settings
        
        devices = server.get_discovered_devices()
        
        assert devices == []
    
    def test_get_discovered_devices_with_clients(self, mock_settings, mock_client):
        """Test get_discovered_devices returns proper device info"""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings
        
        devices = server.get_discovered_devices()
        
        assert len(devices) == 1
        device = devices[0]
        
        assert device["clientKey"] == "test123"
        assert device["friendlyName"] == "TestClient"
        assert device["displayID"] == "Desktop"
        assert device["deviceType"] == "desktop"
        assert device["resolution"] == "1920x1080"
        assert device["isOnline"] is True
        assert device["autoConfigured"] is True
        assert device["capabilities"] == ["HD", "keyboard", "mouse"]
    
    def test_get_discovered_devices_sorted_by_last_seen(self, mock_settings):
        """Test devices are sorted by lastSeen timestamp"""
        # Create clients with different lastSeen times
        client1 = server.Client()
        client1.friendlyName = "Client1"
        client1.lastSeen = 100
        
        client2 = server.Client() 
        client2.friendlyName = "Client2"
        client2.lastSeen = 200
        
        mock_settings.clients["client1"] = client1
        mock_settings.clients["client2"] = client2
        server.settings = mock_settings
        
        devices = server.get_discovered_devices()
        
        # Should be sorted by lastSeen (most recent first)
        assert len(devices) == 2
        assert devices[0]["friendlyName"] == "Client2"  # More recent
        assert devices[1]["friendlyName"] == "Client1"  # Older


class TestClientHandlers:
    """Test client disconnect and management handlers"""

    def test_handle_client_disconnect(self, mock_settings, mock_client):
        """Test client disconnect handler updates client state"""
        session_id = "test_session_123"
        mock_client.clientID = session_id
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings

        # Mock time.time() to control lastSeen
        with patch('time.time', return_value=12345):
            server.handle_client_disconnect(session_id)

        assert mock_client.lastSeen == 12345
        assert mock_client.ready is False

    def test_handle_client_disconnect_no_client(self, mock_settings):
        """Test disconnect handler gracefully handles unknown session"""
        server.settings = mock_settings

        # Should not raise an exception
        server.handle_client_disconnect("unknown_session")

        # Settings should remain unchanged
        assert len(mock_settings.clients) == 0


class TestClientCacheFields:
    """Test cache-state fields added in 2026-06-03"""

    def test_client_cachemode_default_is_none(self):
        c = server.Client()
        assert c.cacheMode == "none"

    def test_client_cachedsegments_default_is_empty_set(self):
        c = server.Client()
        assert c.cachedSegments == set()
        # mutating one client's set must not affect another's
        c.cachedSegments.add("abc_1")
        c2 = server.Client()
        assert c2.cachedSegments == set()

    def test_migrate_backfills_cache_fields_on_old_client(self, mock_settings):
        """A Client pickled before the cache fields were added should get
        backfilled to defaults by migrate_client_objects()."""
        server.settings = mock_settings
        c = server.Client()
        # Simulate a pre-cache-fields Client by deleting the attributes
        del c.cacheMode
        del c.cachedSegments
        server.settings.clients["legacy"] = c
        server.migrate_client_objects()
        after = server.settings.clients["legacy"]
        assert after.cacheMode == "none"
        assert after.cachedSegments == set()