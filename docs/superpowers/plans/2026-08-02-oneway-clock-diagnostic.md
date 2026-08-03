# One-Way Downlink Clock Diagnostic (v4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the v4a per-screen "one-way downlink" clock diagnostic — a pure analysis function that extracts the downlink-floor offset estimator from the existing probe stream, its unit tests, and a `clocktest-oneway.html` page — to de-risk whether the downlink floor is sharp enough on real iPad-1 WiFi to justify building the v4b cross-screen rung.

**Architecture:** One more rung on the existing clock-diagnostic ladder. A new pure ES5 function `mmOneWayEstimates` in `js/clockdiag/analysis.js` computes three offset estimators (round-trip, downlink, uplink) plus a downlink-floor stability metric from `{t1,t2,t3,t4}` samples. A near-clone of `clocktest-gotime.html` drives `/clockprobe` probes, renders the estimators, and fire-and-forgets a summary to `/clockresult`. **Zero new server surface** — both routes already exist. Full spec: `docs/superpowers/specs/2026-08-02-oneway-clock-diagnostic-design.md`.

**Tech Stack:** ES5 JavaScript (iPad-1 Safari 5.1 compatible), Node 20 `--test` for unit tests, existing aiohttp `/clockprobe` + `/clockresult` routes (unchanged).

## Global Constraints

- **ES5 ONLY** in `js/clockdiag/analysis.js` and `clocktest-oneway.html` — no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. These run in iPad-1 Safari 5.1. (`analysis.js` also loads as a node side-effect import for tests.)
- `analysis.js` stays **pure**: no DOM, no network, no timers, no `Date`, no `Math.random`. Reuse the existing module helpers (`percentile`, `pstdev`, `mean`, `rttOf`, `offsetOf`).
- Sample shape is `{ t1, t2, t3, t4 }` (client-send, server-recv, server-send, client-recv), epoch ms. `rttOf(s) = (s.t4-s.t1)-(s.t3-s.t2)`; `offsetOf(s) = ((s.t2-s.t1)+(s.t3-s.t4))/2`.
- **No new server routes.** Reuse `/clockprobe` (echoes `t2`/`t3`) and `/clockresult` (logs `CLOCKRESULT <ip> <json>`).
- Run JS unit tests via `python pytest_runner.py --js` (wraps `node --test tests/unit/js/*.js`).

---

### Task 1: `mmOneWayEstimates` pure function + unit tests

**Files:**
- Modify: `js/clockdiag/analysis.js` (add the function before the `root.mmClockStats = ...` export block; add its export)
- Test: `tests/unit/js/clockdiag-analysis.test.js` (append test cases to the existing suite)

**Interfaces:**
- Consumes: existing module-locals `percentile(arr, p)`, `pstdev(arr)`, `rttOf(s)`, `offsetOf(s)` (already defined in `analysis.js`).
- Produces: `mmOneWayEstimates(samples, opts)` on the module root. Returns
  `{ n, roundTrip, downlink, uplink, minRtt, dlStabilityMs, dlSharpnessMs }`
  (or `{ n: 0 }` for empty input). `roundTrip` = min-RTT sample's `offsetOf`;
  `downlink` = `percentile(down, pct)`; `uplink` = `percentile(up, 1-pct)`;
  `down = t3-t4`, `up = t2-t1`. Default `opts = { pct: 0.95, subWindows: 5 }`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/clockdiag-analysis.test.js` (the `mk` builder and `g`/`close` helpers already exist at the top of the file):

```js
test('mmOneWayEstimates: symmetric path -> down/up/roundTrip exact, gap = minRtt/2', () => {
  const s = [];
  for (let i = 0; i < 30; i++) { s.push(mk(i * 1000, 100, 20, 20)); }
  const e = g.mmOneWayEstimates(s);
  close(e.downlink, 80);            // t3-t4 = offset - Dd = 100 - 20
  close(e.uplink, 120);            // t2-t1 = offset + Du = 100 + 20
  close(e.roundTrip, 100);         // min-RTT best offset
  close(e.minRtt, 40);
  close(e.roundTrip - e.downlink, e.minRtt / 2);   // the identity: gap is half min-RTT
});

test('mmOneWayEstimates: asymmetry -> roundTrip false, downlink carries only Dd_floor', () => {
  // Du=50, Dd=10, OFF=0: roundTrip = (Du-Dd)/2 = 20 (false); downlink = offset - Dd = -10.
  const s = [];
  for (let i = 0; i < 30; i++) { s.push(mk(i * 1000, 0, 50, 10)); }
  const e = g.mmOneWayEstimates(s);
  close(e.roundTrip, 20);          // the false offset round-trip cannot escape
  close(e.downlink, -10);          // offset - Dd_floor, asymmetry-free
  close(e.roundTrip - e.downlink, e.minRtt / 2);   // 20 - (-10) = 30 = 60/2
});

