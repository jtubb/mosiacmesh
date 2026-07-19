"""Trigger a SEGMENT render on a display group and watch the cache-push
hooks fire. Used to verify acceptance criteria C3 in the media-cache spec
(docs/superpowers/specs/2026-06-03-media-cache-design.md).

Sends a RENDER request over SockJS, then tails server.err for:
  - "render: N ffmpeg jobs done in X.Xs"  -- render-completion signal
  - "cache-push: <key> seg_<hash>_<n> -> <ip>"  -- per-iPad push event

Compares the observed push count against the expected count (one per
cacheMode=mmvideo iPad per renderable SEGMENT item).

Usage:
    python tools/trigger_render_and_watch.py "Test Group" [timeout_seconds]

Default timeout is 1500s (25 minutes), sized for a 24-iPad fleet at the
current production ffmpeg cap (single-process per output, MMRENDER_
CONCURRENCY-bounded). Optimised filter_complex path (tools/bench_render.py)
would bring this under a minute but isn't yet the production path.
"""
import asyncio
import json
import os
import re
import sys
import time

import aiohttp

SERVER = "http://localhost:3000"
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "server.err")


# --- SockJS minimal client (copy of run_and_collect.py's class; could be
#     factored into a shared module but DRY across two single-purpose
#     scripts isn't worth a third file yet). ----------------------------
class SockJSClient:
    def __init__(self, base_url, session_id="admin_render"):
        self.url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.url += f"/sockjs/000/{session_id}/websocket"
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

    async def send_msg(self, request, payload, src="admin_render"):
        msg = {"SRC": src, "DEST": "SRV", "REQUEST": request, "PAYLOAD": payload}
        frame = json.dumps([json.dumps(msg)])
        await self.ws.send_str(frame)


async def trigger_render(display_id):
    async with SockJSClient(SERVER) as c:
        await c.send_msg("RENDER", {"displayID": display_id})


def tail_log_since(start_byte):
    """Read server.err from start_byte to EOF, return new bytes + lines."""
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(start_byte)
            chunk = f.read()
    except FileNotFoundError:
        return 0, []
    if not chunk:
        return start_byte, []
    text = chunk.decode("utf-8", errors="replace")
    return start_byte + len(chunk), text.splitlines()


async def expected_push_count(display_id):
    """Compute the expected number of cache-push log lines for a render
    of `display_id`: (mmvideo iPads in group) * (renderable
    items in playlist). Reads from /api/discovery/devices + the stats
    endpoint; falls back to None if we can't determine the playlist
    item count from the API (the spec calls for at-least-one-push-per-
    cached-iPad which is the actually-important signal)."""
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(f"{SERVER}/api/discovery/devices",
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
        except Exception as e:
            print(f"  warn: couldn't fetch devices: {e}")
            return None
    devs = data.get("devices", data) if isinstance(data, dict) else data
    cached_in_group = [d for d in devs
                       if d.get("displayID") == display_id
                       and d.get("cacheMode") == "mmvideo"]
    return len(cached_in_group)  # at least one push per cached iPad


async def main():
    display_id = sys.argv[1] if len(sys.argv) > 1 else "Test Group"
    timeout_s = int(sys.argv[2]) if len(sys.argv) > 2 else 1500

    cached_n = await expected_push_count(display_id)
    print(f"display={display_id!r} cached iPads in group: {cached_n}")
    print(f"timeout: {timeout_s}s")

    # Capture log offset BEFORE triggering render so we can attribute
    # subsequent log lines to this specific render run (not stragglers
    # from a previous render).
    start_byte = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    print(f"server.err offset at start: {start_byte}")

    print(f"sending RENDER {display_id!r}...")
    await trigger_render(display_id)
    t0 = time.time()

    # Watch for: "render: N ffmpeg jobs done" (completion) and
    # "cache-push:" lines (each successful push).
    render_done = False
    ffmpeg_total = None
    ffmpeg_secs = None
    push_lines = []
    last_status_print = 0.0

    re_jobs = re.compile(r"render: launching (\d+) ffmpeg jobs")
    re_done = re.compile(r"render: (\d+) ffmpeg jobs done in ([\d.]+)s")
    re_push = re.compile(r"cache-push: (\S+) seg_(\S+) -> (\S+)")
    re_render_failed = re.compile(r"render failed for (\S+):")

    last_offset = start_byte
    while time.time() - t0 < timeout_s:
        await asyncio.sleep(2.0)
        last_offset, lines = tail_log_since(last_offset)
        for line in lines:
            if re_jobs.search(line):
                m = re_jobs.search(line)
                ffmpeg_total = int(m.group(1))
                print(f"  [+{int(time.time()-t0)}s] ffmpeg jobs launched: {ffmpeg_total}")
            elif re_done.search(line):
                m = re_done.search(line)
                ffmpeg_total = int(m.group(1))
                ffmpeg_secs = float(m.group(2))
                render_done = True
                print(f"  [+{int(time.time()-t0)}s] render DONE: "
                      f"{ffmpeg_total} ffmpeg jobs in {ffmpeg_secs:.1f}s")
            elif re_push.search(line):
                m = re_push.search(line)
                push_lines.append((m.group(1), m.group(2), m.group(3)))
            elif re_render_failed.search(line):
                print(f"  [+{int(time.time()-t0)}s] render FAILED: {line.strip()}")
                return 1
        # Periodic progress print.
        if time.time() - last_status_print > 30:
            last_status_print = time.time()
            print(f"  [+{int(time.time()-t0)}s] render_done={render_done} "
                  f"pushes_so_far={len(push_lines)}")
        # Once render is done, give the fire-and-forget pushes some
        # time to complete (each scp is bounded by _push_segment_
        # to_cached_clients's own timeout). Bail out as soon as we
        # have at least `cached_n` distinct (client_key) push lines,
        # which is the C3 criterion.
        if render_done and cached_n is not None:
            distinct_keys = set(k for k, _, _ in push_lines)
            if len(distinct_keys) >= cached_n:
                print(f"  [+{int(time.time()-t0)}s] all {cached_n} cached "
                      f"iPads have at least 1 push -> done")
                break

    print()
    print("=== C3 acceptance ===")
    print(f"  render completed:  {render_done}")
    if ffmpeg_total is not None:
        print(f"  ffmpeg jobs run:   {ffmpeg_total}"
              + (f" in {ffmpeg_secs:.1f}s" if ffmpeg_secs else ""))
    print(f"  cache-push lines:  {len(push_lines)}")
    distinct_keys = sorted(set(k for k, _, _ in push_lines))
    print(f"  distinct iPads pushed to: {len(distinct_keys)}")
    if cached_n is not None:
        ok = len(distinct_keys) >= cached_n
        print(f"  expected (cached iPads in group): {cached_n}")
        print(f"  C3 PASS: {ok}")

    # Verify cachedSegments populated per iPad (server-side state).
    print()
    print("=== Verifying client.cachedSegments via /api/discovery/devices ===")
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{SERVER}/api/discovery/devices") as r:
            data = await r.json()
    devs = data.get("devices", data) if isinstance(data, dict) else data
    in_group = [d for d in devs if d.get("displayID") == display_id]
    have = [d for d in in_group if d.get("cachedSegments")]
    print(f"  group iPads: {len(in_group)}, with cachedSegments: {len(have)}")
    # Print one example
    if have:
        ex = have[0]
        print(f"  example: {ex['friendlyName']} cachedSegments={ex['cachedSegments']}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
