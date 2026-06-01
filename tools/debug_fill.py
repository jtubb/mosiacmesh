"""Visualize the flood-fill region for a single marker by ID."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2 as cv, numpy as np, jsonpickle
from server import detect_aruco_markers

TARGET_ARUCO_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
img = cv.imread(os.path.join(ROOT, 'cache', '20260601_162307.jpg'))

corners, ids, _ = detect_aruco_markers(img)
target = None
for c, i in zip(corners, ids.flatten()):
    if int(i) == TARGET_ARUCO_ID:
        target = c.reshape(4, 2)
        break
if target is None:
    print(f"marker {TARGET_ARUCO_ID} not found")
    sys.exit(1)

# Replicate _band_from_marker_floodfill internals so we can see seeds + fill.
mc = target.astype('float32')
h_half = 150.0
marker_frame = np.array([[-h_half, -h_half], [h_half, -h_half],
                         [h_half, h_half], [-h_half, h_half]], dtype='float32')
H = cv.getPerspectiveTransform(marker_frame, mc)

gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
img_h, img_w = gray.shape[:2]

# Walk-out seeds
walk_max = h_half * 2.0
walk_step = max(2.0, h_half * 0.05)
seeds = []
for d in [(0,-1),(0,1),(-1,0),(1,0)]:
    for r in np.arange(h_half * 1.05, walk_max, walk_step):
        local = np.array([[[d[0]*r, d[1]*r]]], dtype='float32')
        photo = cv.perspectiveTransform(local, H).reshape(2)
        px, py = int(round(photo[0])), int(round(photo[1]))
        if not (0 <= px < img_w and 0 <= py < img_h):
            break
        if int(gray[py, px]) >= 140:
            seeds.append((px, py, d))
            break

print(f"marker {TARGET_ARUCO_ID}, {len(seeds)} seeds:")
for sx, sy, d in seeds:
    print(f"  seed @ ({sx}, {sy}) dir={d} brightness={int(gray[sy, sx])}")

# Crop to a region around the marker for visualization
cx, cy = int(mc[:, 0].mean()), int(mc[:, 1].mean())
edge = int(np.linalg.norm(mc[1] - mc[0]))
pad = edge * 8
x0, y0 = max(0, cx - pad), max(0, cy - pad)
x1, y1 = min(img_w, cx + pad), min(img_h, cy + pad)
viz = img[y0:y1, x0:x1].copy()

# Run all 4 fills and overlay
colors = [(0,0,255),(0,255,0),(255,0,0),(0,255,255)]
for i, (sx, sy, d) in enumerate(seeds):
    for tol in (70, 100, 140):
        mask = np.zeros((img_h + 2, img_w + 2), dtype=np.uint8)
        flood = gray.copy()
        cv.floodFill(flood, mask, (sx, sy), 200, loDiff=tol, upDiff=tol,
                     flags=cv.FLOODFILL_FIXED_RANGE | (255 << 8))
        fill = mask[1:-1, 1:-1]
        area = int((fill > 0).sum())
        print(f"  fill seed {i} dir={d} tol={tol}: area={area} px")
    # overlay the fill region in this seed's color
    fill_local = fill[y0:y1, x0:x1]
    overlay = np.zeros_like(viz)
    overlay[fill_local > 0] = colors[i]
    viz = cv.addWeighted(viz, 1.0, overlay, 0.3, 0)
    # draw seed dot
    cv.circle(viz, (sx - x0, sy - y0), 8, colors[i], -1)

# Mark marker corners
for j, pt in enumerate(mc):
    px, py = int(pt[0] - x0), int(pt[1] - y0)
    cv.circle(viz, (px, py), 12, (255, 255, 0), 2)
    cv.putText(viz, str(j), (px + 15, py - 15), cv.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 0), 3)

out = os.path.join(ROOT, 'cache', f'debug_fill_{TARGET_ARUCO_ID}.png')
cv.imwrite(out, viz)
print(f"\nwrote {out}")
