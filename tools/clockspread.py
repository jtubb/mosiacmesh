"""Cross-screen CLOCK spread, fit-based. READ-ONLY -- touches nothing.

    python tools/clockspread.py --group "OEB Sign 1"

Answers one question: how far apart are the screens' CLOCKS, separately from
their video positions?

WHY NOT synctool's number: synctool reports a clock-disagreement attribution, but
it is a per-window snapshot of a value observed only at server-receive time -- and
the iPad-1 uplink carries 22-214ms of one-sided jitter. The project's own record
found that number outlier-inflated and says to trust a per-screen `elapsed`
LINEAR FIT across a window instead. That is what this computes.

WHY `elapsed` IS THE RIGHT SIGNAL: it is `GoTime.now() - startEpoch`, and every
screen in a group shares the same server-set startEpoch. So its cross-screen
spread is purely clock disagreement, with no decode/seek error mixed in the way
`ct` has.

READING IT:
  p10-p90 ~tens of ms  -> the clock layer is near its known physical floor; the
                          remaining visible desync is the VIDEO layer, and video
                          work has real headroom.
  p10-p90 ~150ms+      -> the clock layer is co-equal with the video layer, and
                          no video-side fix can get the wall below it.

Requires the server log to carry timestamps (server.py configures the format
unconditionally as of the 2026-09-17 fix). If every line is skipped, check that
first -- an untimestamped log silently blinds this and synctool alike.
"""
import argparse
import json
import os
import urllib.request

import clockspread_core as cs          # same dir (tools/)

TAIL_BYTES_DEFAULT = 64 * 1024 * 1024


def load_roster(group, base):
    try:
        d = json.load(urllib.request.urlopen(base + "/api/discovery/devices", timeout=5))
    except Exception:
        return {}
    devs = d.get("devices") if isinstance(d, dict) else d
    return dict((x.get("clientKey"), x.get("friendlyName") or x.get("clientKey"))
                for x in (devs or []) if x.get("displayID") == group)


def collect(logpath, keys, window_ms, tail_bytes=TAIL_BYTES_DEFAULT):
    """Per-screen [(t_ms, elapsed)] over the most recent `window_ms`."""
    rows = []
    with open(logpath, "rb") as fb:
        if tail_bytes:
            try:
                size = os.fstat(fb.fileno()).st_size
                if size > tail_bytes:
                    fb.seek(size - tail_bytes)
                    fb.readline()
            except OSError:
                pass
        for raw in fb:
            r = cs.parse_elapsed_line(raw.decode("utf-8", "replace"))
            if r and (not keys or r["key"] in keys):
                rows.append(r)
    if not rows:
        return {}, 0
    newest = max(r["t_ms"] for r in rows)
    per = {}
    for r in rows:
        if (newest - r["t_ms"]) <= window_ms:
            per.setdefault(r["key"], []).append((r["t_ms"], r["elapsed"]))
    return per, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", help="display group; omit for all screens")
    ap.add_argument("--log", default="mm_server.err")
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--window-s", type=float, default=120.0,
                    help="fit window; longer averages out more uplink jitter")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    roster = load_roster(a.group, a.base) if a.group else {}
    per, total = collect(a.log, set(roster) if roster else None, a.window_s * 1000.0)
    if not per:
        print("no usable samples in %s" % a.log)
        print("  (parsed %d elapsed rows). If 0, the log has NO TIMESTAMPS -- this" % total)
        print("   metric and synctool both need them; see server.py basicConfig.")
        return

    s = cs.clock_spread(per)
    if a.json:
        print(json.dumps(s, indent=2))
        return

    name = lambda k: (roster.get(k, k))[:20]
    print("CROSS-SCREEN CLOCK SPREAD  (fit-based, %.0fs window)" % a.window_s)
    print("  screens fitted : %d%s" % (s["n"],
          ("   dropped(too few samples): %d" % len(s["dropped"])) if s["dropped"] else ""))
    if not s["n"]:
        return
    print("")
    print("  %-20s %10s %9s %9s" % ("screen", "rel(ms)", "fit rms", "ppm"))
    for k, v in sorted(s["perScreen"].items(), key=lambda kv: kv[1]["relMs"]):
        print("  %-20s %10.0f %9.0f %9.0f" % (name(k), v["relMs"], v["rmsMs"], v["slope"] * 1e6))
    print("")
    print("  p10-p90  : %6.0f ms   <== the trustworthy clock-layer number" % s["p10p90Ms"])
    print("  max-min  : %6.0f ms   (exposes outlier radios)" % s["maxMinMs"])
    print("")
    if s["p10p90Ms"] < 60:
        print("  -> clock layer is NEAR ITS FLOOR. Remaining visible desync is the")
        print("     VIDEO layer; video-side work has real headroom.")
    elif s["p10p90Ms"] < 150:
        print("  -> clock layer is a MINOR but real contributor alongside video.")
    else:
        print("  -> clock layer is CO-EQUAL with video. No video-side fix can take")
        print("     the wall below this; the ceiling is the clock/network path.")


if __name__ == "__main__":
    main()
