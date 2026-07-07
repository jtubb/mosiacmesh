#!/usr/bin/env python3
"""Create/refresh the "All Effects Demo" playlist — one item per animation, with
the transition catalog cycling between them.

The playlist itself is server runtime state (persisted in settings.dat, gitignored),
so this script is the reproducible source of truth for it. Re-run it any time to
recreate or update the playlist on a running server.

Design:
  * 22 items, one per animation (MM_ANIMATIONS in js/animations.js), file=<key>.
  * All 13 transition effects (effects.py) cycle as start/end effects, paired so
    each item's startEffect == the previous item's endEffect (clean cover->reveal,
    loop-safe). With 22 items and 13 transitions, every transition appears.
  * span=mirror + scope=screen: the animation is centered on EACH screen with no
    mesh transform — correct for a single screen or an uncalibrated group, and the
    right choice for eyeballing every animation/transition. For a calibrated
    multi-screen WALL where one animation should span all screens, pass
    --span mesh (then each screen's calibrated quad shows its slice).

Usage:
  python tools/make_all_effects_demo.py [--host 192.168.1.60:3000] [--span mirror|mesh] [--secs 10]
"""
import json, argparse, urllib.request, urllib.parse, urllib.error

ANIMATIONS = [
    "bouncingBalls", "lissajous", "phyllotaxis", "wireframeCube", "radialPulse",
    "particleGalaxy", "plasma", "pendulumWave", "dvdLogo", "analogClock", "wordClock",
    "sunMoonTransit", "gameOfLife", "starfield", "fireworks", "truchet", "spirograph",
    "ballLights", "hyperTunnel", "fire", "ripplePool", "matrixRain",
]

# (name, params) with each effect's effects.py defaults; `scope` gets overridden per --span.
TRANSITIONS = [
    ("fade",        {"duration": 600, "audioFade": True}),
    ("wipe",        {"direction": "left", "scope": "screen", "duration": 600, "audioFade": True}),
    ("slide",       {"direction": "left", "scope": "wall", "duration": 700, "audioFade": True}),
    ("zoom",        {"scale": 0.6, "scope": "wall", "duration": 700, "audioFade": True}),
    ("iris",        {"scope": "wall", "duration": 700, "audioFade": True}),
    ("dissolve",    {"blocks": 16, "duration": 700, "audioFade": True}),
    ("beerfill",    {"beerType": "pale", "scope": "wall", "duration": 2500, "audioFade": True}),
    ("scatter",     {"sprite": "hop", "scope": "wall", "count": 40, "fillMs": 2200, "drainMs": 2200, "audioFade": True, "giantScale": 0.2}),
    ("kegroll",     {"sprite": "keg", "direction": "right", "scope": "wall", "duration": 2000, "audioFade": True}),
    ("frostcreep",  {"tint": "frost", "sprite": "frostymug", "scope": "wall", "duration": 2200, "audioFade": True}),
    ("coasterflip", {"axis": "horizontal", "coaster": "kraft", "sprite": "coaster", "flips": 5, "scope": "wall", "duration": 1800, "audioFade": True}),
    ("wheatpart",   {"tint": "golden", "sprite": "wheatfield", "density": 30, "hold": 0.4, "scope": "wall", "duration": 3000, "audioFade": True}),
    ("splashcrown", {"beerType": "pale", "crownCount": 28, "scope": "wall", "duration": 2000, "audioFade": True}),
]
NAME = "All Effects Demo"


def build_items(span, secs):
    scope = "screen" if span == "mirror" else "wall"
    trans = []
    for nm, params in TRANSITIONS:
        p = dict(params)
        if "scope" in p:
            p["scope"] = scope
        trans.append({"name": nm, "params": p})
    N, T = len(ANIMATIONS), len(trans)
    items = []
    for i, anim in enumerate(ANIMATIONS):
        end_t = trans[i % T]                       # transition OUT of this item
        start_t = trans[((i - 1) % N) % T]         # transition IN == previous item's OUT (loop-safe)
        items.append({
            "id": "demo-%02d" % i, "file": anim, "playmode": "SCRIPT",
            "scriptSpan": span, "duration": secs, "backgroundColor": "#0a0a0a",
            "startEffect": {"name": start_t["name"], "params": dict(start_t["params"])},
            "endEffect": {"name": end_t["name"], "params": dict(end_t["params"])},
        })
    return items


def _req(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return urllib.request.urlopen(r, timeout=10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.60:3000")
    ap.add_argument("--span", choices=["mirror", "mesh"], default="mirror")
    ap.add_argument("--secs", type=int, default=10, help="seconds per animation")
    args = ap.parse_args()

    base = "http://%s/api/playlists" % args.host
    items = build_items(args.span, args.secs)
    ct = {"Content-Type": "application/json"}

    # Does it already exist? -> PUT (needs If-Match); else POST.
    try:
        r = _req("GET", base + "/" + urllib.parse.quote(NAME))
        existing = json.loads(r.read().decode())
        existing = existing.get("playlist", existing)
        ver = existing.get("_serverVersion", 0)
        h = dict(ct); h["If-Match"] = str(ver)
        rr = _req("PUT", base + "/" + urllib.parse.quote(NAME),
                  {"items": items, "loop": True}, h)
        print("updated %r (v%s->%s), span=%s, %d items, all %d transitions"
              % (NAME, ver, ver + 1, args.span, len(items), len(TRANSITIONS)))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            rr = _req("POST", base, {"name": NAME, "items": items, "loop": True}, ct)
            print("created %r, span=%s, %d items, all %d transitions"
                  % (NAME, args.span, len(items), len(TRANSITIONS)))
        else:
            raise


if __name__ == "__main__":
    main()
