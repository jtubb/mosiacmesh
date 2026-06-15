# Fleet Per-Device Cache Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Surface per-device cache download status in Fleet → group detail → Devices card, reading the cache fields the API already returns. No server change.

**Architecture:** A pure `deviceCacheStatus(device)` helper in `fleet-status.js` normalizes the existing per-device fields (`cacheMode`, `cachedSegments[]`, `expectedSegments`, `cachePushProgress`, `propagationPercent`) into a small view-model; the Fleet Devices card renders a compact chip on the collapsed row and a fuller line in the expanded panel. Reuses the existing `.cache-propagation*` CSS vocabulary.

**Tech Stack:** Alpine.js 3 + ES modules (no build); node `--test`; Playwright e2e.

**Data contract (confirmed from live `/api/discovery/devices`):**
- `cacheMode`: `'none' | 'lighttpd-localhost' | 'service-worker'`
- `cachedSegments`: array of keys (length = cached count)
- `expectedSegments`: int (0 = nothing to cache for the applied playlist)
- `propagationPercent`: float 0–100
- `cachePushProgress`: `null` | `{token, n, bytesSent, totalBytes, startedMs, lastChangeMs, status, mbps}` (in-flight push only)

**Note (scope):** Cache fields are fresh at store hydrate; live-during-push streaming over SockJS is out of scope (a follow-up) — the value shown reflects the last device-list load. Devices with `cacheMode==='none'` (e.g. OEB Sign 1 today) show "streams (no local cache)".

---

## Task 1: `deviceCacheStatus` pure helper + test

**Files:**
- Modify: `js/timeline/fleet/fleet-status.js`
- Test: `tests/unit/js/fleet-status.test.js` (append)

- [ ] **Step 1: Append failing tests** to `tests/unit/js/fleet-status.test.js`:

```javascript
import { deviceCacheStatus } from '../../../js/timeline/fleet/fleet-status.js';

test('deviceCacheStatus: cacheMode none → not applicable', () => {
  const s = deviceCacheStatus({ cacheMode: 'none', expectedSegments: 0, cachedSegments: [] });
  assert.equal(s.applicable, false);
  assert.equal(s.label, 'streams (no local cache)');
});

test('deviceCacheStatus: fully cached', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 4,
    cachedSegments: ['a_0','a_1','a_2','a_3'], cachePushProgress: null });
  assert.equal(s.applicable, true);
  assert.equal(s.cached, 4); assert.equal(s.expected, 4);
  assert.equal(s.percent, 100); assert.equal(s.inFlight, false);
  assert.equal(s.label, 'cached 4/4');
});

test('deviceCacheStatus: in-flight shows mbps', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 4,
    cachedSegments: ['a_0','a_1'], cachePushProgress: { status: 'active', mbps: 3.25 } });
  assert.equal(s.percent, 50); assert.equal(s.inFlight, true); assert.equal(s.stalled, false);
  assert.equal(s.label, 'downloading 3.2 MB/s');
});

test('deviceCacheStatus: stalled flagged', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 4,
    cachedSegments: ['a_0'], cachePushProgress: { status: 'stalled', mbps: 0 } });
  assert.equal(s.stalled, true); assert.equal(s.label, 'stalled');
});

test('deviceCacheStatus: caching mode, expected 0 → applicable but idle', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 0, cachedSegments: [] });
  assert.equal(s.applicable, false);          // nothing assigned to cache
  assert.equal(s.label, 'nothing to cache');
});
```

- [ ] **Step 2: Run to fail** — `node --test tests/unit/js/fleet-status.test.js` → fails (`deviceCacheStatus` not exported).

- [ ] **Step 3: Implement** in `js/timeline/fleet/fleet-status.js`:

```javascript
/**
 * Per-device cache download status for the Fleet Devices card. Pure — reads the
 * fields /api/discovery/devices already returns. Returns:
 *   { applicable, percent, cached, expected, inFlight, stalled, mbps, label }
 * `applicable` is false when the device isn't locally caching (cacheMode 'none')
 * or has nothing to cache (expectedSegments 0).
 */
export function deviceCacheStatus(device) {
  const d = device || {};
  const mode = d.cacheMode || 'none';
  const cached = Array.isArray(d.cachedSegments) ? d.cachedSegments.length : (d.cachedSegments || 0);
  const expected = d.expectedSegments || 0;
  const prog = d.cachePushProgress || null;
  if (mode === 'none') {
    return { applicable: false, percent: 100, cached, expected, inFlight: false,
             stalled: false, mbps: null, label: 'streams (no local cache)' };
  }
  if (!expected) {
    return { applicable: false, percent: 100, cached, expected: 0, inFlight: false,
             stalled: false, mbps: null, label: 'nothing to cache' };
  }
  const percent = Math.max(0, Math.min(100, Math.round((cached / expected) * 100)));
  const stalled = !!(prog && prog.status === 'stalled');
  const inFlight = !!(prog && prog.status === 'active');
  const mbps = prog && typeof prog.mbps === 'number' ? prog.mbps : null;
  let label;
  if (stalled) label = 'stalled';
  else if (inFlight) label = `downloading ${(mbps || 0).toFixed(1)} MB/s`;
  else label = `cached ${cached}/${expected}`;
  return { applicable: true, percent, cached, expected, inFlight, stalled, mbps, label };
}
```

