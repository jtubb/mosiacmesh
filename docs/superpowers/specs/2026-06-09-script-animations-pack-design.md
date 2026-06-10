# SCRIPT Animations Pack — Design (playback engine slice 6.1)

**Date:** 2026-06-09
**Status:** Draft — pending review
**Builds on:** [SCRIPT Synced Animations (identical)](./2026-05-26-script-synced-animations-design.md) — the slice that wired up `PlayMode.SCRIPT`, the client-side `animations` registry, the `tMs`-driven render loop, and the lone `bouncingBalls` animation.

## Context

The SCRIPT slice (2026-05-26) shipped exactly one animation, `bouncingBalls`, deliberately scoped to "prove the infrastructure works." This spec adds **eleven more animations** to the registry, plus **one fleet-status dashboard** that runs through the same SCRIPT plumbing but deviates from the pure-function-of-time invariant in a specific, contained way.

The premise is unchanged: each animation is a pure function `fn(ctx, tMs, w, h)`, identical on every screen in the group, driven by `GoTime`-derived elapsed time. The wall reads as one synchronized instrument because the inputs are the same and the function is deterministic.

The infrastructure additions are tiny: each new animation is one entry in the client-side `animations` registry plus (optionally) a small operator-facing dropdown of known animation names in the playlist-editor modal.

**Why pack them together:** none of the animations need new server-side machinery, new protocol fields, or changes to the render loop. They are all leaf additions to a single registry. Shipping them as one spec lets us settle the operator-facing UX (animation picker, naming conventions) and the per-animation cost budget once, instead of negotiating each one independently.

## Goals

- Eleven additional `animations` registry entries, each a pure function of `tMs`, deterministic across screens.
- One fleet-status "animation" (`fleetStatus`) that intentionally departs from the pure-function rule, fed by SockJS broadcasts the client already receives.
- A small operator picker: the playlist-editor modal lists known animation names when `playmode === 'SCRIPT'`, so adding a SCRIPT item doesn't require the operator to remember the registry key.
- Every new animation runs within the iPad 1 (A4) frame budget — measured target: <8 ms per frame at 1024×768 so we have headroom for the rAF loop overhead.

## Non-goals (deferred)

