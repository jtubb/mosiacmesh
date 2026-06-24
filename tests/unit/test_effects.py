"""Unit tests for effects.py — effect catalog, parameter schemas, and ffmpeg filter generation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import effects


def test_get_effect_unknown_returns_none():
    assert effects.get_effect("nope") is None


def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve", "beerfill", "scatter"}


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


def test_effect_audio_fade_default():
    assert effects.effect_audio_fade_default("fade") is True
    assert effects.effect_audio_fade_default("wipe") is True
    assert effects.effect_audio_fade_default("nope") is False


def test_slide_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "slide")
    by = {p["key"]: p for p in e["params"]}
    assert by["direction"]["choices"] == ["left", "right", "up", "down"]
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["audioFade"]["type"] == "boolean"

def test_zoom_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "zoom")
    by = {p["key"]: p for p in e["params"]}
    assert by["scale"]["type"] == "number" and by["scale"]["default"] == 0.6
    assert by["scope"]["choices"] == ["screen", "wall"]

def test_iris_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "iris")
    by = {p["key"]: p for p in e["params"]}
    assert by["scope"]["choices"] == ["screen", "wall"] and by["duration"]["type"] == "number"

def test_dissolve_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "dissolve")
    by = {p["key"]: p for p in e["params"]}
    assert by["blocks"]["type"] == "number" and by["blocks"]["default"] == 16

def test_end_fade_survives_none_duration_ms():
    # The render path feeds the item's length as ctx['duration_ms']. An "Auto"
    # (duration=None) item could leave it None; the END fade computed
    # float(ctx['duration_ms']) and crashed the whole render. It must clamp to 0
    # instead (fade-out starts at st=0), never raise.
    fade = effects.get_effect("fade")
    v, a = fade.video_filters("end", fade.resolve({"duration": 600, "audioFade": True}),
                              {"duration_ms": None})
    assert v == [] and a == ["afade=t=out:st=0:d=0.6"]


def test_end_fade_missing_duration_ms_key():
    # Same guard when the key is absent entirely.
    fade = effects.get_effect("fade")
    v, a = fade.video_filters("end", fade.resolve({"duration": 600, "audioFade": True}), {})
    assert a == ["afade=t=out:st=0:d=0.6"]


def test_new_effects_bake_audio_only():
    for name in ("slide", "zoom", "iris", "dissolve"):
        eff = effects.get_effect(name)
        v, a = eff.video_filters("start", eff.resolve({"duration": 600, "audioFade": True}), {"duration_ms": 5000})
        assert v == [] and a == ["afade=t=in:st=0:d=0.6"]
        v2, a2 = eff.video_filters("start", eff.resolve({"duration": 600, "audioFade": False}), {"duration_ms": 5000})
        assert v2 == [] and a2 == []


def test_beerfill_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "beerfill")
    by = {p["key"]: p for p in e["params"]}
    assert by["beerType"]["choices"] == ["pale", "amber", "stout"] and by["beerType"]["default"] == "pale"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["fillMs"]["type"] == "number" and by["fillMs"]["default"] == 2500
    assert by["drainMs"]["type"] == "number" and by["drainMs"]["default"] == 2500
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_beerfill_audio_uses_fillMs_on_end_drainMs_on_start():
    bf = effects.get_effect("beerfill")
    ctx = {"duration_ms": 6000}
    # start role (drain) -> fade in over drainMs
    v, a = bf.video_filters("start", bf.resolve({"drainMs": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    # end role (fill) -> fade out over fillMs, ending at clip end
    v2, a2 = bf.video_filters("end", bf.resolve({"fillMs": 1500, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.5:d=1.5"]


def test_beerfill_no_audio_when_off():
    bf = effects.get_effect("beerfill")
    v, a = bf.video_filters("end", bf.resolve({"audioFade": False}), {"duration_ms": 6000})
    assert v == [] and a == []


def test_scatter_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "scatter")
    by = {p["key"]: p for p in e["params"]}
    assert by["sprite"]["type"] == "string" and by["sprite"]["default"] == "hop"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["count"]["type"] == "number" and by["count"]["default"] == 40
    assert by["count"]["min"] == 1 and by["count"]["max"] == 120
    assert by["fillMs"]["default"] == 2500 and by["drainMs"]["default"] == 2500
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True
    assert by["giantScale"]["type"] == "number" and by["giantScale"]["default"] == 0.6
    assert by["giantScale"]["min"] == 0 and by["giantScale"]["max"] == 2


def test_scatter_audio_uses_fillMs_on_end_drainMs_on_start():
    sc = effects.get_effect("scatter")
    ctx = {"duration_ms": 6000}
    v, a = sc.video_filters("start", sc.resolve({"drainMs": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = sc.video_filters("end", sc.resolve({"fillMs": 1500, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.5:d=1.5"]
