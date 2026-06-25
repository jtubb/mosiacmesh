# Keg-Roll Auto-Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Auto-size the rolling keg so the **smallest dimension of its opaque content** (ignoring the sprite's transparent padding) equals the mesh perpendicular dimension — removing the magic `1.3` fill constant.

**Architecture:** A pure core (opaque-bounding-box scan over RGBA data + a fit-factor formula) plus thin canvas glue that measures the sprite once on decode (downsampled offscreen canvas + `getImageData`), memoizes the factor on the `Image`, and falls back to `1.3` when measurement is unavailable. The drawer multiplies the mesh perpendicular dim by the auto factor; `?kgfill=N` stays as an optional fine-tune **multiplier** (default `1.0`).

**Tech Stack:** ES5 JavaScript (`js/transitions.js`), node `--test`.

## Global Constraints

- **ES5 ONLY** in `js/transitions.js`: no `let`/`const`/arrow/template-literal/`class`/`Promise`/`fetch`. `var`/`function` only.
- **Canvas ops Safari-5.1-safe:** offscreen `<canvas>` + `drawImage` (downscale) + `getImageData` are all supported on iOS 5.1. The measurement runs **once per sprite**, memoized — never per frame.
- Run JS tests: `node --test tests/unit/js/test_kegroll.js`; full suite `python pytest_runner.py --js`.
- Commit trailer EXACTLY (the "(1M context)" is REQUIRED, standing convention for this branch): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Do NOT change the cover/reveal geometry (`mmKegCoverRect`/`mmKegPos`/`_kegAxisOffset`), `mmTransitionState`, or the index.html wiring — this task only changes how `kegD` is sized inside `mmDrawKegRoll`, plus new helpers + their exports.

## Design notes (approved)

- "Smallest dimension" = the smaller side of the **opaque** bounding box (non-transparent pixels), NOT the raw PNG canvas — the padding is exactly what made a raw fit too small.
- Target: scale so `min(opaqueW, opaqueH)` (after the uniform stamp scale) equals the mesh perpendicular dim `P`. The shared-axis alignment fix keeps the keg centered on the cover edge, so min-opaque = P fully hides the straight edge with no extra margin.
- `mmStampSprite` scales the whole sprite uniformly by `k = globalSize / img.height`. With opaque fractions `fracW = opaqueW/iw`, `fracH = opaqueH/ih`, the opaque dims after stamping are `fracH·kegD` (height) and `fracW·iw·kegD/ih` (width). Setting their min to `P` gives:
  `kegD = P / min(fracH, fracW·iw/ih)`  →  fit factor `F = 1 / min(fracH, fracW·iw/ih)`.

---

### Task 1: Pure opaque-box + fit-factor helpers

**Files:**
- Modify: `js/transitions.js` (add two pure functions near the keg helpers ~line 488, after `mmKegAngle`; add two exports in the `root.*` block)
- Test: `tests/unit/js/test_kegroll.js` (append)

**Interfaces:**
- Consumes: nothing (pure).
- Produces (exported on `root`):
  - `mmOpaqueBox(data, w, h)` → `{fracW, fracH}` or `null`. `data` is RGBA bytes (length `4·w·h`, alpha at index `i*4+3`). Scans pixels with alpha `> 8`, finds the tight bounding box, returns its width/height as fractions of `w`/`h`. Returns `null` when no opaque pixel exists.
  - `mmKegFitFactor(box, iw, ih)` → number. Given an opaque box `{fracW, fracH}` and the sprite's pixel dims `iw`,`ih`, returns `1 / min(fracH, fracW*iw/ih)` (the `kegD = P*F` multiplier). Returns `1` for a null/degenerate box (treat as a full-bleed sprite → keg height = P).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/test_kegroll.js`:

