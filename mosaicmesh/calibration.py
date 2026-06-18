"""Physical-layout calibration: detect ArUco markers in an uploaded photo,
map each marker ID back to a client, compute per-screen quads + group
bounding boxes for perspective rendering.

The HTTP route handlers (generateAruco, calibrate) stay in server.py and
call into this module for the pure-math/CV operations.
"""
import numpy as np
import cv2 as cv


def order_points(pts):
    """Reduce a set of quad points (Nx1x2 or Nx2) to 4 corners [TL, TR, BR, BL]."""
    pts = np.array(pts, dtype="float64").reshape(-1, 2)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array([
        pts[np.argmin(s)],   # TL: smallest x+y
        pts[np.argmax(d)],   # TR: largest x-y
        pts[np.argmax(s)],   # BR: largest x+y
        pts[np.argmin(d)],   # BL: smallest x-y
    ], dtype="float32")


def _draw_fitted_label(image, text, marker_corners, color=(255, 0, 0),
                       font=cv.FONT_HERSHEY_SIMPLEX,
                       width_mult=1.5, gap_frac=0.15):
    """Draw `text` aligned to the marker's TL->TR edge -- i.e. in the same
    reading direction as the canvas's +x axis, regardless of how the iPad
    is oriented in the photo.

    Anchoring to the MARKER (not the screen quad) has two big wins:
      1. The marker's corners are detected directly from the photo with
         pattern-defined ordering, so they're robust even when band
         detection is poor or the screen quad is fiducial-only.
      2. The marker's TL->TR vector is the canvas's reading direction,
         so labels read the right way up on rotated panels (a 90deg-
         rotated iPad's label is rotated 90deg too -- looks correct
         from the panel's viewpoint).

    Position: just above the marker (outside the marker's top edge by
    gap_frac of the marker's height), centered on the TL->TR midpoint.
    Text is rotated to align with the TL->TR direction via warpAffine.

    Size: text width matches width_mult * marker edge length (default
    1.5x). Marker is rendered at 300px in canvas coords; the screen is
    typically 3-4x that on each side, so 1.5x-marker text reads as
    proportional without overflowing the screen edges in normal layouts.

    The text is rendered onto a small transparent-style buffer and warped
    in via cv.warpAffine. We use a single-channel mask to compose: the
    text writes only where the buffer is non-zero, leaving the photo
    untouched everywhere else."""
    mc = np.array(marker_corners, dtype="float32").reshape(4, 2)
    tl, tr = mc[0], mc[1]
    edge = tr - tl
    edge_len = float(np.linalg.norm(edge))
    if edge_len < 8:
        return
    # Reading direction (along TL->TR) and "up" relative to the marker
    # (out of the canvas, away from marker's center).
    dx, dy = edge / edge_len
    # "up" is perpendicular to edge, pointing away from the marker's
    # centroid (so text goes ABOVE the marker, not into it).
    centroid = mc.mean(axis=0)
    perp_a = np.array([-dy, dx])   # rotate edge 90deg CCW
    perp_b = np.array([dy, -dx])   # rotate edge 90deg CW
    # Pick whichever perp points AWAY from the marker centroid.
    tl_to_centroid = centroid - tl
    up = perp_a if float(np.dot(perp_a, tl_to_centroid)) < 0 else perp_b

    # Size the text to fit within width_mult * marker edge.
    target_w = edge_len * width_mult
    (tw1, th1), _ = cv.getTextSize(str(text), font, 1.0, 1)
    if tw1 <= 0 or th1 <= 0:
        return
    scale = target_w / tw1
    if scale < 0.3:
        return
    thickness = max(2, int(round(scale * 1.5)))
    (tw, th), baseline = cv.getTextSize(str(text), font, scale, thickness)

    # Render text into its own small buffer (BGR), then warpAffine it
    # into the main image at the rotated, translated position.
    pad = max(2, int(round(scale * 2)))
    buf_w = tw + 2 * pad
    buf_h = th + baseline + 2 * pad
    text_buf = np.zeros((buf_h, buf_w, 3), dtype=np.uint8)
    text_mask = np.zeros((buf_h, buf_w), dtype=np.uint8)
    # Baseline at (pad, pad + th); thickness drawn into both buffers.
    cv.putText(text_buf, str(text), (pad, pad + th), font, scale, color,
               thickness, cv.LINE_AA)
    cv.putText(text_mask, str(text), (pad, pad + th), font, scale, 255,
               thickness, cv.LINE_AA)

    # Place buffer in the image: TL of the BUFFER maps to a photo point
    # such that the BUFFER'S BOTTOM CENTRE is at the marker's top edge
    # midpoint, offset upward by gap_frac * edge_len. The buffer is
    # rotated so its X axis aligns with the marker's TL->TR direction.
    edge_mid = (tl + tr) / 2.0
    gap = edge_len * gap_frac
    # The text's "bottom centre" anchor in the photo (right at the gap
    # above the marker's TL->TR edge).
    photo_anchor = edge_mid + up * gap
    # Buffer-local point that should land at photo_anchor: (buf_w/2, buf_h - pad).
    # We define the affine M such that
    #   M @ (buf_w/2, buf_h - pad, 1) = photo_anchor
    # and M's linear part is rotation by angle theta = atan2(dy, dx).
    cos_t, sin_t = float(dx), float(dy)
    # Photo point of an offset (bx, by) from anchor: anchor + bx*[dx,dy] + by*(-up).
    # We need the affine that maps buffer coords (bx_, by_) -> photo coords.
    # bx, by relative to anchor = (bx_ - buf_w/2, by_ - (buf_h - pad)).
    # Photo coord = anchor + (bx_ - buf_w/2)*[dx,dy] + (by_ - (buf_h - pad))*[-up_x,-up_y]
    # In matrix form:
    #   [photo_x]   [ dx  -up_x ] [bx_]   [ tx ]
    #   [photo_y] = [ dy  -up_y ] [by_] + [ ty ]
    # where (tx, ty) = anchor - (buf_w/2)*[dx,dy] - (buf_h - pad)*(-up).
    ax, ay = float(photo_anchor[0]), float(photo_anchor[1])
    bcx, bcy = buf_w / 2.0, buf_h - pad
    tx_ = ax - bcx * dx - bcy * (-up[0])
    ty_ = ay - bcx * dy - bcy * (-up[1])
    M = np.array([[dx, -up[0], tx_],
                  [dy, -up[1], ty_]], dtype="float32")
    h, w = image.shape[:2]
    warped = cv.warpAffine(text_buf, M, (w, h), flags=cv.INTER_LINEAR,
                           borderValue=(0, 0, 0))
    warped_mask = cv.warpAffine(text_mask, M, (w, h), flags=cv.INTER_LINEAR,
                                borderValue=0)
    # Composite: image[mask>0] = warped[mask>0]. Use np.where on the mask.
    mask3 = warped_mask[:, :, None] > 0
    np.copyto(image, warped, where=mask3)


