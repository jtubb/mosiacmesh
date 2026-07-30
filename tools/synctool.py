"""Cross-screen sync measurement CLI. Reads mm_server.err + the device roster, computes the
ground-truth cross-screen spread (see docs/superpowers/specs/2026-07-30-synctool-*). All math
is in tools/synctool_core.py (unit-tested); this shell only does I/O + printing."""
import argparse, json, re, statistics, urllib.request
import synctool_core as sc  # same dir (tools/)


def load_roster(group, base):
    """clientKey -> friendlyName for the group, via /api/discovery/devices."""
    try:
        d = json.load(urllib.request.urlopen(base + "/api/discovery/devices", timeout=5))
    except Exception:
        return {}
    devs = d.get("devices") if isinstance(d, dict) else d
    return {x.get("clientKey"): (x.get("friendlyName") or x.get("clientKey"))
            for x in (devs or []) if x.get("displayID") == group}


def collect(logpath, keys):
    """Per-key list of (t_ms, ct, err, off, rate) from drift/hb lines with a ct."""
    per = {}
    with open(logpath, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "CLIENTLOG" not in line:
                continue
            r = sc.parse_clientlog_line(line)
            if not r or r["key"] not in keys or r["tag"] not in ("drift", "hb") or r["ct"] is None:
                continue
            per.setdefault(r["key"], []).append(r)
    return per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--log", default="mm_server.err")
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--loop-ms", type=int, default=30016)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    roster = load_roster(a.group, a.base)
    per = collect(a.log, set(roster))
    if not per:
        print("no telemetry for %s" % a.group); return
    now = max(r["t_ms"] for rows in per.values() for r in rows)

    fresh, stale, insufficient = {}, [], []
    for key, rows in per.items():
        if now - rows[-1]["t_ms"] > sc.FRESH_MS:
            stale.append(roster[key]); continue
        win = [r for r in rows if now - r["t_ms"] <= sc.WINDOW_MS]
        fit = sc.fit_ct([(r["t_ms"], r["ct"]) for r in win])
        if not fit:
            insufficient.append(roster[key]); continue
        errs = [r["err"] for r in win if r["err"] is not None]
        fresh[key] = {"fit": fit, "err": statistics.median(errs) if errs else None,
                      "ct_at": sc.eval_fit(fit, now), "slope": fit["slope"]}

    names = {k: roster[k] for k in fresh}
    positions = [v["ct_at"] for v in fresh.values()]
    order = list(fresh.keys())
    if len(positions) < 2:
        print("not enough playing screens (%d)" % len(positions)); return

    spread = sc.circular_spread(positions, a.loop_ms)
    center, corr = sc.corrections(positions, a.loop_ms)
    # settled: split window into halves, spread of each; slopes near 1x
    half = now - sc.WINDOW_MS // 2
    def half_positions(newer):
        ps = []
        for key in order:
            rows = [r for r in per[key] if now - r["t_ms"] <= sc.WINDOW_MS
                    and ((r["t_ms"] >= half) == newer)]
            fit = sc.fit_ct([(r["t_ms"], r["ct"]) for r in rows])
            if fit: ps.append(sc.eval_fit(fit, now))
        return ps
    early = sc.circular_spread(half_positions(False), a.loop_ms)
    late = sc.circular_spread(half_positions(True), a.loop_ms)
    state, reason = sc.settled_verdict(early, late, [fresh[k]["slope"] for k in order])

    # attribution: video-offset = spread of err; clock ~ headline - video
    errvals = [fresh[k]["err"] for k in order if fresh[k]["err"] is not None]
    video_off = (max(errvals) - min(errvals)) if len(errvals) >= 2 else 0
    clock = max(0.0, spread - video_off)

    if a.json:
        print(json.dumps({"group": a.group, "state": state, "spread_ms": spread,
            "video_offset_ms": video_off, "clock_ms": clock, "center": center,
            "screens": {names[k]: {"ct_at": fresh[k]["ct_at"], "correction": corr[i],
                        "slope": fresh[k]["slope"]} for i, k in enumerate(order)},
            "stale": stale, "insufficient": insufficient})); return

    print("%s  cross-screen sync   [%s: %s]" % (a.group, state, reason))
    print("  HEADLINE visual desync (circular spread of ct@T): %.0f ms" % spread)
    print("  attribution: video-offset %.0f ms | clock-disagreement ~%.0f ms" % (video_off, clock))
    print("  playing %d | stale %d | insufficient %d" % (len(fresh), len(stale), len(insufficient)))
    print("  %-15s %10s %10s %8s" % ("screen", "corr(ms)", "err", "slope"))
    for i, k in sorted(enumerate(order), key=lambda ik: corr[ik[0]]):
        print("  %-15s %+10.0f %10s %8.3f" % (names[k], corr[i], fresh[k]["err"], fresh[k]["slope"]))
    if stale: print("  stale/absent: %s" % ", ".join(sorted(stale)))


if __name__ == "__main__":
    main()
