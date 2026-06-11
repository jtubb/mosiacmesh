/**
 * Unified content model. Merges media (/api/media) + the animations registry
 * (window.MM_ANIMATIONS) into one list of content items, and maps a picked
 * content item to a playlist item. Pure + DOM-free (testable).
 */
function basename(p) { return String(p || '').split('/').pop() || ''; }

export function buildContentItems({ media = {}, animations = [] } = {}) {
  const out = [];
  for (const url of media.images || []) {
    out.push({ kind: 'image', ref: url, name: basename(url) });
  }
  for (const url of media.videos || []) {
    out.push({ kind: 'video', ref: url, name: basename(url), duration: (media.videoDurations || {})[url] });
  }
  for (const a of animations || []) {
    out.push({ kind: 'animation', ref: a.key, name: a.key, label: a.label });
  }
  return out;
}

// The trigger fix: an animation pick becomes a SCRIPT item automatically; the
// operator never touches play-mode for an animation. Media picks default to loop.
const ANIMATION_DEFAULT_DURATION_S = 20;
export function contentItemToPlaylistItem(ci) {
  if (ci.kind === 'animation') {
    return { file: ci.ref, playmode: 'SCRIPT', duration: ANIMATION_DEFAULT_DURATION_S };
  }
  return { file: ci.ref, playmode: 'loop', duration: ci.duration == null ? undefined : ci.duration };
}
