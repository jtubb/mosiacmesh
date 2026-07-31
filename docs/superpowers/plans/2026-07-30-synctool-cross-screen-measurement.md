# synctool — cross-screen sync measurement CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI that reports the actual cross-screen visual desync of a display group (ground-truth `ct`-interpolated circular spread), attributes it to video vs clock, reports per-screen centering corrections, and states SETTLED/CONVERGING — with the tool's own math unit-tested.

**Architecture:** Pure functions (fit, circular geometry, settled verdict, log parse) in `tools/synctool_core.py` with no I/O, pytest-tested against synthetic data. A thin CLI shell `tools/synctool.py` parses `mm_server.err` + the device roster and calls the pure functions to print.

**Tech Stack:** Python 3 (stdlib only), pytest.

## Global Constraints

- **Pure functions live in `tools/synctool_core.py` (NO I/O)**; the CLI `tools/synctool.py` does all file/network reading + printing. All judgment is in the tested pure functions.
- **Run tests:** `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -v` (pytest config is in `tests/`, not root — per repo convention).
- **`ct` slope is ms-of-video per ms-of-walltime** — steady 1× playback ⇒ slope ≈ 1.0. The settled check gates on `|slope − 1| ≤ SLOPE_TOL`.
- **Circular math on a circle of circumference `loop_len`** (the playlist item duration, ms). Aligned positions ⇒ spread 0; wrap-straddling (29900, 100) ⇒ spread 200, NOT 29800.
- **Constants (initial, in synctool_core.py):** `WINDOW_MS=18000`, `FRESH_MS=20000`, `MIN_SAMPLES=5`, `OUTLIER_MS=120`, `SETTLE_TOL_MS=25`, `SLOPE_TOL=0.05`.
- Telemetry source line shape (already emitted): `2026-07-30 15:39:21,220 WARNING CLIENTLOG <clientKey> {'tag': 'drift', ..., 'ct': 12043, 'err': -18, 'off': -112725, 'rateGot': 1.02, ...}` — `t_recv_ms` = the `HH:MM:SS,mmm` timestamp; payload is a Python-dict literal (`ast.literal_eval`).

---

### Task 1: Circular geometry — spread, center, corrections

**Files:**
- Create: `tools/synctool_core.py`
- Test: `tests/unit/test_synctool.py`

**Interfaces:**
- Produces:
  - `signed_circ_dist(a, b, loop_len) -> float` — signed shortest distance to move `a` to `b` on the circle (range `(-loop_len/2, loop_len/2]`).
  - `circular_spread(positions, loop_len) -> float` — minimal-arc spread; 0 for aligned, handles wrap.
  - `circular_center(positions, loop_len) -> float` — midpoint of the minimal enclosing arc.
  - `corrections(positions, loop_len) -> (center, [signed dist from each pos to center])`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_synctool.py`:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tools import synctool_core as sc


def test_circular_spread_aligned_is_zero():
    assert sc.circular_spread([500, 500, 500], 30000) == 0


def test_circular_spread_simple():
    assert sc.circular_spread([0, 100, 200], 30000) == 200


def test_circular_spread_wrap_straddle():
    # 29900 and 100 are 200ms apart across the loop boundary, NOT 29800
    assert sc.circular_spread([29900, 100], 30000) == 200


def test_signed_circ_dist_wraps_short_way():
    assert sc.signed_circ_dist(29900, 100, 30000) == 200      # forward across wrap
    assert sc.signed_circ_dist(100, 29900, 30000) == -200     # backward across wrap
    assert sc.signed_circ_dist(100, 300, 30000) == 200


def test_corrections_center_and_offsets():
    # points at -50, 0, +50 around 10000 -> center ~10000, corrections pull to center
    center, corr = sc.corrections([9950, 10000, 10050], 30000)
    assert abs(center - 10000) < 1
    assert abs(corr[0] - 50) < 1 and abs(corr[1]) < 1 and abs(corr[2] + 50) < 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (synctool_core not created).

- [ ] **Step 3: Write minimal implementation**

Create `tools/synctool_core.py`:
```python
"""Pure functions for cross-screen sync measurement (no I/O). Unit-tested in
tests/unit/test_synctool.py. See docs/superpowers/specs/2026-07-30-synctool-*-design.md."""

WINDOW_MS = 18000
FRESH_MS = 20000
MIN_SAMPLES = 5
OUTLIER_MS = 120
SETTLE_TOL_MS = 25
SLOPE_TOL = 0.05


