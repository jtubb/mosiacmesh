"""Fleet clock-sync timing profiler (WebSocket vs XHR). Gitignored _ prefix.

Parses CLIENTLOG 'clock-sync' ticks from the server log, aggregates per-device
precision/phase metrics, labels each device's ACTUAL transport from the access-log
(xhr_send present => XHR; websocket-101 + no xhr_send => WS), and summarizes by
transport group. Devices must be in ?tdbg to emit these metrics.

The transport-sensitive metric is sPrec (server-time round-trip precision over the
SockJS transport) and prec (offset precision); phStd is the beat's phase std-dev.
Lower is better for all.

Usage:
  python tools/_ws_timing_profile.py [--log mm_live.err] [--last-min 12] [--label before]
"""
import re, ast, sys, argparse, statistics, json, urllib.request
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--log", default="mm_live.err")
ap.add_argument("--last-min", type=float, default=12.0, help="only samples from the last N minutes of log")
ap.add_argument("--label", default="")
ap.add_argument("--server", default="http://192.168.1.60:3000")
args = ap.parse_args()

_TS = re.compile(r'^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d),(\d{3})')
_CL = re.compile(r'CLIENTLOG ([a-z0-9]+) (\{.*\})\s*$')
_ACC = re.compile(r'(\d{2}:\d{2}:\d{2}).* (\d+\.\d+\.\d+\.\d+) .*"(?:GET|POST) /sockjs/\d+/\w+/(websocket|xhr_send|xhr_streaming)')

def log_ms(line):
    m = _TS.match(line)
    if not m: return None
    Y, Mo, D, h, mi, s, ms = map(int, m.groups())
    return ((h * 60 + mi) * 60 + s) * 1000 + ms

# roster: client -> ip
roster = {}
try:
    data = json.load(urllib.request.urlopen(args.server + "/api/discovery/devices", timeout=8))
    devs = data.get("devices", data) if isinstance(data, dict) else data
    for d in devs:
        if d.get("clientKey") and d.get("ip"):
            roster[d["clientKey"]] = {"ip": d["ip"], "did": d.get("displayID", "?")}
except Exception as e:
    print(f"(roster fetch failed: {e} — transport labels will be unknown)")

lines = open(args.log, encoding="utf-8", errors="replace").read().splitlines()
# find the window: last timestamp minus last-min
last_t = None
for l in reversed(lines):
    t = log_ms(l)
    if t is not None: last_t = t; break
cutoff = (last_t - args.last_min * 60000) if last_t is not None else 0

# per-client clock-sync samples
samp = defaultdict(lambda: defaultdict(list))   # client -> metric -> [values]
ip_transport = {}                                # ip -> set of transports seen in window
cur_t = 0
for l in lines:
    t = log_ms(l)
    if t is not None: cur_t = t
    if cur_t < cutoff: continue
    a = _ACC.search(l)
    if a:
        ip_transport.setdefault(a.group(2), set()).add(a.group(3))
    m = _CL.search(l)
    if not m: continue
    try:
        p = ast.literal_eval(m.group(2))
    except Exception:
        continue
    if p.get("tag") != "clock-sync": continue
    c = m.group(1)
    for k in ("prec", "phStd", "sPrec", "off"):
        v = p.get(k)
        if isinstance(v, (int, float)): samp[c][k].append(v)
    samp[c]["synced"].append(1 if p.get("synced") else 0)

def transport_of(client):
    info = roster.get(client)
    if not info: return "?"
    ts = ip_transport.get(info["ip"], set())
    if "xhr_send" in ts or "xhr_streaming" in ts: return "XHR"
    if "websocket" in ts: return "WS"
    return "?"

def med(xs): return round(statistics.median(xs), 1) if xs else None

rows = []
for c, mm in samp.items():
    if not mm.get("prec"): continue
    off = mm.get("off", [])
    off_jit = round(statistics.pstdev(off), 1) if len(off) > 1 else None
    rows.append({
        "client": c[:16], "did": roster.get(c, {}).get("did", "?"),
        "transport": transport_of(c), "n": len(mm["prec"]),
        "prec": med(mm["prec"]), "phStd": med(mm.get("phStd", [])),
        "sPrec": med(mm.get("sPrec", [])), "offJit": off_jit,
        "synced%": round(100 * statistics.mean(mm["synced"])) if mm.get("synced") else 0,
    })

rows.sort(key=lambda r: (r["transport"], r["client"]))
lbl = f" [{args.label}]" if args.label else ""
print(f"\n=== clock-sync timing profile{lbl}  (last {args.last_min:g} min, {len(rows)} devices w/ samples) ===")
print(f"{'client':16} {'group':12} {'tx':4} {'n':>3} {'prec':>6} {'phStd':>6} {'sPrec':>6} {'offJit':>7} {'sync%':>5}")
for r in rows:
    print(f"{r['client']:16} {str(r['did'])[:12]:12} {r['transport']:4} {r['n']:>3} "
          f"{str(r['prec']):>6} {str(r['phStd']):>6} {str(r['sPrec']):>6} {str(r['offJit']):>7} {r['synced%']:>5}")

def agg(tx, metric):
    vals = [r[metric] for r in rows if r["transport"] == tx and r[metric] is not None]
    return (round(statistics.median(vals), 1), len(vals)) if vals else (None, 0)

print(f"\n--- by transport (median of per-device medians) ---")
for tx in ("XHR", "WS", "?"):
    n = sum(1 for r in rows if r["transport"] == tx)
    if not n: continue
    prec = agg(tx, "prec"); phstd = agg(tx, "phStd"); sprec = agg(tx, "sPrec"); ojit = agg(tx, "offJit")
    print(f"  {tx:4} ({n:2} dev):  prec={prec[0]}  phStd={phstd[0]}  sPrec={sprec[0]}  offJit={ojit[0]}")
