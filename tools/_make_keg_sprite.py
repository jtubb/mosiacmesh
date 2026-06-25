"""Render a simple side-view wooden keg/barrel PNG (transparent) to seed the
kegroll transition. Output: media/server/images/keg.png (BGRA). Replace with
nicer art later — the effect degrades to a plain wipe if the sprite is absent."""
import os
import numpy as np
import cv2

W = H = 900
img = np.zeros((H, W, 4), dtype=np.uint8)
cx, cy = W / 2.0, H / 2.0

BODY = (40, 95, 150, 255)        # BGRA — warm wood brown
BODY_D = (24, 60, 100, 255)
HOOP = (120, 120, 130, 255)      # steel hoop grey
OUTLINE = (12, 28, 45, 255)

# Barrel body: a vertical "staved" barrel that bulges in the middle. Build the
# silhouette as a closed polygon (left edge down, right edge up) using a cosine bulge.
half_h = H * 0.40
top_w = W * 0.30                 # half-width at the ends
mid_w = W * 0.42                 # half-width at the belly
n = 40
left, right = [], []
for i in range(n + 1):
    t = i / n                                   # 0 (top) .. 1 (bottom)
    y = cy - half_h + t * 2 * half_h
    bulge = top_w + (mid_w - top_w) * np.sin(t * np.pi)   # 0 at ends, max at middle
    left.append((cx - bulge, y))
    right.append((cx + bulge, y))
poly = np.array(left + right[::-1], dtype=np.int32)
cv2.fillPoly(img, [poly], BODY, lineType=cv2.LINE_AA)

# vertical stave shading lines
for k in range(-3, 4):
    x = int(cx + k * (mid_w / 3.5))
    cv2.line(img, (x, int(cy - half_h * 0.9)), (x, int(cy + half_h * 0.9)),
             BODY_D, thickness=3, lineType=cv2.LINE_AA)

# steel hoops (top, upper-belly, lower-belly, bottom) as horizontal bands
for ty, bw in [(0.16, top_w * 1.02), (0.40, mid_w * 1.0), (0.60, mid_w * 1.0), (0.84, top_w * 1.02)]:
    y = int(cy - half_h + ty * 2 * half_h)
    cv2.line(img, (int(cx - bw), y), (int(cx + bw), y), HOOP, thickness=14, lineType=cv2.LINE_AA)

# silhouette outline
cv2.polylines(img, [poly], True, OUTLINE, thickness=8, lineType=cv2.LINE_AA)

out = os.path.join("media", "server", "images", "keg.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
cv2.imwrite(out, img)
print("wrote", out, img.shape)
