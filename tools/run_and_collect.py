"""Trigger a playlist run on a display group and collect per-device
CLIENTLOG diagnostics (only useful when the group's iPads have been
opened with ?tdbg via the "Start Testing" lifecycle action).

Connects to the server's SockJS endpoint over a raw websocket, sends
ASSIGN_PLAYLIST + PLAY, then watches server.err for CLIENTLOG entries
and prints a per-iPad summary at the end.

Usage:
    python tools/run_and_collect.py "Test Group" "Test 2" [seconds]

Default collection window is 30 seconds.
"""
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict

import aiohttp

SERVER = "http://localhost:3000"
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "server.err")


# --- SockJS minimal client ---------------------------------------------------
# The SockJS websocket sub-protocol is:
#   server sends "o"                        -> session open
#   client sends '["json-msg"]'             -> message (must be a JSON-array
#                                              with one JSON-encoded string)
#   server sends 'a["msg"]'                 -> incoming message
#   server sends 'h'                        -> heartbeat
#   server sends 'c[code,reason]'           -> close
class SockJSClient:
    def __init__(self, base_url):
        # SockJS endpoint: /sockjs/<server_id>/<session_id>/websocket
        # server_id is 3-digit numeric (load-balancer hint); session_id is
        # an opaque alphanumeric chosen by the client. The 'mosiacmesh'
        # NAME used in sockjs.add_endpoint is internal to the server and
        # doesn't appear in the URL.
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.url += "/sockjs/000/admin_collect/websocket"
        self.session = None
        self.ws = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url)
        # Wait for the SockJS open frame ("o")
        open_frame = await self.ws.receive_str(timeout=5)
        if open_frame != "o":
            raise RuntimeError(f"expected SockJS open 'o', got {open_frame!r}")
        return self

    async def __aexit__(self, *args):
        try:
            await self.ws.close()
        finally:
            await self.session.close()

    async def send_msg(self, request, payload, src="admin_collect"):
        msg = {"SRC": src, "DEST": "SRV", "REQUEST": request, "PAYLOAD": payload}
        # SockJS expects a JSON array containing one JSON-encoded string.
        frame = json.dumps([json.dumps(msg)])
        await self.ws.send_str(frame)


# --- Run + collect -----------------------------------------------------------
async def trigger(display_id, playlist_name):
    async with SockJSClient(SERVER) as c:
        await c.send_msg("ASSIGN_PLAYLIST",
                         {"name": playlist_name, "displayID": display_id})
        # Settle delay: ASSIGN_PLAYLIST triggers a PRELOAD broadcast to every
        # client in the group. Clients need time to process the new playlist
        # state (set up media element URLs, attach event listeners) before
        # PREPARE arrives -- otherwise PREPARE can land before the page is
        # ready and the recv-PREPARE handler bails silently. 3s is generous
        # for the 24-iPad fleet on iOS 5 (each iPad takes ~200-500ms to
        # process PRELOAD after a fresh Safari restart).
        await asyncio.sleep(3.0)
        await c.send_msg("PLAY", {"displayID": display_id})


async def start_testing(display_id):
    """Fire login -> start -> test lifecycle scripts on every iPad in
    the group, in sequence with short pauses. The login script wakes
    the iPad (lockscreen.dismiss + autolock off). The test script kills
    + relaunches Safari with the ?tdbg URL. If the fleet went silent
    overnight (WiFi power-save, Safari crashed, LaunchDaemon retry never
    fired), this sequence is what brings them all back to a known
    listening state before we try to play."""
    async with SockJSClient(SERVER) as c:
        # Wake all devices first (state-independent: works on locked,
        # sleeping, or already-awake iPads). Then test script which
        # killalls Safari and uiopens ?tdbg.
        await c.send_msg("RUN_SCRIPT", {"displayID": display_id, "script": "login"})
        await asyncio.sleep(3.0)
        await c.send_msg("RUN_SCRIPT", {"displayID": display_id, "script": "test"})


async def wait_for_reconnect(display_id, expected_count, timeout_s):
    """Poll the discovery API for clients in the group that have completed
    the SYN/SYNACK handshake (`synced=True`). This is a stronger readiness
    signal than fresh `lastSeen` -- lastSeen ticks on any client message
    including pre-handshake REGISTERs, while `synced=True` means the page
    has finished its clock-sync round-trip AND is ready to receive
    coordinated PREPARE/PLAY messages.

    Without this gate, PREPARE can land before some iPads' page handlers
    are wired up and the broadcast is silently dropped on the iPad side
    (the recv-PREPARE handler bails when sock_callback isn't yet
    registered). Empirically dropping PREPARE delivery from ~50% to ~0%."""
    deadline = time.time() + timeout_s
    async with aiohttp.ClientSession() as session:
        synced = 0
        while time.time() < deadline:
            await asyncio.sleep(2)
            try:
                async with session.get(f"{SERVER}/api/discovery/devices",
                                        timeout=aiohttp.ClientTimeout(total=3)) as r:
                    data = await r.json()
            except Exception:
                continue
            devs = data.get("devices", data) if isinstance(data, dict) else data
            synced_list = [d for d in devs
                           if d.get("displayID") == display_id
                           and d.get("synced", False)
                           and d.get("isOnline", False)]
            synced = len(synced_list)
            print(f"  synced (SYN/SYNACK complete): {synced}/{expected_count}",
                  end="\r")
            if synced >= expected_count:
                print()
                return synced
    print()
    return synced