def group_bounding_box(quads):
    """Tight axis-aligned [x, y, w, h] enclosing all screen quads (photo coords)."""
    if not quads:
        return None
    allpts = np.concatenate([np.array(q, dtype="int32").reshape(-1, 2) for q in quads])
    x, y, w, h = cv.boundingRect(allpts)
    return [int(x), int(y), int(w), int(h)]


def reconstruct_screen_quad(marker_quad, cw, ch, marker_px=300):
    """Photo-space quad of the full screen, extrapolated from the centered,
    fixed-size ArUco marker (marker and screen are coplanar). marker_quad is
    [TL,TR,BR,BL] in photo px (ordered). Returns a (4,1,2) int32 array of the
    screen corners [TL,TR,BR,BL]."""
    cw = float(cw); ch = float(ch); h = marker_px / 2.0
    marker_canvas = np.array([
        [cw/2 - h, ch/2 - h], [cw/2 + h, ch/2 - h],
        [cw/2 + h, ch/2 + h], [cw/2 - h, ch/2 + h]], dtype="float32")
    dst = np.array(marker_quad, dtype="float32").reshape(4, 2)
    H = cv.getPerspectiveTransform(marker_canvas, dst)
    screen = np.array([[[0, 0]], [[cw, 0]], [[cw, ch]], [[0, ch]]], dtype="float32")
    return cv.perspectiveTransform(screen, H).astype("int32")


def _quad_box(contour):
    """Clean convex 4-corner box (minAreaRect) from any contour/quad, ordered."""
    pts = np.array(contour, dtype="float32").reshape(-1, 1, 2)
    return order_points(cv.boxPoints(cv.minAreaRect(pts)))


