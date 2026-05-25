"""
pytest configuration and shared fixtures for MosaicMesh tests
"""
import pytest
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Import server module with test patches to avoid argparse conflicts
def get_server_module():
    """Get server module with test patches applied"""
    try:
        # Try importing from our test patch first
        from .server_test_patch import server
        return server
    except ImportError:
        # Fall back to direct import with manual patching
        import sys
        from pathlib import Path
        from unittest.mock import MagicMock
        
        # Add parent to path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        
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
            return server
        finally:
            # Restore original
            argparse.ArgumentParser.parse_args = original_parse_args


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_settings():
    """Create a mock settings object for testing"""
    server = get_server_module()
    settings = server.Settings()
    settings.displays = {
        "Default": server.Display(),
        "Desktop": server.Display(), 
        "Mobile": server.Display()
    }
    settings.clients = {}
    settings.scripts = {}
    return settings


@pytest.fixture
def mock_client():
    """Create a mock client object for testing"""
    server = get_server_module()
    client = server.Client()
    client.friendlyName = "TestClient"
    client.clientID = "test123"
    client.displayID = "Desktop"
    client.deviceType = "desktop"
    client.deviceWidth = 1920
    client.deviceHeight = 1080
    client.ip = "127.0.0.1"
    client.osName = "Windows"
    client.osVersion = "10"
    client.autoConfigured = True
    client.discoverySource = "test"
    client.capabilities = ["HD", "keyboard", "mouse"]
    client.ready = True       # media cached, ready to display
    client.isOnline = True    # live / recent heartbeat
    client.synced = True       # SYN/SYNACK handshake complete
    return client


@pytest.fixture
def temp_settings_file():
    """Create a temporary settings file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.dat', delete=False) as f:
        f.write('{"py/object": "server.Settings"}')
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def mock_websocket_session():
    """Create a mock WebSocket session for testing"""
    session = MagicMock()
    session.id = "test_session_123"
    session.manager = MagicMock()
    session.request = MagicMock()
    session.request.remote = "127.0.0.1"
    session.request.headers = {"User-Agent": "Mozilla/5.0 Test Browser"}
    session.send = AsyncMock()
    return session


@pytest.fixture
def sample_discovery_data():
    """Sample discovery data for testing UI components"""
    return {
        "success": True,
        "devices": [
            {
                "clientKey": "test123",
                "friendlyName": "Test Desktop",
                "displayID": "Desktop",
                "deviceType": "desktop",
                "deviceBrand": "TestBrand",
                "deviceModel": "TestModel",
                "resolution": "1920x1080",
                "ip": "127.0.0.1",
                "osName": "Windows",
                "osVersion": "10",
                "discoveryTime": 1234567890,
                "lastSeen": 1234567890,
                "isOnline": True,
                "timeSinceLastSeen": 5,
                "capabilities": ["HD", "keyboard", "mouse"],
                "autoConfigured": True,
                "discoverySource": "websocket",
                "connectionCount": 1
            },
            {
                "clientKey": "mobile456", 
                "friendlyName": "Test Mobile",
                "displayID": "Mobile",
                "deviceType": "smartphone",
                "deviceBrand": "TestPhone",
                "deviceModel": "TestPhone X",
                "resolution": "390x844",
                "ip": "192.168.1.50",
                "osName": "Android",
                "osVersion": "12",
                "discoveryTime": 1234567800,
                "lastSeen": 1234567800,
                "isOnline": False,
                "timeSinceLastSeen": 300,
                "capabilities": ["touch", "mobile"],
                "autoConfigured": True,
                "discoverySource": "websocket", 
                "connectionCount": 3
            }
        ],
        "total": 2,
        "online": 1
    }


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test"""
    server = get_server_module()
    # Reset any global variables that might affect tests
    server.socketmanager = None
    server.file_cache.clear()
    server.cache_stats['hits'] = 0
    server.cache_stats['misses'] = 0
    
    yield
    
    # Cleanup after test
    server.close_file_pool()


class TestConfig:
    """Test configuration constants"""
    TEST_PORT = 8899
    TEST_HOST = "127.0.0.1"
    WEBSOCKET_TIMEOUT = 5.0
    API_TIMEOUT = 3.0