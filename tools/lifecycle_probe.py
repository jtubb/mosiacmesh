"""End-to-end lifecycle test for a display group.

Sequence:
  1. Snapshot the fleet roster (online iPads in the target group).
  2. STOP phase: fire RUN_SCRIPT stop, tail server.err for
     "Client X disconnected" lines, report per-iPad time-to-disconnect.
  3. Settle: brief sleep so disconnects fully propagate.
  4. START phase: fire RUN_SCRIPT start, poll the discovery API for
     each iPad's lastSeen ticking forward past the pre-Start mark,
     report per-iPad time-to-reregister.
  5. Print a unified summary table.

Used to verify the lifecycle scripts (stop / start) actually take effect
on every iPad, and to spot stragglers (one iPad that took 8s to reconnect
when the rest came back in 2s, or one that never reconnected at all).

Usage:
    python tools/lifecycle_probe.py "Test Group" [stop_wait=20] [start_wait=45]
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime

import aiohttp

SERVER = "http://localhost:3000"
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "server.err")

# Matches:  2026-06-02 15:53:02,552 INFO Client sign1screen24 disconnected
DISCONNECT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) INFO Client (\S+) disconnected"
)


# --- minimal SockJS client (same shape as run_and_collect.py) ----------------
class SockJSClient:
    def __init__(self, base_url):
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.url += "/sockjs/000/lifecycle_probe/websocket"
        self.session = None
        self.ws = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url)
        open_frame = await self.ws.receive_str(timeout=5)
        if open_frame != "o":
            raise RuntimeError(f"expected SockJS open 'o', got {open_frame!r}")
        return self

    async def __aexit__(self, *args):
        try:
            await self.ws.close()
        finally:
            await self.session.close()

    async def send_msg(self, request, payload, src="lifecycle_probe"):
        msg = {"SRC": src, "DEST": "SRV", "REQUEST": request, "PAYLOAD": payload}
        frame = json.dumps([json.dumps(msg)])
        await self.ws.send_str(frame)


# --- helpers -----------------------------------------------------------------
async def fetch_devices(http):
    """Return list of device dicts from the discovery API."""
    async with http.get(f"{SERVER}/api/discovery/devices",
                        timeout=aiohttp.ClientTimeout(total=5)) as r:
        data = await r.json()
    return data.get("devices", data) if isinstance(data, dict) else data


def parse_disconnects_since(start_byte, deadline_ts):
    """Yield (timestamp, friendly_name) tuples for disconnect log lines
    written between `start_byte` in server.err and now. Stops when EOF
    is reached (caller loops with sleeps until deadline)."""
    if not os.path.exists(LOG_PATH):
        return
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start_byte)
            for line in f:
                m = DISCONNECT_RE.match(line)
                if not m:
                    continue
                ts = datetime.strptime(f"{m.group(1)}.{m.group(2)}000",
                                       "%Y-%m-%d %H:%M:%S.%f")
                yield ts, m.group(3)
    except (OSError, IOError):
        return


# --- phases ------------------------------------------------------------------
async def snapshot_roster(http, display_id):
    """Return {client_key: {'name': ..., 'ip': ..., 'lastSeen': ...}} for
    every online iPad in the group."""
    devs = await fetch_devices(http)
    roster = {}
    for d in devs:
        if d.get("displayID") != display_id:
            continue
        if not d.get("isOnline", False):
            continue
        roster[d.get("clientKey")] = {
            "name": d.get("friendlyName") or d.get("ip"),
            "ip": d.get("ip"),
            "lastSeen": d.get("lastSeen", 0),
        }
    return roster


async def stop_phase(display_id, roster, wait_seconds):
    """Fire RUN_SCRIPT stop and watch server.err for per-iPad disconnect
    log lines. Returns {client_key: seconds_to_disconnect} for iPads that
    disconnected within the window; missing keys never disconnected."""
    expected_names = {info["name"] for info in roster.values()}
    name_to_key = {info["name"]: k for k, info in roster.items()}

    log_offset = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    print(f"\n=== STOP phase: {len(roster)} online iPads, log mark {log_offset} bytes ===")

    t0 = time.time()
    async with SockJSClient(SERVER) as c:
        await c.send_msg("RUN_SCRIPT", {"displayID": display_id, "script": "stop"})
    print(f"[{0:5.2f}s] RUN_SCRIPT stop dispatched")

    disconnect_times = {}  # client_key -> elapsed seconds since RUN_SCRIPT
    deadline = t0 + wait_seconds
    last_report = 0

    while time.time() < deadline and len(disconnect_times) < len(roster):
        await asyncio.sleep(0.5)
        for ts, name in parse_disconnects_since(log_offset, deadline):
            if name not in expected_names:
                continue
            key = name_to_key[name]
            if key in disconnect_times:
                continue
            # Compute elapsed from script-dispatch (use server's log ts).
            elapsed = ts.timestamp() - t0
            # Server clock and our wall clock are the same process on the
            # same host, but use absolute log-timestamp delta to be safe.
            disconnect_times[key] = max(0.0, elapsed)
        # Periodic progress
        now = time.time() - t0
        if now - last_report >= 2.0:
            last_report = now
            print(f"[{now:5.2f}s] disconnected: {len(disconnect_times)}/{len(roster)}")

    # Final tally
    elapsed = time.time() - t0
    print(f"[{elapsed:5.2f}s] STOP phase done: {len(disconnect_times)}/{len(roster)} disconnected")
    return disconnect_times


async def start_phase(http, display_id, roster, pre_lastseen, wait_seconds):
    """Fire RUN_SCRIPT start, poll discovery API until each iPad's
    lastSeen ticks past the pre-Start mark. Returns
    {client_key: seconds_to_reregister}."""
    print(f"\n=== START phase: waiting up to {wait_seconds}s for re-REGISTERs ===")

    t0 = time.time()
    async with SockJSClient(SERVER) as c:
        await c.send_msg("RUN_SCRIPT", {"displayID": display_id, "script": "start"})
    print(f"[{0:5.2f}s] RUN_SCRIPT start dispatched")

    reregister_times = {}
    deadline = t0 + wait_seconds
    last_report = 0

    while time.time() < deadline and len(reregister_times) < len(roster):
        await asyncio.sleep(1.0)
        try:
            devs = await fetch_devices(http)
        except Exception:
            continue
        for d in devs:
            k = d.get("clientKey")
            if k not in roster or k in reregister_times:
                continue
            new_ls = d.get("lastSeen", 0)
            if new_ls > pre_lastseen.get(k, 0) + 0.5:
                reregister_times[k] = time.time() - t0
        now = time.time() - t0
        if now - last_report >= 3.0:
            last_report = now
            print(f"[{now:5.2f}s] re-registered: {len(reregister_times)}/{len(roster)}")

    elapsed = time.time() - t0
    print(f"[{elapsed:5.2f}s] START phase done: {len(reregister_times)}/{len(roster)} re-registered")
    return reregister_times


# --- summary -----------------------------------------------------------------
def print_summary(roster, stop_times, start_times):
    print("\n=== summary: per-iPad lifecycle timing ===\n")
    header = f"  {'iPad':22s}  {'IP':15s}  {'stop -> off':>12s}  {'start -> on':>12s}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    stop_observed = []
    start_observed = []
    for key in sorted(roster, key=lambda k: roster[k]["name"]):
        info = roster[key]
        s = stop_times.get(key)
        r = start_times.get(key)
        s_str = f"{s:>7.2f}s" if s is not None else "  TIMEOUT"
        r_str = f"{r:>7.2f}s" if r is not None else "  TIMEOUT"
        print(f"  {info['name']:22s}  {info['ip']:15s}  {s_str:>12s}  {r_str:>12s}")
        if s is not None: stop_observed.append(s)
        if r is not None: start_observed.append(r)

    print()
    n = len(roster)
    if stop_observed:
        med = sorted(stop_observed)[len(stop_observed) // 2]
        print(f"  STOP:  {len(stop_observed)}/{n} disconnected.  "
              f"min={min(stop_observed):.2f}s  median={med:.2f}s  "
              f"max={max(stop_observed):.2f}s")
    else:
        print(f"  STOP:  0/{n} disconnected -- something is wrong.")
    if start_observed:
        med = sorted(start_observed)[len(start_observed) // 2]
        print(f"  START: {len(start_observed)}/{n} re-registered.  "
              f"min={min(start_observed):.2f}s  median={med:.2f}s  "
              f"max={max(start_observed):.2f}s")
    else:
        print(f"  START: 0/{n} re-registered -- something is wrong.")

    missing_stop = [roster[k]["name"] for k in roster if k not in stop_times]
    missing_start = [roster[k]["name"] for k in roster if k not in start_times]
    if missing_stop:
        print(f"\n  Never disconnected ({len(missing_stop)}): {', '.join(missing_stop)}")
    if missing_start:
        print(f"\n  Never re-registered ({len(missing_start)}): {', '.join(missing_start)}")


# --- main --------------------------------------------------------------------
async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    display_id = sys.argv[1]
    stop_wait = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    start_wait = int(sys.argv[3]) if len(sys.argv) > 3 else 45

    async with aiohttp.ClientSession() as http:
        roster = await snapshot_roster(http, display_id)
        if not roster:
            print(f"No online iPads in display group '{display_id}'.")
            sys.exit(1)

        print(f"Pre-test roster: {len(roster)} online iPads in '{display_id}'")
        for key, info in sorted(roster.items(), key=lambda kv: kv[1]["name"]):
            print(f"  {info['name']:22s}  {info['ip']:15s}  {key}")

        pre_lastseen = {k: info["lastSeen"] for k, info in roster.items()}

        stop_times = await stop_phase(display_id, roster, stop_wait)

        # Settle between phases so SockJS sessions fully tear down before
        # we ask the iPads to come back.
        print(f"\nsettle (3s)...")
        await asyncio.sleep(3.0)

        start_times = await start_phase(http, display_id, roster,
                                        pre_lastseen, start_wait)
        print_summary(roster, stop_times, start_times)


if __name__ == "__main__":
    asyncio.run(main())
