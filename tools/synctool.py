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
    """Per-key list of (t_ms, ct, err, off, rate) from drift/hb lines with a ct, plus the
    newest timestamp among the group's telemetry of ANY tag (drift/hb/clock-sync). The latter
    is the liveness reference: if the newest ct lags it, the group is reporting but not playing.
    Returns (per, latest_any_ms)."""
    per = {}
    latest_any = 0
    with open(logpath, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "CLIENTLOG" not in line:
                continue
            r = sc.parse_clientlog_line(line)
            if not r or r["key"] not in keys:
                continue
            if r["t_ms"] > latest_any:
                latest_any = r["t_ms"]
            if r["tag"] not in ("drift", "hb") or r["ct"] is None:
                continue
            per.setdefault(r["key"], []).append(r)
    return per, latest_any


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--log", default="mm_server.err")
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--loop-ms", type=int, default=30016)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    roster = load_roster(a.group, a.base)
    per, latest_any = collect(a.log, set(roster))
    if not per:
        print("no telemetry for %s" % a.group); return
    now = max(r["t_ms"] for rows in per.values() for r in rows)

    # Liveness guard: if the newest ct lags the group's newest telemetry, no video is playing
    # now — the ct window is a frozen pre-stop snapshot. Refuse to report a stale number.
    stale_pb, age_ms = sc.playback_stale(now, latest_any)
    if stale_pb:
        if a.json:
            print(json.dumps({"group": a.group, "state": "NO_LIVE_PLAYBACK",
                              "newest_ct_age_ms": age_ms})); return
        print("%s  NO LIVE PLAYBACK - newest video ct is %.0fs old "
              "(group telemetry is current; no video is playing on this group)"
              % (a.group, age_ms / 1000.0))
        return

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
    # steady = in normal ~1x playback; not_steady = stalled/seeking/otherwise off-rate.
    # Only steady screens count toward the spread/corrections/attribution — a stalled or
    # seeking screen isn't visually desynced from the others, it's just not comparable.
    steady = {k: v for k, v in fresh.items() if abs(v["slope"] - 1.0) <= sc.SLOPE_TOL}
    not_steady = {k: v for k, v in fresh.items() if k not in steady}

    positions = [steady[k]["ct_at"] for k in steady]
    order = list(steady.keys())
    if len(order) < 2:
        print("only %d screens in steady playback — nothing to measure yet" % len(order)); return

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
    state, reason = sc.settled_verdict(early, late, [steady[k]["slope"] for k in order])

    # attribution: video-offset = spread of err; clock ~ headline - video
    errvals = [steady[k]["err"] for k in order if steady[k]["err"] is not None]
    video_off = (max(errvals) - min(errvals)) if len(errvals) >= 2 else 0
    clock = max(0.0, spread - video_off)

    if a.json:
        print(json.dumps({"group": a.group, "state": state, "spread_ms": spread,
            "video_offset_ms": video_off, "clock_ms": clock, "center": center,
            "screens": {names[k]: {"ct_at": steady[k]["ct_at"], "correction": corr[i],
                        "slope": steady[k]["slope"]} for i, k in enumerate(order)},
            "not_steady": [{"name": names[k], "slope": v["slope"]} for k, v in not_steady.items()],
            "stale": stale, "insufficient": insufficient})); return

    print("%s  cross-screen sync   [%s: %s]" % (a.group, state, reason))
    print("  HEADLINE visual desync (circular spread of ct@T): %.0f ms" % spread)
    print("  attribution: video-offset %.0f ms | clock-disagreement ~%.0f ms" % (video_off, clock))
    print("  playing %d (steady) | not-steady %d | stale %d | insufficient %d" %
          (len(steady), len(not_steady), len(stale), len(insufficient)))
    print("  %-15s %10s %10s %8s" % ("screen", "corr(ms)", "err", "slope"))
    for i, k in sorted(enumerate(order), key=lambda ik: corr[ik[0]]):
        print("  %-15s %+10.0f %10s %8.3f" % (names[k], corr[i], steady[k]["err"], steady[k]["slope"]))
    if not_steady:
        print("  not-steady (stalled/seeking): %s" % ", ".join(
            "%s (%.2f)" % (names[k], not_steady[k]["slope"]) for k in sorted(not_steady, key=lambda k: names[k])))
    if stale: print("  stale/absent: %s" % ", ".join(sorted(stale)))


if __name__ == "__main__":
    main()
