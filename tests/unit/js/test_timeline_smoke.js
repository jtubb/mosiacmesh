/**
 * Module-load smoke for js/timeline/*.js. Catches syntax errors and
 * missing imports without touching the DOM. Run with:
 *
 *   node --test tests/unit/js/test_timeline_smoke.js
 *
 * As new modules land in subsequent tasks, ADD them to MODULES below.
 * Tests should fail-closed: a typo or missing export in any module
 * breaks the test, not silently degrades the admin page.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '../../..');

const MODULES = [
  'js/timeline/api.js',
  'js/timeline/store.js',
  'js/timeline/util/time.js',
  'js/timeline/util/conflicts.js',
  'js/timeline/timeline/grid-axis.js',
  'js/timeline/timeline/track-header.js',
  'js/timeline/timeline/clip.js',
  'js/timeline/timeline/timeline.js',
  'js/timeline/toolbar.js',
  'js/timeline/timeline/sockjs-status.js',
  'js/timeline/timeline/now-line.js',
  'js/timeline/bin/media-bin.js',
  'js/timeline/bin/playlist-bin.js',
  'js/timeline/util/optimistic.js',
  'js/timeline/util/refetch-merge.js',
  'js/timeline/util/snap.js',
  'js/timeline/timeline/toast.js',
  'js/timeline/drag/dragstate.js',
  'js/timeline/drag/playlist-to-track.js',
  'js/timeline/drag/clip-move.js',
  'js/timeline/drag/clip-resize.js',
  'js/timeline/select.js',
  'js/timeline/drill-in.js',
  'js/timeline/drag/media-to-clip.js',
  'js/timeline/upload.js',
  'js/timeline/modals/modal-shell.js',
  'js/timeline/modals/recurrence-editor.js',
  'js/timeline/context-menu.js',
  'js/timeline/modals/playlist-editor.js',
  'js/timeline/modals/profile-editor.js',
  'js/timeline/modals/calibration.js',
];

for (const rel of MODULES) {
  test(`${rel} loads without error`, async () => {
    const url = pathToFileURL(path.join(ROOT, rel)).href;
    const mod = await import(url);
    assert.ok(mod, `expected ${rel} to export something`);
  });
}
