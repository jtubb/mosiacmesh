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
 * `create{Linear,Radial}Gradient` return a tiny gradient whose color stops
 * are recorded as plain data on the gradient object (`__stops`), with a
 * MODULE-SHARED `addColorStop` reference. This matters for determinism: when
 * an animation assigns the gradient to `ctx.fillStyle`, the gradient object is
 * recorded in `__ops`. A per-gradient closure would differ by reference
 * between two runs and break `deepStrictEqual`; a shared function ref + data
 * stops makes two equal gradients compare equal. So an animation can use a
 * real gradient fill (not a flat-color workaround) and still be sync-tested.
 */
function gradientAddColorStop(offset, color) {
  this.__stops.push([offset, color]);
}

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
          return { __gradient: true, __stops: [], addColorStop: gradientAddColorStop };
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