def signed_circ_dist(a, b, loop_len):
    """Signed shortest distance to move a -> b on a circle of circumference loop_len.
    Result in (-loop_len/2, loop_len/2]. Positive = forward (increasing)."""
    d = (b - a) % loop_len
    if d > loop_len / 2.0:
        d -= loop_len
    return d


def circular_spread(positions, loop_len):
    """Minimal-arc spread of positions on the circle: circumference minus the largest empty
    gap. Aligned -> 0; a wrap-straddling pair reads as its short-way distance."""
    if len(positions) < 2:
        return 0.0
    xs = sorted(p % loop_len for p in positions)
    n = len(xs)
    gaps = [xs[i + 1] - xs[i] for i in range(n - 1)]
    gaps.append(xs[0] + loop_len - xs[-1])   # wrap gap
    return loop_len - max(gaps)


def circular_center(positions, loop_len):
    """Midpoint of the minimal enclosing arc (the arc opposite the largest empty gap)."""
    xs = sorted(p % loop_len for p in positions)
    n = len(xs)
    gaps = [(xs[(i + 1) % n] - xs[i]) % loop_len for i in range(n)]
    k = max(range(n), key=lambda i: gaps[i])   # largest gap is AFTER xs[k]
    start = xs[(k + 1) % n]                     # arc begins at the point after the gap
    spread = loop_len - gaps[k]
    return (start + spread / 2.0) % loop_len


def corrections(positions, loop_len):
    """Returns (center, [signed distance from each position to center])."""
    c = circular_center(positions, loop_len)
    return c, [signed_circ_dist(p % loop_len, c, loop_len) for p in positions]
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/synctool_core.py tests/unit/test_synctool.py
git commit -m "feat(synctool): circular geometry (spread/center/corrections) + tests"
```

---

### Task 2: Per-screen ct fit + evaluation

**Files:**
- Modify: `tools/synctool_core.py`
- Test: `tests/unit/test_synctool.py` (append)

**Interfaces:**
- Consumes: constants from Task 1's module.
- Produces:
  - `fit_ct(samples) -> dict | None` where `samples = [(t_ms, ct), ...]`. Splits at loop-wrap (backward ct step), keeps the longest run, rejects outliers, least-squares fits. Returns `{'ct0','slope','t0','n','rejected'}` or `None` if `< MIN_SAMPLES` kept.
  - `eval_fit(fit, T) -> ct` = `ct0 + slope*(T - t0)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_synctool.py`:
```python
def test_fit_ct_recovers_clean_line():
    # ct advances 1ms per ms starting at 5000 at t=1000
    samples = [(1000 + 500 * i, 5000 + 500 * i) for i in range(10)]
    f = sc.fit_ct(samples)
    assert abs(f['slope'] - 1.0) < 1e-6
    assert abs(sc.eval_fit(f, 1000) - 5000) < 1e-6
    assert abs(sc.eval_fit(f, 6000) - 10000) < 1e-6


def test_fit_ct_drops_loop_wrap_keeps_longest_run():
    # 3 samples pre-wrap (ct ~29500->29900) then 7 post-wrap (ct 100->3100): keep the 7
    pre = [(1000 + 500 * i, 29500 + 200 * i) for i in range(3)]
    post = [(2500 + 500 * i, 100 + 500 * i) for i in range(7)]
    f = sc.fit_ct(pre + post)
    assert abs(f['slope'] - 1.0) < 0.02          # fit the post-wrap 1x run
    assert abs(sc.eval_fit(f, 2500) - 100) < 60


def test_fit_ct_rejects_outlier_spike():
    samples = [(1000 + 500 * i, 5000 + 500 * i) for i in range(10)]
    samples[5] = (samples[5][0], samples[5][1] + 400)   # a +400ms readback spike
    f = sc.fit_ct(samples)
    assert f['rejected'] >= 1
    assert abs(f['slope'] - 1.0) < 0.05


