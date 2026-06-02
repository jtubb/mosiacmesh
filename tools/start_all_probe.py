"""Fire RUN_SCRIPT start on a display group and measure the inbound
connection burst.

The original "Failed to load, server did not respond" symptom on a
random iPad each click was traced to aiohttp's default TCPSite
backlog=128. This probe verifies (a) whether the burst actually
exceeded 128 concurrent inbound connections (it must have if the
backlog fix is necessary) and (b) which iPads, if any, fail to send
a fresh REGISTER within the post-Start window.

Usage:
    python tools/start_all_probe.py "Test Group" [seconds]
"""
import asyncio
import json
import re
import subprocess
import sys
import time
from collections import Counter

import aiohttp

SERVER = "http://localhost:3000"
PORT = 3000


class SockJSClient:
    def __init__(self, base_url):
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.url += "/sockjs/000/start_all_probe/websocket"
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

    async def send_msg(self, request, payload, src="start_all_probe"):
        msg = {"SRC": src, "DEST": "SRV", "REQUEST": request, "PAYLOAD": payload}
        frame = json.dumps([json.dumps(msg)])
        await self.ws.send_str(frame)


def count_port_3000_conns():
    """Return (total_inbound, by_state) for connections to local port 3000.
    Uses PowerShell Get-NetTCPConnection for an authoritative count."""
    # -Format Json is not available on older versions, parse plain output.
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue | "
         "Select-Object State,RemoteAddress | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=5)
    if p.returncode != 0 or not p.stdout.strip():
        return 0, Counter()
    raw = p.stdout.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0, Counter()
    if isinstance(data, dict):
        data = [data]
    states = Counter()
    inbound = 0
    for row in data:
        st = row.get("State", "")
        ra = row.get("RemoteAddress", "")
        states[str(st)] += 1
        # Inbound = connection where the remote is *not* localhost.
        if ra and not ra.startswith(("127.", "::1", "0.0.0.0")):
            inbound += 1
    return inbound, states


async def fetch_devices(session):
    async with session.get(f"{SERVER}/api/discovery/devices",
                           timeout=aiohttp.ClientTimeout(total=5)) as r:
        data = await r.json()
    devs = data.get("devices", data) if isinstance(data, dict) else data
    return devs


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    display_id = sys.argv[1]
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    async with aiohttp.ClientSession() as http:
        # Snapshot the fleet roster before we click Start.
        pre = await fetch_devices(http)
        targets = [d for d in pre
                   if d.get("displayID") == display_id and d.get("isOnline", False)]
        print(f"pre-Start: {len(targets)} online iPads in '{display_id}'")
        target_keys = {d.get("clientKey") for d in targets}
        pre_lastseen = {d.get("clientKey"): d.get("lastSeen", 0) for d in targets}

        # Baseline connection count before the burst.
        base_in, base_states = count_port_3000_conns()
        print(f"baseline inbound conns: {base_in}  states: {dict(base_states)}")

        t0 = time.time()
        print(f"\n[{0:5.2f}s] firing RUN_SCRIPT start on '{display_id}'")
        async with SockJSClient(SERVER) as c:
            await c.send_msg("RUN_SCRIPT",
                             {"displayID": display_id, "script": "start"})

        # Sample connection count tightly during the burst.
        peak_in = base_in
        peak_t = 0.0
        peak_states = base_states
        samples = []
        end = time.time() + secs
        while time.time() < end:
            await asyncio.sleep(0.25)
            t = time.time() - t0
            inb, states = count_port_3000_conns()
            samples.append((t, inb))
            if inb > peak_in:
                peak_in = inb
                peak_t = t
                peak_states = states

        print(f"\npeak inbound conns: {peak_in} at t={peak_t:.2f}s  "
              f"states: {dict(peak_states)}")
        print(f"baseline was {base_in}  -> burst added {peak_in - base_in}")

        # How many iPads have re-registered (fresh lastSeen) within the window?
        post = await fetch_devices(http)
        reconnected = 0
        slow = []
        for d in post:
            k = d.get("clientKey")
            if k not in target_keys:
                continue
            new_ls = d.get("lastSeen", 0)
            old_ls = pre_lastseen.get(k, 0)
            if new_ls > old_ls:
                reconnected += 1
            else:
                slow.append((k, d.get("friendlyName") or d.get("ip")))

        print(f"\nfresh REGISTERs within {secs}s: "
              f"{reconnected}/{len(target_keys)}")
        if slow:
            print("  iPads with no fresh REGISTER (potential SYN-drop victims):")
            for k, name in slow:
                print(f"    - {name}  ({k})")
        else:
            print("  all iPads re-registered cleanly")

        # If the old 128 backlog would have been blown out, log it loudly.
        if peak_in > 128:
            print(f"\nNB: peak inbound ({peak_in}) exceeds the old default "
                  f"backlog of 128 -- so the previous server would have dropped "
                  f"SYNs during this burst.")
        else:
            print(f"\nNB: peak inbound ({peak_in}) stayed within the old "
                  f"default backlog of 128. The bottleneck may be elsewhere.")


if __name__ == "__main__":
    asyncio.run(main())
