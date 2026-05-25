"""
Unit tests for WebSocket handlers and messaging
"""
import pytest
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestWebSocketHandlers:
    """Test WebSocket message handlers"""
    
    @pytest.mark.asyncio
    async def test_handle_websocket_message_client_info(self, mock_websocket_session, mock_settings):
        """Test handling client info WebSocket message"""
        server.settings = mock_settings
        
        message_data = {
            'type': 'clientInfo',
            'friendlyName': 'Test Device',
            'deviceWidth': 1920,
            'deviceHeight': 1080,
            'deviceType': 'desktop',
            'userAgent': 'Mozilla/5.0 Test Browser'
        }
        
        with patch('server.device_detector') as mock_detector, \
             patch('server.auto_configure_client') as mock_auto_config, \
             patch('time.time', return_value=12345):
            
            mock_device = MagicMock()
            mock_device.device_type = 'desktop'
            mock_device.device_brand = 'TestBrand'
            mock_device.device_model = 'TestModel'
            mock_device.os_name = 'Windows'
            mock_device.os_version = '10'
            mock_detector.parse.return_value = mock_device
            
            await server.handle_websocket_message(mock_websocket_session, message_data)
            
            # Verify client was created and configured
            assert mock_websocket_session.id in mock_settings.clients
            client = mock_settings.clients[mock_websocket_session.id]
            assert client.friendlyName == 'Test Device'
            assert client.deviceWidth == 1920
            assert client.deviceHeight == 1080
            assert client.deviceType == 'desktop'
            assert client.ip == '127.0.0.1'
            
            mock_auto_config.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_websocket_message_ready(self, mock_websocket_session, mock_settings, mock_client):
        """Test handling ready status WebSocket message"""
        server.settings = mock_settings
        mock_settings.clients[mock_websocket_session.id] = mock_client
        
        message_data = {
            'type': 'ready',
            'ready': True
        }
        
        with patch('time.time', return_value=12345):
            await server.handle_websocket_message(mock_websocket_session, message_data)
            
            assert mock_client.ready is True
            assert mock_client.lastSeen == 12345
    
    @pytest.mark.asyncio
    async def test_handle_websocket_message_heartbeat(self, mock_websocket_session, mock_settings, mock_client):
        """Test handling heartbeat WebSocket message"""
        server.settings = mock_settings
        mock_settings.clients[mock_websocket_session.id] = mock_client
        
        message_data = {
            'type': 'heartbeat',
            'timestamp': 12345
        }
        
        with patch('time.time', return_value=12345):
            await server.handle_websocket_message(mock_websocket_session, message_data)
            
            assert mock_client.lastSeen == 12345
            # Should send heartbeat response
            mock_websocket_session.send.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_websocket_message_display_data(self, mock_websocket_session, mock_settings, mock_client):
        """Test handling display data WebSocket message"""
        server.settings = mock_settings
        mock_settings.clients[mock_websocket_session.id] = mock_client
        mock_client.displayID = 'Desktop'
        
        # Add display to settings
        display = server.Display()
        display.clientList = []
        mock_settings.displays['Desktop'] = display
        
        message_data = {
            'type': 'displayData',
            'displayID': 'Desktop',
            'data': {'key': 'value'}
        }
        
        await server.handle_websocket_message(mock_websocket_session, message_data)
        
        # Should broadcast to other clients in the same display
        mock_websocket_session.send.assert_called()
    
    @pytest.mark.asyncio
    async def test_handle_websocket_message_invalid_json(self, mock_websocket_session, mock_settings):
        """Test handling invalid JSON message"""
        server.settings = mock_settings
        
        # Invalid message data (not a dict)
        message_data = "invalid_json_string"
        
        # Should not raise an exception
        await server.handle_websocket_message(mock_websocket_session, message_data)
    
    @pytest.mark.asyncio
    async def test_handle_websocket_message_unknown_type(self, mock_websocket_session, mock_settings):
        """Test handling message with unknown type"""
        server.settings = mock_settings
        
        message_data = {
            'type': 'unknownMessageType',
            'data': 'test'
        }
        
        # Should handle gracefully without error
        await server.handle_websocket_message(mock_websocket_session, message_data)


