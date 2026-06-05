"""Local test harness for the calibration pipeline.

Usage:
    python tools/test_calibrate.py [path/to/image.jpg]

If no path given, picks the most recent image in cache/. Runs the four
pipeline stages defined in server.py:

  1. find_screen_quads_bright  (adaptive-threshold + 4-point convex filter)
  2. _select_per_marker_quads  (per-marker smallest-enclosing, marker-area floor)
  3. _filter_outlier_area      (reject area outliers vs the median)
  4. _drop_overlapping         (reject pairs with > IoU threshold overlap)

Prints stage-by-stage diagnostics and writes a visualization to
`cache/calibrate_test_result.png` showing all candidate quads (faint),
the surviving per-marker selections (bold green), and the ArUco markers
(blue). Lets us iterate parameter tuning without restarting the server
or re-uploading from the device.
"""
import os, sys, glob, cv2 as cv, numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import (
    find_screen_quads_bright, _select_per_marker_quads,
    _filter_outlier_area, _drop_overlapping,
    _per_marker_fallback_search, _band_from_marker_floodfill,
    detect_aruco_markers, reconcile_screen_quad,
)


def latest_cache_image():
    cache = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
    files = [f for f in glob.glob(os.path.join(cache, '*.jpg')) if os.path.isfile(f)]
    files += [f for f in glob.glob(os.path.join(cache, '*.png')) if os.path.isfile(f)]
    # Exclude our own generated visualization files so re-runs pick the
    # actual upload, not the previous test output.
    files = [f for f in files if 'calibrate_test_' not in os.path.basename(f)]
    if not files: return None
    return max(files, key=os.path.getmtime)


def fmt_area_stats(quads):
    if not quads: return "(none)"
    areas = [cv.contourArea(q) for q in quads]
    return f"min={min(areas):.0f} median={np.median(areas):.0f} max={max(areas):.0f}"


