/**
 * Tiny shared module holding a single in-flight drag payload. Drag
 * handlers in this directory cooperate via this object — HTML5 drag
 * events fire dragstart on the source element and drop on the target,
 * with no payload accessible to handlers that don't share the
 * `dataTransfer` reference (cross-frame restrictions). We mirror the
 * payload here so multi-handler coordination is simple.
 */

let _current = null;

export function setDrag(payload) { _current = payload; }
export function getDrag() { return _current; }
export function clearDrag() { _current = null; }
