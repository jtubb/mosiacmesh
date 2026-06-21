# Mesh-Animation Geometry Rectification (homography) — Design

**Date:** 2026-06-21
**Status:** Approved — ready for implementation plan.
**Type:** Experiment. Default OFF; opt-in via a toggle. May be reverted based on on-wall results.
**Builds on:** the merged mesh-display feature (`scriptSpan`, `Display.meshGlobal`, per-client `meshQuad`, `mmMeshTransform`, the boot backfill).

## Context

A meshed SCRIPT animation currently renders **low / vertically nonlinear** on the OEB wall. Root cause (confirmed from live data): the global coordinate space is the axis-aligned bounding box of the screen quads in **calibration-photo pixel space**, and that photo has ~2:1 vertical keystone (bottom screens ~2× taller than top). So the bbox center sits ~99 px below the screens' geometric centroid, and the vertical mapping is nonlinear. The code does **not** assume width=height — `meshGlobal` preserves the true bbox aspect with uniform scale; the defect is that the bbox *is* keystoned photo space.

The OEB layout is a clean **6×4 grid of identical iPad-1s**. We already measure each screen's absolute position+size (`Client.measuredPerimeter`, photo px). This experiment **rectifies** those measured positions — removes the perspective via a homography fit from the grid structure — so a meshed animation centers on the true physical grid center with linear mapping and real (uniform) gaps preserved.

## Decisions (settled during brainstorming)

- **Approach A — homography rectification from the grid.** Fit a homography from the screen centers to a regular lattice (24 points, robust least-squares), apply it to the real quad corners. Chosen over a uniform-cell idealization (discards measured shape) and an outer-4-corner homography (fragile, needs a guessed target aspect).
- **Gap-aware.** The homography maps centers to a regular lattice (uniform spacing — the keystone fix), and is applied to each screen's *real* corners, so cells land on the lattice with their de-keystoned real shape and inter-cell gaps come out uniform — correct for a wall of identical, regularly-mounted screens (the measured per-gap variation is perspective noise, not real).
- **Default OFF, opt-in.** `MESH_RECTIFY` defaults `False`. Raw-bbox is the standing/production behavior, byte-identical to today, until the flag is flipped. Flip + restart to A/B on the wall; revert = flip back (or abandon this branch).
- **Isolated to the mesh-animation path.** SEGMENT / INDIVIDUAL media keep using raw `measuredPerimeter` / `boundingBox` untouched. The client (`mmMeshTransform`, `runScriptLoop`) is unchanged — it just receives a cleaner quad.
- **Graceful fallback.** If grid detection is ambiguous, rectification is skipped (fields stay `None`) and the mesh path uses raw-bbox. Never breaks playback.

## Toggle semantics

`MESH_RECTIFY` (module constant in `mosaicmesh/calibration.py`, default `False`) gates **both**:
- **Compute:** rectified geometry is computed/stored only when the flag is on (no extra work in normal production).
- **Use:** `_per_client_items` selects the rectified geometry only when the flag is on AND the rectified fields are present.

So with the flag off, nothing is computed and the render path is exactly today's. Stale rectified fields left in `settings.dat` from a prior on-run are ignored because the render condition tests the flag, not just field presence. A/B = flip the constant + restart (the boot backfill recomputes-or-skips accordingly).

## Data model

- `Display.meshGlobalRect` — `[GW, GH]` device-pixel rectified global canvas, or `None`. Default `None`.
- `Client.meshCellQuad` — the screen's rectified quad, normalized 0..1 (TL/TR/BR/BL) into the rectified bbox, or `None`. Default `None`.

Both persisted, computed at calibration and the boot backfill (when `MESH_RECTIFY`). Defensive `getattr(..., None)` at read sites; no migration code needed (defaults + getattr cover old `settings.dat`).

## Rectification pipeline (server-side, `mosaicmesh/calibration.py`)

A new function `rectify_group_grid(display_id)` (called from `assign_group_bounding_boxes` for each group **only when `MESH_RECTIFY`**, after the raw bbox is set). `numpy`/`cv2` are already imported there.