- **Mosaic-spanning animations.** Per-screen viewports (the "one giant clock face spanning a wall" class) require `measuredPerimeter` plumbed into the SCRIPT context plus a global-canvas → per-screen coordinate transform. We capture the design surface in the [Mosaic-spanning roadmap](#mosaic-spanning-roadmap) section but no implementation is in scope.
- **Per-animation parameters** (count, colors, speed). Each animation ships with one canonical look. A future parameterization slice can layer config on top of the registry; this spec keeps the item model unchanged (`file = animationName`).
- **Operator-uploaded animation code.** All animations are built-in, vetted, and shipped with `index.html`. No `eval`, no remote script loading.
- **Audio reactivity.** No audio path exists yet.

## Item model

**Unchanged from the SCRIPT slice.** A SCRIPT playlist item is `{id, file, duration, playmode}` where `file` is the registry key (no leading slash). The PLAY payload still carries `playmode` per item. No new fields on `Playlist`, `MediaElement`, or the wire format.

## Server changes

**None.** The registry is entirely client-side. The server doesn't know — and shouldn't know — what animation names are valid. An unknown animation name produces a blank canvas on the client (see [Edge cases](#edge-cases)), exactly as the existing slice already specifies.

The one optional change that this spec leaves out by design: a `/api/animations` endpoint returning the registry keys for the operator picker. We could add it for honesty, but since the registry lives in `index.html` (and `index.html` is the same file every client loads), the admin page can read the same file's `<script>` tag at runtime — or hardcode a JS-side mirror of the names in `js/timeline/animations-catalog.js`. We pick the hardcoded mirror (see [Client changes](#client-changes)) because it avoids a round-trip and the catalog rarely changes.

## Client changes

### `index.html` (display client, ES5)

For each animation below, add one entry to the `animations` object inside the `<script>` block that already defines `bouncingBalls`. Functions must:

- Be `function` (not arrow), no `let`/`const`, no template literals — ES5 only.
- Take `(ctx, tMs, w, h)` and **return nothing** — they mutate `ctx`.
- Be **stateless across calls** — no module-level mutable state. Internal constants (ball count, palette, period) are fine; per-frame derived values must be recomputed from `tMs`.
- Not allocate per-frame where avoidable. A `for` loop with primitive locals is the iPad 1 friendly path. Avoid `Array.prototype.map`/`filter`/`forEach` in hot loops (they're available on iOS 5 Safari but slower than the equivalent counted `for`).
- Not call `Date.now()` or read `performance.now()`. `tMs` is the only time input. If an animation needs wall-clock state (the clock face, word clock), the call site passes it via a fifth `extras` argument — see [Wall-clock animations](#wall-clock-animations).

### `admin.html` (admin console)

Two additions, both small:

1. **`js/timeline/animations-catalog.js`** — exports `ANIMATIONS = [{key, label, description}, ...]`. Mirrors the index.html registry. Hardcoded; updated by hand when an animation lands.
2. **`js/timeline/modals/playlist-editor.js`** — when the selected item's `playmode === 'SCRIPT'`, swap the "file" text input for a `<select>` populated from `ANIMATIONS`. The existing free-text input stays as a fallback (operator types `?` and gets a free-text mode for animations not in the catalog — useful for forward-compat with bleeding-edge entries).

### Wall-clock animations

The "pure function of `tMs`" invariant is the synchronization mechanism — same `tMs`, same frame. But three of the animations below (`analogClock`, `wordClock`, `sunMoonTransit`) need **wall-clock time** to be meaningful. Their math is a pure function of *(`tMs`, wall-clock time)*, where the wall-clock is read from `GoTime.now()` at the start of the frame and passed as a fifth argument.

This keeps the synchronization property intact: every screen reads `GoTime.now()` against the same shared offset, so every screen sees the same wall-clock value to within the GoTime drift budget (already measured at <50 ms). The contract for `analogClock`, `wordClock`, and `sunMoonTransit` is:

```js
animations.analogClock = function(ctx, tMs, w, h, nowMs) { /* nowMs = GoTime.now() */ }
```

The loop in `renderPlayback` is updated to pass the GoTime value as the fifth arg for every animation. Animations that don't need it ignore it. (The cost of one extra function argument on the iPad 1 is negligible — measured at noise-floor in micro-benchmarks.)

## The animations

Each entry below is one registry function. The math is given in concrete enough form that an implementer can transcribe it. Tuning constants (color palette, count, period) are starting points; visual polish is expected during implementation.

### 1. `lissajous` — parametric curve

**Visual:** a single closed self-intersecting curve that morphs over time as the frequency ratio changes.

**Math:** trace `N` points along
- `x(s) = w/2 + (w·0.35) · sin(a · s + φ)`
- `y(s) = h/2 + (h·0.35) · sin(b · s)`

where `s` ∈ [0, 2π], `a:b` is the frequency ratio, and `φ` is a phase offset. Animate `a`, `b`, `φ` slowly from `tMs`:
- `a = 3 + 2·sin(tMs / 8000)`
- `b = 4 + 2·sin(tMs / 11000)` (period intentionally co-prime with `a`'s)
- `φ = tMs / 3000`

Stroke with a slowly-cycling hue (`hsl((tMs/40) % 360, 70%, 60%)`).

**Cost:** `N=300` line segments per frame. Cheap. <2 ms on iPad 1.

**Tuning:** start with `N=300`, line width 2, anti-alias off (Canvas2D default works).

### 2. `phyllotaxis` — golden-angle spiral

**Visual:** a sunflower-seed field of dots growing outward, gently rotating.

**Math:** for `i ∈ [0, N)`:
- `θ_i = i · 137.508° + tMs/4000` (golden angle + slow rotation)
- `r_i = c · √i` where `c = min(w,h) / (2·√N) · 0.92` (fits the canvas)
- `x_i = w/2 + r_i · cos(θ_i)`, `y_i = h/2 + r_i · sin(θ_i)`
- Dot radius `r = 3 + 2·sin(tMs/1500 + i·0.02)` for a subtle pulse along the spiral

Color: each dot's hue is `(i / N) · 360` — a rainbow that the rotation carries around.

**Cost:** `N=600` dots × one `arc` per dot = 600 path ops. ~5 ms on iPad 1. If profiling shows iPad 1 stuttering, drop to `N=400`.

### 3. `radialPulse` — concentric pulse rings

**Visual:** color-shifted rings expanding outward from center, fading at the edge.

**Math:** for `k ∈ [0, K)`:
- Phase offset per ring: `φ_k = (k / K) · 2π`
- Ring radius: `R_k = ((tMs/PERIOD + φ_k) mod 1) · maxR`, where `maxR = √(w² + h²)/2` (corner-reaching)
- Ring opacity: `α_k = 1 - (R_k / maxR)` (fade as it expands)
- Stroke width: `4 + 6·sin(tMs/1000)` — breathing
- Color: `hsl((tMs/40 + k·30) % 360, 80%, 60%)`

`PERIOD = 4000` ms per ring traversal. `K = 5`.

**Cost:** 5 stroked circles per frame. Trivial — <1 ms.

### 4. `plasma` — demoscene plasma

**Visual:** the classic '90s demo effect: smoothly-varying color clouds.

**Math:** decimate the canvas to a `GW × GH = 96 × 72` grid (~7000 cells). For each cell at grid pos `(gx, gy)`:
- Normalize to `[0, 1]`: `u = gx/GW`, `v = gy/GH`
- `c = sin(u·k1 + tMs/T1) + sin(v·k2 + tMs/T2) + sin((u+v)·k3 + tMs/T3) + sin(√((u-0.5)² + (v-0.5)²) · k4 + tMs/T4)`
- Map `c ∈ [-4, +4]` → hue `((c+4)/8) · 360`, full saturation/value

Write to an `ImageData` of size `GW × GH`. Once per frame, `putImageData` and then `ctx.drawImage(offscreen, 0, 0, w, h)` to scale up — Canvas2D's nearest-neighbor scaling is fine here (smearing reads as the plasma's natural softness).

Constants: `k1=8, k2=12, k3=10, k4=14, T1=2500, T2=3300, T3=4100, T4=1900` (incommensurate → no obvious repeat).

**Cost:** 7000-cell pixel write per frame + one drawImage. ~6 ms on iPad 1 measured against a comparable demoscene Canvas2D plasma. If too slow, drop to `64 × 48`.

### 5. `pendulumWave` — multi-pendulum interference

**Visual:** N pendulums hung in a row, each with a slightly different period, starting in phase. Over ~60 s they go through full phase scramble and re-synchronize. Looks intentional and almost mechanical.

**Math:** for `i ∈ [0, N)`:
- Anchor: `(x_i, y0)` where `x_i = (i + 0.5) · w / N`, `y0 = h · 0.15`
- Period: `T_i = T_BASE - i · T_STEP` (the classic pendulum-wave formula)
- Angle from vertical: `θ_i = A_MAX · sin(2π · tMs / T_i)`
- Bob position: `(x_i + L · sin(θ_i), y0 + L · cos(θ_i))`

Draw a thin line from anchor to bob, then a filled circle (the bob).

Constants: `N=16`, `L = h · 0.7`, `T_BASE = 4000` ms, `T_STEP = 80` ms, `A_MAX = π/6`. Full cycle (`T_BASE / T_STEP · T_BASE`) = ~3.3 minutes for the wave pattern to fully traverse and re-sync.

Color: monochrome white pendulums on dark background; bobs colored by index hue (`(i/N)·360`).

**Cost:** 16 thin lines + 16 small filled arcs. <2 ms. Cheapest "wow" animation in the pack.

### 6. `particleGalaxy` — orbital particles

**Visual:** a slow galactic swirl — particles orbiting an invisible attractor at varying radii.

**Math:** for `i ∈ [0, N)`:
- Particle's intrinsic orbit: `r_i = R_MIN + (R_MAX - R_MIN) · ((i * 0.6180339887) mod 1)` (golden-ratio spreading)
- Angular velocity: `ω_i = ω0 · √(R_MIN / r_i)` (Keplerian-ish — outer orbits slower)
- Phase offset: `φ_i = i · 137.5°`
- Position: `(w/2 + r_i · cos(ω_i · tMs + φ_i), h/2 + r_i · sin(ω_i · tMs + φ_i))`
- Hue: `(tMs/80 + i·2) mod 360`

Constants: `N = 400`, `R_MIN = min(w,h) · 0.08`, `R_MAX = min(w,h) · 0.45`, `ω0 = 0.0008 rad/ms`.

Render as 1-2 px filled circles (cheaper than `arc` for tiny dots — use `fillRect(x-0.5, y-0.5, 1, 1)` if `arc` profiles slow).

**Cost:** 400 tiny dots/frame. ~3 ms with `arc`, ~1 ms with `fillRect`. Start with `fillRect` for iPad 1 and upgrade if it looks janky.

### 7. `wireframeCube` — 3D rotating wireframe

**Visual:** a spinning wireframe cube (extendable to tesseract — see notes). Reads as a confident tech demo.

**Math:** define cube vertices `V` (8 of them) and edges `E` (12 of them) in unit coordinates `(-1, +1)`. Per frame:
- Rotation angles: `αx = tMs/2500`, `αy = tMs/3700`, `αz = tMs/5300` (incommensurate)
- Build 3D rotation matrix `Rx · Ry · Rz` (just 9 multiplies)
- For each vertex, multiply by `R` → rotated 3D point
- Project to 2D: `(x', y') = (cx + s·X / (1 + Z·persp), cy + s·Y / (1 + Z·persp))` where `s = min(w,h)/4`, `persp = 0.5`
- Stroke each edge between projected vertices

Color: edge stroke `hsl((tMs/30) % 360, 80%, 60%)`, line width 3.

**Cost:** 8 matrix multiplies + 12 strokes = trivial. <1 ms.

**Tesseract follow-up:** add a fourth axis and a 4D→3D projection step. Visually richer but the math is twice the size. Ships as `wireframeTesseract` in a later batch if there's appetite.

### 8. `wordClock` — letter-grid word clock (wall-clock animation)

**Visual:** an 11 × 10 grid of letters where the right ones light up to spell *"IT IS HALF PAST TEN"* or similar. Updates once a minute (the in-between minutes show the same lit set).

**Math:** uses the canonical word-clock grid:

```
ITLISASTIMEACQUARTERDCTWENTYFIVEXHALFSTENFTOPASTERUONESIXTHREE FOURFIVETWOEIGHTELEVENSEVENTWELVETENSEOCLOCK
```

(Spaces normalized; one line per row in the actual implementation — 11 columns × 10 rows. Use the standard layout — there's a well-known reference grid.)

The lit set for a given `(hour, minute)` is precomputed as a lookup table at module load (220 minute buckets × ~5 lit ranges each — under 4 KB). Render:

- Draw every letter in muted color (e.g., `#333`).
- Draw lit letters in foreground color (e.g., `#fff`) using the lookup.

Font: monospace, sized to fit (`floor(min(w/11, h/10)) · 0.8` px), drawn with `fillText`.

**Wall-clock input:** `nowMs` (the fifth argument). Convert to local hour/minute → table lookup.

**Cost:** 110 `fillText` calls per frame, mostly identical between frames. ~4 ms on iPad 1.

### 9. `analogClock` — analog clock face (wall-clock animation)

**Visual:** a clean analog clock — hour markers, hour hand, minute hand, second hand. Same on every screen.

**Math:** center `(w/2, h/2)`. Clock radius `R = min(w, h) · 0.45`.
- 12 hour ticks: at angles `θ_k = k · 30° - 90°`, draw a short line from `0.92·R` to `R` for `k=0..11` (longer for 12/3/6/9).
- Hour hand: angle `θ_h = ((H + M/60) · 30 - 90)°`, length `0.5·R`, thickness 5.
- Minute hand: angle `θ_m = ((M + S/60) · 6 - 90)°`, length `0.7·R`, thickness 3.
- Second hand: angle `θ_s = (S · 6 - 90)°`, length `0.75·R`, thickness 1, color red.

Background: black; face: white; second hand: red. Subtle drop-shadow optional (cheap on Canvas2D but skippable).

**Wall-clock input:** `nowMs` → `(H, M, S)` via `new Date(nowMs)` (cheap allocation but only once per frame).

**Cost:** 12 ticks + 3 hands + a face circle. <1 ms.

### 10. `sunMoonTransit` — astronomical transit (wall-clock animation)

**Visual:** an arc across the upper portion of the canvas; a sun (or moon at night) traverses it according to time-of-day. Day/night palette shifts.

**Math:** for "day" (06:00 → 18:00 local, configurable later):
- Fractional position along the arc: `t = (HH + MM/60 - 6) / 12`  (clamped to [0,1])
- Sun position: along a semi-elliptical arc — `cx = w·t`, `cy = h·0.4 - h·0.3·sin(π·t)`
- Background gradient: dawn → noon → dusk → night, via `hsl` interpolation against `t`.

For "night" (18:00 → 06:00 next day):
- Same arc parameterization, but draw a moon instead and shift the background to deep blue.

Draw a small filled circle for the body. Optional star sprinkle at night (`N=30` deterministically-seeded dots).

**Cost:** one filled rect (background gradient via `createLinearGradient` once per frame), one filled arc (the body), at most 30 tiny dots. <2 ms.

### 11. `dvdLogo` — bouncing logo (deliberate meme)

**Visual:** a stylized "MOSAICMESH" logo bouncing off the canvas edges, changing color on each impact.

**Math:** the bouncing trajectory is closed-form on `tMs`, NOT an integrator. Let logo size `(lw, lh) = (w·0.18, h·0.06)`. Free-flight bounds: `x ∈ [0, w - lw]`, `y ∈ [0, h - lh]`.

For a constant velocity `(vx, vy)` starting at origin:
- `xRaw = vx · tMs / 1000`
- Fold into the bounded interval via reflection: let `period = 2·(w - lw)`, `x_fold = abs((xRaw mod period) - (w - lw))` (triangle wave). Similarly for `y`.

The bounce count is `floor(xRaw / (w-lw))` (and the equivalent for y) — use the sum as the index into a fixed color palette (15 colors, cycling). Color step is deterministic from `tMs`, so every screen impacts at the same moment in the same color. Audience-friendly.

**Tuning:** `vx = 80 px/s`, `vy = 50 px/s`. Logo rendered as a stylized text label (no image asset needed — `fillText` with a bold font).

**Cost:** 2 trigonometry-free arithmetic ops + one `fillText`. <0.5 ms.

### 12. `gameOfLife` — Conway's Game of Life (precomputed cycle)

**Visual:** the classic. Cells live, die, blink, glide.

**Math:** Conway's GoL needs per-frame state, which violates pure-function-of-time. We resolve this by **precomputing a fixed cycle** at module load:

```
At first invocation only:
  Seed an 80 × 60 grid from a deterministic LFSR (fixed seed → identical seed everywhere).
  Run G = 600 generations, storing each as a packed bitmap (60 KB total).
  Cache as a closure-local var: precomputed[0..599].
On every frame:
  gen = floor(tMs / 100) % G        # advance 10 generations/sec, loop after 60s
  Render precomputed[gen] to the canvas, scaled.
```

Render via `ImageData` write (white = alive, black = dead) at `80 × 60`, then `drawImage` scaled to canvas.

The "first invocation only" precompute happens during the first call to `gameOfLife()`. The cost is ~30 ms one-time on iPad 1 (the rAF loop will skip one frame; acceptable). Subsequent frames are pure render → ~3 ms.

**Determinism:** the LFSR has a fixed seed; all screens compute the identical `precomputed` table; the `gen` index is a pure function of `tMs`. The first frame on each screen takes longer than steady-state, but the visible animation only depends on `precomputed[gen]` — which is identical everywhere.

**Caveat:** if a screen joins mid-playback (a late-arriving iPad), its `precomputed` table will be built on-the-fly during its first frame. The screen will be black for ~30 ms while it builds. Subsequent frames sync. Acceptable for the "one client joining late" case.

### 13. `fleetStatus` — fleet-status dashboard (REST-fed, NOT pure function of time)

**Visual:** a grid of cells, one per known display group / iPad, color-coded by status. Shows uptime, # iPads online, current playlist per group, last-calibration timestamp.

**Why it's different:** this animation is the platform earning its keep when nothing else is scheduled — an always-on operational view. It depends on the *current fleet state*, which the screen learns by listening to SockJS broadcasts. So `fleetStatus` is the **one animation in the pack that breaks pure-function-of-time**.

**How we contain the deviation:**

1. The display client already receives `DISCOVERY_HEARTBEAT` broadcasts (~every 5 s) — it just doesn't do anything with them today. `fleetStatus` subscribes to that channel and caches the latest payload in a closure-local var (one extra `if` in the existing `sock.onmessage`).
2. The render function is a pure function of *(`tMs`, last-broadcast-payload)*. Every screen received the same broadcast at the same moment (SockJS multi-cast), so the cached payload is identical across screens to within network jitter (<200 ms typically).
3. Drift effects: if iPad A receives the heartbeat 50 ms before iPad B, the wall shows mismatched cell counts for those 50 ms. Acceptable — far below human-perceptible threshold for a status display.
4. **No REST polling.** Avoid extra HTTP round-trips when the server is already broadcasting the data.

**Layout:** rows = display groups (from heartbeat), cells per row = clients in that group. Color encoding:
- Green: online + currently playing
- Yellow: online + idle
- Gray: offline
- Red: render error / not calibrated
- Pulsing accent (sin-modulated alpha from `tMs`): currently rendering

Bottom strip: server uptime, total clients, time-since-last-calibration.

**Cost:** depends on fleet size. 20 cells: <2 ms. 100 cells: ~5 ms. Caps gracefully.

**Operator value:** even without a customer-facing reason, this is the wall every install will turn on at 5 PM when the storefront closes — "show me my fleet is healthy" instead of a dark screen.

## Edge cases

- **Unknown animation name** — already handled by the SCRIPT slice's `if (animations[file])` guard. Blank canvas, no crash.
- **First frame of a precomputed animation** (`gameOfLife`) — first call takes ~30 ms longer than steady-state. The rAF loop drops the frame; the next frame catches up. No visible glitch beyond a half-frame stall.
- **Wall-clock animations during clock drift** — GoTime drift is <50 ms; the wall-clock animations sample `GoTime.now()` once per frame, so the worst-case visible drift across screens is one frame (~16 ms). Below human perception for clock-hand motion.
- **`fleetStatus` before any heartbeat** — the cached payload is `null` for the first ~5 s after boot. Render a "Waiting for fleet status…" placeholder; first heartbeat populates and the dashboard appears.
- **Color-blindness** — `radialPulse`, `lissajous`, `particleGalaxy`, `phyllotaxis` all cycle hue. The animations are decorative (not information-bearing), so color encoding is not load-bearing. The exception is `fleetStatus`, where the green/yellow/red encoding *is* load-bearing — pair color with a one-letter status code in each cell (`P` playing, `I` idle, `O` offline, `R` rendering, `!` error) so an operator with red-green color-blindness can still parse it.
- **Per-screen `(w, h)` differences** — animations should fit any aspect ratio. `min(w, h)` is used for radii; `w/N` (not a fixed pixel count) is used for grid spacings. iPad 1 is 1024×768 in landscape, but the same animations will run on the operator's preview at the admin desk (potentially a different resolution). Don't assume 1024×768.

## Server changes summary

**None required.** The SCRIPT slice already wired `playmode` into PLAY/SETPLAYLIST. Adding animations is a pure client-side change.

**Optional:** if the operator picker turns out to feel limiting, a future `GET /api/animations` endpoint returning `[{key, label, description}]` lets the admin discover new animations without an admin-page rebuild. Out of scope for this spec; the hardcoded catalog mirror is fine for v1.

## Client changes summary

- `index.html`: 12 new entries in the `animations` object (11 visual animations + `fleetStatus`).
- `index.html`: tiny SockJS-message extension to cache the latest `DISCOVERY_HEARTBEAT` payload for `fleetStatus`. (One closure-local var + one `else if` in `sock.onmessage`.)
- `index.html`: the rAF loop in `renderPlayback` passes `GoTime.now()` as a fifth arg to `animations[file]`. Animations that don't need it ignore it; the three wall-clock animations consume it.
- `js/timeline/animations-catalog.js`: new file. Exports `ANIMATIONS = [{key, label, description}, ...]`.
- `js/timeline/modals/playlist-editor.js`: when the selected item's `playmode === 'SCRIPT'`, swap the "file" field for a `<select>` populated from `ANIMATIONS`. Free-text fallback if the operator types `?`.

## Testing

### pytest

- The protocol surface is unchanged (the SCRIPT slice's tests already cover `playmode` in PLAY payloads). No new server tests.
- One new test against `mosaicmesh/api/playlists.py`: a playlist with multiple SCRIPT items round-trips correctly (id preserved per item, `file` field accepted as a non-URL string). This may already be covered by existing schedule + playlist tests; verify and add only if missing.

### Node (`node --test`) — pure-function determinism

For each new animation, one unit test that:

1. Imports the animation function (via a small ES5-friendly extraction script that pulls the registry out of `index.html`, OR — simpler — a parallel module `tests/unit/js/_animations_mirror.js` that copy-paste mirrors the registry for testing; the mirror is a known maintenance cost we accept).
2. Calls the function at `tMs = 12345` against a stub Canvas2D context that records draw operations.
3. Asserts that calling it twice at the same `tMs` produces the same recorded operation log.
4. For wall-clock animations: calling at the same `(tMs, nowMs)` twice produces the same operation log.

This is the synchronization guarantee in test form.

### Playwright

- Light browser-driven smoke per animation (in batches of 3 — full suite would be 13 specs, overkill for visual smoke). Verify:
  1. A SCRIPT item with `file = <animationName>` renders something to the canvas (sample 4 pixels, assert at least one is non-background).
  2. The transition to a non-SCRIPT item tears down the canvas.
  3. `STOP` clears the loop (`playback.scriptRaf` cleared).

- One end-to-end test for `fleetStatus`: post a synthetic `DISCOVERY_HEARTBEAT` and assert the canvas updates within 1 s.

### iPad 1 hardware smoke

Per-animation manual sign-off on a real iPad 1 (the platform's defining device). Target: each animation sustains 30+ FPS without dropped frames over a 60 s run. This is not automatable — it's a checklist the implementer runs once at the end. Cost budget per animation: <8 ms/frame measured via `performance.now()` deltas captured in the rAF loop (or visual estimation if `performance.now()` doesn't exist — it's available on iOS 5, just `webkit`-prefixed).

## ES5 / legacy compliance

- All registry functions are `function`, no arrow.
- All locals are `var`, no `let`/`const`.
- No template literals. All string composition uses `+`.
- No `Array.prototype.includes`, `Array.from`, `Object.assign` — use `indexOf`, `for` loops, manual copy.
- Canvas2D operations available on iOS 5 Safari: `fillRect`, `strokeRect`, `fillText`, `strokeText`, `arc`, `moveTo`/`lineTo`, `stroke`/`fill`, `createLinearGradient`, `putImageData`/`getImageData`/`drawImage`, `save`/`restore`. All used by these animations. No `Path2D` (not available on iOS 5).
- `requestAnimationFrame` already shimmed by the SCRIPT slice.

The animation registry is a single `<script>` block in `index.html`. It does not load any external code.

## Mosaic-spanning roadmap

This section is **non-normative** — captures design surface for the next animation slice (slice 6.2 or later), not in scope for this one. Included so the design conversation has fuel when that work begins.

The platform's distinctive capability is knowing where each iPad physically sits on a wall — `client.measuredCenter`, `client.measuredPerimeter`, and the per-group `boundingBox`. A mosaic-spanning animation renders a *slice* of one large composition; the slice is whatever falls inside that iPad's physical bounds.

**What needs to land for mosaic-spanning:**

1. **Coordinate transform in the SCRIPT context.** The animation receives the iPad's `(localOriginX, localOriginY, localWidth, localHeight)` in the *global* canvas coordinate space (the bounding box of the entire wall). It also receives `(w, h)` — its own screen pixels. The transform is: a point at global `(X, Y)` is at local `(X - localOriginX) / localWidth · w`, etc.
2. **Server-side payload extension.** The PLAY payload's per-client SCRIPT item needs to carry the iPad's slot in the bounding box. This is the first extension of the per-client SCRIPT payload — it implies SCRIPT items become *per-client* (like SEGMENT), no longer group-broadcast.
3. **Calibration becomes load-bearing for SCRIPT.** Today a SCRIPT-only playlist needs no calibration. After mosaic-spanning lands, mosaic-spanning SCRIPT items need calibration; identical-on-every-screen SCRIPT items still don't. A new `mode` field on the SCRIPT item (`identical` | `mosaic`) signals the per-item choice.

**Mosaic-spanning animation candidates** (once the infrastructure lands):

- **Wave-across-the-wall** — a `sin(globalX / k + tMs)` band of color. Trivially extends `radialPulse` to a wall-scale sine wave.
- **One giant analog clock** — the existing `analogClock` math, but drawn against the global wall-bbox instead of the local canvas.
- **Galaxy slice** — `particleGalaxy` rendered against the global bbox; each screen is a window into the same swirl.
- **Tron-grid / starfield** — large-scale perspective-warped lines, each screen showing its viewport. Looks like windows on a moving spaceship.
- **Equalizer** — one tall bar per screen, heights driven by a shared spectrum function of `tMs`. Visually simple, dramatic at scale.

The mosaic-spanning slice is a real architectural step (per-client SCRIPT payloads, calibration dependency, new item-mode field) — not just five more animations. Worth its own design document.

## Open questions

1. **Catalog mirror vs. server-served catalog.** This spec picks the hardcoded `animations-catalog.js` mirror to avoid a round-trip. If the registry grows past ~20 entries, server-served becomes more attractive (one source of truth). Re-evaluate when adding the next batch.
2. **Animation discoverability.** Beyond the dropdown — should the playlist editor show a thumbnail / preview of each animation? Cheap to add later (a small static image per animation); ships without it for v1.
3. **Operator's ability to disable specific animations.** A `SCRIPT_ENABLED_ANIMATIONS` setting? Probably overkill — operators just don't add the items they don't want. Defer.
4. **`gameOfLife` precompute cost on join.** The 30 ms one-time stall on first call is acceptable in tests but might be visible on iPad 1 hardware as a single dropped frame at item-start. Verify on hardware; if visible, move the precompute into the SETPLAYLIST handler (run it at PRELOAD time) instead of first-call.

## Sequencing

This spec doesn't mandate a single-PR implementation — it explicitly invites batching. A reasonable sequence (each batch is one PR's worth):

- **Batch 1 — geometry pack** (cheap, foundational): `lissajous`, `phyllotaxis`, `wireframeCube`. Adds the registry-extension pattern + the `animations-catalog.js` mirror + the playlist-editor dropdown. Once this batch ships, future animations are pure leaf additions.
- **Batch 2 — pulse pack:** `radialPulse`, `particleGalaxy`, `plasma`. Visual variety; no new infrastructure.
- **Batch 3 — wall-clock pack:** `analogClock`, `wordClock`, `sunMoonTransit`. Introduces the fifth `nowMs` argument to the rAF loop.
- **Batch 4 — kinetic / meme pack:** `pendulumWave`, `dvdLogo`, `gameOfLife`. Adds the precomputed-cycle pattern (for GoL).
- **Batch 5 — fleet status:** `fleetStatus` (alone). The only animation that breaks pure-function-of-time; deserves its own focused review.

Total = 5 implementation PRs.

## Decision log

- **Hardcoded animation catalog mirror, not server endpoint.** Round-trip avoided; mirror is a known small maintenance cost. Revisit at >20 entries.
- **Fifth `nowMs` argument unconditionally.** Cheaper than branching at the call site. Animations that don't need it ignore it.
- **Heartbeat-fed `fleetStatus`, not REST-polled.** Preserves "same payload on every screen at every moment" property as closely as the network allows. <200 ms jitter is acceptable for a status display.
- **Precomputed `gameOfLife` cycle, not live evolution.** The synchronization invariant requires it. Six hundred generations × 10 fps = 60 s of unique visual before the loop — long enough to not read as a loop.
- **No per-animation parameterization in this spec.** Each animation ships with one canonical look. Defer the parameterization slice.
- **Mosaic-spanning explicitly out of scope.** Documented as future work; not a non-goal-by-omission. The next animation slice will need its own design document.
