"""WS stability check via CLIENTLOG continuity (gitignored _ prefix).

A connected client emits a clock-sync CLIENTLOG every ~30s. So the HONEST test of
whether the websocket bridge is holding is: are that client's CLIENTLOG timestamps
continuous (no gap > GAP_S) across the window? A multi-minute gap = the webclip
crashed/went offline (NOT a "held socket" — silence in the access log is ambiguous,
CLIENTLOG cadence is not). This is the metric that would have caught the false
"37-min stable" reading.

Usage: python tools/_ws_continuity.py <client_id> [--last-min 20] [--gap 75] [--log mm_live.err]
"""
import re, sys, argparse

ap = argparse.ArgumentParser()
ap.add_argument("client")
ap.add_argument("--last-min", type=float, default=20.0)
ap.add_argument("--gap", type=float, default=75.0, help="seconds; a gap larger than this = a disconnect")
ap.add_argument("--log", default="mm_live.err")
args = ap.parse_args()

_TS = re.compile(r'^\d{4}-\d\d-\d\d (\d\d):(\d\d):(\d\d)')

def secs(line):
    m = _TS.match(line)
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) if m else None

stamps = []
last = None
for l in open(args.log, encoding="utf-8", errors="replace"):
    s = secs(l)
    if s is not None: last = s
    if ("CLIENTLOG " + args.client) in l and s is not None:
        stamps.append(s)

if not stamps:
    print(f"no CLIENTLOG for {args.client}"); sys.exit(1)

cutoff = last - args.last_min * 60
win = [s for s in stamps if s >= cutoff]
if len(win) < 2:
    print(f"{args.client}: only {len(win)} sample(s) in last {args.last_min:g} min — offline/just-connected"); sys.exit(1)

gaps = []
# LEADING silence: window start to the first sample (was the client already dead?)
if win[0] - cutoff > args.gap:
    gaps.append((int(cutoff), win[0], int(win[0] - cutoff)))
for i in range(1, len(win)):
    d = win[i] - win[i-1]
    if d > args.gap:
        gaps.append((win[i-1], win[i], d))
# TRAILING silence: last sample to NOW (log's newest ts). Catches a client that hung/died and
# never came back — the exact false-STABLE bug this tool had. `last` is "now" per the log.
if last - win[-1] > args.gap:
    gaps.append((win[-1], last, int(last - win[-1])))

span_min = (win[-1] - win[0]) / 60.0
print(f"\n=== {args.client} continuity (last {args.last_min:g} min) ===")
print(f"  samples: {len(win)}   span: {span_min:.1f} min of {args.last_min:g}-min window   expected ~{int(args.last_min*2)} @30s cadence")
print(f"  silent since last sample (to now): {last - win[-1]}s   (threshold {args.gap:g}s)")
if gaps:
    print(f"  DISCONNECTS ({len(gaps)}):")
    for a, b, d in gaps:
        print(f"    gap of {d}s  (=~{d/60:.1f} min) — webclip was OFFLINE here")
    print(f"\n  VERDICT: NOT STABLE — {len(gaps)} disconnect(s). Bridge still crashing/flapping.")
    sys.exit(2)
else:
    print(f"\n  VERDICT: STABLE — continuous {span_min:.1f} min, no gap > {args.gap:g}s. Genuinely held.")
