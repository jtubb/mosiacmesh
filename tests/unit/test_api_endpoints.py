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

    def test_in_addr_arpa(self):
        assert server._in_addr_arpa('192.168.1.50') == '50.1.168.192.in-addr.arpa.'

    def test_is_private_ipv4(self):
        assert server._is_private_ipv4('192.168.1.50') is True
        assert server._is_private_ipv4('10.0.0.5') is True
        assert server._is_private_ipv4('172.16.4.4') is True
        assert server._is_private_ipv4('169.254.1.1') is True

    def test_is_not_private_ipv4(self):
        assert server._is_private_ipv4('8.8.8.8') is False
        assert server._is_private_ipv4('172.32.0.1') is False   # outside 16-31
        assert server._is_private_ipv4('::1') is False          # not IPv4 dotted
        assert server._is_private_ipv4('') is False


class TestArucoIds:
    """generateAruco must give every client a globally-unique arucoID, resolving
    collisions left by the old counter (which produced duplicate markers)."""

    def test_unique_ids_resolves_collisions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s = server.Settings()
        a = server.Client(); a.arucoID = 1; a.displayID = "G"
        b = server.Client(); b.arucoID = 1; b.displayID = "G"   # collision with a
        c = server.Client(); c.arucoID = None; c.displayID = "G"
        s.clients = {"a": a, "b": b, "c": c}
        server.settings = s

        class _SM:
            def broadcast(self, *x, **k): pass
        monkeypatch.setattr(server, "socketmanager", _SM(), raising=False)

        server.generateAruco()
        ids = [a.arucoID, b.arucoID, c.arucoID]
        assert all(i is not None for i in ids)
        assert len(set(ids)) == 3            # all distinct
        assert a.arucoID == 1                # first keeps its id


class TestClientMerge:
    """A browser-cache-cleared device reconnects with a new id; once it resolves
    to the same hostname + attributes as an OFFLINE prior client, merge them."""

    def _make(self, key, online, host="sign1screen1", dt="tablet", w=768, h=1024):
        c = server.Client()
        c.hostname = host; c.isOnline = online
        c.deviceType = dt; c.deviceWidth = w; c.deviceHeight = h
        return key, c

    def _settings_with(self, *clients):
        s = server.Settings(); s.clients = {k: c for k, c in clients}
        server.settings = s
        return s

    def test_merges_offline_match_and_adopts_config(self):
        ok, old = self._make("oldkey", False)
        old.displayID = "Test Group"; old.friendlyName = "Lobby Screen"
        old.nameIsCustom = True; old.measuredPerimeter = [[1, 2]]; old.arucoID = 7
        nk, new = self._make("newkey", True)
        new.displayID = "Tablet"; new.friendlyName = "iPad_newkey1"
        s = self._settings_with((ok, old), (nk, new))

        merged = server._merge_reconnected_client("newkey", new)

        assert merged == "oldkey"
        assert "oldkey" not in s.clients          # old record removed
        assert new.displayID == "Test Group"      # group preserved
        assert new.friendlyName == "Lobby Screen" # custom name preserved
        assert new.measuredPerimeter == [[1, 2]]  # calibration preserved
        assert new.arucoID == 7

    def test_merges_even_when_old_still_online(self):
        # No offline gate: a duplicate is collapsed immediately, without waiting
        # for the old session's socket-close to be detected.
        ok, old = self._make("oldkey", True)      # still marked online
        old.displayID = "Test Group"
        nk, new = self._make("newkey", True)
        s = self._settings_with((ok, old), (nk, new))
        assert server._merge_reconnected_client("newkey", new) == "oldkey"
        assert "oldkey" not in s.clients
        assert new.displayID == "Test Group"

    def test_no_merge_when_hostname_differs(self):
        ok, old = self._make("oldkey", False, host="other-screen")
        nk, new = self._make("newkey", True, host="sign1screen1")
        self._settings_with((ok, old), (nk, new))
        assert server._merge_reconnected_client("newkey", new) is None

    def test_no_merge_when_attributes_differ(self):
        ok, old = self._make("oldkey", False, w=1024, h=768)  # rotated/different
        nk, new = self._make("newkey", True, w=768, h=1024)
        self._settings_with((ok, old), (nk, new))
        assert server._merge_reconnected_client("newkey", new) is None

    def test_no_merge_without_hostname(self):
        ok, old = self._make("oldkey", False, host="")
        nk, new = self._make("newkey", True, host="")
        self._settings_with((ok, old), (nk, new))
        assert server._merge_reconnected_client("newkey", new) is None


