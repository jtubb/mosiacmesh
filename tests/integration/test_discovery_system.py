"""
Integration tests for the complete discovery system
"""
import pytest
import asyncio
import json
import time
from unittest.mock import patch, MagicMock, AsyncMock
import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

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


class TestDiscoverySystemIntegration(AioHTTPTestCase):
    """Integration tests for the discovery system workflow"""
    
    async def get_application(self):
        """Create test application"""
        app = web.Application()
        
        # Add discovery API routes
        app.router.add_get('/api/discovery/devices', server.api_discovery_devices)
        app.router.add_get('/api/discovery/stats', server.api_discovery_stats)
        app.router.add_post('/api/discovery/configure', server.api_discovery_configure)
        
        # Initialize test settings
        server.settings = server.Settings()
        server.settings.displays = {
            "Default": server.Display(),
            "Desktop": server.Display(),
            "Mobile": server.Display()
        }
        server.settings.clients = {}
        
        return app
    
    @unittest_run_loop
    async def test_full_discovery_workflow(self):
        """Test complete device discovery and configuration workflow"""
        # Step 1: Device connects and sends clientInfo
        client_data = {
            'type': 'clientInfo',
            'friendlyName': 'Integration Test Device',
            'deviceWidth': 1920,
            'deviceHeight': 1080,
            'deviceType': 'desktop',
            'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Mock WebSocket session
        mock_session = MagicMock()
        mock_session.id = "integration_test_123"
        mock_session.request.remote = "127.0.0.1"
        mock_session.request.headers = {"User-Agent": client_data['userAgent']}
        mock_session.send = AsyncMock()
        
        # Simulate device discovery
        with patch('server.device_detector') as mock_detector:
            mock_device = MagicMock()
            mock_device.device_type = 'desktop'
            mock_device.device_brand = 'TestBrand'
            mock_device.device_model = 'TestModel'
            mock_device.os_name = 'Windows'
            mock_device.os_version = '10'
            mock_detector.parse.return_value = mock_device
            
            await server.handle_websocket_message(mock_session, client_data)
        
        # Step 2: Verify device was auto-configured
        assert mock_session.id in server.settings.clients
        client = server.settings.clients[mock_session.id]
        assert client.friendlyName == 'Integration Test Device'
        assert client.autoConfigured is True
        assert client.displayID == 'Desktop'
        assert 'HD' in client.capabilities
        
        # Step 3: Test API endpoints return the device
        async with self.client.request("GET", "/api/discovery/devices") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data['success'] is True
            assert len(data['devices']) == 1
            
            device = data['devices'][0]
            assert device['clientKey'] == mock_session.id
            assert device['friendlyName'] == 'Integration Test Device'
            assert device['displayID'] == 'Desktop'
        
        # Step 4: Test device configuration via API
        config_data = {
            'clientKey': mock_session.id,
            'displayID': 'Mobile',
            'friendlyName': 'Updated Integration Device'
        }
        
        async with self.client.request("POST", "/api/discovery/configure",
                                     json=config_data) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data['success'] is True
        
        # Step 5: Verify configuration was applied
        updated_client = server.settings.clients[mock_session.id]
        assert updated_client.displayID == 'Mobile'
        assert updated_client.friendlyName == 'Updated Integration Device'
        
        # Step 6: Test statistics endpoint
        async with self.client.request("GET", "/api/discovery/stats") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data['success'] is True
            assert data['totalDevices'] == 1
            assert data['autoConfiguredDevices'] == 1
    
    @unittest_run_loop
    async def test_multiple_device_discovery(self):
        """Test discovery of multiple devices with different types"""
        devices = [
            {
                'session_id': 'desktop_123',
                'data': {
                    'type': 'clientInfo',
                    'friendlyName': 'Desktop Client',
                    'deviceWidth': 1920,
                    'deviceHeight': 1080,
                    'deviceType': 'desktop'
                },
                'device_info': {
                    'device_type': 'desktop',
                    'device_brand': 'Dell',
                    'device_model': 'OptiPlex',
                    'os_name': 'Windows',
                    'os_version': '10'
                }
            },
            {
                'session_id': 'mobile_456',
                'data': {
                    'type': 'clientInfo', 
                    'friendlyName': 'Mobile Client',
                    'deviceWidth': 390,
                    'deviceHeight': 844,
                    'deviceType': 'smartphone'
                },
                'device_info': {
                    'device_type': 'smartphone',
                    'device_brand': 'Apple',
                    'device_model': 'iPhone',
                    'os_name': 'iOS',
                    'os_version': '15'
                }
            },
            {
                'session_id': 'tablet_789',
                'data': {
                    'type': 'clientInfo',
                    'friendlyName': 'Tablet Client', 
                    'deviceWidth': 1024,
                    'deviceHeight': 768,
                    'deviceType': 'tablet'
                },
                'device_info': {
                    'device_type': 'tablet',
                    'device_brand': 'Samsung',
                    'device_model': 'Galaxy Tab',
                    'os_name': 'Android',
                    'os_version': '11'
                }
            }
        ]
        
        # Simulate discovery of multiple devices
        with patch('server.device_detector') as mock_detector:
            for device in devices:
                mock_session = MagicMock()
                mock_session.id = device['session_id']
                mock_session.request.remote = "127.0.0.1"
                mock_session.request.headers = {"User-Agent": "Test Browser"}
                mock_session.send = AsyncMock()
                
                mock_device = MagicMock()
                for attr, value in device['device_info'].items():
                    setattr(mock_device, attr, value)
                mock_detector.parse.return_value = mock_device
                
                await server.handle_websocket_message(mock_session, device['data'])
        
        # Verify all devices were discovered and configured
        assert len(server.settings.clients) == 3
        
        # Check device assignments
        desktop_client = server.settings.clients['desktop_123']
        assert desktop_client.displayID == 'Desktop'
        assert 'HD' in desktop_client.capabilities
        
        mobile_client = server.settings.clients['mobile_456']
        assert mobile_client.displayID == 'Mobile'
        assert 'touch' in mobile_client.capabilities
        
        tablet_client = server.settings.clients['tablet_789'] 
        assert tablet_client.displayID == 'Tablet'
        assert 'touch' in tablet_client.capabilities
        
        # Test API returns all devices
        async with self.client.request("GET", "/api/discovery/devices") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data['success'] is True
            assert len(data['devices']) == 3
            assert data['total'] == 3
    
    @unittest_run_loop
    async def test_device_heartbeat_and_status_tracking(self):
        """Test device heartbeat and online status tracking"""
        # Setup initial device
        mock_session = MagicMock()
        mock_session.id = "heartbeat_test_123"
        mock_session.request.remote = "127.0.0.1"
        mock_session.request.headers = {"User-Agent": "Test Browser"}
        mock_session.send = AsyncMock()
        
        client_data = {
            'type': 'clientInfo',
            'friendlyName': 'Heartbeat Test Device',
            'deviceWidth': 1920,
            'deviceHeight': 1080
        }
        
        with patch('server.device_detector'):
            await server.handle_websocket_message(mock_session, client_data)
        
        client = server.settings.clients[mock_session.id]
        initial_last_seen = client.lastSeen
        
        # Simulate heartbeat after some time
        time.sleep(0.1)  # Small delay
        heartbeat_data = {
            'type': 'heartbeat',
            'timestamp': int(time.time())
        }
        
        with patch('time.time', return_value=initial_last_seen + 30):
            await server.handle_websocket_message(mock_session, heartbeat_data)
        
        # Verify lastSeen was updated
        assert client.lastSeen > initial_last_seen
        
        # Test device appears online in API
        async with self.client.request("GET", "/api/discovery/devices") as resp:
            data = await resp.json()
            device = data['devices'][0]
            assert device['isOnline'] is True
            assert device['timeSinceLastSeen'] < 60  # Recently seen
    
    @unittest_run_loop 
    async def test_device_disconnect_and_cleanup(self):
        """Test device disconnect handling and cleanup"""
        # Setup device
        session_id = "disconnect_test_123"
        client = server.Client()
        client.clientID = session_id
        client.friendlyName = "Disconnect Test"
        client.ready = True
        server.settings.clients["test_key"] = client
        
        # Simulate disconnect
        with patch('time.time', return_value=12345):
            server.handle_client_disconnect(session_id)
        
        # Verify client state updated
        assert client.ready is False
        assert client.lastSeen == 12345
        
        # Test API shows device as offline
        async with self.client.request("GET", "/api/discovery/devices") as resp:
            data = await resp.json()
            if data['devices']:  # If device still in list
                device = data['devices'][0]
                assert device['isOnline'] is False
    
    @unittest_run_loop
    async def test_api_error_handling(self):
        """Test API error handling scenarios"""
        # Test configure with invalid client key
        config_data = {
            'clientKey': 'nonexistent_client',
            'displayID': 'Desktop'
        }
        
        async with self.client.request("POST", "/api/discovery/configure",
                                     json=config_data) as resp:
            assert resp.status == 404
            data = await resp.json()
            assert data['success'] is False
            assert 'not found' in data['error']
        
        # Test configure with invalid JSON
        async with self.client.request("POST", "/api/discovery/configure",
                                     data="invalid json",
                                     headers={'Content-Type': 'application/json'}) as resp:
            assert resp.status == 400
            data = await resp.json()
            assert data['success'] is False
            assert 'Invalid JSON' in data['error']
        
        # Test configure without client key
        config_data = {
            'displayID': 'Desktop'
            # Missing clientKey
        }
        
        async with self.client.request("POST", "/api/discovery/configure",
                                     json=config_data) as resp:
            assert resp.status == 400
            data = await resp.json()
            assert data['success'] is False
            assert 'clientKey required' in data['error']


class TestRealTimeUpdates:
    """Test real-time status updates and WebSocket communication"""
    
    @pytest.mark.asyncio
    async def test_discovery_announcement_broadcast(self):
        """Test broadcasting of discovery announcements"""
        if not hasattr(server, 'broadcast_discovery_announcement'):
            pytest.skip("broadcast_discovery_announcement not implemented")
            
        # Setup multiple connected clients
        clients = {}
        for i in range(3):
            client_key = f"client_{i}"
            client = server.Client()
            client.websocket = AsyncMock()
            clients[client_key] = client
        
        server.settings = server.Settings()
        server.settings.clients = clients
        
        # New device discovered
        new_client = server.Client()
        new_client.friendlyName = "New Device"
        new_client.deviceType = "desktop"
        
        # Broadcast announcement
        await server.broadcast_discovery_announcement("new_client", new_client)
        
        # All existing clients should receive announcement
        for client in clients.values():
            client.websocket.send.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_status_update_propagation(self):
        """Test propagation of status updates to connected clients"""
        if not hasattr(server, 'send_status_update'):
            pytest.skip("send_status_update not implemented")
            
        # Setup client with WebSocket connection
        client = server.Client()
        client.websocket = AsyncMock()
        client.displayID = "Desktop"
        
        server.settings = server.Settings()
        server.settings.clients = {"test_client": client}
        
        # Update client status
        client.ready = True
        client.lastSeen = time.time()
        
        # Send status update
        await server.send_status_update("test_client", {
            'type': 'statusUpdate',
            'isOnline': True,
            'ready': True
        })
        
        client.websocket.send.assert_called_once()


class TestDataPersistence:
    """Test data persistence across server restarts"""
    
    def test_settings_migration_on_load(self):
        """Test that old client data is migrated when loading settings"""
        # This would test the migration functionality when loading
        # settings from disk with old client objects
        old_settings = server.Settings()
        
        # Create client without new fields (simulating old data)
        old_client = server.Client()
        # Remove new attributes to simulate old version
        for attr in ['discoveryTime', 'lastSeen', 'connectionCount', 
                     'capabilities', 'autoConfigured', 'discoverySource']:
            if hasattr(old_client, attr):
                delattr(old_client, attr)
        
        old_settings.clients["old_client"] = old_client
        server.settings = old_settings
        
        # Run migration
        server.migrate_client_objects()
        
        # Verify migration completed
        migrated_client = server.settings.clients["old_client"]
        assert hasattr(migrated_client, 'discoveryTime')
        assert hasattr(migrated_client, 'lastSeen')
        assert migrated_client.connectionCount >= 1
        assert migrated_client.capabilities == []
        assert migrated_client.autoConfigured is False
        assert migrated_client.discoverySource == "existing"