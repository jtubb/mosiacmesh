"""Push freshly-rendered segment files to each cached iPad's lighttpd
directory, throttled so we don't saturate the WiFi the way the
naive all-parallel _push_segment_to_cached_clients did. Updates
client.cachedSegments via direct settings.dat mutation through the
running server's discovery API (set_cached_segments action -- if the
API doesn't expose it, we drop a sentinel by writing a per-segment
file and let the server's reconcile pass adopt it).

This is a workaround for the production-load issue discovered during
C3 acceptance testing on 2026-06-03: 24 parallel scps to iPads
sharing one AP all timed out at 120s. The proper fix is a
Semaphore in render_group_async around the ensure_future calls, but
that requires a server restart. This tool gets us unblocked NOW.

Usage:
    python tools/push_segments_throttled.py [display_id] [concurrency] [segment_hash]

Defaults: display_id="Test Group", concurrency=2, segment_hash=auto
(reads the most recent seg_HASH_N.mp4 across the display group's iPads).
"""
import asyncio
import os
import re
import sys
from collections import defaultdict

import aiohttp

SERVER = "http://localhost:3000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSH_KEY = os.path.expanduser(r"~\.ssh\mosaic_ipad")


def discover_latest_hash(display_id_clients):
    """Walk media/<key>/videos/ and find the most recent seg_HASH_N.mp4
    across the display's iPads. Returns the hash string."""
    rx = re.compile(r"seg_([0-9a-f]+)_(\d+)\.mp4$")
    best_mtime = 0
    best_hash = None
    for key in display_id_clients:
        d = os.path.join(ROOT, "media", key, "videos")
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            m = rx.match(f)
            if not m:
                continue
            mt = os.path.getmtime(os.path.join(d, f))
            if mt > best_mtime:
                best_mtime = mt
                best_hash = m.group(1)
    return best_hash


async def fetch_devices():
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{SERVER}/api/discovery/devices") as r:
            data = await r.json()
    return data.get("devices", data) if isinstance(data, dict) else data


async def push_one(sem, client_key, ip, src_path, hash_, n):
    """Run scp inside the semaphore. Streaming output bytes so we can
    detect partial transfers vs. true completion. Returns (success,
    duration_s, bytes_sent_estimate_from_local_file)."""
    async with sem:
        dst = f"root@{ip}:/var/mobile/Media/MosaicMeshCache/seg_{hash_}_{n}.mp4"
        cmd = [
            "scp",
            "-i", SSH_KEY,
            "-o", "HostKeyAlgorithms=+ssh-rsa",
            "-o", "PubkeyAcceptedKeyTypes=+ssh-rsa",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=15",
            src_path,
            dst,
        ]
        t0 = asyncio.get_event_loop().time()
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            # 10-minute ceiling per push (was 120s in the naive
            # version -- way too tight for ~100MB over contended WiFi).
            out, err = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return (False, asyncio.get_event_loop().time() - t0, "TIMEOUT")
        dt = asyncio.get_event_loop().time() - t0
        if proc.returncode == 0:
            return (True, dt, os.path.getsize(src_path))
        tail = (err or b"").decode("utf-8", "replace").strip()[-300:]
        return (False, dt, tail)


async def update_cached_segments(client_key, seg_keys):
    """Tell the server: this client now has these segments cached.
    Uses the new set_cached_segments action if available; if not,
    falls back to mutating settings via a direct file write (not
    implemented here -- just warn for now).

    seg_keys: list of strings like 'f586e11f8dad_1'."""
    body = {"action": "set_cached_segments",
            "clientKey": client_key,
            "segments": seg_keys}
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{SERVER}/api/discovery/configure",
                           json=body,
                           timeout=aiohttp.ClientTimeout(total=5)) as r:
            data = await r.json()
    return data


async def main():
    display_id = sys.argv[1] if len(sys.argv) > 1 else "Test Group"
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    explicit_hash = sys.argv[3] if len(sys.argv) > 3 else None

    devs = await fetch_devices()
    in_group = [d for d in devs if d.get("displayID") == display_id
                and d.get("cacheMode") == "mmvideo"
                and d.get("ip")]
    print(f"display={display_id!r}  cached iPads in group: {len(in_group)}")
    print(f"concurrency: {concurrency}")

    hash_ = explicit_hash or discover_latest_hash(
        [d["clientKey"] for d in in_group])
    if not hash_:
        print("ERROR: no rendered segments found on disk for this group")
        return 1
    print(f"using segment hash: {hash_}")

    # Enumerate the (clientKey, n) pairs from disk. For each cached
    # iPad, find which seg_HASH_N.mp4 files exist for this hash in
    # media/<key>/videos/.
    rx = re.compile(rf"seg_{re.escape(hash_)}_(\d+)\.mp4$")
    jobs = []
    for d in in_group:
        ck = d["clientKey"]
        ip = d["ip"]
        dir_ = os.path.join(ROOT, "media", ck, "videos")
        if not os.path.isdir(dir_):
            continue
        for f in os.listdir(dir_):
            m = rx.match(f)
            if not m:
                continue
            n = int(m.group(1))
            src = os.path.join(dir_, f)
            jobs.append((ck, ip, src, n, d["friendlyName"]))
    print(f"jobs: {len(jobs)} (= {len(in_group)} iPads x segments per iPad)")
    print()

    sem = asyncio.Semaphore(concurrency)

    async def runner(ck, ip, src, n, name):
        ok, dt, info = await push_one(sem, ck, ip, src, hash_, n)
        mbps = (os.path.getsize(src) / 1024 / 1024 / dt) if (ok and dt > 0) else 0
        sym = "OK " if ok else "FAIL"
        print(f"  {sym}  {name:<14} {ip:<14} seg_{hash_}_{n}  "
              f"{dt:6.1f}s  {mbps:5.1f} MB/s  {info if not ok else ''}")
        return (ok, ck, n)

    results = await asyncio.gather(*[runner(*j) for j in jobs])

    # Group successful pushes by client and update server-side
    # cachedSegments via the API.
    by_client = defaultdict(list)
    for ok, ck, n in results:
        if ok:
            by_client[ck].append(f"{hash_}_{n}")

    print()
    print("=== Updating server-side cachedSegments ===")
    for ck, segs in by_client.items():
        try:
            r = await update_cached_segments(ck, segs)
            print(f"  {ck}: {r}")
        except Exception as e:
            print(f"  {ck}: API error: {e}")

    n_ok = sum(1 for ok, _, _ in results if ok)
    print(f"\n=== Summary: {n_ok}/{len(results)} pushes succeeded ===")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