class TestCalibrate:
    """Calibration upload must not 500 when an image has no ArUco markers."""

    def test_calibrate_no_markers_returns_url_without_crashing(self, tmp_path):
        import numpy as np, cv2
        # A blank image -> zero markers -> relevantContours stays empty
        img = np.full((120, 160, 3), 240, np.uint8)
        p = tmp_path / "blank.png"
        cv2.imwrite(str(p), img)

        server.settings = server.Settings()
        result = server.calibrate(str(p))

        # Returns the 2-segment media URL (media_handler inserts images/),
        # not the 3-segment disk path, and does not raise.
        assert result == ("media/displays/calibration.png", "text/html")


class TestMediaRange:
    """media_handler must answer byte-range requests with 206 — including the
    OPEN-ENDED ranges browsers send when seeking video ("bytes=N-"). Regression:
    those used to fall through to a 200 full-file-from-0, so Chrome treated a
    seek as a reload and restarted playback at 0 (iOS Safari sends bounded
    ranges, so it was unaffected — which masked the bug)."""

    FILE_SIZE = 10000

    def _request(self, range_header=None):
        headers = {'Range': range_header} if range_header else {}
        req = make_mocked_request('GET', '/media/c/videos/clip.mp4', headers=headers)
        req._match_info = {'client': 'c', 'file': 'clip.mp4'}
        return req

    @pytest.mark.asyncio
    async def test_open_ended_range_returns_206(self):
        """bytes=1000-  ->  206 covering 1000..EOF (the seek case that regressed)."""
        handle = MagicMock()
        handle.read.return_value = b'x' * (self.FILE_SIZE - 1000)
        with patch('server.os.path.isfile', return_value=True), \
             patch('server.os.path.getsize', return_value=self.FILE_SIZE), \
             patch('server.get_pooled_file_handle', return_value=handle):
            resp = await server.media_handler(self._request('bytes=1000-'))
        assert resp.status == 206
        assert resp.headers['Content-Range'] == f'bytes 1000-{self.FILE_SIZE - 1}/{self.FILE_SIZE}'
        assert resp.headers['Accept-Ranges'] == 'bytes'
        handle.seek.assert_called_once_with(1000)
        handle.read.assert_called_once_with(self.FILE_SIZE - 1000)

    @pytest.mark.asyncio
    async def test_bounded_range_returns_206(self):
        """bytes=0-1023  ->  206 for exactly that window (the always-worked case)."""
        handle = MagicMock()
        handle.read.return_value = b'x' * 1024
        with patch('server.os.path.isfile', return_value=True), \
             patch('server.os.path.getsize', return_value=self.FILE_SIZE), \
             patch('server.get_pooled_file_handle', return_value=handle):
            resp = await server.media_handler(self._request('bytes=0-1023'))
        assert resp.status == 206
        assert resp.headers['Content-Range'] == f'bytes 0-1023/{self.FILE_SIZE}'
        handle.seek.assert_called_once_with(0)
        handle.read.assert_called_once_with(1024)

    @pytest.mark.asyncio
    async def test_no_range_returns_200_full(self):
        """No Range header  ->  200 full file, no Content-Range."""
        with patch('server.os.path.isfile', return_value=True), \
             patch('server.os.path.getsize', return_value=self.FILE_SIZE), \
             patch('server.get_cached_file', return_value=b'full'):
            resp = await server.media_handler(self._request())
        assert resp.status == 200
        assert 'Content-Range' not in resp.headers

    @pytest.mark.asyncio
    async def test_open_ended_range_reads_to_eof(self):
        """Open-ended range returns through EOF (no chunk cap) -- a truncated 206
        makes Chrome-for-iOS treat the file as unsupported."""
        big = 20 * 1024 * 1024
        handle = MagicMock()
        handle.read.return_value = b'x' * (big - 1000)
        with patch('server.os.path.isfile', return_value=True), \
             patch('server.os.path.getsize', return_value=big), \
             patch('server.get_pooled_file_handle', return_value=handle):
            resp = await server.media_handler(self._request('bytes=1000-'))
        assert resp.status == 206
        assert resp.headers['Content-Range'] == f'bytes 1000-{big - 1}/{big}'
        handle.read.assert_called_once_with(big - 1000)
