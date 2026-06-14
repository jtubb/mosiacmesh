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
    assert "pad=1280:720" in j
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
