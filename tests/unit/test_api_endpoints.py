"""
Unit tests for API endpoints
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp.test_utils import make_mocked_request, TestServer, TestClient
from aiohttp import web

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
        """Test device configuration endpoint moves a client to an
        existing display group (PR-14: target must exist in
        settings.displays — Mobile is in the mock fixture)."""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings

        config_data = {
            'clientKey': 'test123',
            'displayID': 'Mobile',
            'friendlyName': 'Updated Client'
        }

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value=config_data)

        with patch('mosaicmesh.api.discovery.saveSettings') as mock_save:
            response = await server.api_discovery_configure(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data['success'] is True

        # Check client was updated
        updated_client = mock_settings.clients["test123"]
        assert updated_client.displayID == 'Mobile'
        assert updated_client.friendlyName == 'Updated Client'
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_discovery_configure_rejects_unknown_displayID(self, mock_settings, mock_client):
        """PR-14: assigning a displayID that doesn't exist in
        settings.displays returns 404. Without this guard, a typo
        ('Tablt') silently sets client.displayID to a string no group
        has — the client vanishes from the timeline."""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'clientKey': 'test123',
            'displayID': 'NonExistent',
        })

        response = await server.api_discovery_configure(request)
        assert response.status == 404
        data = json.loads(response.text)
        assert data['success'] is False
        assert "NonExistent" in data['error']
        assert "create it first" in data['error']
        # Client untouched
        assert mock_settings.clients["test123"].displayID == 'Desktop'

    @pytest.mark.asyncio
    async def test_api_discovery_configure_rejects_empty_displayID(self, mock_settings, mock_client):
        """PR-14: empty/whitespace displayID rejected with 400."""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'clientKey': 'test123',
            'displayID': '   ',
        })
        response = await server.api_discovery_configure(request)
        assert response.status == 400
        assert mock_settings.clients["test123"].displayID == 'Desktop'

    @pytest.mark.asyncio
    async def test_api_discovery_configure_rejects_non_string_displayID(self, mock_settings, mock_client):
        """PR-14: non-string displayID rejected with 400."""
        mock_settings.clients["test123"] = mock_client
        server.settings = mock_settings

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'clientKey': 'test123',
            'displayID': 42,
        })
        response = await server.api_discovery_configure(request)
        assert response.status == 400
        assert mock_settings.clients["test123"].displayID == 'Desktop'


class TestBulkAssign:
    """PR-15: POST /api/discovery/configure {action:'bulk_assign', clientKeys, displayID}."""

    def _make_clients(self, mock_settings, count, displayID='Desktop'):
        from unittest.mock import MagicMock
        for i in range(count):
            c = server.Client()
            c.friendlyName = f"Client{i}"
            c.displayID = displayID
            c.deviceType = "tablet"
            c.autoConfigured = True
            mock_settings.clients[f"k{i}"] = c

    @pytest.mark.asyncio
    async def test_bulk_assign_happy(self, mock_settings):
        self._make_clients(mock_settings, 3, displayID='Desktop')
        server.settings = mock_settings

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'action': 'bulk_assign',
            'clientKeys': ['k0', 'k1', 'k2'],
            'displayID': 'Mobile',
        })

        with patch('mosaicmesh.api.discovery.saveSettings'):
            resp = await server.api_discovery_configure(request)
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['success'] is True
        assert data['movedCount'] == 3
        assert set(data['moved']) == {'k0', 'k1', 'k2'}
        assert data['missing'] == []
        assert all(mock_settings.clients[k].displayID == 'Mobile' for k in ['k0', 'k1', 'k2'])
        # PR-15: explicit move clears autoConfigured so the next REGISTER
        # doesn't undo the operator's choice via auto_configure_client.
        assert all(mock_settings.clients[k].autoConfigured is False for k in ['k0', 'k1', 'k2'])

    @pytest.mark.asyncio
    async def test_bulk_assign_partial_with_missing(self, mock_settings):
        self._make_clients(mock_settings, 2)
        server.settings = mock_settings

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'action': 'bulk_assign',
            'clientKeys': ['k0', 'ghost', 'k1'],
            'displayID': 'Mobile',
        })
        with patch('mosaicmesh.api.discovery.saveSettings'):
            resp = await server.api_discovery_configure(request)
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['movedCount'] == 2
        assert set(data['moved']) == {'k0', 'k1'}
        assert data['missing'] == ['ghost']

    @pytest.mark.asyncio
    async def test_bulk_assign_unknown_target_404(self, mock_settings):
        self._make_clients(mock_settings, 2)
        server.settings = mock_settings

        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'action': 'bulk_assign',
            'clientKeys': ['k0', 'k1'],
            'displayID': 'NonExistent',
        })
        resp = await server.api_discovery_configure(request)
        assert resp.status == 404
        data = json.loads(resp.text)
        assert 'NonExistent' in data['error']
        assert 'create it first' in data['error']
        # Atomic guard: clients are NOT moved when target validation fails.
        assert all(mock_settings.clients[k].displayID == 'Desktop' for k in ['k0', 'k1'])

    @pytest.mark.asyncio
    async def test_bulk_assign_empty_keys_400(self, mock_settings):
        server.settings = mock_settings
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'action': 'bulk_assign',
            'clientKeys': [],
            'displayID': 'Mobile',
        })
        resp = await server.api_discovery_configure(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_bulk_assign_missing_keys_field_400(self, mock_settings):
        server.settings = mock_settings
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'action': 'bulk_assign',
            'displayID': 'Mobile',
        })
        resp = await server.api_discovery_configure(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_bulk_assign_empty_target_400(self, mock_settings):
        self._make_clients(mock_settings, 1)
        server.settings = mock_settings
        request = make_mocked_request('POST', '/api/discovery/configure')
        request.json = AsyncMock(return_value={
            'action': 'bulk_assign',
            'clientKeys': ['k0'],
            'displayID': '  ',
        })
        resp = await server.api_discovery_configure(request)
        assert resp.status == 400
    
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
    """Calibration upload must not 500 when an image has no ArUco markers,
    and must report the true detection count when markers are present."""

    def test_calibrate_no_markers_returns_url_without_crashing(self, tmp_path):
        import numpy as np, cv2, json
        # A blank image -> zero markers -> relevantContours stays empty
        img = np.full((120, 160, 3), 240, np.uint8)
        p = tmp_path / "blank.png"
        cv2.imwrite(str(p), img)

        server.settings = server.Settings()
        body_str, content_type = server.calibrate(str(p))

        # PR-28: calibrate() now returns JSON so the calibration modal can
        # surface detected/mapped counts. Image URL is the 2-segment media
        # URL (media_handler inserts images/), not the 3-segment disk path.
        assert content_type == "application/json"
        body = json.loads(body_str)
        assert body["success"] is True
        assert body["detected"] == 0
        assert body["mapped"] == 0
        assert body["imageUrl"] == "media/displays/calibration.png"

    def test_calibrate_reports_actual_marker_count(self, tmp_path):
        """Regression: PR-28 first cut read `len(corners)` AFTER the per-marker
        loop, but the loop body reassigns `corners` to a single marker's
        (4, 2) reshape — so detected always reported 4 regardless of the true
        count. Synthesize a photo with 6 markers and assert detected == 6."""
        import numpy as np, cv2, json
        # Build a 600x400 image with 6 DICT_6X6_50 markers laid out in a grid.
        # generateMarker (cv2 >= 4.7) replaces drawMarker (< 4.7); accept both.
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_50)
        gen = getattr(cv2.aruco, "generateImageMarker", None) or cv2.aruco.drawMarker
        img = np.full((400, 600, 3), 255, np.uint8)
        marker_px = 80
        positions = [(20, 20), (260, 20), (500, 20),
                     (20, 300), (260, 300), (500, 300)]
        for i, (x, y) in enumerate(positions):
            m = gen(d, i, marker_px)
            img[y:y+marker_px, x:x+marker_px] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
        p = tmp_path / "six.png"
        cv2.imwrite(str(p), img)

        server.settings = server.Settings()
        body_str, _ = server.calibrate(str(p))
        body = json.loads(body_str)
        # detect_aruco_markers must see all 6; mapped is 0 because no client
        # in Settings owns these arucoIDs.
        assert body["detected"] == 6
        assert body["mapped"] == 0


class TestMediaRange:
    """media_handler streams video via aiohttp FileResponse (T1.6). It must
    answer byte-range requests with 206 — including the OPEN-ENDED ranges
    browsers send when seeking video ("bytes=N-"), which MUST return every byte
    through EOF: a truncated 206 makes the iPad-1 UIWebView report
    MEDIA_ERR_SRC_NOT_SUPPORTED. A no-range request must still 200 the full file.

    These exercise the REAL FileResponse over an actual HTTP round-trip against a
    real file on disk (stronger than mocking seek/read): the 206 status,
    Content-Range, and body length are computed by FileResponse itself."""

    FILE_SIZE = 10000

    async def _client(self, tmp_path, monkeypatch):
        """Lay out media/c/videos/clip.mp4 under a temp cwd and serve it through
        the real media_handler route. media_handler resolves the file relative
        to cwd, so chdir into the temp dir."""
        mdir = tmp_path / "media" / "c" / "videos"
        mdir.mkdir(parents=True)
        (mdir / "clip.mp4").write_bytes(b"A" * self.FILE_SIZE)
        monkeypatch.chdir(tmp_path)
        app = web.Application()
        app.router.add_get('/media/{client}/{sub}/{file}', server.media_handler)
        cli = TestClient(TestServer(app))
        await cli.start_server()
        return cli

    @pytest.mark.asyncio
    async def test_open_ended_range_returns_206_to_eof(self, tmp_path, monkeypatch):
        """bytes=1000-  ->  206 covering 1000..EOF with EVERY byte to EOF (the
        iPad-1 seek case; a short read here would break video playback)."""
        cli = await self._client(tmp_path, monkeypatch)
        try:
            r = await cli.get('/media/c/videos/clip.mp4', headers={'Range': 'bytes=1000-'})
            body = await r.read()
            assert r.status == 206
            assert r.headers['Content-Range'] == f'bytes 1000-{self.FILE_SIZE - 1}/{self.FILE_SIZE}'
            assert r.headers['Accept-Ranges'] == 'bytes'
            assert len(body) == self.FILE_SIZE - 1000        # full bytes to EOF
            assert r.headers['Content-Type'] == 'video/mp4'
        finally:
            await cli.close()

    @pytest.mark.asyncio
    async def test_bounded_range_returns_206(self, tmp_path, monkeypatch):
        """bytes=0-1023  ->  206 for exactly that window."""
        cli = await self._client(tmp_path, monkeypatch)
        try:
            r = await cli.get('/media/c/videos/clip.mp4', headers={'Range': 'bytes=0-1023'})
            body = await r.read()
            assert r.status == 206
            assert r.headers['Content-Range'] == f'bytes 0-1023/{self.FILE_SIZE}'
            assert len(body) == 1024
        finally:
            await cli.close()

    @pytest.mark.asyncio
    async def test_suffix_range_returns_last_n_bytes(self, tmp_path, monkeypatch):
        """bytes=-512  ->  206 of the last 512 bytes (suffix range)."""
        cli = await self._client(tmp_path, monkeypatch)
        try:
            r = await cli.get('/media/c/videos/clip.mp4', headers={'Range': 'bytes=-512'})
            body = await r.read()
            assert r.status == 206
            assert r.headers['Content-Range'] == \
                f'bytes {self.FILE_SIZE - 512}-{self.FILE_SIZE - 1}/{self.FILE_SIZE}'
            assert len(body) == 512
        finally:
            await cli.close()

    @pytest.mark.asyncio
    async def test_no_range_returns_200_full(self, tmp_path, monkeypatch):
        """No Range header  ->  200 full file, Accept-Ranges advertised, no Content-Range."""
        cli = await self._client(tmp_path, monkeypatch)
        try:
            r = await cli.get('/media/c/videos/clip.mp4')
            body = await r.read()
            assert r.status == 200
            assert len(body) == self.FILE_SIZE
            assert r.headers['Accept-Ranges'] == 'bytes'
            assert 'Content-Range' not in r.headers
        finally:
            await cli.close()
