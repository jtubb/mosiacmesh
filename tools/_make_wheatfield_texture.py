"""Bake a dense wheat-field texture -> media/server/images/wheatfield.png.

A warm straw vertical gradient packed with many upright golden stalks + grain-head
clusters, drawn to tile reasonably across the wall. 768x768 (~0.59 MP, under the
iPad-1 ~3 MP decode cap). Deterministic (fixed seed) so re-bakes are identical.
Uses numpy + cv2 (both runtime deps) -- no PIL dependency.
"""
import os
import numpy as np
import cv2

W = H = 768
rng = np.random.RandomState(7)

# --- warm straw vertical gradient background (BGR) ---
top = np.array([150, 196, 222], dtype=np.float32)    # lighter straw (B,G,R) ~ #dec496
bot = np.array([24, 118, 168], dtype=np.float32)     # deeper amber  ~ #a8761c
img = np.zeros((H, W, 3), dtype=np.float32)
for y in range(H):
    t = y / float(H - 1)
    img[y, :, :] = top * (1.0 - t) + bot * t

STALK = (40, 150, 198)      # golden stalk  ~ #c6962 8 -> BGR
HEAD = (90, 205, 232)       # lighter grain ~ #e8cd5a
HEAD_D = (60, 170, 205)     # grain shade


def grain(cx, cy, hw, hh, fan):
    """One wheat grain: an upward teardrop (rounded base, pointed tip), tip fanned by `fan`."""
    tx = cx + fan
    pts = np.array([
        [tx, cy - hh],
        [cx + hw, cy - hh * 0.15],
        [cx + hw, cy + hh * 0.35],
        [cx + hw * 0.6, cy + hh * 0.85],
        [cx, cy + hh],
        [cx - hw * 0.6, cy + hh * 0.85],
        [cx - hw, cy + hh * 0.35],
        [cx - hw, cy - hh * 0.15],
    ], np.int32)
    cv2.fillConvexPoly(img, pts, HEAD, cv2.LINE_AA)
    cv2.polylines(img, [pts], True, HEAD_D, 1, cv2.LINE_AA)


def stalk(cx, base_y, h, lean, sw):
    """Draw one upright golden stalk with a clustered teardrop-grain ear at the tip."""
    tip = (int(cx + lean), int(base_y - h))
    cv2.line(img, (int(cx), int(base_y)), tip, STALK, sw, cv2.LINE_AA)
    # grain ear: a cluster of paired upward teardrops up the top ~45%, fanning outward
    ear = h * 0.45
    rows = 6
    for k in range(rows):
        f = k / float(rows - 1)
        ky = tip[1] + ear * f
        kx = cx + lean * (1.0 - f)
        tap = 1.0 - f * 0.5
        hw = max(1.5, 2.4 * tap)
        hh = max(2.5, 4.6 * tap)
        off = 2.6 * tap + sw
        fan = 1.8 * tap
        grain(kx - off, ky, hw, hh, -fan)
        grain(kx + off, ky, hw, hh, fan)
    grain(tip[0], tip[1] - 2, max(1.5, 2.2), 5.0, 0)   # crowning tip grain


# --- pack the field densely; wrap x so it tiles horizontally ---
N = 520
for _ in range(N):
    cx = rng.uniform(0, W)
    h = rng.uniform(H * 0.45, H * 0.95)
    base_y = H + rng.uniform(0, 40)              # roots below the frame -> no hard bottom line
    lean = rng.uniform(-10, 10)
    sw = rng.choice([1, 1, 2])
    for dx in (-W, 0, W):                        # wrap for horizontal tiling
        stalk(cx + dx, base_y, h, lean, sw)

out = np.clip(img, 0, 255).astype(np.uint8)
dst = os.path.join("media", "server", "images", "wheatfield.png")
os.makedirs(os.path.dirname(dst), exist_ok=True)
cv2.imwrite(dst, out)
print("wrote", dst, out.shape, os.path.getsize(dst), "bytes")
