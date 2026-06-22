import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import effects


def test_catalog_has_fade_and_wipe_only():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe"}          # audiofade folded into the audioFade toggle


def test_fade_params_include_duration_and_audioFade_boolean():
    fade = next(e for e in effects.effect_catalog() if e["name"] == "fade")
    by_key = {p["key"]: p for p in fade["params"]}
    assert by_key["duration"]["type"] == "number" and by_key["duration"]["default"] == 600
    assert by_key["audioFade"]["type"] == "boolean" and by_key["audioFade"]["default"] is True


def test_wipe_params_include_direction_scope_duration_audioFade():
    wipe = next(e for e in effects.effect_catalog() if e["name"] == "wipe")
    by_key = {p["key"]: p for p in wipe["params"]}
    assert by_key["direction"]["choices"] == ["left", "right", "up", "down"]
    assert by_key["scope"]["choices"] == ["screen", "wall"]
    assert by_key["duration"]["type"] == "number"
    assert by_key["audioFade"]["type"] == "boolean"


def test_fade_bakes_audio_only_when_audioFade_on():
    fade = effects.get_effect("fade")
    ctx = {"duration_ms": 5000}
    v, a = fade.video_filters("start", fade.resolve({"duration": 600, "audioFade": True}), ctx)
    assert v == []                                  # visual is client-side, never baked
    assert a == ["afade=t=in:st=0:d=0.6"]
    v2, a2 = fade.video_filters("end", fade.resolve({"duration": 600, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.4:d=0.6"]


def test_fade_bakes_nothing_when_audioFade_off():
    fade = effects.get_effect("fade")
    v, a = fade.video_filters("start", fade.resolve({"duration": 600, "audioFade": False}), {"duration_ms": 5000})
    assert v == [] and a == []


def test_wipe_bakes_audio_only_when_audioFade_on():
    wipe = effects.get_effect("wipe")
    v, a = wipe.video_filters("start", wipe.resolve({"duration": 600, "audioFade": True}), {"duration_ms": 5000})
    assert v == [] and a == ["afade=t=in:st=0:d=0.6"]


def test_wipe_bakes_nothing_when_audioFade_off():
    wipe = effects.get_effect("wipe")
    v, a = wipe.video_filters("start", wipe.resolve({"duration": 600, "audioFade": False}), {"duration_ms": 5000})
    assert v == [] and a == []