def _quad_iou(a, b):
    """Intersection-over-union of two convex quads (each (4,2) or (4,1,2)).

    PRECISE convex intersection via cv.intersectConvexConvex. Used by
    reconcile_screen_quad to decide whether a detected outer contour
    overlaps the marker quad well enough to trust as the screen border.

    NOTE: server.py defines a separate _quad_iou that uses an axis-aligned
    bounding-box approximation (fast path for _drop_overlapping). The two
    are intentionally different algorithms for different call sites; do
    not merge them.
    """
    a = np.array(a, dtype="float32").reshape(-1, 2)
    b = np.array(b, dtype="float32").reshape(-1, 2)
    inter, _ = cv.intersectConvexConvex(a, b)
    union = cv.contourArea(a) + cv.contourArea(b) - inter
    return float(inter / union) if union > 0 else 0.0


def _quad_aspect(quad):
    """Width / height of a quad's axis-aligned bounding rect. Used as an
    orientation-only signal -- aspect is invariant to translation, scale, and
    the band's well-known ~10-15% per-side inward shrink, so it's a more
    robust rotation detector than absolute IoU."""
    pts = np.array(quad, dtype="float32").reshape(-1, 1, 2)
    x, y, w, h = cv.boundingRect(pts.astype(np.int32))
    return float(w) / max(1.0, float(h))


def _aspect_in_marker_frame(quad, marker_corners):
    """Aspect ratio (width/height) of `quad` measured in the marker's local
    coordinate frame, after un-warping the marker's perspective.

    Why this is better than `_quad_aspect`: that function uses the quad's
    photo-frame AABB, which is *not* invariant to perspective tilt. A 2:1
    rectangle tilted 45deg in photo has an AABB aspect of 1:1 -- the
    orientation info has been erased by the bounding-rect operation.

    This function computes the homography from photo coords back to the
    marker's intrinsic 300x300 frame (centered at origin), applies it to
    the quad's corners, then measures the quad's extent in that flat
    rectified frame. The marker is coplanar with the screen (both are
    rendered on the same canvas), so the rectification that flattens the
    marker also flattens the screen -- giving the screen's true aspect
    as if you were looking straight at it.

    Use this for "does the band match the reported canvas aspect?" -- a
    direct comparison of ratios in the marker frame, no perspective bias."""
    mp = np.array(marker_corners, dtype="float32").reshape(4, 2)
    # Marker's intrinsic frame: 300x300 square centered at origin.
    h = 150.0
    mc = np.array([[-h, -h], [h, -h], [h, h], [-h, h]], dtype="float32")
    # Homography from photo back to marker frame.
    H = cv.getPerspectiveTransform(mp, mc)
    pts = np.array(quad, dtype="float32").reshape(-1, 1, 2)
    in_marker = cv.perspectiveTransform(pts, H).reshape(-1, 2)
    xs = in_marker[:, 0]
    ys = in_marker[:, 1]
    width = float(xs.max() - xs.min())
    height = float(ys.max() - ys.min())
    if height < 1e-6:
        return 1.0
    return width / height


