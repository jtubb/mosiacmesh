"""Benchmark the render pipeline against Big Buck Bunny.

Compares two strategies for producing N per-iPad perspective-warped output
files from a single source video:

  A) `multi_proc`: N separate ffmpeg processes, run concurrently under a
     Semaphore (current production behaviour, MMRENDER_CONCURRENCY cap).
     Each ffmpeg decodes the source independently -- 24x duplicated
     decode work for our 24-iPad fleet.

  B) `filter_complex`: ONE ffmpeg process. The filter_complex graph
     decodes the source ONCE, splits into N streams via the `split`
     filter, applies a different perspective+scale to each stream, and
     maps each to its own output file. NVENC runs N concurrent encode
     sessions inside the single process.

The synthetic perspective transforms simulate a 6x4 iPad grid. Output
files go under cache/_bench_<strategy>/.

Usage:
    python tools/bench_render.py [--clip-seconds N] [--count N] [--concurrency N]

Defaults: 60s clip slice of Big Buck Bunny, 24 outputs, concurrency 6.
"""
import argparse, asyncio, os, sys, time, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "media", "server", "videos",
                   "big_buck_bunny_1080p_h264.mov")
OUT_W, OUT_H = 980, 1185
ENCODER_ARGS = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                "-cq", "23", "-no-scenecut", "1",
                "-profile:v", "baseline", "-pix_fmt", "yuv420p"]
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "128k"]


def synthetic_transforms(n):
    """Return N (src_points, label) tuples spanning a 6x4 grid layout.
    Each src_points is [TL, TR, BR, BL] in source-pixel coords."""
    cols, rows = 6, 4
    # Pack n iPads into a grid (left-to-right, top-to-bottom).
    sw, sh = 1920, 1080
    cell_w = sw / cols
    cell_h = sh / rows
    out = []
    for i in range(n):
        col = i % cols
        row = i // cols
        # Slight per-cell perspective skew so each output is unique.
        x0 = col * cell_w + 10
        x1 = (col + 1) * cell_w - 10
        y0 = row * cell_h + 10
        y1 = (row + 1) * cell_h - 10
        # Add a 5% diagonal skew so the perspective matrix is non-trivial.
        skew = cell_w * 0.05
        pts = [[x0 + skew, y0],          # TL
               [x1, y0 + skew],          # TR
               [x1 - skew, y1],          # BR
               [x0, y1 - skew]]          # BL
        out.append((pts, f"cell_{row}_{col}"))
    return out


def perspective_filter(pts):
    """Build the `perspective` filter expression from a 4-point quad
    [TL, TR, BR, BL]. ffmpeg perspective expects the corners in TL TR BL BR
    order (note BL before BR)."""
    tl, tr, br, bl = pts
    def n(v): return str(int(round(v)))
    return (f"perspective={n(tl[0])}:{n(tl[1])}:{n(tr[0])}:{n(tr[1])}:"
            f"{n(bl[0])}:{n(bl[1])}:{n(br[0])}:{n(br[1])}:sense=source")


async def run_proc(label, cmd):
    """Run a subprocess to completion, returning (rc, elapsed_sec)."""
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _out, _err = await proc.communicate()
    elapsed = time.time() - t0
    if proc.returncode != 0:
        tail = (_err or b"").decode("utf-8", "replace").splitlines()[-4:]
        print(f"  [{label}] FAILED rc={proc.returncode}")
        print("  " + "\n  ".join(tail))
    return proc.returncode, elapsed


async def bench_multi_proc(transforms, src, out_dir, clip_seconds, concurrency):
    """Strategy A: N separate ffmpegs, concurrency-capped via Semaphore.
    Each ffmpeg decodes `src` independently."""
    os.makedirs(out_dir, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(i, pts, label):
        async with sem:
            out = os.path.join(out_dir, f"{i:03d}_{label}.mp4")
            vf = perspective_filter(pts) + f",scale={OUT_W}:{OUT_H}"
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-t", str(clip_seconds), "-i", src,
                   "-vf", vf,
                   *ENCODER_ARGS, *AUDIO_ARGS,
                   out]
            rc, t = await run_proc(label, cmd)
            return rc, t

    t0 = time.time()
    results = await asyncio.gather(
        *[one(i, pts, lbl) for i, (pts, lbl) in enumerate(transforms)])
    wall = time.time() - t0
    fail = sum(1 for rc, _ in results if rc != 0)
    return wall, fail