```javascript
// --- auto-fill: opaque bbox + fit factor (pure) ---
// Build RGBA bytes for a w x h image with an opaque rect [x0,x1) x [y0,y1).
function rgbaWithBox(w, h, x0, y0, x1, y1) {
  const d = new Uint8ClampedArray(w * h * 4);   // all transparent (alpha 0)
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      d[(y * w + x) * 4 + 3] = 255;             // opaque alpha
    }
  }
  return d;
}

test('mmOpaqueBox: tight fractions of the opaque region', () => {
  // 10x10 image, opaque 4x6 block at (2,1)..(6,7) -> fracW=4/10, fracH=6/10
  const d = rgbaWithBox(10, 10, 2, 1, 6, 7);
  const b = g.mmOpaqueBox(d, 10, 10);
  assert.ok(Math.abs(b.fracW - 0.4) < 1e-9);
  assert.ok(Math.abs(b.fracH - 0.6) < 1e-9);
});

test('mmOpaqueBox: ignores sub-threshold alpha, null when fully transparent', () => {
  const d = new Uint8ClampedArray(4 * 4 * 4);
  d[3] = 8;                                       // exactly threshold -> NOT counted (> 8)
  assert.equal(g.mmOpaqueBox(d, 4, 4), null);
  d[3] = 9;                                       // just over -> a 1x1 box at (0,0)
  const b = g.mmOpaqueBox(d, 4, 4);
  assert.ok(Math.abs(b.fracW - 0.25) < 1e-9 && Math.abs(b.fracH - 0.25) < 1e-9);
});

test('mmKegFitFactor: smallest opaque dim scales to P', () => {
  // square sprite (iw=ih), opaque 0.5 x 0.8 -> min(0.8, 0.5) = 0.5 -> F = 2
  assert.ok(Math.abs(g.mmKegFitFactor({ fracW: 0.5, fracH: 0.8 }, 100, 100) - 2) < 1e-9);
  // full-bleed square -> min(1,1)=1 -> F=1
  assert.ok(Math.abs(g.mmKegFitFactor({ fracW: 1, fracH: 1 }, 100, 100) - 1) < 1e-9);
  // wide sprite iw=200 ih=100, opaque fracW=0.5 fracH=1 -> width term=0.5*200/100=1.0,
  //   min(1.0, 1.0)=1.0 -> F=1
  assert.ok(Math.abs(g.mmKegFitFactor({ fracW: 0.5, fracH: 1 }, 200, 100) - 1) < 1e-9);
  // null/degenerate -> 1
  assert.equal(g.mmKegFitFactor(null, 100, 100), 1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: FAIL — `mmOpaqueBox`/`mmKegFitFactor` are not functions.

- [ ] **Step 3: Add the helpers**

In `js/transitions.js`, after `mmKegAngle` (after its closing `}` ~line 488):

```javascript
  // Opaque bounding box of an RGBA buffer, as fractions of (w,h). data[i*4+3] is
  // alpha; pixels with alpha > 8 count as opaque. Returns {fracW, fracH} or null
  // (no opaque pixel). Pure — the canvas/getImageData glue lives in mmSpriteFit. */
  function mmOpaqueBox(data, w, h) {
    var minx = w, miny = h, maxx = -1, maxy = -1, x, y, a;
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        a = data[(y * w + x) * 4 + 3];
        if (a > 8) {
          if (x < minx) { minx = x; }
          if (x > maxx) { maxx = x; }
          if (y < miny) { miny = y; }
          if (y > maxy) { maxy = y; }
        }
      }
    }
    if (maxx < 0) { return null; }                 // fully transparent
    return { fracW: (maxx - minx + 1) / w, fracH: (maxy - miny + 1) / h };
  }

  // kegD/P multiplier so the SMALLEST opaque dimension lands on the mesh perp dim P.
  // mmStampSprite scales uniformly by globalSize/ih, so after stamping the opaque
  // height is fracH*kegD and width is fracW*iw*kegD/ih; setting their min to P gives
  // kegD = P / min(fracH, fracW*iw/ih). Null/degenerate box -> 1 (full-bleed). Pure.
  function mmKegFitFactor(box, iw, ih) {
    if (!box || !(ih > 0) || !(iw > 0)) { return 1; }
    var wTerm = box.fracW * iw / ih, hTerm = box.fracH;
    var m = wTerm < hTerm ? wTerm : hTerm;
    return (m > 1e-6) ? (1 / m) : 1;
  }
