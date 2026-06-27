"""Unit tests for effects.py — effect catalog, parameter schemas, and ffmpeg filter generation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import effects


def test_get_effect_unknown_returns_none():
    assert effects.get_effect("nope") is None


def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve",
                     "beerfill", "scatter", "kegroll", "frostcreep", "coasterflip", "wheatpart", "splashcrown"}


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
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 2500
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True
    assert "fillMs" not in by and "drainMs" not in by   # consolidated to a single duration


def test_beerfill_audio_uses_single_duration():
    bf = effects.get_effect("beerfill")
    ctx = {"duration_ms": 6000}
    # start role -> fade in over duration
    v, a = bf.video_filters("start", bf.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    # end role -> fade out over duration, ending at clip end
    v2, a2 = bf.video_filters("end", bf.resolve({"duration": 1500, "audioFade": True}), ctx)
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
    assert by["giantScale"]["type"] == "number" and by["giantScale"]["default"] == 0.2
    assert by["giantScale"]["min"] == 0 and by["giantScale"]["max"] == 2


def test_scatter_audio_uses_fillMs_on_end_drainMs_on_start():
    sc = effects.get_effect("scatter")
    ctx = {"duration_ms": 6000}
    v, a = sc.video_filters("start", sc.resolve({"drainMs": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = sc.video_filters("end", sc.resolve({"fillMs": 1500, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.5:d=1.5"]


def test_catalog_includes_kegroll():
    names = {e["name"] for e in effects.effect_catalog()}
    assert "kegroll" in names


def test_kegroll_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "kegroll")
    by = {p["key"]: p for p in e["params"]}
    assert by["sprite"]["type"] == "string" and by["sprite"]["default"] == "keg"
    assert by["direction"]["choices"] == ["left", "right", "up", "down"]
    assert by["direction"]["default"] == "right"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 2000
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_kegroll_audio_single_duration_role_aware():
    kr = effects.get_effect("kegroll")
    ctx = {"duration_ms": 6000}
    v, a = kr.video_filters("start", kr.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = kr.video_filters("end", kr.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4:d=2"]
    v3, a3 = kr.video_filters("end", kr.resolve({"duration": 2000, "audioFade": False}), ctx)
    assert v3 == [] and a3 == []


def test_catalog_includes_frostcreep():
    names = {e["name"] for e in effects.effect_catalog()}
    assert "frostcreep" in names


def test_frostcreep_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "frostcreep")
    by = {p["key"]: p for p in e["params"]}
    assert by["tint"]["choices"] == ["frost", "blue", "clear"] and by["tint"]["default"] == "frost"
    assert by["sprite"]["type"] == "string" and by["sprite"]["default"] == "frostymug"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 2200
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_frostcreep_audio_single_duration():
    fc = effects.get_effect("frostcreep")
    ctx = {"duration_ms": 6000}
    v, a = fc.video_filters("start", fc.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = fc.video_filters("end", fc.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4:d=2"]
    v3, a3 = fc.video_filters("end", fc.resolve({"audioFade": False}), ctx)
    assert v3 == [] and a3 == []


def test_catalog_includes_coasterflip():
    names = {e["name"] for e in effects.effect_catalog()}
    assert "coasterflip" in names


def test_coasterflip_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "coasterflip")
    by = {p["key"]: p for p in e["params"]}
    assert by["axis"]["choices"] == ["horizontal", "vertical"] and by["axis"]["default"] == "horizontal"
    assert by["coaster"]["choices"] == ["kraft", "cork", "slate"] and by["coaster"]["default"] == "kraft"
    assert by["sprite"]["type"] == "string" and by["sprite"]["default"] == "coaster"
    assert by["flips"]["type"] == "number" and by["flips"]["default"] == 5
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 1800
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_coasterflip_audio_single_duration():
    cf = effects.get_effect("coasterflip")
    ctx = {"duration_ms": 6000}
    v, a = cf.video_filters("start", cf.resolve({"duration": 700, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=0.7"]
    v2, a2 = cf.video_filters("end", cf.resolve({"duration": 700, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=5.3:d=0.7"]
    v3, a3 = cf.video_filters("end", cf.resolve({"audioFade": False}), ctx)
    assert v3 == [] and a3 == []


def test_wheatpart_in_catalog_with_defaults():
    import effects
    cat = {e["name"]: e for e in effects.effect_catalog()}
    assert "wheatpart" in cat
    params = {p["key"]: p for p in cat["wheatpart"]["params"]}
    assert params["tint"]["default"] == "golden"
    assert params["tint"]["choices"] == ["golden", "amber", "pale"]
    assert params["sprite"]["default"] == "wheatfield"
    assert params["density"]["default"] == 30
    assert params["density"]["min"] == 10 and params["density"]["max"] == 200
    assert params["hold"]["default"] == 0.5
    assert params["hold"]["min"] == 0 and params["hold"]["max"] == 0.5
    assert params["scope"]["default"] == "wall"
    assert params["duration"]["default"] == 4000
    assert params["audioFade"]["default"] is True


def test_wheatpart_video_filters_audio_only_role_aware():
    import effects
    eff = effects.get_effect("wheatpart")
    p = eff.resolve({"audioFade": True, "duration": 2000})
    ctx = {"duration_ms": 8000}
    vstart, astart = eff.video_filters("start", p, ctx)
    vend, aend = eff.video_filters("end", p, ctx)
    assert vstart == [] and vend == []                      # no baked video
    assert astart == ["afade=t=in:st=0:d=2"]
    assert aend == ["afade=t=out:st=6:d=2"]


def test_wheatpart_audiofade_off_bakes_nothing():
    import effects
    eff = effects.get_effect("wheatpart")
    p = eff.resolve({"audioFade": False, "duration": 2000})
    assert eff.video_filters("start", p, {"duration_ms": 8000}) == ([], [])
    assert eff.video_filters("end", p, {"duration_ms": 8000}) == ([], [])


def test_splashcrown_in_catalog_with_defaults():
    import effects
    cat = {e["name"]: e for e in effects.effect_catalog()}
    assert "splashcrown" in cat
    params = {p["key"]: p for p in cat["splashcrown"]["params"]}
    assert params["beerType"]["default"] == "pale"
    assert params["beerType"]["choices"] == ["pale", "amber", "stout"]
    assert params["crownCount"]["default"] == 28
    assert params["crownCount"]["min"] == 8 and params["crownCount"]["max"] == 60
    assert params["scope"]["default"] == "wall"
    assert params["duration"]["default"] == 2000
    assert params["audioFade"]["default"] is True


def test_splashcrown_video_filters_audio_only_role_aware():
    import effects
    eff = effects.get_effect("splashcrown")
    p = eff.resolve({"audioFade": True, "duration": 2000})
    ctx = {"duration_ms": 8000}
    vstart, astart = eff.video_filters("start", p, ctx)
    vend, aend = eff.video_filters("end", p, ctx)
    assert vstart == [] and vend == []
    assert astart == ["afade=t=in:st=0:d=2"]
    assert aend == ["afade=t=out:st=6:d=2"]


def test_splashcrown_audiofade_off_bakes_nothing():
    import effects
    eff = effects.get_effect("splashcrown")
    p = eff.resolve({"audioFade": False, "duration": 2000})
    assert eff.video_filters("start", p, {"duration_ms": 8000}) == ([], [])
    assert eff.video_filters("end", p, {"duration_ms": 8000}) == ([], [])