def test_fit_ct_insufficient_returns_none():
    assert sc.fit_ct([(1000, 5000), (1500, 5500)]) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q -k fit`
Expected: FAIL — `fit_ct` not defined.

- [ ] **Step 3: Write minimal implementation**

Append to `tools/synctool_core.py`:
```python
def _linfit(pts):
    """Least-squares (slope, intercept) for [(x, y), ...]. slope 0 if degenerate."""
    n = len(pts)
    sx = sum(x for x, _ in pts); sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts); sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, (sy / n if n else 0.0)
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def fit_ct(samples, outlier_ms=OUTLIER_MS, min_samples=MIN_SAMPLES):
    """Fit ct(t)=ct0+slope*(t-t0) over the longest wrap-free run of samples, rejecting
    outliers. samples=[(t_ms, ct)]. Returns a fit dict or None if too few kept."""
    if len(samples) < 2:
        return None
    s = sorted(samples)
    runs = [[s[0]]]
    for i in range(1, len(s)):
        if s[i][1] < runs[-1][-1][1] - 1000:   # ct dropped >1s => loop wrap
            runs.append([s[i]])
        else:
            runs[-1].append(s[i])
    run = max(runs, key=len)
    if len(run) < min_samples:
        return None
    t0 = run[0][0]
    pts = [(t - t0, ct) for t, ct in run]
    slope, ct0 = _linfit(pts)
    kept = [(x, y) for x, y in pts if abs(y - (ct0 + slope * x)) <= outlier_ms]
    rejected = len(pts) - len(kept)
    if len(kept) < min_samples:
        kept = pts; rejected = 0        # don't over-reject on a noisy screen
    slope, ct0 = _linfit(kept)
    return {'ct0': ct0, 'slope': slope, 't0': t0, 'n': len(kept), 'rejected': rejected}


def eval_fit(fit, T):
    return fit['ct0'] + fit['slope'] * (T - fit['t0'])
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/synctool_core.py tests/unit/test_synctool.py
git commit -m "feat(synctool): per-screen ct linear fit with wrap/outlier rejection + tests"
```

---

### Task 3: Settled verdict

**Files:**
- Modify: `tools/synctool_core.py`
- Test: `tests/unit/test_synctool.py` (append)

**Interfaces:**
- Produces: `settled_verdict(early_spread, late_spread, slopes, settle_tol=SETTLE_TOL_MS, slope_tol=SLOPE_TOL) -> (state, reason)` where `state` is `'SETTLED'` or `'CONVERGING'`.

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_settled_when_stable_and_1x():
    st, _ = sc.settled_verdict(40, 42, [1.0, 1.01, 0.99])
    assert st == 'SETTLED'


def test_converging_when_spread_moves():
    st, _ = sc.settled_verdict(120, 40, [1.0, 1.0])
    assert st == 'CONVERGING'


def test_converging_when_slope_off_1x():
    st, _ = sc.settled_verdict(40, 42, [1.0, 1.2])   # a screen still seeking/arming
    assert st == 'CONVERGING'
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q -k settled`
Expected: FAIL — `settled_verdict` not defined.

- [ ] **Step 3: Write minimal implementation**

Append:
```python
def settled_verdict(early_spread, late_spread, slopes, settle_tol=SETTLE_TOL_MS, slope_tol=SLOPE_TOL):
    """SETTLED only if the spread is stable across the two sub-windows AND every screen is
    playing at ~1x (not seeking/arming). Otherwise CONVERGING with the reason."""
    if abs(early_spread - late_spread) > settle_tol:
        return 'CONVERGING', 'spread moved %.0f->%.0f ms' % (early_spread, late_spread)
    off = [s for s in slopes if abs(s - 1.0) > slope_tol]
    if off:
        return 'CONVERGING', '%d screen(s) not at 1x (seeking/arming)' % len(off)
    return 'SETTLED', 'stable'
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/synctool_core.py tests/unit/test_synctool.py
git commit -m "feat(synctool): settled/converging verdict + tests"
```

---

### Task 4: Log-line parse

**Files:**
- Modify: `tools/synctool_core.py`
- Test: `tests/unit/test_synctool.py` (append)

**Interfaces:**
- Produces: `parse_clientlog_line(line) -> dict | None` returning `{'t_ms': int, 'key': str, 'tag': str, 'ct': int|None, 'err': int|None, 'off': int|None, 'rate': float|None}` for a drift/hb CLIENTLOG line, else `None`. `t_ms` = absolute ms-of-day from the `HH:MM:SS,mmm` timestamp.

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_parse_clientlog_line():
    line = ("2026-07-30 15:39:21,220 WARNING CLIENTLOG ei49puuugjznz5mi "
            "{'tag': 'drift', 'ct': 12043, 'err': -18, 'off': -112725, 'rateGot': 1.02}")
    r = sc.parse_clientlog_line(line)
    assert r['key'] == 'ei49puuugjznz5mi'
    assert r['tag'] == 'drift' and r['ct'] == 12043 and r['err'] == -18
    assert abs(r['rate'] - 1.02) < 1e-9
    # 15:39:21.220 -> ms of day
    assert r['t_ms'] == ((15 * 3600 + 39 * 60 + 21) * 1000 + 220)