```

Add exports in the `root.*` block (next to the other keg exports):

```javascript
  root.mmOpaqueBox = mmOpaqueBox;
  root.mmKegFitFactor = mmKegFitFactor;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: PASS (all prior + 3 new).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_kegroll.js
git commit -m "feat(transitions): keg-roll opaque-bbox + fit-factor pure helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `mmSpriteFit` glue + wire auto-fit into the drawer

**Files:**
- Modify: `js/transitions.js` (add `mmSpriteFit` after `mmKegFitFactor`; change `kegD` computation in `mmDrawKegRoll`; export `mmSpriteFit`)
- Test: `tests/unit/js/test_kegroll.js` (append a glue smoke test using a fake document/canvas; reuse the `withFakeDocument` pattern from `test_scatter.js`)

**Interfaces:**
- Consumes: `mmOpaqueBox`, `mmKegFitFactor` (Task 1); `document.createElement('canvas')` + 2d `getImageData` when present.
- Produces:
  - `mmSpriteFit(img)` → number. Measures the sprite's opaque box ONCE on a downsampled (~64px longest edge) offscreen canvas, computes the fit factor via `mmKegFitFactor`, memoizes it on `img._mmKegFit`, and returns it. Returns `null` when measurement is impossible (no `document`/canvas API, image not yet decoded, or `getImageData` throws) so the caller can fall back. Re-reads the memoized value on later calls (no rescan).
  - `mmDrawKegRoll` `kegD` becomes `perpDim × autoFactor × fudge`, where `autoFactor = mmSpriteFit(img)` (fallback `1.3` when null) and `fudge = root._mmKegFill != null ? root._mmKegFill : 1.0`.

- [ ] **Step 1: Write the failing glue test**

Append to `tests/unit/js/test_kegroll.js`:

```javascript
// mmSpriteFit: measures opaque box on an offscreen canvas, memoizes, falls back.
// Fake a document whose canvas getImageData returns a known opaque-center block.
function withFakeDocFit(opaqueFrac, fn) {
  const prev = globalThis.document;
  globalThis.document = { createElement() {
    return {
      width: 0, height: 0,
      getContext() {
        return {
          drawImage() {},
          getImageData(x, y, w, h) {
            // opaque centered block of size opaqueFrac in both axes
            const d = new Uint8ClampedArray(w * h * 4);
            const bx = Math.round(w * (1 - opaqueFrac) / 2), ex = w - bx;
            const by = Math.round(h * (1 - opaqueFrac) / 2), ey = h - by;
            for (let yy = by; yy < ey; yy++) for (let xx = bx; xx < ex; xx++) d[(yy * w + xx) * 4 + 3] = 255;
            return { data: d, width: w, height: h };
          }
        };
      }
    };
  } };
  try { return fn(); } finally {
    if (prev === undefined) delete globalThis.document; else globalThis.document = prev;
  }
}

test('mmSpriteFit: square sprite, opaque half -> factor ~2, memoized', () => {
  withFakeDocFit(0.5, () => {
    const img = { width: 100, height: 100 };
    const f = g.mmSpriteFit(img);
    assert.ok(Math.abs(f - 2) < 0.1);             // min opaque ~0.5 -> ~2x
    assert.ok(Math.abs(img._mmKegFit - f) < 1e-9); // memoized on the image
  });
});

test('mmSpriteFit: null when no canvas API (node fallback path)', () => {
  const prev = globalThis.document; delete globalThis.document;
  try { assert.equal(g.mmSpriteFit({ width: 100, height: 100 }), null); }
  finally { if (prev !== undefined) globalThis.document = prev; }
});

test('mmSpriteFit: null for an undecoded image (width 0)', () => {
  withFakeDocFit(0.5, () => { assert.equal(g.mmSpriteFit({ width: 0, height: 0 }), null); });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: FAIL — `mmSpriteFit` is not a function.

- [ ] **Step 3: Add `mmSpriteFit` + export**

In `js/transitions.js`, after `mmKegFitFactor`:

```javascript
  // Measure a sprite's opaque-content fit factor ONCE (memoized on img._mmKegFit).
  // Downsamples to <=64px on an offscreen canvas, reads alpha via getImageData, and
  // computes mmKegFitFactor. Returns null when measurement isn't possible (no canvas
  // API, undecoded image, or a security/getImageData error) so callers fall back.
  function mmSpriteFit(img) {
    if (!img || !img.width || !img.height) { return null; }
    if (img._mmKegFit != null) { return img._mmKegFit; }
    if (typeof document === 'undefined' || !document.createElement) { return null; }
    var iw = img.width, ih = img.height;
    var s = 64 / (iw > ih ? iw : ih); if (s > 1) { s = 1; }
    var sw = Math.max(1, Math.round(iw * s)), sh = Math.max(1, Math.round(ih * s));
    try {
      var cv = document.createElement('canvas'); cv.width = sw; cv.height = sh;
      var cx = cv.getContext('2d');
      cx.drawImage(img, 0, 0, sw, sh);
      var id = cx.getImageData(0, 0, sw, sh);
      var box = mmOpaqueBox(id.data, sw, sh);
      var f = mmKegFitFactor(box, iw, ih);
      img._mmKegFit = f;
      return f;
    } catch (e) { return null; }                   // tainted canvas / no data -> fall back
  }