# CLIENTLOG line format in server.err:
#   <iso-ts> WARNING CLIENTLOG <src-udid> {'tag': 'play', 'activated': True, ...}
CLIENTLOG_RE = re.compile(
    r"^(?P<ts>\S+ \S+) WARNING CLIENTLOG (?P<src>\S+) (?P<payload>\{.*\})\s*$"
)


def parse_logs(start_byte):
    """Yield (ts, src, payload_dict) tuples for CLIENTLOG entries
    written after `start_byte` in the server log."""
    if not os.path.exists(LOG_PATH):
        return
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        f.seek(start_byte)
        for line in f:
            m = CLIENTLOG_RE.match(line)
            if not m:
                continue
            try:
                # Python repr of a dict is parseable via ast.literal_eval.
                import ast
                payload = ast.literal_eval(m.group("payload"))
            except Exception:
                continue
            yield m.group("ts"), m.group("src"), payload


def summarize(events):
    """Group events by client UDID and print per-iPad stats: event count,
    tag breakdown, video error counts, readyState distribution, current-
    time progression, and elapsed-vs-clock drift."""
    by_src = defaultdict(list)
    for ts, src, payload in events:
        by_src[src].append((ts, payload))

    if not by_src:
        print("(no CLIENTLOG events captured — are the iPads in ?tdbg mode?)")
        return

    print(f"\n=== captured {sum(len(v) for v in by_src.values())} events from "
          f"{len(by_src)} client(s) ===\n")
    for src in sorted(by_src):
        evs = by_src[src]
        tags = defaultdict(int)
        verrs = defaultdict(int)
        rs_counts = defaultdict(int)
        ct_first = ct_last = None
        elapsed_first = elapsed_last = None
        for _, p in evs:
            tags[p.get("tag", "")] += 1
            if p.get("verr") is not None:
                verrs[p["verr"]] += 1
            if p.get("rs") is not None:
                rs_counts[p["rs"]] += 1
            ct = p.get("ct")
            if ct is not None:
                if ct_first is None:
                    ct_first = ct
                ct_last = ct
            el = p.get("elapsed")
            if el is not None:
                if elapsed_first is None:
                    elapsed_first = el
                elapsed_last = el

        ct_delta = (ct_last - ct_first) if ct_first is not None else None
        el_delta = (elapsed_last - elapsed_first) if elapsed_first is not None else None
        drift = None
        if ct_delta is not None and el_delta is not None and el_delta > 0:
            drift = ct_delta - el_delta  # ms; +ve = video ran ahead of clock

        print(f"-- {src}")
        print(f"   events: {len(evs)}")
        tag_list = ", ".join(f"{t}={n}" for t, n in sorted(tags.items()))
        print(f"   tags  : {tag_list}")
        if verrs:
            print(f"   video errors: {dict(verrs)}")
        if rs_counts:
            print(f"   readyState  : {dict(rs_counts)}")
        if ct_delta is not None:
            print(f"   video progressed {ct_delta/1000:.2f}s "
                  f"(currentTime {ct_first}ms -> {ct_last}ms)")
        if drift is not None:
            print(f"   drift vs synced clock: {drift:+d}ms over {el_delta/1000:.2f}s")
        print()


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    display_id = sys.argv[1]
    playlist = sys.argv[2]
    secs = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    expected_clients = int(sys.argv[4]) if len(sys.argv) > 4 else 24

    print(f"step 1/4: firing Start Testing (RUN_SCRIPT test) on '{display_id}'")
    await start_testing(display_id)

    print(f"step 2/4: waiting up to 30s for tdbg-mode reconnects "
          f"(expecting ~{expected_clients} clients)")
    n_reconnected = await wait_for_reconnect(display_id, expected_clients, 30)
    print(f"  {n_reconnected} client(s) sending CLIENTLOG before we proceed")

    start_byte = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    print(f"\nstep 3/4: server log mark: {start_byte} bytes; "
          f"triggering ASSIGN_PLAYLIST '{playlist}' + PLAY on '{display_id}'")
    await trigger(display_id, playlist)

    print(f"step 4/4: collecting for {secs}s...")
    await asyncio.sleep(secs)

    events = list(parse_logs(start_byte))
    summarize(events)


if __name__ == "__main__":
    asyncio.run(main())