def test_parse_ignores_non_drift_and_garbage():
    assert sc.parse_clientlog_line("2026-07-30 15:39:21,220 INFO something else") is None
    assert sc.parse_clientlog_line("garbage") is None
    # clock-sync (idle) has no ct -> still parses but ct is None (caller filters)
    cs = sc.parse_clientlog_line("2026-07-30 15:39:21,220 WARNING CLIENTLOG k {'tag': 'clock-sync'}")
    assert cs is not None and cs['ct'] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q -k parse`
Expected: FAIL — `parse_clientlog_line` not defined.

- [ ] **Step 3: Write minimal implementation**

Append (add `import re, ast` at the top of the module):
```python
_LINE_RE = re.compile(r'(\d\d):(\d\d):(\d\d),(\d\d\d) WARNING CLIENTLOG (\S+) (\{.*\})\s*$')


def parse_clientlog_line(line):
    """Parse one CLIENTLOG line -> dict (or None). t_ms = ms-of-day from the timestamp."""
    m = _LINE_RE.search(line)
    if not m:
        return None
    hh, mm, ss, ms, key, payload = m.groups()
    try:
        p = ast.literal_eval(payload)
    except Exception:
        return None
    if not isinstance(p, dict):
        return None
    t_ms = ((int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms))
    return {'t_ms': t_ms, 'key': key, 'tag': p.get('tag'),
            'ct': p.get('ct'), 'err': p.get('err'), 'off': p.get('off'),
            'rate': p.get('rateGot')}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q`
Expected: PASS (15 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/synctool_core.py tests/unit/test_synctool.py
git commit -m "feat(synctool): CLIENTLOG line parser + tests"
```

---

### Task 5: CLI shell + on-wall smoke (operator-run)

**Files:**
- Create: `tools/synctool.py`

**Interfaces:**
- Consumes: all `synctool_core` functions.
- Produces: a CLI: `python tools/synctool.py --group "OEB Sign 1" [--log mm_server.err] [--loop-ms 30016] [--json]`.

- [ ] **Step 1: Write the CLI shell**

