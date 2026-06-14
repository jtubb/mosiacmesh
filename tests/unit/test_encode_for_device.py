"""Always-encode-for-device: fit helper, transcode cmd, FULL render path."""
import sys
from pathlib import Path
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

import pytest
from mosaicmesh import render as R


def test_fit_within_downscales_keeping_aspect():
    assert R._fit_within(1920, 1080, (1280, 720)) == (1280, 720)

def test_fit_within_portrait():
    w, h = R._fit_within(1080, 1920, (1280, 720))
    assert h == 720 and w % 2 == 0 and w <= 1280 and abs(w/h - 1080/1920) < 0.02

def test_fit_within_no_upscale():
    assert R._fit_within(640, 480, (1280, 720)) == (640, 480)

def test_fit_within_even_dims():
    w, h = R._fit_within(1001, 333, (1280, 720))
    assert w % 2 == 0 and h % 2 == 0


def test_build_transcode_cmd_shape():
    cmd = R.build_ffmpeg_transcode_cmd("/src/a.mp4", "/out/full_tok_0.mp4", 1280, 720)
    assert cmd[0] == "ffmpeg"
    assert "/src/a.mp4" in cmd and "/out/full_tok_0.mp4" == cmd[-1]
    j = " ".join(cmd)
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in j
    assert "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x000000" in j
    assert "-profile:v baseline" in j

def test_build_transcode_cmd_extra_filters():
    cmd = R.build_ffmpeg_transcode_cmd("/s.mp4", "/o.mp4", 640, 480,
                                       extra_video_filters=["fade=in:0:30"])
    assert "fade=in:0:30" in " ".join(cmd)


def test_is_renderable_includes_full():
    from mosaicmesh.state import MediaElement, PlayMode
    def me(pm):
        m = MediaElement(); m.playmode = pm; m.file = "/media/server/videos/a.mp4"; return m
    assert R._is_renderable(me(PlayMode.SEGMENT)) is True
    assert R._is_renderable(me(PlayMode.INDIVIDUAL)) is True
    assert R._is_renderable(me(PlayMode.FULL)) is True
    assert R._is_renderable(me(PlayMode.SCRIPT)) is False
    assert R._is_renderable(me(PlayMode.DEFAULT)) is False


def test_per_client_items_full_uses_shared_central_asset():
    from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    try:
        d = Display(); d.boundingBox = [0, 0, 10, 10]; d.renderedToken = "tok9"; d.loop = False
        server.settings.displays["G1"] = d
        c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
        c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]; c.cacheMode = "none"
        server.settings.clients["c1"] = c
        me = MediaElement(); me.id = 0; me.file = "/media/server/videos/big.mov"
        me.playmode = PlayMode.FULL; me.duration = 5
        d.mediaElements = [me]
        items = R._per_client_items(d, "c1", c)
        assert items[0]["file"] == "/media/server/videos/full_tok9_0.mp4"
        assert items[0]["file"] != me.file
    finally:
        server.settings = prev


import asyncio

def test_encode_group_full_writes_shared_asset(tmp_path, monkeypatch):
    from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    try:
        d = Display(); d.boundingBox = [0, 0, 10, 10]
        server.settings.displays["G1"] = d
        c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
        c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
        server.settings.clients["c1"] = c
        me = MediaElement(); me.id = 0; me.file = "/media/server/videos/a.mp4"
        me.playmode = PlayMode.FULL; me.duration = 5

        captured = {}
        monkeypatch.setattr(R, "resolve_media_path", lambda f: "/abs/a.mp4")
        monkeypatch.setattr(R, "get_video_dimensions", lambda p: (1920, 1080))
        def _fake_cmd(src, out, w, h, **kw):
            captured["out"] = out; captured["wh"] = (w, h); return ["ffmpeg", out]
        monkeypatch.setattr(R, "build_ffmpeg_transcode_cmd", _fake_cmd)
        async def _fake_run(cmd, label, sem): captured["ran"] = True
        monkeypatch.setattr(R, "_run_ffmpeg", _fake_run)

        asyncio.run(R._encode_group([me], "G1", "tok123"))
        assert captured.get("ran") is True
        assert captured["out"].replace("\\", "/").endswith("media/server/videos/full_tok123_0.mp4")
        assert captured["wh"] == (1280, 720)
    finally:
        server.settings = prev
