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
 * `create{Linear,Radial}Gradient` return a tiny recording gradient
 * (later batches use it); their addColorStop calls are logged too.
 */
export function makeRecordingCtx() {
  const ops = [];
  const target = { __ops: ops };
  return new Proxy(target, {
    get(t, prop) {
      if (prop === '__ops') return ops;
      // `then` must NOT return a function: a universal function-dispenser
      // Proxy is otherwise a thenable, so `await fn(ctx)` (if an animation
      // accidentally returns ctx) would hang until the test timeout instead
      // of failing cleanly. Returning undefined keeps the object non-thenable.
      if (prop === 'then') return undefined;
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
