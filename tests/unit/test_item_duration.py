"""Auto (missing) item duration resolves to the content's natural length
(video) or a 20s default (image/animation) — never 0."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import server
from mosaicmesh.state import MediaElement
from mosaicmesh import render


def _me(file=None, duration=None):
    me = MediaElement()
    me.file = file
    me.duration = duration
    return me


class TestResolvedDuration:
    def test_explicit_duration_passes_through(self):
        assert render._duration_ms(_me(file="/media/server/videos/a.mp4", duration=12)) == 12000

    def test_missing_image_duration_defaults_to_20s(self):
        assert render._duration_ms(_me(file="/media/server/images/logo.png", duration=None)) == 20000

    def test_missing_animation_duration_defaults_to_20s(self):
        assert render._duration_ms(_me(file="lissajous", duration=None)) == 20000

    def test_missing_unprobed_video_defaults_to_20s(self):
        # a video whose length isn't in the probe cache -> default, not 0
        assert render._duration_ms(_me(file="/media/server/videos/never_probed.mp4", duration=None)) == 20000

    def test_never_zero_for_missing(self):
        assert render._duration_ms(_me(file="/media/server/images/x.png", duration=None)) > 0

    def test_missing_video_uses_probed_length_when_cached(self, monkeypatch):
        # Seed whatever cache /api/media uses so the resolver finds 30.0s.
        me = _me(file="/media/server/videos/probed.mp4", duration=None)
        monkeypatch.setattr(render, "_probed_video_seconds", lambda f: 30.0, raising=False)
        assert render._duration_ms(me) == 30000
