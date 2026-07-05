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

  // Video-buffer manager. Owns 1 (legacy) or 2 (warmable) <video> elements and
  // exposes one "active" element. deps injects element creation/mount + isVideo so
  // this is node-testable without a DOM.
  function makeVideoBuffer(deps) {
    var warmable = false, activeEl = null, bufferEl = null, bufferSrc = null;
    // Both elements stay display:block so the hidden buffer still BUFFERS/decodes (a
    // display:none video won't warm). Hidden = opacity 0, behind (zIndex 1).
    function show(el) { if (el && el.style) { el.style.display = 'block'; el.style.opacity = '1'; el.style.zIndex = '2'; } }
    function hide(el) { if (el && el.style) { el.style.display = 'block'; el.style.opacity = '0'; el.style.zIndex = '1'; } }
    return {
      setup: function (warm) {
        warmable = !!warm;
        activeEl = deps.mkVideo(); deps.mount(activeEl); show(activeEl);
        if (warmable) { bufferEl = deps.mkVideo(); deps.mount(bufferEl); hide(bufferEl); }
        else { bufferEl = null; }
        bufferSrc = null;
      },
      active: function () { return activeEl; },
      warmNext: function (item) {
        if (!warmable || !bufferEl || !item || !deps.isVideo(item.file)) { return; }
        if (bufferSrc === item.file) { return; }          // already warm
        bufferEl.src = item.file; bufferSrc = item.file;
        try { bufferEl.load(); } catch (e) {}
      },
      flipTo: function (file) {
        if (!warmable || !bufferEl || bufferSrc !== file) { return null; }  // cold-fallback
        show(bufferEl); hide(activeEl);
        try { activeEl.pause(); } catch (e) {}
        var t = activeEl; activeEl = bufferEl; bufferEl = t;   // old active becomes the free buffer
        bufferSrc = null;
        return activeEl;                                       // warm + loaded; caller seeks+plays
      }
    };
  }

  root.nextPlaylistIndex = nextPlaylistIndex;
  root.makeVideoBuffer = makeVideoBuffer;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