test('mmOneWayEstimates: additive queueing -> MAX of down recovers the floor', () => {
  // offset=100; Dd walks 10 (floor) up to 100 (queued). down = 100 - Dd.
  // Queueing only LOWERS down, so the high percentile lands on the least-queued sample.
  const dds = [10, 10, 10, 10, 10, 10, 25, 40, 60, 80, 100, 100];
  const s = dds.map((dd, i) => mk(i * 1000, 100, 15, dd));
  const e = g.mmOneWayEstimates(s);
  close(e.downlink, 90, 1);        // offset - Dd_floor = 100 - 10
});

test('mmOneWayEstimates: p95 (not literal max) rejects a single glitched t4', () => {
  const s = [];
  for (let i = 0; i < 20; i++) { s.push(mk(i * 1000, 100, 15, 12)); } // clean: down = 88
  s.push(mk(20000, 100, 15, -500));                                   // glitch: t4 wild-early -> down = 600
  const e = g.mmOneWayEstimates(s);
  close(e.downlink, 88, 1);        // the glitch (single max) is above p95 -> excluded
});

test('mmOneWayEstimates: dlStabilityMs small for a stable floor, large for a wandering one', () => {
  const stable = [];
  for (let i = 0; i < 40; i++) { stable.push(mk(i * 1000, 100, 15, 12)); } // floor flat -> down=88
  const es = g.mmOneWayEstimates(stable);
  assert.ok(es.dlStabilityMs < 2, 'stable floor: ' + es.dlStabilityMs);

  const wander = [];
  for (let i = 0; i < 40; i++) {
    const dd = i < 20 ? 12 : 60;   // floor shifts halfway through -> down drops 88 -> 40
    wander.push(mk(i * 1000, 100, 15, dd));
  }
  const ew = g.mmOneWayEstimates(wander);
  assert.ok(ew.dlStabilityMs > 10, 'wandering floor: ' + ew.dlStabilityMs);
});

