"""Triggers PLAY on a display group, captures server.err during the
PLAY window, and quantifies which iPads went through the central
/media/ path vs. their localhost lighttpd cache. Used to verify
acceptance criteria C4 + C5 in the media-cache spec.

For each /media/ GET observed during the capture window, attributes
it to its source client (by IP via aiohttp access log), checks that
client's cacheMode, and tallies bytes-served. The expected outcome
when the cache is healthy:

  - C4 PASS: cacheMode=mmvideo iPads produce ZERO /media/
    GETs for seg_<hash>_<n>.mp4 files (the cache routing intercepted
    them at the URL-payload level via _per_client_items).
  - C5 PASS: aggregate /media/ bytes-served during PLAY is dominated
    by non-cached clients (mostly browser-tab admin clients, no
    iPads), and is ≪ 1 GB (vs. the ~27 GB baseline with no caching).

Drift measurement (C6) is left to tools/run_and_collect.py which
already does the CLIENTLOG-based drift sampling end-to-end.

Usage:
    python tools/measure_play_bandwidth.py "Test Group" [capture_seconds]

Default capture_seconds = 60. The Test Group playlist's video item is
~10 minutes long; capturing the first minute is enough to see the iPads
begin fetching their segment URLs and prove which path they came from.
"""
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

import aiohttp

SERVER = "http://localhost:3000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "server.err")


class SockJSClient:
    def __init__(self, base_url, session_id="admin_bw"):
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.url += f"/sockjs/000/{session_id}/websocket"
        self.session = None
        self.ws = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url)
        o = await self.ws.receive_str(timeout=5)
        if o != "o":
            raise RuntimeError(f"expected 'o', got {o!r}")
        return self

    async def __aexit__(self, *args):
        try:
            await self.ws.close()
        finally:
            await self.session.close()

    async def send(self, request, payload, src="admin_bw"):
        msg = {"SRC": src, "DEST": "SRV", "REQUEST": request, "PAYLOAD": payload}
        await self.ws.send_str(json.dumps([json.dumps(msg)]))


async def fetch_devices():
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{SERVER}/api/discovery/devices") as r:
            data = await r.json()
    return data.get("devices", data) if isinstance(data, dict) else data


def file_size(rel_path):
    """rel_path is the URL path after /media/. Map to disk path and
    return file size in bytes, or 0 if missing."""
    # /media/<client>/<sub>/<file> or /media/<client>/<file>
    disk = os.path.join(ROOT, "media", rel_path.lstrip("/"))
    try:
        return os.path.getsize(disk)
    except OSError:
        return 0


