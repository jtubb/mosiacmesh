"""Render a simple round cork/cardboard beer-coaster PNG (transparent) as the
default back-face sprite for the coasterflip transition. Output:
media/server/images/coaster.png (BGRA, 1000x1000, ~1MP -> iPad-1 safe).
Replace with real art later via the effect's `sprite` param."""
import os, numpy as np, cv2
S = 1000
img = np.zeros((S, S, 4), dtype=np.uint8)
cx = cy = S / 2.0
R = S * 0.47
CORK = (110, 170, 205, 255)     # BGRA cork tan
CORK_D = (80, 130, 165, 255)
RIM = (60, 95, 125, 255)
INK = (50, 75, 100, 255)
# body disc
cv2.circle(img, (int(cx), int(cy)), int(R), CORK, -1, lineType=cv2.LINE_AA)
# cork speckles (seeded, deterministic)
rng = np.random.RandomState(7)
for _ in range(900):
    a = rng.rand() * 2 * np.pi; rr = R * 0.95 * np.sqrt(rng.rand())
    x = int(cx + np.cos(a) * rr); y = int(cy + np.sin(a) * rr)
    sp = rng.randint(2, 6)
    cv2.circle(img, (x, y), sp, CORK_D, -1, lineType=cv2.LINE_AA)
# rim rings
cv2.circle(img, (int(cx), int(cy)), int(R), RIM, max(4, int(S * 0.018)), lineType=cv2.LINE_AA)
cv2.circle(img, (int(cx), int(cy)), int(R * 0.82), INK, max(2, int(S * 0.006)), lineType=cv2.LINE_AA)
# a simple foam-mug glyph in the center ring (so the back reads as a beer coaster)
gx, gy, gw, gh = cx - S * 0.13, cy - S * 0.16, S * 0.26, S * 0.30
cv2.rectangle(img, (int(gx), int(gy + gh * 0.18)), (int(gx + gw), int(gy + gh)), INK, max(2, int(S * 0.012)), lineType=cv2.LINE_AA)
cv2.ellipse(img, (int(gx + gw / 2), int(gy + gh * 0.18)), (int(gw / 2), int(gh * 0.14)), 0, 180, 360, INK, max(2, int(S * 0.012)), lineType=cv2.LINE_AA)
out = os.path.join("media", "server", "images", "coaster.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
cv2.imwrite(out, img)
a = cv2.imread(out, cv2.IMREAD_UNCHANGED)
print("wrote", out, a.shape, "MP=%.2f" % (a.shape[0] * a.shape[1] / 1e6), "alpha", int(a[:, :, 3].min()), int(a[:, :, 3].max()))