Create `tools/synctool.py`:
```python
"""Cross-screen sync measurement CLI. Reads mm_server.err + the device roster, computes the
ground-truth cross-screen spread (see docs/superpowers/specs/2026-07-30-synctool-*). All math
is in tools/synctool_core.py (unit-tested); this shell only does I/O + printing."""
import argparse, json, re, statistics, urllib.request
import synctool_core as sc  # same dir (tools/)


def load_roster(group, base):
    """clientKey -> friendlyName for the group, via /api/discovery/devices."""
    try:
        d = json.load(urllib.request.urlopen(base + "/api/discovery/devices", timeout=5))
    except Exception:
        return {}
    devs = d.get("devices") if isinstance(d, dict) else d
    return {x.get("clientKey"): (x.get("friendlyName") or x.get("clientKey"))
            for x in (devs or []) if x.get("displayID") == group}


def collect(logpath, keys):
    """Per-key list of (t_ms, ct, err, off, rate) from drift/hb lines with a ct."""
    per = {}
    with open(logpath, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "CLIENTLOG" not in line:
                continue
            r = sc.parse_clientlog_line(line)
            if not r or r["key"] not in keys or r["tag"] not in ("drift", "hb") or r["ct"] is None:
                continue
            per.setdefault(r["key"], []).append(r)
    return per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--log", default="mm_server.err")
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--loop-ms", type=int, default=30016)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    roster = load_roster(a.group, a.base)
    per = collect(a.log, set(roster))
    if not per:
        print("no telemetry for %s" % a.group); return
    now = max(r["t_ms"] for rows in per.values() for r in rows)

    fresh, stale, insufficient = {}, [], []
    for key, rows in per.items():
        if now - rows[-1]["t_ms"] > sc.FRESH_MS:
            stale.append(roster[key]); continue
        win = [r for r in rows if now - r["t_ms"] <= sc.WINDOW_MS]
        fit = sc.fit_ct([(r["t_ms"], r["ct"]) for r in win])
        if not fit:
            insufficient.append(roster[key]); continue
        errs = [r["err"] for r in win if r["err"] is not None]
        fresh[key] = {"fit": fit, "err": statistics.median(errs) if errs else None,
                      "ct_at": sc.eval_fit(fit, now), "slope": fit["slope"]}

    names = {k: roster[k] for k in fresh}
    positions = [v["ct_at"] for v in fresh.values()]
    order = list(fresh.keys())
    if len(positions) < 2:
        print("not enough playing screens (%d)" % len(positions)); return

    spread = sc.circular_spread(positions, a.loop_ms)
    center, corr = sc.corrections(positions, a.loop_ms)
    # settled: split window into halves, spread of each; slopes near 1x
    half = now - sc.WINDOW_MS // 2
    def half_positions(newer):
        ps = []
        for key in order:
            rows = [r for r in per[key] if now - r["t_ms"] <= sc.WINDOW_MS
                    and ((r["t_ms"] >= half) == newer)]
            fit = sc.fit_ct([(r["t_ms"], r["ct"]) for r in rows])
            if fit: ps.append(sc.eval_fit(fit, now))
        return ps
    early = sc.circular_spread(half_positions(False), a.loop_ms)
    late = sc.circular_spread(half_positions(True), a.loop_ms)
    state, reason = sc.settled_verdict(early, late, [fresh[k]["slope"] for k in order])

    # attribution: video-offset = spread of err; clock ~ headline - video
    errvals = [fresh[k]["err"] for k in order if fresh[k]["err"] is not None]
    video_off = (max(errvals) - min(errvals)) if len(errvals) >= 2 else 0
    clock = max(0.0, spread - video_off)

    if a.json:
        print(json.dumps({"group": a.group, "state": state, "spread_ms": spread,
            "video_offset_ms": video_off, "clock_ms": clock, "center": center,
            "screens": {names[k]: {"ct_at": fresh[k]["ct_at"], "correction": corr[i],
                        "slope": fresh[k]["slope"]} for i, k in enumerate(order)},
            "stale": stale, "insufficient": insufficient})); return

    print("%s  cross-screen sync   [%s: %s]" % (a.group, state, reason))
    print("  HEADLINE visual desync (circular spread of ct@T): %.0f ms" % spread)
    print("  attribution: video-offset %.0f ms | clock-disagreement ~%.0f ms" % (video_off, clock))
    print("  playing %d | stale %d | insufficient %d" % (len(fresh), len(stale), len(insufficient)))
    print("  %-15s %10s %10s %8s" % ("screen", "corr(ms)", "err", "slope"))
    for i, k in sorted(enumerate(order), key=lambda ik: corr[ik[0]]):
        print("  %-15s %+10.0f %10s %8.3f" % (names[k], corr[i], fresh[k]["err"], fresh[k]["slope"]))
    if stale: print("  stale/absent: %s" % ", ".join(sorted(stale)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs (syntax + import)**

Run: `python tools/synctool.py --help`
Expected: prints usage (no import/syntax error). If `synctool_core` import fails, add `import sys, os; sys.path.insert(0, os.path.dirname(__file__))` before the `import synctool_core`.

- [ ] **Step 3: Full unit suite still green**

Run: `python -m pytest tests/unit/test_synctool.py -c tests/pytest.ini -q`
Expected: PASS (15 tests, unchanged — Task 5 added no pure functions).

- [ ] **Step 4: On-wall smoke (operator-run)**

With OEB Sign 1 playing a video playlist (arm PullTest3), run:
```bash
python tools/synctool.py --group "OEB Sign 1"
```
Confirm: it prints a headline spread + per-screen corrections + a SETTLED/CONVERGING verdict, the numbers are plausible (spread in the tens-to-low-hundreds of ms, not 30000), and the corrections sum toward center. Cross-check ONE screen's `ct@T` against a raw `grep "CLIENTLOG <key>" mm_server.err | tail` near `T` (fit ≈ raw). Record the reading.

- [ ] **Step 5: Commit**

```bash
git add tools/synctool.py
git commit -m "feat(synctool): CLI shell (parse + measure + center + settled) reading mm_server.err"
```

## Self-Review

**Spec coverage:** §1 headline B (ct@T circular spread) → T1 (circular) + T2 (fit/eval) + T5 (assemble); §1 attribution A (err/off) → T5; §1 center/corrections → T1 + T5. §2 algorithm (fit, wrap/outlier reject, common-T, circular spread) → T2 + T1. §3 source (parse mm_server.err) → T4 + T5 collect; fresh gating + settled verdict → T3 + T5. §4 output (table + --json) → T5. §5 tool unit-tested → T1–T4 pytest. ✓

**Placeholder scan:** no TBD/TODO; every step has literal code. On-wall smoke (T5 S4) is operator-run by nature (needs the live wall + log), not a placeholder.

**Type consistency:** `synctool_core` names (`signed_circ_dist`, `circular_spread`, `circular_center`, `corrections`, `fit_ct`, `eval_fit`, `settled_verdict`, `parse_clientlog_line`) match across tasks and the CLI. `fit_ct` returns the dict shape `{ct0,slope,t0,n,rejected}` used by `eval_fit` and the CLI. Constants defined once in T1's module, imported by all. `parse_clientlog_line` output keys (`t_ms,key,tag,ct,err,off,rate`) match the CLI's `collect`/`fresh` usage.
