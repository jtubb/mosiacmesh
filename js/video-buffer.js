// js/video-buffer.js — client-side clip warming. ES5 only (iPad-1 / iOS 5.1). Loaded by index.html
// AND node-tested (attaches to window in the browser, globalThis in node).
(function (root) {
  'use strict';

  // Next playlist index to warm. Returns -1 when there is no distinct next item
  // (last item + no loop; single-item playlist; bad input). Mirrors the wrap
  // semantics of playlistIndex() in index.html.
  function nextPlaylistIndex(curIndex, itemCount, loop) {
    if (!(itemCount > 1) || !(curIndex >= 0) || curIndex >= itemCount) { return -1; }
    if (curIndex < itemCount - 1) { return curIndex + 1; }
    return loop ? 0 : -1;   // last item: wrap only if looping
  }

  root.nextPlaylistIndex = nextPlaylistIndex;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