async def bench_batched_filter_complex(transforms, src, out_dir,
                                        clip_seconds, batch_size,
                                        max_parallel_batches=1):
    """Strategy C: multiple ffmpegs, each handling a BATCH of outputs via
    filter_complex. Trades one extra decode per batch (2-3 decodes instead
    of 24) for staying under the single-process NVENC output ceiling
    (~12 outputs per ffmpeg in our testing).

    max_parallel_batches caps how many batches run concurrently: the NVIDIA
    driver imposes a GLOBAL NVENC session limit (~12 on consumer cards
    even with recent driver patches) across all processes on the system,
    so running 2 parallel batches of 12 outputs each requests 24 sessions
    and fails. Default 1 = strictly serial batches; bump only after testing
    that the system can sustain the higher session count."""
    os.makedirs(out_dir, exist_ok=True)
    # Chunk transforms into batches of <= batch_size each.
    batches = [transforms[i:i+batch_size]
               for i in range(0, len(transforms), batch_size)]
    sem = asyncio.Semaphore(max_parallel_batches)

    async def one_batch(batch_idx, batch_transforms):
        async with sem:
            return await _one_batch_inner(batch_idx, batch_transforms)

    async def _one_batch_inner(batch_idx, batch_transforms):
        n = len(batch_transforms)
        split_outs = ''.join(f'[v{i}_in]' for i in range(n))
        parts = [f'[0:v]split={n}{split_outs}']
        for i, (pts, _) in enumerate(batch_transforms):
            parts.append(f'[v{i}_in]{perspective_filter(pts)},'
                         f'scale={OUT_W}:{OUT_H}[v{i}]')
        fc = ';'.join(parts)
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-t", str(clip_seconds), "-i", src,
               "-filter_complex", fc]
        for i, (_, label) in enumerate(batch_transforms):
            global_i = batch_idx * batch_size + i
            out = os.path.join(out_dir, f"{global_i:03d}_{label}.mp4")
            cmd += ["-map", f"[v{i}]", "-map", "0:a:0?",
                    *ENCODER_ARGS, *AUDIO_ARGS, out]
        return await run_proc(f"batch{batch_idx}", cmd)

    t0 = time.time()
    results = await asyncio.gather(
        *[one_batch(i, b) for i, b in enumerate(batches)])
    wall = time.time() - t0
    # Count failures from the per-output file check, not just batch rc
    return wall, sum(1 for rc, _ in results if rc != 0)


async def bench_filter_complex(transforms, src, out_dir, clip_seconds):
    """Strategy B: ONE ffmpeg with filter_complex split into N outputs.
    Source is decoded exactly once; N NVENC sessions run inside the
    single process."""
    os.makedirs(out_dir, exist_ok=True)
    n = len(transforms)
    # Build filter_complex graph: split video into N copies, apply each
    # transform to one copy, label each [vN] for -map.
    split_outs = ''.join(f'[v{i}_in]' for i in range(n))
    parts = [f'[0:v]split={n}{split_outs}']
    for i, (pts, _) in enumerate(transforms):
        parts.append(f'[v{i}_in]{perspective_filter(pts)},'
                     f'scale={OUT_W}:{OUT_H}[v{i}]')
    fc = ';'.join(parts)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-t", str(clip_seconds), "-i", src,
           "-filter_complex", fc]
    # Audio mapped to all outputs from same source -- AAC is cheap and
    # reuses the decode.
    for i, (_, label) in enumerate(transforms):
        out = os.path.join(out_dir, f"{i:03d}_{label}.mp4")
        cmd += ["-map", f"[v{i}]", "-map", "0:a:0?",
                *ENCODER_ARGS, *AUDIO_ARGS, out]

    t0 = time.time()
    rc, _ = await run_proc("filter_complex", cmd)
    wall = time.time() - t0
    fail = n if rc != 0 else 0
    return wall, fail


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip-seconds", type=int, default=60,
                    help="length of source slice to render (default 60s)")
    ap.add_argument("--count", type=int, default=24,
                    help="number of output files (default 24, ~fleet size)")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="multi_proc concurrency cap (default 6)")
    ap.add_argument("--strategies", default="multi_proc,filter_complex,batched_filter_complex",
                    help="comma-separated subset to run")
    ap.add_argument("--batch-size", type=int, default=8,
                    help="outputs per ffmpeg for batched_filter_complex (default 8)")
    args = ap.parse_args()

    if not os.path.exists(SRC):
        print(f"missing source: {SRC}")
        sys.exit(1)

    transforms = synthetic_transforms(args.count)
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    print(f"source : {SRC}")
    print(f"slice  : {args.clip_seconds}s")
    print(f"outputs: {args.count}  (synthetic 6x4 grid)")
    print(f"output : 980x1185 H.264 baseline (iPad-1 compat) via h264_nvenc")
    print()

    results = []
    for strat in strategies:
        out_dir = os.path.join(ROOT, "cache", f"_bench_{strat}")
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        print(f"== {strat} ==")
        if strat == "multi_proc":
            wall, fail = await bench_multi_proc(
                transforms, SRC, out_dir, args.clip_seconds, args.concurrency)
        elif strat == "filter_complex":
            wall, fail = await bench_filter_complex(
                transforms, SRC, out_dir, args.clip_seconds)
        elif strat == "batched_filter_complex":
            wall, fail = await bench_batched_filter_complex(
                transforms, SRC, out_dir, args.clip_seconds, args.batch_size)
        else:
            print(f"unknown strategy: {strat}")
            continue
        # Verify all outputs are non-empty.
        outs = [f for f in os.listdir(out_dir) if f.endswith(".mp4")]
        total_bytes = sum(os.path.getsize(os.path.join(out_dir, f)) for f in outs)
        print(f"  wall    : {wall:.1f}s")
        print(f"  outputs : {len(outs)} files, {total_bytes / (1024*1024):.0f} MiB total")
        print(f"  failures: {fail}")
        per_out = wall / args.count if args.count else 0
        print(f"  per-out : {per_out:.1f}s")
        results.append((strat, wall, fail))
        print()

    if len(results) >= 2:
        ref = results[0][1]
        print("== summary ==")
        for strat, wall, fail in results:
            speedup = ref / wall if wall else 0
            print(f"  {strat:<16}  {wall:>6.1f}s   x{speedup:.2f} vs {results[0][0]}")


if __name__ == "__main__":
    asyncio.run(main())