async def main():
    display_id = sys.argv[1] if len(sys.argv) > 1 else "Test Group"
    capture_s = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    # Pre-PLAY: snapshot fleet state + log offset.
    devs = await fetch_devices()
    by_ip = {d["ip"]: d for d in devs if d.get("ip")}
    cached_ips = {ip for ip, d in by_ip.items()
                  if d.get("cacheMode") == "mmvideo"}
    print(f"display={display_id!r}  capture={capture_s}s")
    print(f"cached iPads (mmvideo): {len(cached_ips)}")
    print(f"non-cached clients: {len(by_ip) - len(cached_ips)}")

    start_byte = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    print(f"server.err offset at start: {start_byte}")

    # Trigger PLAY. We don't ASSIGN_PLAYLIST here because the goal is
    # to exercise the *cache* with whatever playlist is already attached
    # to the group -- if the operator wants a specific playlist they
    # can run tools/run_and_collect.py beforehand.
    #
    # Hold the WS open inside the context manager for the full capture
    # window. Earlier versions of this script closed the WS immediately
    # after `await c.send("PLAY", ...)`, which raced the server's
    # response write -- "Cannot write to closing transport" exception
    # in the SockJS dispatch path AND the PLAY message itself may not
    # have been received before the close. Keeping the connection open
    # for the whole window also lets us observe any CACHE_PROGRESS /
    # STARTED_PLAYBACK / etc. broadcasts.
    print(f"sending PLAY {display_id!r}...")
    async with SockJSClient(SERVER) as c:
        await c.send("PLAY", {"displayID": display_id})
        try:
            await asyncio.wait_for(c.ws.receive(), timeout=2)  # consume PLAY response
        except (asyncio.TimeoutError, Exception):
            pass
        # Wait for the capture window. The video segments are streamed
        # via HTTP range requests so we expect repeated GETs per file.
        await asyncio.sleep(capture_s)
        # Stop playback (don't leave the fleet running an unmonitored
        # 10-minute video). STOP goes over the SAME connection so we
        # don't race close-vs-dispatch a second time.
        await c.send("STOP", {"displayID": display_id})
        try:
            await asyncio.wait_for(c.ws.receive(), timeout=2)
        except (asyncio.TimeoutError, Exception):
            pass

    end_byte = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else start_byte

    # Read the capture window's log delta. aiohttp logs access lines in
    # the format:
    #   IP - - [DD/MM/YYYY:HH:MM:SS +ZZZZ] "GET /path HTTP/1.1" STATUS BYTES ...
    with open(LOG_PATH, "rb") as f:
        f.seek(start_byte)
        text = f.read(end_byte - start_byte).decode("utf-8", errors="replace")

    # Match access lines and pull (ip, method, path, status, bytes).
    rx = re.compile(
        r'^([0-9a-f.:]+)\s+\[[^\]]+\]\s+'  # ip + bracketed date
        r'"GET\s+(/media/[^\s"]+)\s+HTTP[^"]*"\s+(\d+)\s+(\d+)\b',
        re.MULTILINE,
    )

    # The actual aiohttp access log we see in server.err is
    # "IP [date] \"METHOD URL HTTP/1.1\" status bytes ..." with one
    # space, but the IP may be IPv6 (::1) or IPv4. Match flexibly.
    rx2 = re.compile(
        r'(\S+)\s+\[\d{2}/\w{3}/\d{4}[^\]]*\]\s+"GET\s+(/media/\S+)\s+HTTP[^"]*"\s+(\d+)\s+(\d+)',
    )

    gets = []
    for m in rx2.finditer(text):
        ip, path, status, nbytes = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
        # Strip query string if any (range requests have none typically).
        if "?" in path:
            path = path.split("?", 1)[0]
        gets.append((ip, path, status, nbytes))

    print(f"\n=== Capture window: {len(text)} bytes of server.err, "
          f"{len(gets)} /media/ GETs observed ===")

    # Bucket by client (iPad if known, else "non-iPad/admin").
    by_client = defaultdict(lambda: {"gets": 0, "bytes": 0,
                                      "cacheMode": None, "name": None})
    for ip, path, status, nbytes in gets:
        d = by_ip.get(ip)
        name = d["friendlyName"] if d else f"(unknown {ip})"
        mode = d["cacheMode"] if d else "(unregistered)"
        b = by_client[ip]
        b["gets"] += 1
        b["bytes"] += nbytes
        b["cacheMode"] = mode
        b["name"] = name

    # Print per-client summary, sorted by bytes desc.
    print(f"\n=== Per-client /media/ traffic during PLAY window ===")
    print(f"{'name':<22} {'ip':<16} {'mode':<22} {'GETs':>6} {'bytes':>14}")
    total_bytes_cached = 0
    total_bytes_uncached = 0
    for ip, info in sorted(by_client.items(),
                            key=lambda kv: -kv[1]["bytes"]):
        print(f"{info['name']:<22} {ip:<16} {info['cacheMode']:<22} "
              f"{info['gets']:>6} {info['bytes']:>14,}")
        if info["cacheMode"] == "mmvideo":
            total_bytes_cached += info["bytes"]
        else:
            total_bytes_uncached += info["bytes"]

    print(f"\n=== Acceptance ===")
    cached_with_gets = sum(1 for info in by_client.values()
                            if info["cacheMode"] == "mmvideo"
                            and info["gets"] > 0)
    print(f"C4 (cached iPads with central /media/ GETs): {cached_with_gets}")
    print(f"   PASS = 0; observed = {cached_with_gets}")

    total_bytes = total_bytes_cached + total_bytes_uncached
    print(f"\nC5 aggregate /media/ bytes during {capture_s}s PLAY: {total_bytes:,}")
    print(f"  from cached iPads (should be 0): {total_bytes_cached:,}")
    print(f"  from non-cached clients:         {total_bytes_uncached:,}")
    print(f"  baseline was ~27,000,000,000 (27 GB / 93s)")
    pace_per_s = total_bytes / capture_s if capture_s else 0
    extrap_93s = int(pace_per_s * 93)
    print(f"  extrapolated to 93s: {extrap_93s:,} bytes "
          f"({extrap_93s/1e9:.2f} GB)")


if __name__ == "__main__":
    asyncio.run(main())