test('mmOneWayEstimates: empty input is safe', () => {
  assert.deepEqual(g.mmOneWayEstimates([]), { n: 0 });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python pytest_runner.py --js`
Expected: FAIL — `g.mmOneWayEstimates is not a function` (the new tests error; the existing 12 still pass).

- [ ] **Step 3: Implement `mmOneWayEstimates`**

In `js/clockdiag/analysis.js`, add this function just before the `root.mmClockStats = mmClockStats;` export block:

```js
  // v4a (one-way rung): extract the DOWNLINK-floor offset estimator from the same
  // round-trip stream. Queueing/MAC/PSM delay is strictly additive -> it only makes
  // t4 later, i.e. down=(t3-t4) smaller. So the MAX (high percentile) of `down` lands
  // on the least-delayed sample = offset - Dd_floor, asymmetry-free. Min-RTT can't do
  // this: it minimizes Du+Dd (the sum), leaving (Du-Dd)/2. See the v4 design doc.
  // opts: { pct=0.95, subWindows=5 }
  function mmOneWayEstimates(samples, opts) {
    opts = opts || {};
    var pct = opts.pct != null ? opts.pct : 0.95;
    var subWindows = opts.subWindows != null ? opts.subWindows : 5;
    var n = samples.length, i;
    if (n === 0) { return { n: 0 }; }

    var up = [], down = [], rtts = [];
    for (i = 0; i < n; i++) {
      up.push(samples[i].t2 - samples[i].t1);      // offset + Du
      down.push(samples[i].t3 - samples[i].t4);    // offset - Dd
      rtts.push(rttOf(samples[i]));
    }

    // round-trip best offset = the min-RTT sample's offset (least queueing noise)
    var minRtt = Infinity, minIdx = 0;
    for (i = 0; i < n; i++) { if (rtts[i] < minRtt) { minRtt = rtts[i]; minIdx = i; } }

    var downlink = percentile(down, pct);          // robust MAX  -> offset - Dd_floor
    var uplink   = percentile(up, 1 - pct);        // robust MIN  -> offset + Du_floor
    var dlSharpnessMs = percentile(down, pct) - percentile(down, 0.5);

    // stability: split the (time-ordered) down stream into subWindows contiguous chunks,
    // recompute the downlink estimate in each, and take the std across chunks. Small std
    // = a sharp, repeatable floor (the go/no-go signal for building v4b).
    var chunkEsts = [], w, lo, hi, chunk, j;
    var nw = subWindows < n ? subWindows : n;
    for (w = 0; w < nw; w++) {
      lo = Math.floor(w * n / nw);
      hi = Math.floor((w + 1) * n / nw);
      chunk = [];
      for (j = lo; j < hi; j++) { chunk.push(down[j]); }
      if (chunk.length) { chunkEsts.push(percentile(chunk, pct)); }
    }
    var dlStabilityMs = pstdev(chunkEsts);

    return {
      n: n, roundTrip: offsetOf(samples[minIdx]), downlink: downlink, uplink: uplink,
      minRtt: minRtt, dlStabilityMs: dlStabilityMs, dlSharpnessMs: dlSharpnessMs
    };
  }
```

Then add to the export block (next to the other `root.*` assignments):

```js
  root.mmOneWayEstimates = mmOneWayEstimates;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python pytest_runner.py --js`
Expected: PASS — all existing tests plus the 6 new `mmOneWayEstimates` cases.

- [ ] **Step 5: Commit**

```bash
git add js/clockdiag/analysis.js tests/unit/js/clockdiag-analysis.test.js
git commit -m "feat(clockdiag): mmOneWayEstimates downlink-floor offset estimator + tests"
```

---

### Task 2: `clocktest-oneway.html` page

**Files:**
- Create: `clocktest-oneway.html` (repo root)

**Interfaces:**
- Consumes: `window.mmOneWayEstimates` and `window.mmClockStats` (from `analysis.js`), `window.mmXhrProbe` (from `probe-xhr.js`), the `/clockprobe` and `/clockresult` routes.
- Produces: nothing consumed by other tasks (leaf page). Posts `{ mode:'oneway', n, roundTrip, downlink, uplink, minRtt, dlStabilityMs, dlSharpnessMs, jitter, failed }` to `/clockresult`.

- [ ] **Step 1: Create the page**

Create `clocktest-oneway.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>One-way downlink rung (v4a)</title>
  <!-- ES5 ONLY (iPad-1 Safari 5.1). Ladder v4a: extracts the one-way DOWNLINK-floor
       offset estimator from the same /clockprobe round-trips and reports how sharp/stable
       that floor is (dlStabilityMs) -> the go/no-go for the v4b cross-screen rung. -->
  <script src="/js/clockdiag/analysis.js"></script>
  <script src="/js/clockdiag/probe-xhr.js"></script>
</head>
<body style="margin:0;background:#f6f6f6">
  <div id="out" style="font:16px -apple-system,Helvetica,Arial,sans-serif;padding:20px;text-align:center;color:#666">
    starting one-way rung…
  </div>
  <script>
  (function () {
    var COLOR = { PASS: '#1a7f37', WARN: '#9a6700', FAIL: '#cf222e' };
    function qp(name, dflt) {
      var m = new RegExp('[?&]' + name + '=([0-9]+)').exec(location.search || '');
      return m ? parseInt(m[1], 10) : dflt;
    }
    var TOTAL = qp('probes', 400), INTERVAL = qp('interval', 200);
    var out = document.getElementById('out');
    var samples = [], sent = 0, failed = 0;

    function r1(x) { return (typeof x === 'number') ? (Math.round(x * 10) / 10) : x; }
    // dlStabilityMs band: sharp floor PASS < 15ms, WARN < 40ms, else FAIL (provisional;
    // recalibrate from the first fleet run, same discipline as the v1 verdicts).
    function stabilityBand(v) { return v < 15 ? 'PASS' : (v < 40 ? 'WARN' : 'FAIL'); }

    function draw(done) {
      var e = window.mmOneWayEstimates(samples);
      var st = samples.length >= 3 ? window.mmClockStats(samples) : null;
      var html = '<div style="font:16px/1.5 -apple-system,Helvetica,Arial,sans-serif;max-width:560px;margin:0 auto;padding:14px">';
      html += '<h2>One-way downlink rung (v4a)</h2>';
      html += '<div style="color:#666;font-size:13px">probes ' + samples.length + '/' + TOTAL +
              (failed ? (' · failed ' + failed) : '') + (done ? ' · DONE' : ' · running…') + '</div>';
      if (e && e.n >= 3) {
        var band = stabilityBand(e.dlStabilityMs);
        html += '<div style="margin:12px 0;padding:10px;border-radius:6px;font-weight:bold;font-size:18px;text-align:center;color:#fff;background:' +
                COLOR[band] + '">downlink floor stability: ' + r1(e.dlStabilityMs) + ' ms — ' + band + '</div>';
        html += '<table style="width:100%;border-collapse:collapse" border="0" cellpadding="6">';
        html += '<tr><td>round-trip offset</td><td align="right">' + r1(e.roundTrip) + ' ms</td></tr>';
        html += '<tr><td><b>downlink offset (p95)</b></td><td align="right"><b>' + r1(e.downlink) + ' ms</b></td></tr>';
        html += '<tr><td>uplink offset (p05)</td><td align="right">' + r1(e.uplink) + ' ms</td></tr>';
        html += '<tr><td>min RTT</td><td align="right">' + r1(e.minRtt) + ' ms</td></tr>';
        html += '<tr><td>downlink floor sharpness (p95−p50)</td><td align="right">' + r1(e.dlSharpnessMs) + ' ms</td></tr>';
        if (st) {
          html += '<tr><td>one-way jitter up / down</td><td align="right">' +
                  r1(st.jitter.up) + ' / ' + r1(st.jitter.down) + ' ms</td></tr>';
        }
        html += '</table>';
        html += '<div style="font-size:11px;color:#888;margin-top:6px">Sharp, low, stable downlink floor across the fleet => the one-way lock is viable => build v4b.</div>';
      }
      html += '</div>';
      out.innerHTML = html;
    }

    function report() {
      try {
        var e = window.mmOneWayEstimates(samples);
        var st = window.mmClockStats(samples);
        var d = { mode: 'oneway', n: e.n, roundTrip: e.roundTrip, downlink: e.downlink,
                  uplink: e.uplink, minRtt: e.minRtt, dlStabilityMs: e.dlStabilityMs,
                  dlSharpnessMs: e.dlSharpnessMs, jitter: st.jitter, failed: failed };
        var x = new XMLHttpRequest();
        x.open('GET', '/clockresult?d=' + encodeURIComponent(JSON.stringify(d)), true);
        x.send();
      } catch (er) {}
    }

    function step() {
      if (sent >= TOTAL) { draw(true); report(); return; }
      sent++;
      window.mmXhrProbe('/clockprobe', function (s) {
        if (s) { samples.push(s); } else { failed++; }
        if ((samples.length + failed) % 10 === 0) { draw(false); }
      });
      setTimeout(step, INTERVAL);
    }

    setTimeout(step, 300);
  })();
  </script>
</body>
</html>
```

- [ ] **Step 2: Verify the page serves and renders against a live server**

Start the server on a spare port and confirm the page is served with its script tags intact (the server serves repo-root HTML directly):

```bash
python server.py -p 3999 &
sleep 3
curl -s http://localhost:3999/clocktest-oneway.html | grep -c 'mmOneWayEstimates\|/clockprobe\|/js/clockdiag/analysis.js'
```

Expected: a non-zero count (the page references the analysis module, the probe route, and the estimator). Stop the server afterward (`kill %1`).

- [ ] **Step 3: Commit**

```bash
git add clocktest-oneway.html
git commit -m "feat(clockdiag): clocktest-oneway.html v4a downlink-floor diagnostic page"
```

---

### Task 3: iPad-1 fleet go/no-go run (manual — controller runs)

**This task is manual and gated on the fleet being up.** It is not code; it produces the go/no-go decision for v4b. The subagent-driven runner should mark it for the human controller rather than dispatching an implementer.

**Files:** none.

- [ ] **Step 1: Serve the page from the live server** (already served from repo root by the running `server.py`; no restart needed since no server code changed).

- [ ] **Step 2: Launch the page on every screen via `uiopen`** (Safari is fine — pure clock path, no webclip/mmvideo dependency). Use the sequential, paced SSH sweep (never burst-SSH the fleet): for each roster `client.ip`, `uiopen 'http://<server>:<port>/clocktest-oneway.html'`.

- [ ] **Step 3: Wait ~90 s** for the default 400-probe run to finish on all screens.

- [ ] **Step 4: Collect + tabulate** the `CLOCKRESULT` lines (`mode:'oneway'`) from the server log; extract each screen's `dlStabilityMs` and `downlink`. Read the **fleet distribution of `dlStabilityMs`**.

- [ ] **Step 5: Decision.** Tight-and-low (`dlStabilityMs` PASS) across the fleet → the downlink floor is real hardware-wide → proceed to design v4b. Wandering/FAIL on many screens → the hybrid is not viable on this WiFi; record the finding and stop. Either way, update `memory/wall-cross-screen-sync-state.md` with the result.

---

## Self-Review

**Spec coverage:** design doc §"v4a architecture" (new function, tests, page, fleet run) → Tasks 1/2/3. §"v4b sketch" is explicitly out of scope (design-only). §"Non-goals" (no server routes, no rolling tracker, no skew detrend) respected — Task 1 reuses `/clockprobe`, computes a batch estimate, no detrending. ✓

**Placeholder scan:** all steps contain runnable code/commands. `dlStabilityMs` thresholds (15/40) are explicitly provisional-and-recalibrated (a stated decision, matching the existing verdict discipline), not a TODO. ✓

**Type consistency:** `mmOneWayEstimates` return keys (`roundTrip`, `downlink`, `uplink`, `minRtt`, `dlStabilityMs`, `dlSharpnessMs`, `n`) are identical across the Interfaces block, the tests (Task 1), the implementation (Task 1), and the page's `draw`/`report` (Task 2). `opts` defaults (`pct=0.95`, `subWindows=5`) match between design, function body, and the note that `uplink = percentile(up, 1-pct)` = p05. ✓