1. **Centers.** For each calibrated client in the group, center = mean of its quad corners (`np.array(measuredPerimeter).reshape(-1,2).mean(axis=0)`). Need ≥4 calibrated screens; else skip (set fields `None`).
2. **Grid detection.** Cluster the center y-values into rows and x-values into columns by gap-splitting: sort the values, split where a gap exceeds `0.5 ×` the median adjacent gap; row/col index = band index. Determine `R` rows × `C` cols. **Validity check:** every `(row,col)` cell is occupied by exactly one screen and `R*C == N`. If not (irregular/partial grid), **skip** (fields `None`, fall back to raw).
3. **Homography.** Source = measured centers `(N,2) float32`; destination = ideal lattice `(col, row)` `(N,2) float32`. `H, _ = cv2.findHomography(src, dst, 0)` (method 0 = least-squares; no outliers expected on a clean grid). If `H is None`, skip.
4. **Apply to corners.** For each client, transform its 4 real quad corners through `H` via `cv2.perspectiveTransform` → rectified corners in lattice units. (De-keystoned; uniform spacing; real cell shape retained.)
5. **Scale to device px.** Per-axis scale so one cell ≈ device resolution, giving the global canvas the *physical* wall aspect: `scaleX = medianDeviceWidth / medianRectifiedCellWidth`, `scaleY = medianDeviceHeight / medianRectifiedCellHeight` (cell width/height = axis-aligned extent of each rectified quad; medians over the group; guard divide-by-zero / missing resolution → fall back to a unit scale that preserves the lattice aspect). Rectified bbox (lattice units) → `meshGlobalRect = [round(rectW*scaleX), round(rectH*scaleY)]`.
6. **Store.** `Display.meshGlobalRect = [GW,GH]`; for each client, `meshCellQuad = [[ (cx-rbx)/rbw, (cy-rby)/rbh ] for corners]` (rectified corner normalized into the rectified bbox; native floats for JSON). `rbx,rby,rbw,rbh` = rectified bbox in lattice units (normalization is scale-independent).

Coordinates emitted to clients (`meshCellQuad`, `meshGlobalRect`) must be **native Python floats/ints** — the payload is JSON-serialized over SockJS (same constraint as the existing `meshQuad`).

## Render path (`mosaicmesh/render.py`, `_per_client_items`)

In the existing SCRIPT-mesh branch, choose the geometry:

```
if MESH_RECTIFY and getattr(display, "meshGlobalRect", None) and getattr(c, "meshCellQuad", None):
    item["meshQuad"]   = c.meshCellQuad            # rectified cell
    item["meshGlobal"] = list(display.meshGlobalRect)
elif c.measuredPerimeter is not None and display.boundingBox and getattr(display, "meshGlobal", None):
    # today's raw-bbox path (unchanged)
    item["meshQuad"]   = [[(px-bx)/float(bw), (py-by)/float(bh)] for ...]
    item["meshGlobal"] = list(display.meshGlobal)
# else: omit -> client goes black (uncalibrated mesh) / mirror is unaffected
```

`MESH_RECTIFY` is imported from `calibration`. The condition tests the flag first, so flag-off is exactly today's behavior.

## Testing

- **Grid detection** (`tests/unit/`): a clean synthetic 6×4 of centers → `R=4, C=6` with correct `(row,col)` assignment; an irregular set (missing cell / off-grid point) → skip signal (returns None / no rectification).
- **Homography rectification — the core fix:** build a **synthetic keystoned** 6×4 grid (apply a known perspective to a regular lattice), run the pipeline, assert the rectified centers form a regular lattice and the rectified-bbox center coincides with the screen centroid (within tolerance) — i.e., the low-center is gone. Assert gaps are preserved (uniform, non-zero between cells).
- **`_per_client_items` selection:** with `MESH_RECTIFY=True` + rectified fields present → emits `meshCellQuad` + `meshGlobalRect`; with the flag patched `False` → emits the raw-bbox quad even if rectified fields exist; uncalibrated → no mesh fields. JSON-serializable (native floats), like the existing mesh test.
- **No-regression:** full `--unit` + `--js` suites green; raw-bbox path and SEGMENT/INDIVIDUAL untouched.
- **iPad-1 on-wall sign-off (the real acceptance):** flip `MESH_RECTIFY=True`, restart, play a meshed radial animation on OEB — center sits at the true wall center, vertical mapping linear, gaps intact. Flip off → reverts to today's low-center raw behavior. This is an experiment; on-wall comparison decides keep-vs-revert.

## Non-goals / known limits

- **Regular-grid assumption.** Works for a clean R×C grid (OEB's 6×4 of identical screens). Irregular / non-grid / partial layouts fall back to raw-bbox automatically.
- No change to SEGMENT/INDIVIDUAL media geometry, to `mmMeshTransform`, or to the client render path.
- Not a general perspective-correction for media; scoped to mesh SCRIPT animations only.
- Per-screen physical rotation beyond what the homography + per-screen affine capture is out of scope.
