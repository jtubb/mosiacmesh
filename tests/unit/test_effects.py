"""Unit tests for the transition effect plugin framework."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import effects


class TestRegistry:
    def test_three_effects_registered(self):
        names = set(effects.EFFECTS.keys())
        assert {"fade", "audiofade", "wipe"} <= names

    def test_get_effect(self):
        assert effects.get_effect("fade").name == "fade"
        assert effects.get_effect("nope") is None

    def test_catalog_shape(self):
        cat = {e["name"]: e for e in effects.effect_catalog()}
        assert cat["fade"]["label"]
        assert cat["fade"]["params"][0]["key"] == "duration"
        assert cat["fade"]["params"][0]["default"] == 600
        wipe_params = {p["key"]: p for p in cat["wipe"]["params"]}
        assert wipe_params["direction"]["type"] == "choice"
        assert wipe_params["direction"]["choices"] == ["left", "right", "up", "down"]


class TestFade:
    def test_fade_start(self):
        v, a = effects.get_effect("fade").video_filters(
            "start", {"duration": 600}, {"duration_ms": 5000, "out_w": 80, "out_h": 60})
        assert v == ["fade=t=in:st=0:d=0.6"]
        assert a == []

    def test_fade_end_start_time_from_duration(self):
        v, a = effects.get_effect("fade").video_filters(
            "end", {"duration": 600}, {"duration_ms": 5000, "out_w": 80, "out_h": 60})
        assert v == ["fade=t=out:st=4.4:d=0.6"]
        assert a == []

    def test_audiofade_only_audio(self):
        v, a = effects.get_effect("audiofade").video_filters(
            "start", {"duration": 1000}, {"duration_ms": 5000})
        assert v == []
        assert a == ["afade=t=in:st=0:d=1"]

    def test_wipe_is_noop(self):
        v, a = effects.get_effect("wipe").video_filters(
            "start", {"direction": "left", "duration": 600}, {"duration_ms": 5000})
        assert v == [] and a == []

    def test_resolve_applies_defaults(self):
        resolved = effects.get_effect("fade").resolve({})
        assert resolved["duration"] == 600