- [ ] **Step 4: Run to pass** — `node --test tests/unit/js/fleet-status.test.js` → all pass. Then `node --test tests/unit/js/*.js` → all pass.

- [ ] **Step 5: Commit** — `git add js/timeline/fleet/fleet-status.js tests/unit/js/fleet-status.test.js && git commit -m "feat(fleet): deviceCacheStatus helper for per-device cache download status"`

---

## Task 2: Wire into the Fleet Devices card + CSS

**Files:**
- Modify: `js/timeline/fleet/fleet-view.js` (expose helper to the template)
- Modify: `admin.html` (collapsed-row chip + expanded-panel detail + CSS)

- [ ] **Step 1: Expose the helper in `fleet-view.js`.** Add to the existing import from `./fleet-status.js` the name `deviceCacheStatus`, and add a method to the component object:

```javascript
  cacheStatus(device) { return deviceCacheStatus(device); },
```

- [ ] **Step 2: Collapsed-row chip in `admin.html`** — in the `.mm-fleet-dev-row` (after `.mm-fleet-dev-type`, before the chevron), add a compact chip shown only when applicable or stalled:

```html
                            <span class="mm-dev-cache" x-show="cacheStatus(d).applicable || cacheStatus(d).stalled"
                                  :class="{ stalled: cacheStatus(d).stalled, inflight: cacheStatus(d).inFlight }"
                                  :title="cacheStatus(d).label">
                              <span class="mm-dev-cache-bar"><span class="mm-dev-cache-fill" :style="'width:' + cacheStatus(d).percent + '%'"></span></span>
                              <span class="mm-dev-cache-pct" x-text="cacheStatus(d).percent + '%'"></span>
                            </span>
```

- [ ] **Step 3: Expanded-panel detail in `admin.html`** — inside `.mm-fleet-dev-panel` (after the Move-to field), add a read-only cache line:

```html
                              <div class="mm-fleet-dev-field mm-dev-cache-detail">
                                <span>Cache</span>
                                <span x-text="cacheStatus(d).label + (cacheStatus(d).applicable ? '' : '')"></span>
                              </div>
```

- [ ] **Step 4: CSS in `admin.html`** (near `.mm-fleet-device` rules, reuse theme vars):

```css
.mm-dev-cache { display:inline-flex; align-items:center; gap:4px; font-size:11px; color:var(--text-muted); margin-left:auto; }
.mm-dev-cache-bar { position:relative; width:46px; height:5px; background:var(--border); border-radius:3px; overflow:hidden; }
.mm-dev-cache-fill { position:absolute; inset:0 auto 0 0; height:100%; background:#2e7d32; transition:width 250ms ease-out; }
.mm-dev-cache.inflight .mm-dev-cache-fill { background:#81c784; }
.mm-dev-cache.stalled .mm-dev-cache-fill { background:#d32f2f; }
.mm-dev-cache.stalled .mm-dev-cache-pct { color:#d32f2f; font-weight:600; }
.mm-dev-cache-detail { display:flex; justify-content:space-between; }
```

(If `.mm-fleet-dev-row` isn't a flex row, `margin-left:auto` is harmless; verify it sits sensibly — adjust to match the row's layout.)

- [ ] **Step 5: Verify** — `node --test tests/unit/js/*.js` → all pass (module-load smoke confirms fleet-view.js still imports). Manually confirm the chip only shows for caching devices.

- [ ] **Step 6: Commit** — `git add js/timeline/fleet/fleet-view.js admin.html && git commit -m "feat(fleet): per-device cache status chip + expanded detail in Devices card"`

---

## Task 3: e2e assertion + docs

**Files:**
- Modify: `tests/e2e/test-fleet.spec.js` (or the existing fleet spec — append a light assertion) OR add to `test-render-model.spec.js`
- Modify: `js/timeline/README.md` (note the helper)

- [ ] **Step 1: e2e** — in the existing Fleet e2e spec, after opening a group's Devices card, assert the cache chip element exists in the DOM for caching devices OR (since the e2e env may have only cacheMode='none' devices) assert that `deviceCacheStatus` is wired by checking the expanded panel shows a "Cache" line. Keep it a light smoke; mirror the existing spec's structure. If a meaningful caching device can't be guaranteed, assert the expanded-panel Cache line renders with the `label` text.

- [ ] **Step 2: Run** — `node tests/e2e/run.js fleet` (if server + chromium available) → pass; else syntax-check + note.

- [ ] **Step 3: docs** — add a line to `js/timeline/README.md` fleet section noting `deviceCacheStatus` + the Devices-card cache chip.

- [ ] **Step 4: Commit** — `git add tests/e2e/ js/timeline/README.md && git commit -m "test+docs(fleet): per-device cache status e2e + README"`

---

## Self-Review
- Helper covers all 3 cache modes + the in-flight/stalled/idle/none cases (Task 1 tests).
- `applicable` gates the chip so non-caching devices (OEB Sign 1 today) don't show a misleading bar — they show the label only in the expanded panel.
- No server change; reads existing `/api/discovery/devices` fields.
- Type consistency: `deviceCacheStatus` returns the same shape everywhere; `cacheStatus(d)` is the template entry point.
