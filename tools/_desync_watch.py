"""Desync detector (diagnostic). Catches a screen that's persistently out of
sync with the group while content plays.

Math: each CLIENTLOG carries elapsed = client's (GoTime.now() - startEpoch), and
the -v server log stamps when it was received. So
    dev_i = (elapsed_i + startEpoch) - log_time_ms_i
        = client_i's GoTime.now() - server's real time at receive
        = that client's clock error vs the server (minus ~network delay).
Per-sample network/skew noise averages out, so the MEAN dev per client over a
window is its persistent clock bias. The group MEDIAN of those means is the
baseline (shared network delay); any client whose mean dev deviates from the
baseline by > THRESH ms is persistently desynced — the "lagger".

Usage: python tools/_desync_watch.py [seconds] [thresh_ms]
Run while a playlist is playing (start one first).
"""
import re, ast, time, sys, urllib.request, json, collections

HOST = "127.0.0.1:3000"
LOG = "mm_server_dbg.err"
WIN = int(sys.argv[1]) if len(sys.argv) > 1 else 60
THRESH = int(sys.argv[2]) if len(sys.argv) > 2 else 300

_TS = re.compile(r'^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d),(\d{3})')
_CL = re.compile(r'CLIENTLOG ([a-z0-9]+) (\{.*\})\s*$')


def _log_ms(line):
    m = _TS.match(line)
    if not m:
        return None
    import calendar
    y, mo, d, h, mi, s, ms = [int(x) for x in m.groups()]
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) * 1000 + ms  # log is local; offset cancels in the median


def _start_epoch():
    with urllib.request.urlopen("http://%s/api/playback" % HOST, timeout=5) as r:
        d = json.loads(r.read().decode())
    for g in d.get("groups", []):
        if g.get("state") == "playing" and g.get("startedEpoch"):
            return g["displayID"], g["startedEpoch"]
    return None, None


def main():
    did, se = _start_epoch()
    if not se:
        print("No group is playing — start a playlist first."); return
    print("watching '%s' startEpoch=%d for %ds (thresh %dms)..." % (did, se, WIN, THRESH))
    # read only lines appended during the window
    f = open(LOG, encoding="utf-8", errors="replace")
    f.seek(0, 2)
    devs = collections.defaultdict(list)   # client -> [clock dev,...]
    vdevs = collections.defaultdict(list)  # client -> [video ct dev,...]
    state = {}                             # client -> last (off,fps,steering)
    t_end = time.time() + WIN
    while time.time() < t_end:
        line = f.readline()
        if not line:
            time.sleep(0.2); continue
        lm = _log_ms(line)
        cm = _CL.search(line)
        if lm is None or not cm:
            continue
        try:
            p = ast.literal_eval(cm.group(2))
        except Exception:
            continue
        el = p.get("elapsed")
        if el is None:
            continue
        key = cm.group(1)
        devs[key].append((el + se) - lm)
        # Video layer: currentTime (ms) of an ACTUALLY-PLAYING video, skew-
        # corrected by the same log timestamp. Spread here = video-to-clock
        # binding error across the wall (separate from clock sync above).
        ctv = p.get("ct")
        if ctv and not p.get("paused"):
            vdevs[key].append(ctv - lm)
        st = p.get("steer") or {}
        state[key] = (p.get("off"), p.get("fps"), st.get("steering"), st.get("kept"), st.get("best"))
    if not devs:
        print("no playback telemetry captured (is ?tdbg on + playing?)"); return
    # Require a minimum sample count before trusting a client's mean deviation —
    # a 1-2 sample mean is dominated by per-sample network skew and produces
    # false "lagger" flags. Clients below MIN_SAMPLES are reported but never
    # flagged. Baseline is the median over WELL-SAMPLED clients only.
    MIN_SAMPLES = 5
    means = {k: sum(v) / len(v) for k, v in devs.items()}
    well = {k: m for k, m in means.items() if len(devs[k]) >= MIN_SAMPLES}
    base_pool = well or means
    base = sorted(base_pool.values())[len(base_pool) // 2]   # median baseline
    print("\nclients=%d (%d with >=%d samples)  baseline(median dev)=%.0fms"
          % (len(means), len(well), MIN_SAMPLES, base))
    print("%-18s %9s %7s %8s %5s %5s %6s %s" % ("client", "dev-base", "samples", "off", "fps", "steer", "kept", ""))
    laggers = []
    for k in sorted(means, key=lambda x: means[x]):
        rel = means[k] - base
        off, fps, steer, kept, best = state.get(k, (None,)*5)
        flag = ""
        if len(devs[k]) < MIN_SAMPLES:
            flag = "  (low samples — ignored)"
        elif abs(rel) > THRESH:
            flag = "  <== DESYNCED %.0fms" % rel
            laggers.append((k, rel, off, fps, steer, kept, best))
        print("%-18s %9.0f %7d %8s %5s %5s %6s%s" % (k, rel, len(devs[k]), off, fps, steer, kept, flag))
    print("\n%d persistent lagger(s) over %ds." % (len(laggers), WIN))
    for k, rel, off, fps, steer, kept, best in laggers:
        print("  %s: %.0fms off-group | offset=%s fps=%s steering=%s kept=%s best=%s"
              % (k, rel, off, fps, steer, kept, best))

    # --- video layer (only when a video is actually playing) ---
    vmeans = {k: sum(v) / len(v) for k, v in vdevs.items() if len(v) >= MIN_SAMPLES}
    if vmeans:
        vbase = sorted(vmeans.values())[len(vmeans) // 2]
        vrel = sorted(vmeans[k] - vbase for k in vmeans)
        vspread = vrel[-1] - vrel[0]
        print("\n=== VIDEO (currentTime) sync: %d playing, spread %.0fms (%.0f..%.0f) ==="
              % (len(vmeans), vspread, vrel[0], vrel[-1]))
        for k in sorted(vmeans, key=lambda x: vmeans[x]):
            r = vmeans[k] - vbase
            f = "  <== %s %.0fms" % (("AHEAD" if r > 0 else "BEHIND"), r) if abs(r) > THRESH else ""
            print("  %-18s ct_dev=%7.0fms%s" % (k, r, f))
    else:
        print("\n(no playing-video telemetry — video layer not measured this run)")


main()
