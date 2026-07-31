# mmvideo fine-rate experiment — CMTimebase (2026-07-31)

**Why:** on-wall diagnosis proved iOS 5.1 `AVPlayer.rate` ignores small fractional rates
(~7% effective; measured `d(actualAdvance)/d(commandedRate) ≈ +0.03..0.07`, should be ~+1.0).
Large rates (2×) work via a frame-drop fast-forward path. So the drift steer's ±3.5% fine
correction does almost nothing — the wall syncs via seeks, and the ~400 ms floor is
seek-landing residual. This experiment tests whether driving the item's **CMTimebase**
directly honors fractional rates where the high-level `AVPlayer.rate` doesn't.

**Build blocker (why this is a handoff, not a shipped change):** the mmvideo dylib builds
only on the Linux/theos host (`$HOME/theos`, `build.sh`). The Windows dev box has no iOS
cross-toolchain and WSL is broken there, so this must be built + canary-tested on the Linux
box + a single iPad.

**Feasibility caveats (why it may not work):**
1. `AVPlayerItem.timebase` is public only in iOS 6+. On 5.1 it must be reached via a private
   `timebase` selector that may not exist — the log line below (`tb=%p`) reveals it.
2. On iOS the AVPlayer continuously drives the item timebase from its own rate/masterClock, so
   a direct `CMTimebaseSetRate` may be **overridden on the next player update**. If the canary
   shows ct-advance still not tracking `r`, this approach is dead → use the PWM fallback.

## The change — `mm_engine_set_rate` in MMTransplantEngine.m

Replace the body (current lines ~165-171):

```objc
void mm_engine_set_rate(MMEngine *e, float rate) {
    if (!e || !e->player) return;
    float r = mm_clamp_rate(rate, 1, 1);   // iOS 5.1 has no canPlayFast/SlowForward; assume capable
    if (r > 0.0f) e->desiredRate = r;      // remember nonzero rate so play() resumes at it
    PL(e).rate = r;
    e->playing = (r != 0.0f);

    // EXPERIMENT (2026-07-31): AVPlayer.rate ignores small fractional rates on iOS 5.1.
    // Try the item's CMTimebase directly. Logs tb pointer + rate so the canary reveals
    // (a) whether the timebase is reachable and (b) whether ct-advance then tracks r.
    if (r > 0.0f) {
        AVPlayerItem *it = IT(e);
        CMTimebaseRef tb = NULL;
        if ([it respondsToSelector:@selector(timebase)]) {
            tb = (CMTimebaseRef)(void *)objc_msgSend((id)it, @selector(timebase));
        }
        FILE *_f = fopen("/tmp/mmvideo.log", "a");
        if (tb) {
            OSStatus st = CMTimebaseSetRate(tb, (Float64)r);
            if (_f) { fprintf(_f, "[rate-exp] CMTimebaseSetRate r=%.4f tb=%p st=%ld%c", r, (void*)tb, (long)st, 10); }
        } else if (_f) {
            fprintf(_f, "[rate-exp] no timebase selector; r=%.4f (AVPlayer.rate only)%c", r, 10);
        }
        if (_f) fclose(_f);
    }
}
```

(`objc_msgSend` is already imported at line 13. If the compiler complains about the
`objc_msgSend` prototype for a CF return, cast through a typed function pointer:
`CMTimebaseRef (*msg)(id,SEL) = (CMTimebaseRef(*)(id,SEL))objc_msgSend; tb = msg((id)it, @selector(timebase));`.)

## Build + canary test (Linux/theos box + one iPad)

1. `bash tweak/mmvideo/build.sh` → produces the dylib (watch for the CLEAN/NONE symbol checks).
2. Deploy to ONE canary iPad only (per [[fleet-tweak-deploy-ssh]]: Windows OpenSSH / the
   server's `_exec_ssh` path, `client.ip`, binary `cat > path < dylib`), then relaunch the
   webclip (`sbdidlaunch`) so the new dylib injects.
3. Play a video on that one screen. **Read `/tmp/mmvideo.log` on the device** (note: Web.app's
   /tmp is sandboxed — see [[onwall-signoff-needs-webclip-not-safari]]):
   - `no timebase selector` → CMTimebase route is dead on 5.1 → go to PWM fallback.
   - `CMTimebaseSetRate ... st=0` → it's reachable; now measure whether it holds.
4. **Measure honoring** with the same method that found the problem: on the server,
   `python tools/synctool.py` is overkill for one screen — instead grep that client's drift
   telemetry and compute actual ct-advance vs commanded rate (the analysis in
   `scratchpad/` from 2026-07-31, or re-derive: `d(ct)/d(wall)` over a clean run while the
   steer commands ~1.03). If actual advance now tracks the command (slope → ~1), CMTimebase
   works → re-enable the drift steer fleet-wide. If still ~1.0, the player overrode it → PWM.

## Fallback if CMTimebase is overridden: rate-PWM

Duty-cycle the **supported** 2× to average a fractional rate. To run effective `1+δ`:
each control tick of length `T`, spend `δ·T` at `AVPlayer.rate = 2.0` and the rest at `1.0`
(gains `δ·T` of extra playback time per tick). Keep `δ` small (≤ ~3%) so the 2× bursts are
brief (tens of ms) and ideally imperceptible. Risks: visible micro-stutter, and 2×
switch latency/glitch on iOS 5. Prototype on the canary and eyeball before fleet rollout.

## If neither works

The wall is not rate-correctable on this hardware. Pivot to **seek-residual reduction**
(the actual ~400 ms floor): tighter keyframe grid + boundary-seek residual centering so the
periodic re-align seeks land at a consistent cross-screen offset. See
[[wall-cross-screen-sync-state]] for the full root-cause chain.
