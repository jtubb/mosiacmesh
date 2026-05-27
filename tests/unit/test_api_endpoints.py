"""
Unit tests for API endpoints
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp.test_utils import make_mocked_request

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


class TestDiscoveryAPI:
    """Test discovery API endpoints"""
    
    @pytest.mark.asyncio
    async def test_api_discovery_devices_success(self, mock_settings, mock_client):
        """Test successful devices endpoint"""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings
        
        request = make_mocked_request('GET', '/api/discovery/devices')
        
        response = await server.api_discovery_devices(request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data['success'] is True
        assert len(data['devices']) == 1
        assert data['total'] == 1
        assert data['online'] == 1
        
        device = data['devices'][0]
        assert device['clientKey'] == 'test123'
        assert device['friendlyName'] == 'TestClient'
        assert device['displayID'] == 'Desktop'
    
    @pytest.mark.asyncio
    async def test_api_discovery_devices_empty(self, mock_settings):
        """Test devices endpoint with no clients"""
        server.settings = mock_settings
        
        request = make_mocked_request('GET', '/api/discovery/devices')
        
        response = await server.api_discovery_devices(request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data['success'] is True
        assert len(data['devices']) == 0
        assert data['total'] == 0
        assert data['online'] == 0
    
    @pytest.mark.asyncio
    async def test_api_discovery_stats(self, mock_settings, mock_client):
        """Test discovery stats endpoint"""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings
        
        request = make_mocked_request('GET', '/api/discovery/stats')
        
        response = await server.api_discovery_stats(request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data['success'] is True
        assert 'totalDevices' in data
        assert 'onlineDevices' in data
        assert 'autoConfiguredDevices' in data
        assert 'displayGroups' in data
        assert 'cacheStats' in data
    
    @pytest.mark.asyncio 
    async def test_api_discovery_configure_post(self, mock_settings, mock_client):
        """Test device configuration endpoint"""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings
        
        config_data = {
            'clientKey': 'test123',
            'displayID': 'NewDisplay',
            'friendlyName': 'Updated Client'
        }
        
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value=config_data)

        with patch('server.saveSettings') as mock_save:
            response = await server.api_discovery_configure(request)
        
        assert response.status == 200
        data = json.loads(response.text)
        assert data['success'] is True
        
        # Check client was updated
        updated_client = mock_settings.clients["test123"]
        assert updated_client.displayID == 'NewDisplay'
        assert updated_client.friendlyName == 'Updated Client'
        mock_save.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_api_discovery_configure_invalid_client(self, mock_settings):
        """Test configure endpoint with invalid client key"""
        server.settings = mock_settings
        
        config_data = {
            'clientKey': 'nonexistent',
            'displayID': 'NewDisplay'
        }
        
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value=config_data)

        response = await server.api_discovery_configure(request)

        assert response.status == 404
        data = json.loads(response.text)
        assert data['success'] is False
        assert 'not found' in data['error']
    
    @pytest.mark.asyncio
    async def test_api_discovery_configure_invalid_json(self, mock_settings):
        """Test configure endpoint with invalid JSON"""
        server.settings = mock_settings
        
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(side_effect=ValueError("Expecting value"))
        
        response = await server.api_discovery_configure(request)
        
        assert response.status == 400
        data = json.loads(response.text)
        assert data['success'] is False
        assert 'Invalid JSON' in data['error']
    
    @pytest.mark.asyncio
    async def test_api_discovery_configure_missing_client_key(self, mock_settings):
        """Test configure endpoint without client key"""
        server.settings = mock_settings
        
        config_data = {
            'displayID': 'NewDisplay'
            # Missing clientKey
        }
        
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value=config_data)

        response = await server.api_discovery_configure(request)

        assert response.status == 400
        data = json.loads(response.text)
        assert data['success'] is False
        assert 'clientKey required' in data['error']


class TestAPIHelpers:
    """Test API helper functions"""
    
    def test_format_device_info(self, mock_client):
        """Test device information formatting"""
        if hasattr(server, 'format_device_info'):
            client_key = "test123"
            formatted = server.format_device_info(client_key, mock_client)
            
            assert formatted['clientKey'] == client_key
            assert formatted['friendlyName'] == mock_client.friendlyName
            assert formatted['displayID'] == mock_client.displayID
            assert formatted['deviceType'] == mock_client.deviceType
            assert formatted['resolution'] == f"{mock_client.deviceWidth}x{mock_client.deviceHeight}"
            assert 'timeSinceLastSeen' in formatted
            assert 'isOnline' in formatted
    
    def test_calculate_online_status(self, mock_client):
        """Test online status calculation"""
        if hasattr(server, 'calculate_online_status'):
            # Recent activity - should be online
            mock_client.lastSeen = int(time.time()) - 10
            mock_client.ready = True
            assert server.calculate_online_status(mock_client) is True
            
            # Old activity - should be offline
            mock_client.lastSeen = int(time.time()) - 600
            assert server.calculate_online_status(mock_client) is False
            
            # Not ready - should be offline
            mock_client.lastSeen = int(time.time()) - 10
            mock_client.ready = False
            assert server.calculate_online_status(mock_client) is False
    
    def test_get_discovery_statistics(self, mock_settings, mock_client):
        """Test discovery statistics calculation"""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings
        
        if hasattr(server, 'get_discovery_statistics'):
            stats = server.get_discovery_statistics()
            
            assert 'totalDevices' in stats
            assert 'onlineDevices' in stats
            assert 'autoConfiguredDevices' in stats
            assert 'displayGroups' in stats
            assert stats['totalDevices'] >= 0
            assert stats['onlineDevices'] >= 0
            assert stats['autoConfiguredDevices'] >= 0

class TestClientIp:
    def test_prefers_x_forwarded_for(self):
        req = make_mocked_request('GET', '/', headers={'X-Forwarded-For': '203.0.113.7, 10.0.0.1'})
        assert server._client_ip(req) == '203.0.113.7'

    def test_falls_back_to_remote_without_xff(self):
        req = make_mocked_request('GET', '/')
        # no X-Forwarded-For -> returns request.remote (the socket peer)
        assert server._client_ip(req) == req.remote


class TestDeviceNormalization:
    def test_engine_dict_to_string(self):
        assert server._engine_str({'default': 'WebKit'}) == 'WebKit'
    def test_engine_string_passthrough(self):
        assert server._engine_str('Blink') == 'Blink'
    def test_engine_empty(self):
        assert server._engine_str(None) == '' and server._engine_str({}) == ''
    def test_device_type_enum_value(self):
        class _E:  # mimics an enum with a .value
            value = 'desktop'
        assert server._device_type_str(_E()) == 'desktop'
    def test_device_type_string_passthrough(self):
        assert server._device_type_str('tablet') == 'tablet'


class TestLegacyIpadHeuristic:
    """Reclassify legacy iPads that present a Mac ('Request Desktop') UA."""

    def test_apple_desktop_ipad_res_with_touch_is_ipad(self):
        # 1st-gen iPad portrait, parsed as Apple desktop, reports touch
        assert server._is_legacy_ipad_signal('Apple', 'desktop', 768, 1024, True) is True

    def test_orientation_independent(self):
        # landscape (1024x768) matches the same way
        assert server._is_legacy_ipad_signal('Apple', 'desktop', 1024, 768, True) is True

    def test_no_touch_is_not_ipad(self):
        # a real Mac at an iPad-like resolution but without touch stays desktop
        assert server._is_legacy_ipad_signal('Apple', 'desktop', 768, 1024, False) is False

    def test_non_ipad_resolution_is_not_ipad(self):
        assert server._is_legacy_ipad_signal('Apple', 'desktop', 1920, 1080, True) is False

    def test_non_apple_is_not_ipad(self):
        assert server._is_legacy_ipad_signal('Microsoft', 'desktop', 768, 1024, True) is False

    def test_already_tablet_is_not_reprocessed(self):
        # a correctly-parsed iPad (device_type already tablet) is not a desktop-misparse
        assert server._is_legacy_ipad_signal('Apple', 'tablet', 768, 1024, True) is False

    def test_missing_dimensions_guarded(self):
        assert server._is_legacy_ipad_signal('Apple', 'desktop', None, None, True) is False


class TestHostnameResolution:
    """Reverse-DNS hostname helpers."""

    def test_short_hostname_strips_domain_and_dot(self):
        assert server._short_hostname('Jons-iPad.lan.') == 'Jons-iPad'

    def test_short_hostname_bare_label(self):
        assert server._short_hostname('mediawall1') == 'mediawall1'

    def test_short_hostname_empty(self):
        assert server._short_hostname('') == '' and server._short_hostname(None) == ''

    def test_adopt_when_name_not_custom(self):
        c = server.Client()
        c.nameIsCustom = False
        assert server._adopt_hostname_as_name(c, 'living-room-ipad') is True

    def test_no_adopt_when_name_custom(self):
        c = server.Client()
        c.nameIsCustom = True
        assert server._adopt_hostname_as_name(c, 'living-room-ipad') is False

    def test_no_adopt_when_no_hostname(self):
        c = server.Client()
        c.nameIsCustom = False
        assert server._adopt_hostname_as_name(c, '') is False