def reconcile_screen_quad(marker_quad, border_contour, cw, ch, marker_px=300, min_iou=0.5):
    """Choose the screen quad. The marker-derived fiducial is ALWAYS the output
    geometry; the detected band is used purely to VALIDATE the fiducial and
    to detect a stale mobile auto-rotation.

    Why not use the band as output (a previous attempt): on iPad-1 calibrate
    pages with an 8px CSS border and the iPad's own plastic bezel, the bright-
    region threshold detects the white *interior* of the screen, not the
    screen edge -- the bezel + border + JPEG edge blur shrink the detected
    contour by ~10-15% per side from the true panel edge. The fiducial
    extrapolates from the marker to the full canvas (which equals the html
    element extent = the panel) and is correct by construction; substituting
    band geometry made every screen render too small.

    Rotation detection: we want to catch the case where the iPad reported its
    canvas dims in one orientation but was photographed in the other (the
    canvas-resize event didn't make it to the server before calibration).
    Two independent signals decide:
      - IoU comparison: the swapped fiducial (cw<->ch) is closer to the band.
        High specificity but requires a usable band AND enough orientation
        difference to push IoU above min_iou.
      - Aspect comparison: the band's bounding-rect aspect is closer to the
        swapped fiducial's aspect than to the native fiducial's. This is
        invariant to the band's ~10-15% inward shrink (shrink affects width
        and height proportionally), so it works even when band IoU is below
        min_iou -- which is the common case for one-off rotated screens
        whose band is partially occluded or poorly thresholded.

    Returns (quad (4,1,2) int32, source) where source is one of:
      'fiducial'    -- fiducial, band-validated (high confidence)
      'rotated'     -- swapped-orientation fiducial (band confirmed rotation)
      'unverified'  -- fiducial, band didn't validate either orientation
                       (band may be noisy/degenerate; fiducial still trusted)
      'no-band'     -- fiducial, no band quad was provided to validate against
                       (the bright-region pipeline produced no quad for this
                       marker -- typically dim/glare iPads)"""
    fid = reconstruct_screen_quad(marker_quad, cw, ch, marker_px)
    fid_sw = reconstruct_screen_quad(marker_quad, ch, cw, marker_px)
    # Need a usable, non-degenerate band box to validate against.
    box = None
    if border_contour is not None and len(np.array(border_contour).reshape(-1, 2)) >= 3:
        b = _quad_box(border_contour)
        if cv.contourArea(b.astype("float32").reshape(-1, 1, 2)) > 0:
            box = b
    if box is None:
        return fid, "no-band"
    iou = _quad_iou(fid, box)
    iou_sw = _quad_iou(fid_sw, box)
    # Aspect comparison in the MARKER'S frame after perspective un-warp.
    # Both the marker and the screen are coplanar (rendered on the same
    # canvas) so the rectification that flattens the marker also flattens
    # the band, giving the band's true aspect as if seen straight-on.
    # The fiducials' aspect in marker frame IS cw/ch by construction.
    ba = _aspect_in_marker_frame(box, marker_quad)
    fa = float(cw) / max(1.0, float(ch))
    fa_sw = float(ch) / max(1.0, float(cw))

    # AUTO-SWAP CRITERIA: only swap when evidence is OVERWHELMING. Real
    # fleet photos have intra-screen brightness gradients that can pull
    # the band's measured aspect toward 1.0 (square), and a "barely
    # closer to swap than to native" heuristic over-fires on those.
    # Tight criteria below default to KEEP (trust the iPad's reported
    # canvas dims) unless every signal agrees:
    #   (a) band aspect is within 0.15 log units of the swap aspect
    #       (i.e., band shape matches swap shape within ~15%)
    #   (b) band aspect is at least 0.35 log units away from the native
    #       aspect (i.e., band is decisively NOT the reported shape)
    #   (c) IoU with the swapped fiducial corroborates: swapped IoU
    #       beats native IoU by at least 1.5x AND is >= min_iou
    # If any fails, keep the iPad's reported orientation; the user can
    # manually swap via the swap_orientation admin action if needed.
    log_ba = float(np.log(ba))
    log_native = float(np.log(fa))
    log_swap = float(np.log(fa_sw))
    aspect_matches_swap = abs(log_ba - log_swap) < 0.15
    aspect_far_from_native = abs(log_ba - log_native) > 0.35
    iou_corroborates_swap = (iou_sw >= min_iou and
                              iou_sw >= iou * 1.5)   # "at least 1.5x" per the criteria above
    if aspect_matches_swap and aspect_far_from_native and iou_corroborates_swap:
        return fid_sw, "rotated"
    # Distinguish "we checked and it agreed with reported" from "we couldn't
    # decide". Useful in the visualisation: green = checked, yellow = ambiguous.
    if iou >= min_iou and abs(log_ba - log_native) < 0.20:
        return fid, "fiducial"
    return fid, "unverified"


def _render_output_dims(client):
    """Per-screen render output size: the canvas/viewport ASPECT (true shape and
    orientation), scaled to FIT WITHIN the device's reported screen resolution so
    it stays displayable AND decodable on the panel — a 1st-gen iPad's H.264
    decoder maxes near its 768x1024 screen, and the viewport can't exceed the
    screen anyway. Returns even (w, h) for libx264."""
    aw = int(getattr(client, "canvasWidth", 0) or client.deviceWidth) or 1
    ah = int(getattr(client, "canvasHeight", 0) or client.deviceHeight) or 1
    dw = int(getattr(client, "deviceWidth", 0) or 0)
    dh = int(getattr(client, "deviceHeight", 0) or 0)
    if dw and dh:
        s = min(1.0, dw / float(aw), dh / float(ah))
        aw = int(round(aw * s)); ah = int(round(ah * s))
    return max(2, aw - aw % 2), max(2, ah - ah % 2)