def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else latest_cache_image()
    if not img_path:
        print("no image found in cache/ and none passed as argument")
        sys.exit(1)
    print(f"image: {img_path}  ({os.path.getsize(img_path):,} bytes)")

    image = cv.imread(img_path)
    if image is None:
        print("cv.imread failed; bad path or unsupported format")
        sys.exit(2)
    print(f"  shape={image.shape}")

    # Stage 0: ArUco detection
    corners, ids, _ = detect_aruco_markers(image)
    n_markers = 0 if ids is None else len(ids.flatten())
    print(f"\nStage 0 (ArUco): {n_markers} markers detected")
    if n_markers:
        m_areas = [cv.contourArea(c.reshape(4, 2).astype(np.float32)) for c in corners]
        print(f"  marker areas: {fmt_area_stats([c.reshape(4, 2).astype(np.float32) for c in corners])}")

    # Stage 1: bright-region candidate quads
    candidates = find_screen_quads_bright(image)
    print(f"\nStage 1 (find_screen_quads_bright): {len(candidates)} candidate quads")
    print(f"  areas: {fmt_area_stats(candidates)}")

    # Build marker_list as calibrate() does
    marker_list = []
    if n_markers:
        for mc, mid in zip(corners, ids.flatten()):
            marker_list.append((mc.reshape(4, 2), mid))

    # Stage 2: per-marker selection
    m2q_2 = _select_per_marker_quads(candidates, marker_list)
    print(f"\nStage 2 (_select_per_marker_quads, marker-area-ratio>=5): {len(m2q_2)} quads")
    print(f"  areas: {fmt_area_stats(list(m2q_2.values()))}")
    # Per-marker area sorted (catch the outliers visually)
    pairs = sorted([(int(mid), cv.contourArea(q)) for mid, q in m2q_2.items()], key=lambda x: x[1])
    median_area = float(np.median([a for _, a in pairs]))
    print(f"  per-marker (sorted by area, median={median_area:.0f}):")
    for mid, a in pairs:
        ratio = a / median_area if median_area else 0
        flag = ""
        if ratio < 1/3.0: flag = " < small outlier"
        elif ratio > 3.0: flag = " > big outlier"
        print(f"    marker {mid:>3d}: area={a:>10,.0f}  ratio={ratio:5.2f}{flag}")

    # Stage 3: area outlier filter
    m2q_3 = _filter_outlier_area(m2q_2, max_ratio=3.0)
    print(f"\nStage 3 (_filter_outlier_area, max_ratio=3.0): {len(m2q_3)} quads")
    print(f"  areas: {fmt_area_stats(list(m2q_3.values()))}")

    # Stage 4: overlap rejection
    m2q_4 = _drop_overlapping(m2q_3, iou_threshold=0.3)
    print(f"\nStage 4 (_drop_overlapping, iou_threshold=0.3): {len(m2q_4)} quads")
    print(f"  areas: {fmt_area_stats(list(m2q_4.values()))}")

    # Stage 5: PRIMARY band detection -- per-marker flood fill. This is
    # what calibrate() runs first; if it succeeds we use it, otherwise
    # the threshold pipeline above is the fallback.
    floodfill_quads = {}
    for marker_corners, marker_id in marker_list:
        q = _band_from_marker_floodfill(image, marker_corners)
        if q is not None:
            floodfill_quads[int(marker_id)] = q
    print(f"\nStage 5 (_band_from_marker_floodfill, PRIMARY): {len(floodfill_quads)} quads")
    print(f"  areas: {fmt_area_stats(list(floodfill_quads.values()))}")

    # Stage 6: per-marker fallback search for any marker still without a band.
    combined = dict(floodfill_quads)
    for mid, q in m2q_4.items():
        if mid not in combined:
            combined[mid] = q
    n_pre_fallback = len(combined)
    for marker_corners, marker_id in marker_list:
        if int(marker_id) in combined:
            continue
        q = _per_marker_fallback_search(image, marker_corners, marker_id)
        if q is not None:
            combined[int(marker_id)] = q
    n_floodfill = len(floodfill_quads)
    n_thresh_only = len(combined) - n_floodfill - (len(combined) - n_pre_fallback)
    n_fallback = len(combined) - n_pre_fallback
    print(f"\nStage 6 (combined): {len(combined)} quads "
          f"({n_floodfill} flood-fill + {n_thresh_only} threshold-only + {n_fallback} fallback)")
    m2q_4 = combined

    print(f"\n=== summary: {n_markers} markers -> {len(m2q_4)} final quads ===")

    # Visualization: pipeline diagnostic (intermediate stages).
    viz = image.copy()
    # All candidates: faint yellow
    for q in candidates:
        cv.polylines(viz, [q], True, (0, 200, 200), 1)
    # Stage-2 selections: cyan (got picked but may have been filtered later)
    for mid, q in m2q_2.items():
        cv.polylines(viz, [q], True, (255, 200, 0), 2)
    # Final survivors: bold green
    for mid, q in m2q_4.items():
        cv.polylines(viz, [q], True, (0, 255, 0), 4)
    # ArUco markers: blue
    if n_markers:
        for c in corners:
            pts = c.reshape(4, 2).astype(int)
            cv.polylines(viz, [pts], True, (255, 0, 0), 2)

    out = os.path.join(os.path.dirname(img_path), 'calibrate_test_result.png')
    cv.imwrite(out, viz)
    print(f"\nvisualization (pipeline diagnostic): {out}")

    # Visualization: full calibrate() output preview -- run reconcile_screen_quad
    # for every marker (band-detected or fiducial fallback) and draw the
    # reconciled quad. Replicates what calibrate() now writes to
    # media/displays/images/calibration.png so we can iterate without a re-upload.
    # Uses iPad-1 canvas dims (1024x768 landscape) since this fleet is uniform;
    # real calibrate() reads canvasWidth/canvasHeight per client.
    final_viz = image.copy()
    cw, ch = 1024, 768
    all_quads_for_bbox = []
    source_counts = {}
    for marker_corners, marker_id in marker_list:
        quad_candidate = m2q_4.get(int(marker_id))
        border = quad_candidate.reshape(-1, 1, 2) if quad_candidate is not None else None
        reconciled, source = reconcile_screen_quad(
            marker_corners, border, cw, ch)
        source_counts[source] = source_counts.get(source, 0) + 1
        qpts = reconciled.reshape(4, 2).astype(int)
        # Green: fiducial validated by band (high confidence). Yellow: no
        # band quad / band didn't validate -- geometry is still the fiducial.
        colour = (0, 255, 0) if source in ("fiducial", "rotated") else (0, 255, 255)
        for i in range(4):
            cv.line(final_viz, tuple(qpts[i]), tuple(qpts[(i + 1) % 4]), colour, 4)
        all_quads_for_bbox.append(reconciled.reshape(-1, 1, 2).astype(np.int32))
        # ArUco corner outline (blue)
        mpts = marker_corners.astype(int)
        for i in range(4):
            cv.line(final_viz, tuple(mpts[i]), tuple(mpts[(i+1) % 4]), (255, 0, 0), 2)
    # Overall bbox in red
    if all_quads_for_bbox:
        flat = np.concatenate(all_quads_for_bbox)
        x, y, w, h = cv.boundingRect(flat)
        cv.rectangle(final_viz, (x, y), (x + w, y + h), (0, 0, 255), 4)

    out2 = os.path.join(os.path.dirname(img_path), 'calibrate_test_final.png')
    cv.imwrite(out2, final_viz)
    print(f"visualization (final calibrate() preview): {out2}")
    print(f"\nreconcile_screen_quad source breakdown:")
    for src, n in sorted(source_counts.items()):
        print(f"  {src:>14s}: {n}")


if __name__ == '__main__':
    main()