class TestWebSocketBroadcasting:
    """Test WebSocket broadcasting functionality"""
    
    def test_broadcast_to_display_group(self, mock_settings):
        """Broadcasting to a display group fans out via the central
        socketmanager + DEST routing (one broadcast per client in the group)."""
        if not hasattr(server, 'broadcast_to_display_group'):
            pytest.skip("broadcast_to_display_group not implemented")

        # Two clients in the target group, one outside it
        client1 = server.Client()
        client1.displayID = 'TestDisplay'
        client2 = server.Client()
        client2.displayID = 'TestDisplay'
        other = server.Client()
        other.displayID = 'OtherDisplay'

        mock_settings.clients['client1'] = client1
        mock_settings.clients['client2'] = client2
        mock_settings.clients['other'] = other
        server.settings = mock_settings
        server.socketmanager = MagicMock()

        message = {"REQUEST": "broadcast", "PAYLOAD": "test message"}
        server.broadcast_to_display_group('TestDisplay', message)

        # One broadcast per in-group client; the 'other' client is excluded
        assert server.socketmanager.broadcast.call_count == 2
    
    @pytest.mark.asyncio
    async def test_broadcast_discovery_announcement(self, mock_settings, mock_client):
        """Test broadcasting device discovery announcement"""
        if not hasattr(server, 'broadcast_discovery_announcement'):
            pytest.skip("broadcast_discovery_announcement not implemented")
            
        mock_settings.clients['test123'] = mock_client
        server.settings = mock_settings
        
        await server.broadcast_discovery_announcement('test123', mock_client)
        
        # Should broadcast discovery info to all connected clients
        # (Implementation would depend on actual broadcasting mechanism)
    
    @pytest.mark.asyncio
    async def test_send_client_list_update(self, mock_settings, mock_client):
        """Test sending client list update"""
        if not hasattr(server, 'send_client_list_update'):
            pytest.skip("send_client_list_update not implemented")
            
        mock_client.websocket = AsyncMock()
        mock_settings.clients['test123'] = mock_client
        server.settings = mock_settings
        
        await server.send_client_list_update('test123')
        
        # Should send updated client list
        mock_client.websocket.send.assert_called_once()


class TestWebSocketConnectionManagement:
    """Test WebSocket connection lifecycle management"""
    
    def test_handle_client_connect(self, mock_websocket_session, mock_settings):
        """Test client connection handling"""
        if hasattr(server, 'handle_client_connect'):
            server.settings = mock_settings
            
            with patch('time.time', return_value=12345):
                server.handle_client_connect(mock_websocket_session)
                
                # Should create basic client entry
                assert mock_websocket_session.id in mock_settings.clients
                client = mock_settings.clients[mock_websocket_session.id]
                assert client.clientID == mock_websocket_session.id
                assert client.discoveryTime == 12345
    
    def test_handle_client_disconnect_existing_client(self, mock_settings, mock_client):
        """Test client disconnect with existing client"""
        session_id = "test_session_123"
        mock_client.clientID = session_id
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings
        
        with patch('time.time', return_value=54321):
            server.handle_client_disconnect(session_id)
            
            assert mock_client.lastSeen == 54321
            assert mock_client.ready is False
    
    def test_handle_client_disconnect_unknown_client(self, mock_settings):
        """Test client disconnect with unknown session"""
        server.settings = mock_settings
        
        # Should not raise an exception
        server.handle_client_disconnect("unknown_session_id")
    
    @patch('server.saveSettings')
    def test_periodic_client_cleanup(self, mock_save, mock_settings, mock_client):
        """Test periodic cleanup of old clients"""
        if not hasattr(server, 'cleanup_old_clients'):
            pytest.skip("cleanup_old_clients not implemented")
            
        # Create old client (offline for over 24 hours)
        old_client = server.Client()
        old_client.lastSeen = time.time() - (25 * 3600)  # 25 hours ago
        old_client.ready = False
        
        # Create recent client
        recent_client = mock_client
        recent_client.lastSeen = time.time() - 60  # 1 minute ago
        
        mock_settings.clients['old'] = old_client
        mock_settings.clients['recent'] = recent_client
        server.settings = mock_settings
        
        server.cleanup_old_clients()
        
        # Old client should be removed, recent client should remain
        assert 'old' not in mock_settings.clients
        assert 'recent' in mock_settings.clients
        
        mock_save.assert_called_once()


class TestMessageValidation:
    """Test WebSocket message validation"""
    
    def test_validate_client_info_message_valid(self):
        """Test validation of valid client info message"""
        if hasattr(server, 'validate_client_info_message'):
            message = {
                'type': 'clientInfo',
                'friendlyName': 'Test Device',
                'deviceWidth': 1920,
                'deviceHeight': 1080
            }
            
            assert server.validate_client_info_message(message) is True
    
    def test_validate_client_info_message_invalid(self):
        """Test validation of invalid client info message"""
        if hasattr(server, 'validate_client_info_message'):
            # Missing required fields
            message = {
                'type': 'clientInfo',
                'friendlyName': 'Test Device'
                # Missing deviceWidth, deviceHeight
            }
            
            assert server.validate_client_info_message(message) is False
    
    def test_sanitize_message_data(self):
        """Test message data sanitization"""
        if hasattr(server, 'sanitize_message_data'):
            message = {
                'type': 'clientInfo',
                'friendlyName': '<script>alert("xss")</script>',
                'deviceWidth': '1920',  # String instead of int
                'maliciousField': 'DROP TABLE clients;'
            }
            
            sanitized = server.sanitize_message_data(message)
            
            assert 'script' not in sanitized.get('friendlyName', '')
            assert isinstance(sanitized.get('deviceWidth'), int)
            assert 'maliciousField' not in sanitized