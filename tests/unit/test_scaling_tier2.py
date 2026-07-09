"""Tier-2 scaling fixes: cache-push eligibility (T2.1), DeviceDetector UA-skip
(T2.2), and _render_assets_exist listdir membership (T2.4)."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import mosaicmesh.render as r
from mosaicmesh.state import Settings, Display, Client, Playlist, MediaElement, PlayMode


# ----------------------------- T2.1 -----------------------------

def _client(**kw):
    c = Client()
    c.cacheMode = kw.get("cacheMode", "lighttpd-localhost")
    c.isOnline = kw.get("isOnline", True)
    c.ip = kw.get("ip", "192.168.1.5")
    return c


def test_push_eligible_happy_path():
    assert r._client_is_push_eligible(_client()) is True


def test_push_ineligible_when_offline_incapable_or_no_ip():
    assert r._client_is_push_eligible(None) is False
    assert r._client_is_push_eligible(_client(isOnline=False)) is False
    assert r._client_is_push_eligible(_client(cacheMode="none")) is False
    assert r._client_is_push_eligible(_client(ip="")) is False


# ----------------------------- T2.2 -----------------------------

def _register_msg(src="dev1", ua_present=True):
    return {"SRC": src, "DEST": "SRV", "REQUEST": "REGISTER",
            "PAYLOAD": {"width": 768, "height": 1024, "touch": True}}


def _run_register(monkeypatch, src, ua):
    """Drive a REGISTER through msg_response with a fixed UA; returns the client."""
    from mosaicmesh.websocket import legacy
    class _Req:
        headers = {"User-Agent": ua}
        remote = "192.168.1.9"
    class _Sess:
        id = src
    monkeypatch.setattr(legacy, "session_request", lambda s: _Req())
    monkeypatch.setattr(server, "_client_ip", lambda req: "192.168.1.9", raising=False)
    monkeypatch.setattr(legacy, "auto_configure_client", lambda *a, **k: None)
    monkeypatch.setattr(legacy, "sync_new_client_to_group", lambda *a, **k: None)
    class _Mgr:
        def broadcast(self, *a, **k):
            pass
        def get(self, session_id, default=None):
            return default
    monkeypatch.setattr(server, "socketmanager", _Mgr(), raising=False)
    legacy.msg_response(_register_msg(src), _Sess())
    return server.settings.clients[src]


def test_devicedetector_skips_reparse_when_ua_unchanged(monkeypatch):
    monkeypatch.setattr(server, "settings", Settings(), raising=False)
    ua = ("Mozilla/5.0 (iPad; CPU OS 5_1_1 like Mac OS X) AppleWebKit/534.46 "
          "(KHTML, like Gecko) Version/5.1 Mobile/9B206 Safari/7534.48.3")
    with patch("mosaicmesh.websocket.legacy.DeviceDetector") as DD:
        DD.return_value.parse.return_value.os_name.return_value = "iOS"
        DD.return_value.parse.return_value.os_version.return_value = "5.1.1"
        DD.return_value.parse.return_value.engine.return_value = "WebKit"
        DD.return_value.parse.return_value.device_brand.return_value = "Apple"
        DD.return_value.parse.return_value.device_model.return_value = "iPad"
        DD.return_value.parse.return_value.device_type.return_value = "tablet"
        _run_register(monkeypatch, "dev1", ua)
        _run_register(monkeypatch, "dev1", ua)          # reconnect, same UA
        assert DD.call_count == 1, "same UA reconnect must NOT re-parse"
        _run_register(monkeypatch, "dev1", ua + " X")   # UA changed
        assert DD.call_count == 2, "changed UA must re-parse"


# ----------------------------- T2.4 -----------------------------

def _seg_group(monkeypatch, tmp_path, n_clients=3, n_items=2):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "settings", Settings(), raising=False)
    d = Display(); d.boundingBox = [0, 0, 100, 100]
    server.settings.displays["G"] = d
    keys = []
    for j in range(n_clients):
        c = Client(); c.displayID = "G"; c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
        server.settings.clients["c%d" % j] = c
        keys.append("c%d" % j)
    pl = Playlist(); pl.name = "P"
    pl.items = [{"file": "/media/server/videos/a.mp4", "playmode": "SEGMENT",
                 "duration": 5} for _ in range(n_items)]
    server.settings.playlists["P"] = pl
    return keys


def _write_all_assets(keys, token, n_items, present=True, skip=None):
    for k in keys:
        d = Path("media") / k / "videos"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n_items):
            if skip and (k, i) == skip:
                continue
            (d / ("seg_%s_%d.mp4" % (token, i))).write_bytes(b"x")


def test_render_assets_exist_true_when_all_present(monkeypatch, tmp_path):
    keys = _seg_group(monkeypatch, tmp_path, 3, 2)
    tok = r.render_token(r._build_media_elements(server.settings.playlists["P"].items), "G")
    _write_all_assets(keys, tok, 2)
    assert r._render_assets_exist("P", "G", tok) is True


def test_render_assets_exist_false_when_one_missing(monkeypatch, tmp_path):
    keys = _seg_group(monkeypatch, tmp_path, 3, 2)
    tok = r.render_token(r._build_media_elements(server.settings.playlists["P"].items), "G")
    _write_all_assets(keys, tok, 2, skip=("c1", 1))     # one file absent
    assert r._render_assets_exist("P", "G", tok) is False


def test_render_assets_exist_lists_each_dir_once(monkeypatch, tmp_path):
    """The listdir membership cache must stat each dir once, not per-file."""
    keys = _seg_group(monkeypatch, tmp_path, 3, 2)
    tok = r.render_token(r._build_media_elements(server.settings.playlists["P"].items), "G")
    _write_all_assets(keys, tok, 2)
    real_listdir = os.listdir
    calls = {"n": 0}
    def _counting(p):
        calls["n"] += 1
        return real_listdir(p)
    with patch("mosaicmesh.render.os.listdir", _counting):
        assert r._render_assets_exist("P", "G", tok) is True
    # 3 client dirs listed once each (2 items checked via cached set, not re-listed)
    assert calls["n"] == 3, "each client dir listed exactly once (got %d)" % calls["n"]
