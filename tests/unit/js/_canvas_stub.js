/**
 * A Canvas2D-shaped recording stub for animation determinism tests.
 *
 * Every method call is pushed to an ordered `__ops` log as
 * {op, args}; every property assignment (fillStyle, strokeStyle,
 * lineWidth, globalAlpha, font, ...) as {set, value}. Two runs of a
 * pure-function-of-tMs animation against fresh stubs produce
 * deep-equal logs iff the animation is deterministic — which is the
 * cross-screen synchronization guarantee in testable form.
 *
 * `createLinearGradient` returns a tiny recording gradient (later
 * batches use it); its addColorStop calls are logged too.
 */
export function makeRecordingCtx() {
  const ops = [];
  const target = { __ops: ops };
  return new Proxy(target, {
    get(t, prop) {
      if (prop === '__ops') return ops;
      if (prop in t && typeof t[prop] !== 'function') return t[prop];
      return function (...args) {
        ops.push({ op: String(prop), args });
        if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
          return {
            addColorStop(offset, color) {
              ops.push({ op: 'addColorStop', args: [offset, color] });
            },
          };
        }
        return undefined;
      };
    },
    set(t, prop, value) {
      ops.push({ set: String(prop), value });
      t[prop] = value;
      return true;
    },
  });
}