```

Add the export next to the others:

```javascript
  root.mmSpriteFit = mmSpriteFit;
```

- [ ] **Step 4: Wire the drawer**

In `mmDrawKegRoll`, replace the `kfill`/`kegD` lines:

```javascript
    // Auto-fit: size the keg so its SMALLEST opaque dimension lands on the mesh perp
    // dim (covers the straight cover edge for any sprite/padding). Falls back to 1.3
    // until the sprite is measured/decoded. ?kgfill=N is an optional fine-tune mult.
    var auto = (typeof mmSpriteFit === 'function') ? mmSpriteFit(img) : null;
    var base = (auto != null) ? auto : 1.3;
    var fudge = (root._mmKegFill != null) ? root._mmKegFill : 1.0;
    var kegD = (horiz ? reg.h : reg.w) * base * fudge;
```

(Remove the old `var kfill = ... ; var kegD = (horiz ? reg.h : reg.w) * kfill;` lines.)

- [ ] **Step 5: Run tests to verify they pass + no regression**

Run: `node --test tests/unit/js/test_kegroll.js`
Expected: PASS (all kegroll tests incl. the 3 new glue tests).
Run: `python pytest_runner.py --js`
Expected: PASS, 0 fail.

- [ ] **Step 6: Commit**

```bash
git add js/transitions.js tests/unit/js/test_kegroll.js
git commit -m "feat(transitions): keg-roll auto-fits keg size to opaque content

Smallest opaque dimension scales to the mesh perpendicular dim (measured once
per sprite, memoized); replaces the fixed 1.3. ?kgfill becomes a fine-tune
multiplier (default 1.0); falls back to 1.3 when measurement is unavailable.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: iPad-1 on-wall verification (manual)

- [ ] Reload the OEB Sign 1 fleet (mtime-cached static, no restart) and watch a kegroll transition: the keg's opaque body should span the full mesh height and fully cover the leading edge **without** the manual `?kgfill` — for both `wooden_keg` and the generated `keg`. Confirm no regression to alignment or smoothness. `?kgfill=1.1` should still nudge it larger if desired.

---

## Self-Review

**Spec coverage:** opaque-bbox measurement (Task 1 `mmOpaqueBox`); fit-factor formula `1/min(fracH, fracW·iw/ih)` (Task 1 `mmKegFitFactor`); once-per-sprite memoized measurement + fallback (Task 2 `mmSpriteFit`); drawer uses auto factor × `?kgfill` fudge, default fudge 1.0, fallback 1.3 (Task 2 Step 4); on-wall check (Task 3). ✓

**Placeholder scan:** none — all code complete; "rescmy" typo note: the prose says "no rescan" — ensure the memoized branch returns early (it does, `if (img._mmKegFit != null) return`). ✓

**Type consistency:** `mmOpaqueBox` returns `{fracW,fracH}|null`; `mmKegFitFactor(box, iw, ih)` consumes that shape and returns a number; `mmSpriteFit(img)` returns `number|null`; drawer treats `null`→`1.3`. Names + shapes consistent across tasks. ✓
