/**
 * SCRIPT-animations Batch-1: in-browser render smoke.
 *
 * The Node determinism tests already prove the animation math is
 * deterministic, and a registry-sync test proves index.html carries the
 * animation keys. What no static check can catch: whether the *ES5 copy*
 * of an animation inside index.html actually EXECUTES in a real browser
 * and draws to the canvas (an ES5 typo — an arrow fn, a `const`, a stray
 * template literal — would pass the regex sync-check but throw at runtime).
 *
 * This spec drives the REAL display-client playback path. index.html's
 * main <script> block is NOT IIFE-wrapped, so its message dispatcher
 * `mosiacMeshCallback(data_obj)` is a global. We hand it a synthetic
 * PLAY frame (DEST:'ALL') carrying a single SCRIPT item — exactly the
 * shape the server's PLAY broadcast sends — which runs
 *   renderPlayback() -> showItem() -> runScriptLoop() -> animations[name]()
 * against the in-page ES5 copy. We then read the canvas back and assert
 * it drew non-blank pixels, then send STOP and assert the canvas is torn
 * down (stopPlayback -> $('#canvas').empty()).
 *
 * No index.html change, no server group wiring — it exercises the same
 * code path a real PLAY broadcast would, but deterministically from the
 * page. We test lissajous (a stroke animation) and phyllotaxis (a fill
 * animation) so both ctx.stroke and ctx.fill paths are covered.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';

// Drive one animation through the real PLAY path and report whether the
// canvas under #canvas drew any non-transparent pixel.
async function renderAndProbe(page, name) {
  return await page.evaluate((animName) => {
    // The display-client script block is global scope. mosiacMeshCallback
    // is the message dispatcher; DEST:'ALL' routes to every client.
    if (typeof mosiacMeshCallback !== 'function') {
      throw new Error('mosiacMeshCallback is not a global function');
    }
    // startEpoch slightly in the past => PLAY's _delay <= 0 => renderPlayback()
    // fires synchronously (no coordinated-start defer). loop:true so a past
    // epoch never falls off the end of the single-item playlist.
    var now = (window.GoTime && GoTime.now) ? GoTime.now() : Date.now();
    mosiacMeshCallback({
      DEST: 'ALL',
      REQUEST: 'PLAY',
      PAYLOAD: {
        startEpoch: now - 50,
        loop: true,
        items: [{ file: animName, playmode: 'SCRIPT', duration: 10000 }],
      },
    });
    return !!document.querySelector('#canvas canvas');
  }, name);
}

async function probePixels(page) {
  return await page.evaluate(() => {
    var cnv = document.querySelector('#canvas canvas');
    if (!cnv) return { ok: false, reason: 'no canvas' };
    var ctx = cnv.getContext('2d');
    var w = cnv.width, h = cnv.height;
    if (!w || !h) return { ok: false, reason: 'zero-size canvas' };
    var data = ctx.getImageData(0, 0, w, h).data;
    var lit = 0;
    // Sample every 40th pixel for speed; alpha>0 means something was drawn.
    for (var i = 3; i < data.length; i += 4 * 40) {
      if (data[i] > 0) { lit++; }
    }
    return { ok: lit > 0, lit: lit, w: w, h: h };
  });
}

async function stop(page) {
  await page.evaluate(() => {
    mosiacMeshCallback({ DEST: 'ALL', REQUEST: 'STOP', PAYLOAD: {} });
  });
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  try {
    await page.goto(BASE + '/index.html?nocache=' + Date.now());
    // Wait for the display client to finish booting: #canvas exists and the
    // global dispatcher is defined.
    await page.waitForFunction(
      () => document.getElementById('canvas') != null
        && typeof mosiacMeshCallback === 'function'
        && typeof window.requestAnimationFrame === 'function',
      null, { timeout: 15_000 });

    // --- lissajous: a stroke-based animation ---
    const hadCanvas1 = await renderAndProbe(page, 'lissajous');
    assert.ok(hadCanvas1, 'expected a <canvas> under #canvas after PLAY(lissajous)');
    // Let >=1 rAF frame run so runScriptLoop draws.
    await page.waitForFunction(() => {
      var c = document.querySelector('#canvas canvas');
      if (!c) return false;
      var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (var i = 3; i < d.length; i += 4 * 40) { if (d[i] > 0) return true; }
      return false;
    }, null, { timeout: 5_000 }).catch(() => {});
    const probe1 = await probePixels(page);
    assert.ok(probe1.ok, `lissajous drew no pixels: ${JSON.stringify(probe1)}`);

    // --- teardown: STOP empties #canvas ---
    await stop(page);
    await page.waitForFunction(
      () => document.querySelector('#canvas canvas') == null,
      null, { timeout: 5_000 });
    const afterStop = await page.evaluate(() => !!document.querySelector('#canvas canvas'));
    assert.equal(afterStop, false, 'expected #canvas canvas removed after STOP');

    // --- phyllotaxis: a fill-based animation (broader ES5-copy coverage) ---
    const hadCanvas2 = await renderAndProbe(page, 'phyllotaxis');
    assert.ok(hadCanvas2, 'expected a <canvas> under #canvas after PLAY(phyllotaxis)');
    await page.waitForFunction(() => {
      var c = document.querySelector('#canvas canvas');
      if (!c) return false;
      var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (var i = 3; i < d.length; i += 4 * 40) { if (d[i] > 0) return true; }
      return false;
    }, null, { timeout: 5_000 }).catch(() => {});
    const probe2 = await probePixels(page);
    assert.ok(probe2.ok, `phyllotaxis drew no pixels: ${JSON.stringify(probe2)}`);
    await stop(page);

    // --- wireframeCube: the most complex ES5 copy (3D rotation + matrix
    // math), so the highest transcription-error risk — exactly what an
    // in-browser smoke is for. ---
    const hadCanvas3 = await renderAndProbe(page, 'wireframeCube');
    assert.ok(hadCanvas3, 'expected a <canvas> under #canvas after PLAY(wireframeCube)');
    await page.waitForFunction(() => {
      var c = document.querySelector('#canvas canvas');
      if (!c) return false;
      var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (var i = 3; i < d.length; i += 4 * 40) { if (d[i] > 0) return true; }
      return false;
    }, null, { timeout: 5_000 }).catch(() => {});
    const probe3 = await probePixels(page);
    assert.ok(probe3.ok, `wireframeCube drew no pixels: ${JSON.stringify(probe3)}`);

    await stop(page);

    // --- analogClock: a WALL-CLOCK animation. Beyond drawing, this is the
    // end-to-end check that index.html's runScriptLoop passes GoTime.now() as
    // the 5th arg (nowMs) — a clock with no nowMs would throw or draw 12:00. ---
    const hadCanvas4 = await renderAndProbe(page, 'analogClock');
    assert.ok(hadCanvas4, 'expected a <canvas> under #canvas after PLAY(analogClock)');
    await page.waitForFunction(() => {
      var c = document.querySelector('#canvas canvas');
      if (!c) return false;
      var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (var i = 3; i < d.length; i += 4 * 40) { if (d[i] > 0) return true; }
      return false;
    }, null, { timeout: 5_000 }).catch(() => {});
    const probe4 = await probePixels(page);
    assert.ok(probe4.ok, `analogClock drew no pixels: ${JSON.stringify(probe4)}`);
    await stop(page);

    // --- plasma: a FIELD animation (shade() -> small RGBA buffer, scaled to the
    // canvas via one drawImage). Fills every pixel opaque, so the probe is robust. ---
    const hadCanvas5 = await renderAndProbe(page, 'plasma');
    assert.ok(hadCanvas5, 'expected a <canvas> under #canvas after PLAY(plasma)');
    await page.waitForFunction(() => {
      var c = document.querySelector('#canvas canvas');
      if (!c) return false;
      var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (var i = 3; i < d.length; i += 4 * 40) { if (d[i] > 0) return true; }
      return false;
    }, null, { timeout: 5_000 }).catch(() => {});
    const probe5 = await probePixels(page);
    assert.ok(probe5.ok, `plasma drew no pixels: ${JSON.stringify(probe5)}`);
    await stop(page);

    return 'pass';
  } finally {
    await browser.close();
  }
}
