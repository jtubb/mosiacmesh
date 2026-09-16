"""Drift-probe reader: is the wall's residual P-DROOP or a STALE SENSOR?

Reads the `driftprobe` CLIENTLOG telemetry that js/drift-probe.js emits during
normal playback and answers the audit 0.3 question. All math is in
tools/driftprobe_core.py (unit-tested); this shell only does I/O + printing.

    python tools/driftprobe.py --group "OEB Sign 1"

REQUIRES both js/driftprobeflag.js and js/tdbgflag.js set true + a fleet RELOAD
(reporting rides the CLIENTLOG dbg channel). Give it ~1 minute of playback after
the reload so each screen accumulates enough step edges to report.

HOW TO READ IT
  verdict BIAS   -> the videos are where they should be; the controllers are
                    chasing a phantom produced by a 250ms zero-order-hold
                    currentTime. Fix the SENSOR. Do NOT retry PI first -- an
                    integral drives the MEASURED error to zero, which parks the
                    TRUE position at exactly the staleness.
  verdict DROOP  -> the videos really are behind. Retry PI: the reason it failed
                    before (spurious 30s seeks resetting the loop) was removed by
                    Lever A and it has not been re-tested since.
  verdict MIXED  -> both, in the proportion `stale` shows.
  step ~250ms    -> the zero-order hold is confirmed outright.

The headline number is the cross-screen spread of `true`: the ACTUAL visible
desync. Compare it against the spread of `loop` -- if they diverge, every metric
built on the drift loop's own `err` has been measuring the controllers, not the
wall.
"""
import argparse
import json
import time
import urllib.request

import driftprobe_core as dc          # same dir (tools/)


def load_roster(group, base):
    """clientKey -> friendlyName for the group, via /api/discovery/devices."""
    try:
        d = json.load(urllib.request.urlopen(base + "/api/discovery/devices", timeout=5))
    except Exception:
        return {}
    devs = d.get("devices") if isinstance(d, dict) else d
    return dict((x.get("clientKey"), x.get("friendlyName") or x.get("clientKey"))
                for x in (devs or []) if x.get("displayID") == group)


def collect(logpath, keys):
    """Parse probe lines, tagging each with its file position so the reader can
    order them even when the log carries no timestamps (which the live one does
    not)."""
    rows = []
    seq = 0
    with open(logpath, encoding="utf-8", errors="replace") as f:
        for line in f:
            r = dc.parse_probe_line(line, seq=seq)
            if r is None:
                continue
            seq += 1
            if not keys or r["key"] in keys:
                rows.append(r)
    return rows


def _fmt(v, suffix=""):
    return "-" if v is None else ("%d%s" % (round(v), suffix))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", help="display group; omit to include every screen")
    ap.add_argument("--log", default="mm_server.err")
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--fresh-ms", type=int, default=120000)
    ap.add_argument("--live-check-s", type=float, default=6.0,
                    help="seconds to watch for NEW records; 0 disables")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    roster = load_roster(a.group, a.base) if a.group else {}

    # Liveness FIRST. A stopped wall leaves a frozen log whose screens all still
    # look equally recent to the round-based guard, so consecutive reads return
    # identical numbers that look like a beautifully stable measurement. The only
    # way to tell is whether NEW records are still arriving.
    if a.live_check_s > 0:
        before = len(collect(a.log, None))
        time.sleep(a.live_check_s)
        after = len(collect(a.log, None))
        if dc.liveness_verdict(before, after) == "FROZEN":
            print("REFUSING TO REPORT: no new driftprobe records in %.0fs (%d -> %d)."
                  % (a.live_check_s, before, after))
            print("  The log is frozen, so any numbers here would be a stale snapshot.")
            print("  Is the wall actually PLAYING? A reload leaves screens idle until")
            print("  a fresh PLAY/START, and telemetry only flows during video playback.")
            return

    rows = collect(a.log, set(roster) if roster else None)
    if not rows:
        print("no driftprobe telemetry in %s" % a.log)
        print("  both js/driftprobeflag.js AND js/tdbgflag.js must be true, then RELOAD the fleet.")
        return

    latest = dc.latest_per_key(rows, fresh_ms=a.fresh_ms)
    if not latest:
        print("driftprobe lines exist but all are older than %dms - is playback running?"
              % a.fresh_ms)
        return

    summary = dc.fleet_summary(latest)
    if a.json:
        print(json.dumps({"fleet": summary,
                          "screens": {k: v for k, v in latest.items()}}, indent=2))
        return

    print("%-22s %8s %8s %8s %7s %6s  %s" %
          ("screen", "true", "loop", "stale", "step", "edges", "verdict"))
    print("-" * 76)
    for key in sorted(latest, key=lambda k: roster.get(k, k)):
        r = latest[key]
        print("%-22s %8s %8s %8s %7s %6s  %s" % (
            (roster.get(key, key))[:22],
            _fmt(r.get("pTrue")), _fmt(r.get("pLoop")), _fmt(r.get("pStale")),
            _fmt(r.get("pStep")), r.get("pE"), dc.row_verdict(r)))

    print("-" * 76)
    print("screens reporting : %d" % summary["n"])
    print("median true err   : %s ms   <- where the videos REALLY are" % _fmt(summary["medTrue"]))
    print("median loop err   : %s ms   <- what the controllers act on" % _fmt(summary["medLoop"]))
    print("median staleness  : %s ms   <- the sensor's lie" % _fmt(summary["medStale"]))
    print("median step period: %s ms   <- ~250 confirms the zero-order hold"
          % _fmt(summary["medStep"]))
    print("")
    print("CROSS-SCREEN SPREAD (the visible desync)")
    print("  true (p10-p90)  : %s ms      max-min %s ms   <== HEADLINE"
          % (_fmt(summary["trueSpreadMs"]), _fmt(summary["trueSpreadMaxMs"])))
    print("  loop (p10-p90)  : %s ms      max-min %s ms"
          % (_fmt(summary["loopSpreadMs"]), _fmt(summary["loopSpreadMaxMs"])))
    print("")
    fixed = sum(1 for r in latest.values() if r.get("pFix"))
    if fixed:
        print("staleness corrector ACTIVE on %d/%d screens (js/ctfreshflag.js)"
              % (fixed, summary["n"]))
        print("  note: pStale reads the RAW sensor by design, so it should be")
        print("        UNCHANGED (~142ms). The corrector's effect shows up in")
        print("        loop err -> 0, true err -> 0, and a tighter true spread.")
        print("")
    print("FLEET VERDICT: %s   %s" % (summary["verdict"], summary.get("verdictCounts", {})))
    v = summary["verdict"]
    if v == "BIAS":
        print("  -> Fix the SENSOR (timestamp + extrapolate in mm_ctrlCT, or the")
        print("     pure-JS staleness estimator). Do NOT retry PI first.")
    elif v == "DROOP":
        print("  -> The error is real. Retry PI now that Lever A removed the")
        print("     spurious 30s seeks that were blamed for its failure.")
    elif v == "MIXED":
        print("  -> Both present. Fix the sensor first (it is the untried one),")
        print("     then re-measure before touching the controller.")
    elif v == "SPLIT":
        print("  -> Screens disagree; inspect the per-screen table above before acting.")


if __name__ == "__main__":
    main()