def warp_image_for_screen(source_img, bbox, screen_quad, out_w, out_h):
    """Warp the region of source_img under a screen's quad onto that screen's
    pixel rect. bbox is the [x, y, w, h] region of the photo that the source image is stretched to fill
    (the group bbox for SEGMENT, the screen's own quad bbox for INDIVIDUAL); the full image is
    stretched to fill bbox, so the screen quad (photo coords) maps back into
    media coords, then a homography fits it to out_w x out_h."""
    h, w = source_img.shape[:2]
    bx, by, bw, bh = bbox
    # Use the quad in its STORED order (screen TL,TR,BR,BL from the marker), not a
    # geometric re-sort — otherwise a non-upright panel (e.g. 180°-mounted) flips.
    ordered = np.array(screen_quad, dtype="float32").reshape(-1, 2)
    src = np.array([[(px - bx) / bw * w, (py - by) / bh * h] for (px, py) in ordered], dtype="float32")
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype="float32")
    m = cv.getPerspectiveTransform(src, dst)
    return cv.warpPerspective(source_img, m, (out_w, out_h))


def _hex_to_bgr(hexstr):
    """'#rrggbb' -> OpenCV (B, G, R) tuple; falls back to black."""
    h = (hexstr or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def letterbox_to_aspect(img, target_w, target_h, bg_bgr):
    """Scale img to fit within target_w x target_h preserving aspect, centered
    on a solid bg_bgr canvas of exactly that size."""
    target_w = max(1, int(target_w)); target_h = max(1, int(target_h))
    h, w = img.shape[:2]
    scale = min(target_w / float(w), target_h / float(h))
    nw = max(1, int(round(w * scale))); nh = max(1, int(round(h * scale)))
    resized = cv.resize(img, (nw, nh))
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    canvas[:] = bg_bgr
    x = (target_w - nw) // 2; y = (target_h - nh) // 2
    canvas[y:y + nh, x:x + nw] = resized
    return canvas


def assign_group_bounding_boxes():
    """Per display group, set boundingBox/boundingBoxCenter from the ArUco
    screens' quads (photo coords). Call after calibration."""
    import server
    groups = {}
    for key, client in server.settings.clients.items():
        if client.measuredPerimeter is not None and client.displayID:
            groups.setdefault(client.displayID, []).append(client.measuredPerimeter)
    for display_id, quads in groups.items():
        display = server.settings.displays.setdefault(display_id, server.Display())
        bbox = group_bounding_box(quads)
        display.boundingBox = bbox
        if bbox:
            display.boundingBoxCenter = [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2]


def _group_clients(display_id):
    """Sorted [(clientKey, client)] for clients assigned to a display group."""
    import server
    return sorted([(k, c) for k, c in server.settings.clients.items() if c.displayID == display_id])


def find_squares(img):
    # Optimize: Convert to grayscale once instead of processing all channels
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    gray = cv.GaussianBlur(gray, (5, 5), 0)
    squares = []

    # Optimize: Reduce threshold iterations and use more efficient range
    for thrs in range(0, 255, 52):  # Reduced iterations from 10 to 5
        if thrs == 0:
            bin = cv.Canny(gray, 0, 50, apertureSize=5)
            bin = cv.dilate(bin, None)
        else:
            _retval, bin = cv.threshold(gray, thrs, 255, cv.THRESH_BINARY)

        contours, _hierarchy = cv.findContours(bin, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            # Early area check to avoid expensive operations on small contours
            area = cv.contourArea(cnt)
            if area < 1000:
                continue

            cnt_len = cv.arcLength(cnt, True)
            cnt = cv.approxPolyDP(cnt, 0.02*cnt_len, True)
            if len(cnt) == 4 and cv.isContourConvex(cnt):
                cnt = cnt.reshape(-1, 2)
                max_cos = np.max([angle_cos( cnt[i], cnt[(i+1) % 4], cnt[(i+2) % 4] ) for i in range(4)])
                if max_cos < 0.1:
                    squares.append(cnt)
    return squares


def angle_cos(p0, p1, p2):
    d1, d2 = (p0-p1).astype('float'), (p2-p1).astype('float')
    return abs( np.dot(d1, d2) / np.sqrt( np.dot(d1, d1)*np.dot(d2, d2) ) )
