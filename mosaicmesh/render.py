"""ffmpeg-driven render pipeline: per-screen perspective warp, segment
slicing, video mosaic encoding, plus the playback orchestration
(PRELOAD/PLAY, preparation barriers, push concurrency).

This is the largest module in the mosaicmesh package. It owns the
encoding pipeline (ffmpeg builder + executor), the playback state
machine (_start_group_playback / _stop_group_playback), and the
coordinated-start preparation barrier (_begin_prepare /
_prepare_unsynced_clients).

The HTTP route handlers that trigger renders (e.g. /play, /stop)
stay in server.py and call into this module.
"""
import os
import re as _re_seg
import asyncio
import logging
import hashlib
import random
import time
import uuid
from pathlib import Path

import cv2 as cv
import numpy as np

import jsonpickle
import effects

from mosaicmesh.state import (
    Display, PlayState, PlayMode, MediaElement,
)
from mosaicmesh.broadcast import (
    broadcast_to_client, broadcast_to_display_group,
)
from mosaicmesh import calibration
from mosaicmesh.calibration import (
    _render_output_dims, warp_image_for_screen,
    _hex_to_bgr, letterbox_to_aspect,
    _group_clients,
)

# ---------------------------------------------------------------------------
# Render encode note: segments use plain libx264 Constrained Baseline + CRF
# (NO VBV -maxrate/-bufsize, which injects HRD into the SPS that iOS-5 /
# Chrome-29 UIWebView reject with MEDIA_ERR_SRC_NOT_SUPPORTED), plus a
# REGULAR keyframe grid every KEYFRAME_GRID_SEC. iOS-5 seeks
# keyframe-accurately (currentTime snaps to a keyframe), so x264's default
# ragged scene-cut keyframes (1-10s apart) made mid-clip drift-correction
# snap unpredictably far. A fixed grid lets every client seek to the SAME
# grid keyframe (shared GoTime clock + shared grid => mutual sync).
# All-intra (-g 1) is still avoided: it blew the bitrate past the iPad-1
# decoder. Denser grid => smaller snap: the iPad seek lands within
# +-KEYFRAME_GRID_SEC/2 of the clock, so a tighter grid both reduces the
# residual AND its run-to-run spread.
KEYFRAME_GRID_SEC = 0.25

# Fallback window for an item with no explicit duration ("Auto") whose
# natural length can't be resolved server-side (image, animation, or a
# video that hasn't been probed). Synchronized playback needs a concrete
# positive window upfront — a 0-ms window silently skips the item on the
# wall — so we never emit 0 for a real item.
DEFAULT_ITEM_DURATION_S = 20

# Video encoder + concurrency configuration. Render time on a 24-iPad fleet
# was previously: 24 sequential ffmpeg invocations, each a software (libx264)
# encode on one CPU. With a modern GPU's hardware encoder (NVENC on NVIDIA,
# QSV on Intel iGPU, AMF on AMD) and bounded asyncio.gather concurrency,
# the same render runs ~20-50x faster end-to-end (NVENC alone is 5-10x per
# file; concurrency cuts the wall time by another ~4-8x on a 24-thread box).
#
# Both knobs are env-var overridable so an operator on a CPU-only machine
# (or one whose driver session limit differs from ours) can adjust without
# editing source. Defaults assume a single decent NVIDIA GPU.
#
#   MMRENDER_ENCODER:  h264_nvenc (default) | h264_qsv | h264_amf | libx264
#   MMRENDER_CONCURRENCY: max parallel ffmpegs (default 6; NVENC consumer
#                        sessions are typically capped at 8, headroom keeps
#                        other concurrent work from being starved)
#
# Default = libx264 (CPU). Why not h264_nvenc despite the 5-10x perf win:
# iPad-1's H.264 baseline decoder rejects NVENC's SPS/SEI output (decoder
# parses ~65KB of the file, sees SEI NAL types it doesn't handle, fires
# MEDIA_ERR_SRC_NOT_SUPPORTED / vid:abort, and stops fetching). libx264's
# baseline SPS is iPad-1-compatible. For fleets running iPad-2 or newer,
# set MMRENDER_ENCODER=h264_nvenc to opt back in to GPU encoding.
_VIDEO_ENCODER = os.environ.get("MMRENDER_ENCODER") or "libx264"
_RENDER_CONCURRENCY = int(os.environ.get("MMRENDER_CONCURRENCY") or 6)

# Per-playlist render lifecycle states (one entry per playlistName; registry lives on the owning Display).
RENDER_QUEUED = "QUEUED"        # enqueued, not yet started
RENDER_RENDERING = "RENDERING"  # ffmpeg in flight
RENDER_READY = "READY"          # assets on disk + token current
RENDER_STALE = "STALE"          # was READY, inputs changed (recalibrate/edit)
RENDER_FAILED = "FAILED"        # ffmpeg errored; needs manual Retry

# Default OFF after empirical regression: enabling -hwaccel cuda with 12
# concurrent NVENC encodes ran the test fleet (24 iPads) at 397s vs 322s
# without. The PCIe round-trip (GPU decode -> CPU filter chain (no CUDA
# equivalent of `perspective`) -> GPU encode) + GPU memory contention at
# high concurrency outweighed the CPU decode savings on iPad-sized
# output. Worth re-enabling for 4K/high-bitrate sources where CPU decode
# is the real bottleneck. Override:
#   $env:MMRENDER_HWACCEL = "cuda"   (or "qsv", "d3d11va")
_VIDEO_HWACCEL = os.environ.get("MMRENDER_HWACCEL") or ""

# Cap on parallel cache-push scps to the iPad fleet. The cache is meant
# to AVOID WiFi saturation at PLAY time, but if we fire 24 parallel
# scps right after a render we saturate the same AP and every push
# times out. With 24 contending streams the per-iPad rate dropped to
# ~100 KB/s (~2.4 MB/s aggregate, all going to one AP). MMPUSH_
# CONCURRENCY=2 keeps each push at ~LAN line rate and lets a fresh
# render's 24x100MB push fan-out complete in ~5-10 min total instead
# of all timing out.
_PUSH_CONCURRENCY = int(os.environ.get("MMPUSH_CONCURRENCY") or 2)

# Lazy module-level semaphore (created on first use, when an event
# loop is guaranteed to exist; we don't want to bind it to whatever
# loop happened to be current at import time).
_push_sem = None

# Recognized video source extensions. SEGMENT/INDIVIDUAL items are transcoded
# to .mp4 by ffmpeg regardless of source; FULL items play directly in the
# browser (.mp4/.webm/.m4v are broadly playable, .mov needs h264/Safari/Chrome).
_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".ogv", ".mkv")

# seg_<HASH>_<N>.mp4 filename pattern. seg_hash is hex; seg_n is a decimal
# integer. The pattern is anchored on basename so it matches whether item.file
# is a bare filename or a /media/<key>/... path or a full http://server/...
# URL.
_SEG_FILE_RE = _re_seg.compile(r"seg_([a-f0-9]+)_(\d+)\.mp4$")


# ---------------------------------------------------------------------------
# ffmpeg arg builders (pure functions — no server state access)
# ---------------------------------------------------------------------------

def _keyframe_grid_args():
    """ffmpeg args for a regular keyframe grid: force a keyframe every
    KEYFRAME_GRID_SEC of OUTPUT time (fps-independent). Encoder-independent;
    the scene-cut-disable flag is encoder-specific and lives in
    _video_encoder_args() below."""
    return ["-force_key_frames", "expr:gte(t,n_forced*%s)" % KEYFRAME_GRID_SEC]


def _video_input_args():
    """ffmpeg input-option args (go BEFORE -i). If MMRENDER_HWACCEL is set,
    emit `-hwaccel <value>` so the source video is decoded on the GPU.
    Default is OFF (CPU decode) -- see _VIDEO_HWACCEL comment for the
    empirical reasoning."""
    if _VIDEO_HWACCEL:
        return ["-hwaccel", _VIDEO_HWACCEL]
    return []


def _video_encoder_args():
    """Return ffmpeg encoder + preset args for the configured encoder, plus
    the encoder-appropriate "no scene-cut keyframes" flag (keyframe grid
    spacing must be uniform for client-side seek alignment, so any extra
    scene-detection keyframes break the grid).

    All encoder configs target iPad-1 compatible H.264 baseline @ ~CRF 23
    quality; this works for NVENC, QSV, AMF, and libx264.
    """
    enc = _VIDEO_ENCODER
    if enc == "h264_nvenc":
        # NVENC preset names: p1 (fastest) -> p7 (slowest). p4 ~= libx264
        # 'fast'. -rc vbr + -cq 23 mimics libx264 -crf 23 (constant quality
        # rate-control). -no-scenecut 1 disables scene-detection keyframes.
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                "-cq", "23", "-no-scenecut", "1"]
    if enc == "h264_qsv":
        # Intel Quick Sync (integrated GPU). -global_quality is the QSV
        # equivalent of CRF. No-scenecut not exposed; QSV's keyframe
        # behaviour respects -force_key_frames.
        return ["-c:v", "h264_qsv", "-preset", "veryfast",
                "-global_quality", "23"]
    if enc == "h264_amf":
        # AMD AMF (discrete or APU). -quality speed = fastest preset.
        # -rc cqp + -qp_i/-qp_p = constant quality.
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp",
                "-qp_i", "23", "-qp_p", "23"]
    # Fallback: software libx264. -x264-params scenecut=0 is libx264-
    # specific syntax for the same "no scene-cut keyframes" intent.
    return ["-c:v", "libx264", "-preset", "veryfast",
            "-x264-params", "scenecut=0"]


# Largest frame iPad-1 (iOS 5 / WebKit 534) reliably decodes: ~720p H.264
# Constrained Baseline. FULL-mode media is transcoded/downscaled to fit within
# this so raw source is never served to the wall.
DEVICE_DECODE_CAP = (1280, 720)


def _fit_within(src_w, src_h, cap):
    """Scale (src_w, src_h) to fit within cap=(W,H) preserving aspect, never
    upscaling. Returns even integer dims (H.264 requires even W/H)."""
    cw, ch = cap
    sw, sh = int(src_w or 0), int(src_h or 0)
    if sw <= 0 or sh <= 0:
        return (cw, ch)
    scale = min(cw / sw, ch / sh, 1.0)   # 1.0 cap → never upscale
    w = max(2, int(sw * scale)); h = max(2, int(sh * scale))
    if w % 2: w -= 1
    if h % 2: h -= 1
    return (w, h)


def _get_push_sem():
    """Return the module-level push semaphore, creating it on first
    use inside the running event loop. Safe to call from any
    coroutine; not safe to call before any loop has started."""
    global _push_sem
    if _push_sem is None:
        _push_sem = asyncio.Semaphore(_PUSH_CONCURRENCY)
    return _push_sem


# ---------------------------------------------------------------------------
# Media helpers (pure / nearly-pure)
# ---------------------------------------------------------------------------

def isVideoItem(file):
    """True if a media file is a video, mirroring the client's isVideoItem.
    Tolerates a trailing ?query."""
    return str(file or "").lower().split("?")[0].endswith(_VIDEO_EXTS)


def quad_to_source_points(bbox, screen_quad, src_w, src_h):
    """Corners of the screen's quad expressed in source media pixel coords (the
    source is stretched to fill the group bbox). Uses the quad in its STORED
    order — reconstruct_screen_quad emits screen [TL,TR,BR,BL] in the panel's own
    orientation (from the marker). Re-ordering geometrically would discard that
    and flip a non-upright panel (e.g. a 180°-mounted screen)."""
    bx, by, bw, bh = bbox
    pts = np.array(screen_quad, dtype="float32").reshape(-1, 2)
    return [[(float(px) - bx) / bw * src_w, (float(py) - by) / bh * src_h] for (px, py) in pts]


def build_ffmpeg_perspective_cmd(src_path, out_path, src_points, out_w, out_h,
                                 extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg arg list: perspective-warp the source quad to fill the frame, scale
    to the screen resolution, encode iPad-compatible H.264 + AAC audio.
    src_points is [TL, TR, BR, BL]; ffmpeg's perspective wants TL, TR, BL, BR.
    extra_video_filters append to -vf; extra_audio_filters add an -af when present."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = persp + ",scale=" + str(out_w) + ":" + str(out_h)
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y"] + _video_input_args() + ["-i", src_path, "-vf", vf]
    cmd += _video_encoder_args()
    cmd += ["-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += _keyframe_grid_args()
    cmd += ["-movflags", "+faststart", out_path]
    return cmd


def build_ffmpeg_individual_cmd(src_path, out_path, src_points, out_w, out_h,
                                pad_w, pad_h, pad_x, pad_y, bg_hex,
                                extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg args for INDIVIDUAL: pad the source to the screen bbox aspect with
    backgroundColor, perspective-warp the whole padded frame to the screen quad,
    scale to the device resolution. src_points is [TL, TR, BR, BL]."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    _h = (bg_hex or "#000000").lstrip("#")
    if len(_h) != 6:
        _h = "000000"
    hexcol = "0x" + _h
    pad = ("pad=" + str(int(pad_w)) + ":" + str(int(pad_h)) + ":" +
           str(int(pad_x)) + ":" + str(int(pad_y)) + ":color=" + hexcol)
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = pad + "," + persp + ",scale=" + str(out_w) + ":" + str(out_h)
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y"] + _video_input_args() + ["-i", src_path, "-vf", vf]
    cmd += _video_encoder_args()
    cmd += ["-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += _keyframe_grid_args()
    cmd += ["-movflags", "+faststart", out_path]
    return cmd


def build_ffmpeg_transcode_cmd(src_path, out_path, out_w, out_h,
                               extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg args for FULL (mirror): scale the source to fit out_w x out_h
    preserving aspect, letterbox-pad to exactly out_w x out_h, encode iPad-1
    Constrained Baseline H.264 + AAC. No perspective warp. Mirrors the encode
    conventions of build_ffmpeg_perspective_cmd."""
    vf = ("scale=" + str(out_w) + ":" + str(out_h) +
          ":force_original_aspect_ratio=decrease," +
          "pad=" + str(out_w) + ":" + str(out_h) + ":(ow-iw)/2:(oh-ih)/2:color=0x000000")
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y"] + _video_input_args() + ["-i", src_path, "-vf", vf]
    cmd += _video_encoder_args()
    cmd += ["-profile:v", "baseline", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += _keyframe_grid_args()
    cmd += ["-movflags", "+faststart", out_path]
    return cmd


def get_video_dimensions(path):
    """Return (width, height) of a video via OpenCV, or None if unreadable."""
    cap = cv.VideoCapture(path)
    try:
        w = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if w <= 0 or h <= 0:
        return None
    return (w, h)


def resolve_media_path(file_url):
    """Map a media URL ('/media/<client>/<name>') to its on-disk path, matching
    media_handler's convention (images/ or videos/ by extension)."""
    parts = file_url.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "media":
        return None
    client = parts[1]
    name = parts[-1]
    subdir = "videos" if isVideoItem(name) else "images"
    return os.path.join("media", client, subdir, name)


# ---------------------------------------------------------------------------
# Render orchestration helpers (access server.settings / effects)
# ---------------------------------------------------------------------------

def _mint_play_seed():
    """A fresh 32-bit run seed for coordinated animation randomness. Wrapped so
    tests can monkeypatch it. Avoids 0 so MM_RNG never hits its default branch."""
    return random.getrandbits(32) or 0x1


def render_token(media_elements, display_id):
    """Stable hash of the inputs that affect a per-screen render (SEGMENT or
    INDIVIDUAL) for a GIVEN set of media elements against a group's calibration:
    the items, the group bounding box, and each client's resolution + measured
    quad. Generalizes the old compute_render_token so a token can be computed
    for any playlist (not just the one currently applied to the group)."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return ""
    items = []
    for me in media_elements:
        pm = me.playmode.name if hasattr(me.playmode, "name") else str(me.playmode)
        items.append((me.id, me.file, me.duration, pm,
                      getattr(me, "backgroundColor", "#000000"),
                      _audio_fade_sig(getattr(me, "startEffect", None)),
                      _audio_fade_sig(getattr(me, "endEffect", None))))
    clients = []
    for key, c in _group_clients(display_id):
        perim = None
        if c.measuredPerimeter is not None:
            perim = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2).tolist()
        clients.append((key, c.deviceWidth, c.deviceHeight, perim))
    # Bump this when the encode settings change, to invalidate stale renders.
    # v6: encoder default reverted libx264 (NVENC SPS rejected by iPad-1).
    encode_ver = "grid025-cbl-v6"
    raw = repr((items, display.boundingBox, clients, encode_ver))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def compute_render_token(display_id):
    """Token for the playlist CURRENTLY applied to the group (display.mediaElements).
    Thin wrapper over render_token — preserves the historical call site/byte-form
    so existing Display.renderedToken values stay valid."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return ""
    return render_token(display.mediaElements, display_id)


def _broadcast_render_status(display_id, status):
    """Fan a RENDER_STATUS notification out to every connected client so
    the admin UI's render-progress indicators (and any other listeners)
    can update. No-ops if socketmanager hasn't been wired yet — happens
    during module import in tests."""
    import server
    if server.socketmanager is not None:
        server.socketmanager.broadcast(jsonpickle.encode(
            {"REQUEST": "RENDER_STATUS", "PAYLOAD": {"displayID": display_id, "status": status}}))


def _is_renderable(me):
    """SEGMENT, INDIVIDUAL, and FULL all require a server-side encode for the
    device (per-screen warp for SEGMENT/INDIVIDUAL; a shared device transcode/
    downscale for FULL). SCRIPT (animations) and bare DEFAULT do not render."""
    return me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL, PlayMode.FULL)


def _needs_per_client_delivery(elements):
    """True if PLAY/PREPARE must go PER-CLIENT, not group-wide. Two cases need
    per-client payloads: any renderable item (each client gets its own warped
    media URL) OR any MESH SCRIPT animation (each client needs its own meshQuad,
    attached by _per_client_items). A group-wide broadcast can't carry per-client
    geometry, so a mesh animation sent group-wide arrives with no meshQuad and
    the client paints black — hence a SCRIPT-only mesh playlist must still take
    the per-client path even though it has nothing to render."""
    for me in elements:
        if _is_renderable(me):
            return True
        if me.playmode == PlayMode.SCRIPT and getattr(me, "scriptSpan", "mirror") == "mesh":
            return True
    return False


def _set_render_state(display, playlist_name, state, token=None, error=None,
                      percent=None, eta=None, started=None):
    """Single writer for a Display.renders[name] entry. Creates the entry if
    absent, patches only the provided fields, stamps updatedAt. Returns the entry."""
    reg = getattr(display, "renders", None)
    if reg is None:
        reg = display.renders = {}
    entry = reg.get(playlist_name) or {}
    entry["state"] = state
    if token is not None:
        entry["token"] = token
    entry["error"] = error
    if percent is not None:
        entry["percent"] = percent
    if eta is not None:
        entry["eta"] = eta
    if started is not None:
        entry["startedAt"] = started
    entry["updatedAt"] = time.time()
    reg[playlist_name] = entry
    return entry


def is_playlist_ready(playlist_name, display_id):
    """True if (playlist, group) needs no render (N/A — no renderable items) OR
    has a READY registry entry whose token matches the playlist's current
    render_token for that group. Used by every assignment/play/schedule gate."""
    import server
    pl = server.settings.playlists.get(playlist_name)
    display = server.settings.displays.get(display_id)
    if pl is None or display is None:
        return False
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        return True  # N/A — always assignable/playable
    entry = (getattr(display, "renders", {}) or {}).get(playlist_name)
    if not entry:
        return False
    return (entry.get("state") == RENDER_READY
            and entry.get("token") == render_token(elements, display_id))


# Strict filename pattern for rendered-asset GC.  Only matches files written by
# the render pipeline (seg_/ind_/full_ + 12-hex token + index + extension).
# Uploaded source media (e.g. "myvideo.mp4") and ArUco markers ("aruco.png")
# never match, so the sweep can never touch them.
_RENDER_ASSET_RE = _re_seg.compile(r"^(?:seg|ind|full)_([0-9a-f]{12})_\d+\.(?:mp4|png)$")


def _token_is_live(token):
    """True if `token` is still referenced anywhere: any group's render-registry
    entry token, or any group's live renderedToken. The single guard that makes
    asset deletion safe against the shared-token case (identical-item playlists
    on one group hash to the same token and share files)."""
    import server
    if not token:
        return False
    for display in server.settings.displays.values():
        for e in (getattr(display, "renders", {}) or {}).values():
            if e.get("token") == token:
                return True
        if getattr(display, "renderedToken", "") == token:
            return True
    return False


def _normalize_effect(field):
    """Tolerate an effect field as {name, params} | bare-string name | None.
    Legacy 'audiofade' (now folded into the audioFade toggle on fade/wipe) ->
    a fade with audio on, so its baked afade is preserved on re-render. The
    visual half is moot: the client receives the raw stored value and a legacy
    string has no duration, so it shows no visual transition."""
    if not field:
        return None
    if isinstance(field, str):
        if field == "audiofade":
            return {"name": "fade", "params": {"audioFade": True}}
        return {"name": field, "params": {}}
    if isinstance(field, dict) and field.get("name"):
        return field
    return None


def _audio_fade_sig(field):
    """Token contribution for an effect field: ("afade", duration) only when the
    effect bakes an audio fade (audioFade truthy), else None. Visual params
    (type, direction, scope, and duration when audioFade is off) are deliberately
    excluded so client-side visual edits don't invalidate the cached render."""
    spec = _normalize_effect(field)
    if not spec:
        return None
    p = spec.get("params") or {}
    audio_on = p.get("audioFade", effects.effect_audio_fade_default(spec.get("name")))
    if not audio_on:
        return None
    return ("afade", p.get("duration", 600))


def _resolve_effect_filters(me, duration_ms, out_w, out_h):
    """Collect (video_fragments, audio_fragments) for an item's start/end effects."""
    vfs, afs = [], []
    ctx = {"duration_ms": duration_ms, "out_w": out_w, "out_h": out_h}
    for role, field in (("start", getattr(me, "startEffect", None)),
                        ("end", getattr(me, "endEffect", None))):
        spec = _normalize_effect(field)
        if not spec:
            continue
        eff = effects.get_effect(spec.get("name"))
        if eff is None:
            continue
        v, a = eff.video_filters(role, eff.resolve(spec.get("params")), ctx)
        vfs += v
        afs += a
    return vfs, afs


def _parse_ffmpeg_progress_line(line):
    """Parse one ffmpeg `-progress` key=value line. Returns (key, value) where
    value is int for numeric keys, else the raw string; None for non key=value
    lines. Pure — unit-tested without ffmpeg."""
    line = (line or "").strip()
    if "=" not in line:
        return None
    k, _, v = line.partition("=")
    k = k.strip(); v = v.strip()
    if not k:
        return None
    if v.lstrip("-").isdigit():
        return (k, int(v))
    return (k, v)


async def _run_ffmpeg(cmd, label, semaphore):
    """Run one ffmpeg command under the concurrency semaphore. Logs and
    raises with the last few lines of stderr on non-zero exit -- ffmpeg's
    final lines are where the actual error message lives (everything
    before is progress noise)."""
    async with semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, _err = await proc.communicate()
        if proc.returncode != 0:
            tail = (_err or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
            logging.error("ffmpeg rc=%s (%s) cmd=%s\n  %s",
                          proc.returncode, label, " ".join(cmd), "\n  ".join(tail))
            raise RuntimeError("ffmpeg failed for " + label +
                               " (" + str(proc.returncode) + ")")


async def _encode_group(media_elements, display_id, token, progress_cb=None):
    """Encode all SEGMENT/INDIVIDUAL items in `media_elements` for `display_id`'s
    calibrated screens, writing seg_<token>_<i>/ind_<token>_<i> assets. Pure
    encode: no Display.renderStatus / renderedToken / broadcast side effects —
    the caller owns lifecycle state (legacy wrapper or the per-playlist renderer).
    Raises on ffmpeg failure. progress_cb(done, total) is called as video jobs
    complete (best-effort, optional).

    Strategy: build the FULL list of per-client ffmpeg commands first, then
    asyncio.gather them under a Semaphore(_RENDER_CONCURRENCY) so multiple
    encodes run in parallel. The previous implementation awaited each ffmpeg
    in a for-loop -- the async syntax was misleading, the actual execution
    was strictly sequential. On a 24-iPad fleet this is the difference
    between ~10 minutes and ~30 seconds total render time.

    Image warps still happen inline (OpenCV CPU warpPerspective). For a 24-
    iPad fleet at typical output sizes, the whole image-render pass is
    < 1 second; concurrency would buy ~nothing and just complicate the
    code path."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        raise RuntimeError("no such display: " + str(display_id))
    seg_items = [(i, me) for i, me in enumerate(media_elements)
                 if _is_renderable(me)]
    clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
    # Pass 1: collect all video render commands. Pass 2: gather them.
    # This lets us see the total job count and parallelise everything
    # in a single batch (across items AND across clients).
    video_jobs = []        # list of (cmd, label)
    seg_push_targets = []  # list of (client_key, segment_n) for seg_ video jobs only
    for i, me in seg_items:
        if me.playmode == PlayMode.FULL:
            # ONE shared device-decodable asset for the whole group.
            src_path = resolve_media_path(me.file)
            if isVideoItem(me.file):
                dims = get_video_dimensions(src_path) if src_path else None
                if not dims:
                    raise RuntimeError("cannot read source video: " + str(me.file))
                tw, th = _fit_within(dims[0], dims[1], DEVICE_DECODE_CAP)
                out_dir = os.path.join("media", "server", "videos")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_path = os.path.join(out_dir, "full_" + token + "_" + str(i) + ".mp4")
                evf, eaf = _resolve_effect_filters(me, me.duration, tw, th)
                cmd = build_ffmpeg_transcode_cmd(src_path, out_path, tw, th,
                                                 extra_video_filters=evf, extra_audio_filters=eaf)
                video_jobs.append((cmd, "server/full_" + str(i)))
            else:
                img = cv.imread(src_path) if src_path else None
                if img is None:
                    raise RuntimeError("cannot read source image: " + str(me.file))
                sh, sw = img.shape[:2]
                tw, th = _fit_within(sw, sh, DEVICE_DECODE_CAP)
                out_dir = os.path.join("media", "server", "images")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_path = os.path.join(out_dir, "full_" + token + "_" + str(i) + ".png")
                if (tw, th) != (sw, sh):
                    img = cv.resize(img, (tw, th), interpolation=cv.INTER_AREA)
                cv.imwrite(out_path, img)
            continue
        src_path = resolve_media_path(me.file)
        if isVideoItem(me.file):
            dims = get_video_dimensions(src_path) if src_path else None
            if not dims:
                raise RuntimeError("cannot read source video: " + str(me.file))
            sw, sh = dims
            for key, c in clients:
                out_dir = os.path.join("media", key, "videos")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_w, out_h = _render_output_dims(c)
                # NOTE: ffmpeg fade st= is in SECONDS, so this passes the
                # seconds-domain duration (the param name 'duration_ms' is a
                # misnomer). Do NOT convert to ms here — only the client
                # playback payload (_media_item_payload) needs ms.
                evf, eaf = _resolve_effect_filters(me, me.duration,
                                                   out_w, out_h)
                if me.playmode == PlayMode.INDIVIDUAL:
                    quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                    bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                    if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                        raise RuntimeError("degenerate screen quad for client " + str(key))
                    if sw * bh >= sh * bw:                 # source wider/equal -> pad height
                        pad_w = sw; pad_h = int(round(sw * bh / float(bw)))
                    else:                                  # source taller -> pad width
                        pad_h = sh; pad_w = int(round(sh * bw / float(bh)))
                    pad_x = (pad_w - sw) // 2; pad_y = (pad_h - sh) // 2
                    pts = quad_to_source_points([bx, by, bw, bh], c.measuredPerimeter, pad_w, pad_h)
                    out_path = os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".mp4")
                    cmd = build_ffmpeg_individual_cmd(src_path, out_path, pts,
                                                      out_w, out_h,
                                                      pad_w, pad_h, pad_x, pad_y,
                                                      getattr(me, "backgroundColor", "#000000"),
                                                      extra_video_filters=evf, extra_audio_filters=eaf)
                else:
                    pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                    out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                    cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts,
                                                       out_w, out_h,
                                                       extra_video_filters=evf, extra_audio_filters=eaf)
                    seg_push_targets.append((key, i))
                video_jobs.append((cmd, key + "/" + str(i)))
        else:
            img = cv.imread(src_path) if src_path else None
            if img is None:
                raise RuntimeError("cannot read source image: " + str(me.file))
            for key, c in clients:
                out_dir = os.path.join("media", key, "images")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                # Output at the client's TRUE rendered viewport (canvas),
                # falling back to reported device dims when canvas is 0/missing.
                out_w, out_h = _render_output_dims(c)
                if me.playmode == PlayMode.INDIVIDUAL:
                    quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                    bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                    if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                        raise RuntimeError("degenerate screen quad for client " + str(key))
                    bg = _hex_to_bgr(getattr(me, "backgroundColor", "#000000"))
                    canvas = letterbox_to_aspect(img, bw, bh, bg)
                    warped = warp_image_for_screen(canvas, [bx, by, bw, bh], c.measuredPerimeter,
                                                   out_w, out_h)
                    cv.imwrite(os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".png"), warped)
                else:
                    warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter,
                                                   out_w, out_h)
                    cv.imwrite(os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".png"), warped)
    # Pass 2: fire all video ffmpeg jobs in parallel, capped at
    # _RENDER_CONCURRENCY. Any failure raises out of asyncio.gather
    # (return_exceptions=False default) and propagates to the caller.
    if video_jobs:
        sem = asyncio.Semaphore(_RENDER_CONCURRENCY)
        logging.info("render: launching %d ffmpeg jobs concurrency=%d encoder=%s",
                     len(video_jobs), _RENDER_CONCURRENCY, _VIDEO_ENCODER)
        t0 = time.time()
        total = len(video_jobs)
        done = [0]

        async def _run_and_count(cmd, lbl):
            await _run_ffmpeg(cmd, lbl, sem)
            done[0] += 1
            if progress_cb:
                try:
                    progress_cb(done[0], total)
                except Exception:
                    pass

        await asyncio.gather(*[_run_and_count(cmd, lbl) for cmd, lbl in video_jobs])
        logging.info("render: %d ffmpeg jobs done in %.1fs",
                     len(video_jobs), time.time() - t0)
        # Cache-push: fire-and-forget scp of each seg_ file to its
        # iPad's lighttpd cache dir. _push_segment_to_cached_clients
        # is a no-op for clients not in lighttpd-localhost cacheMode,
        # so it is safe to call unconditionally for every seg_ target.
        # See docs/superpowers/specs/2026-06-03-media-cache-design.md
        for _push_key, _push_n in seg_push_targets:
            asyncio.ensure_future(
                server._push_segment_to_cached_clients(_push_key, token, _push_n))


async def render_group_async(display_id):
    """Legacy single-applied-playlist renderer (renders display.mediaElements,
    sets display.renderStatus/renderedToken + broadcasts RENDER_STATUS). The
    auto-render model superseded this: production renders now flow through the
    queue -> render_playlist_for_group_async (PLAY/ASSIGN/schedules gate on the
    per-playlist registry). No production path calls this anymore; it is retained
    only as a thin wrapper over _encode_group and is exercised by tests."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return {"status": "error"}
    display.renderStatus = "rendering"
    _broadcast_render_status(display_id, "rendering")
    token = compute_render_token(display_id)
    try:
        await _encode_group(display.mediaElements, display_id, token)
        display.renderedToken = token
        display.renderStatus = "ready"
        _broadcast_render_status(display_id, "ready")
        return {"status": "ready", "token": token}
    except Exception as e:
        logging.error("render failed for %s: %s", display_id, e)
        display.renderStatus = "error"
        _broadcast_render_status(display_id, "error")
        return {"status": "error", "error": str(e)}


# Throttled RENDERS_CHANGED broadcast (≤1/s). Module-level so all callers coalesce.
_last_renders_broadcast = [0.0]


def _broadcast_renders_changed(force=False):
    """Fan a RENDERS_CHANGED snapshot to all clients, throttled to ≤1/s unless
    force=True (terminal transitions). No-op if socketmanager isn't wired."""
    import server
    if server.socketmanager is None:
        return
    now = time.time()
    if not force and (now - _last_renders_broadcast[0]) < 1.0:
        return
    _last_renders_broadcast[0] = now
    server.socketmanager.broadcast(jsonpickle.encode(
        {"REQUEST": "RENDERS_CHANGED", "PAYLOAD": {"renders": renders_snapshot()}}))


def renders_snapshot():
    """Flat list of every render entry across all groups, for the fleet-wide
    Render Status panel + GET /api/renders."""
    import server
    out = []
    for did, display in server.settings.displays.items():
        for name, e in (getattr(display, "renders", {}) or {}).items():
            out.append({
                "displayID": did, "playlist": name,
                "state": e.get("state"), "percent": e.get("percent"),
                "eta": e.get("eta"), "startedAt": e.get("startedAt"),
                "error": e.get("error"), "updatedAt": e.get("updatedAt"),
            })
    return out


async def render_playlist_for_group_async(playlist_name, display_id):
    """Picks up from QUEUED (set by the caller) and transitions RENDERING→READY/
    FAILED for a NAMED playlist WITHOUT touching display.mediaElements
    (staging-safe). Used by the render queue. No-op (drops the entry) if the
    playlist became N/A."""
    import server
    pl = server.settings.playlists.get(playlist_name)
    display = server.settings.displays.get(display_id)
    if pl is None or display is None:
        return
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        display.renders.pop(playlist_name, None)   # became N/A
        _broadcast_renders_changed(force=True)
        return
    prev_token = (display.renders.get(playlist_name) or {}).get("token")
    token = render_token(elements, display_id)
    _set_render_state(display, playlist_name, RENDER_RENDERING, token=token,
                      percent=0, started=time.time())
    _broadcast_renders_changed(force=True)

    def _progress(done, total):
        pct = int(round(100.0 * done / total)) if total else 100
        entry = display.renders.get(playlist_name) or {}
        started = entry.get("startedAt") or time.time()
        elapsed = max(0.001, time.time() - started)
        rate = done / elapsed
        eta = int(round((total - done) / rate)) if rate > 0 else None
        _set_render_state(display, playlist_name, RENDER_RENDERING, percent=pct, eta=eta)
        _broadcast_renders_changed()

    try:
        await _encode_group(elements, display_id, token, progress_cb=_progress)
        _set_render_state(display, playlist_name, RENDER_READY, token=token,
                          percent=100, eta=0, error=None)
        # If this playlist is the one applied to the group, sync the live token
        # so the per-client PLAY URLs resolve the freshly-rendered assets.
        if getattr(display, "currentPlaylistName", None) == playlist_name:
            display.renderedToken = token
        # Reclaim the superseded token's assets — but only if nothing else
        # references it (shared-token safety).
        if prev_token and prev_token != token and not _token_is_live(prev_token):
            _delete_token_assets(prev_token, display_id)
    except Exception as e:
        logging.error("render_playlist_for_group %s/%s failed: %s", playlist_name, display_id, e)
        entry = _set_render_state(display, playlist_name, RENDER_FAILED, error=str(e))
        entry.pop("percent", None)
        entry.pop("eta", None)
    _broadcast_renders_changed(force=True)
    try:
        from mosaicmesh.persistence import save_settings_incremental
        save_settings_incremental()
    except Exception:
        pass


def _group_is_calibrated(display_id):
    """A group is calibrated iff it has a boundingBox AND ≥1 client with a
    measured perimeter — the minimum needed to produce a per-screen render."""
    import server
    display = server.settings.displays.get(display_id)
    if not display or not display.boundingBox:
        return False
    return any(c.measuredPerimeter is not None for _k, c in _group_clients(display_id))


def _render_assets_exist(playlist_name, display_id, token):
    """True if every renderable item's per-client asset exists on disk for this
    token. Conservative: a single missing file demotes the entry to STALE."""
    import server
    pl = server.settings.playlists.get(playlist_name)
    display = server.settings.displays.get(display_id)
    if pl is None or display is None:
        return False
    elements = _build_media_elements(pl.items)
    clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
    for i, me in enumerate(elements):
        if me.playmode == PlayMode.FULL:
            ext = ".mp4" if isVideoItem(me.file) else ".png"
            sub = "videos" if ext == ".mp4" else "images"
            path = os.path.join("media", "server", sub, "full_" + token + "_" + str(i) + ext)
            if not os.path.exists(path):
                return False
            continue
        if not _is_renderable(me):
            continue
        ext = ".mp4" if isVideoItem(me.file) else ".png"
        prefix = "ind_" if me.playmode == PlayMode.INDIVIDUAL else "seg_"
        subdir = "videos" if ext == ".mp4" else "images"
        for key, _c in clients:
            path = os.path.join("media", key, subdir, prefix + token + "_" + str(i) + ext)
            if not os.path.exists(path):
                return False
    return True


def revalidate_renders_on_boot():
    """Re-validate every persisted render entry once at startup. READY entries
    whose token still matches AND whose assets exist stay READY; everything else
    (stale token, missing asset, or a leftover in-flight QUEUED/RENDERING) drops
    to STALE for lazy re-render. Never auto-storms at boot."""
    import server
    for did, display in server.settings.displays.items():
        reg = getattr(display, "renders", {}) or {}
        for name in list(reg.keys()):
            entry = reg[name]
            pl = server.settings.playlists.get(name)
            if pl is None:
                reg.pop(name, None)   # playlist gone
                continue
            elements = _build_media_elements(pl.items)
            if not any(_is_renderable(me) for me in elements):
                reg.pop(name, None)   # became N/A
                continue
            cur = render_token(elements, did)
            ok = (entry.get("state") == RENDER_READY
                  and entry.get("token") == cur
                  and _render_assets_exist(name, did, cur))
            if not ok:
                _set_render_state(display, name, RENDER_STALE, token=cur)


def enqueue_playlist_for_calibrated_groups(playlist_name):
    """For a saved renderable playlist, set QUEUED + enqueue a render against
    every calibrated group. N/A playlists (no renderable items) are skipped."""
    import server
    from mosaicmesh import render_queue
    pl = server.settings.playlists.get(playlist_name)
    if pl is None:
        return
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        return
    changed = False
    for did, display in server.settings.displays.items():
        if not _group_is_calibrated(did):
            continue
        if is_playlist_ready(playlist_name, did):
            continue   # already current — don't re-encode
        _set_render_state(display, playlist_name, RENDER_QUEUED,
                          token=render_token(elements, did))
        render_queue.enqueue(playlist_name, did)
        changed = True
    if changed:
        _broadcast_renders_changed(force=True)


def mark_group_recalibrated(display_id):
    """Calibration changed for a group (first calibration OR recalibrate):
    enqueue a render of EVERY renderable playlist for this group and return the
    list of playlist names that will render (for the operator warning + ETA).
    Existing entries are reset to QUEUED with the new token. N/A playlists are
    skipped. No-op (returns []) if the group isn't calibrated.
    Already-current renders (READY + matching token) are skipped — a genuinely
    recalibrated group's render_token changes (perimeter is hashed in) so those
    will re-render; untouched groups whose token is unchanged are left alone."""
    import server
    from mosaicmesh import render_queue
    if not _group_is_calibrated(display_id):
        return []
    display = server.settings.displays.get(display_id)
    will = []
    for name, pl in server.settings.playlists.items():
        elements = _build_media_elements(pl.items)
        if not any(_is_renderable(me) for me in elements):
            continue
        if is_playlist_ready(name, display_id):
            continue   # render already current for this group — don't re-encode
        _set_render_state(display, name, RENDER_QUEUED, token=render_token(elements, display_id))
        render_queue.enqueue(name, display_id)
        will.append(name)
    if will:
        _broadcast_renders_changed(force=True)
    return will


def _delete_token_assets(token, display_id):
    """Delete on-disk seg_/ind_/full_ assets for a group at a SPECIFIC token.
    Best-effort; missing files are fine. Caller is responsible for confirming the
    token is no longer live (see _token_is_live).
    Per-client seg_/ind_ files are scoped to display_id's clients; full_<token>
    assets in media/server/ are deleted globally — safe because render_token
    hashes per-group geometry so no two groups ever share a token."""
    import glob
    if not token:
        return
    for key, _c in _group_clients(display_id):
        for sub in ("videos", "images"):
            for prefix in ("seg_", "ind_"):
                for path in glob.glob(os.path.join("media", key, sub, prefix + token + "_*")):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
    for sub in ("videos", "images"):
        for path in glob.glob(os.path.join("media", "server", sub, "full_" + token + "_*")):
            try:
                os.remove(path)
            except OSError:
                pass


def sweep_orphan_render_assets():
    """One-time boot sweep: delete rendered-asset files under media/ whose token
    is referenced by no live render (registry entry or renderedToken). Best-effort.
    Returns the count of files removed. Walks media/<key>/{videos,images}/ and
    media/server/{videos,images}/; the strict _RENDER_ASSET_RE guards the blast
    radius so uploaded source media and aruco markers are never touched."""
    import server, glob
    live = set()
    for display in server.settings.displays.values():
        for e in (getattr(display, "renders", {}) or {}).values():
            t = e.get("token")
            if t:
                live.add(t)
        rt = getattr(display, "renderedToken", "")
        if rt:
            live.add(rt)
    removed = 0
    for sub in ("videos", "images"):
        for path in glob.glob(os.path.join("media", "*", sub, "*")):
            fname = os.path.basename(path)
            m = _RENDER_ASSET_RE.match(fname)
            if m and m.group(1) not in live:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    return removed


def _delete_render_assets(playlist_name, display_id):
    """Delete the assets for a (playlist, group) at its CURRENT registry token.
    Thin wrapper over _delete_token_assets — used by playlist/group delete."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return
    token = (display.renders.get(playlist_name) or {}).get("token", "")
    _delete_token_assets(token, display_id)


def cleanup_playlist_renders(playlist_name):
    """Remove a playlist's render entry + assets from every group (on delete).
    Pops each entry BEFORE the _token_is_live check so a token still shared by
    another entry (identical-item playlists hash to the same token) is not
    deleted out from under it."""
    import server
    for did, display in server.settings.displays.items():
        reg = getattr(display, "renders", {}) or {}
        if playlist_name in reg:
            token = (reg.get(playlist_name) or {}).get("token", "")
            reg.pop(playlist_name, None)
            if token and not _token_is_live(token):
                _delete_token_assets(token, did)
    _broadcast_renders_changed(force=True)


def cleanup_group_renders(display_id):
    """Drop a group's whole render registry + assets (on group delete). Clears
    the registry BEFORE deleting so the _token_is_live guard only sees OTHER
    referrers (a token shared with another live entry is spared). Cross-group
    token collisions can't happen — render_token hashes per-group geometry — so
    a deleted group's tokens become unreferenced once its own registry is cleared."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return
    reg = getattr(display, "renders", {}) or {}
    tokens = {(e or {}).get("token", "") for e in reg.values()}
    display.renders = {}
    for token in tokens:
        if token and not _token_is_live(token):
            _delete_token_assets(token, display_id)
    _broadcast_renders_changed(force=True)


# ---------------------------------------------------------------------------
# Duration / payload / URL helpers
# ---------------------------------------------------------------------------

def _probed_video_seconds(file):
    """Probed natural length in SECONDS for a video URL, or None.

    Reuses the EXACT cache `/api/media` populates: `server._video_duration_cache`
    keyed by `(disk_path, mtime)`, where the disk path is
    `media/server/videos/<basename(url)>` (mirrors `api_media`'s mapping). This
    is a synchronous, best-effort READ of the cache — it never probes (ffprobe
    is async/blocking and `_duration_ms` runs on the wire-build path). Any
    non-video URL, missing file, or cache miss returns None so the caller falls
    back to the default window."""
    try:
        if not isinstance(file, str):
            return None
        if not file.startswith("/media/server/videos/"):
            return None
        if not file.lower().endswith((".mp4", ".mov", ".m4v", ".webm", ".ogv", ".ogg", ".mkv")):
            return None
        import server
        disk = os.path.join("media", "server", "videos", os.path.basename(file))
        mtime = os.path.getmtime(disk)
        return server._video_duration_cache.get((disk, mtime))
    except Exception:
        return None


def _duration_ms(me):
    """Item duration in ms. Explicit duration (seconds) -> ms. Missing
    ('Auto') -> the video's probed natural length if known, else a 20s
    default. Never 0 for a real item — a 0-ms window would skip the item
    on the wall (synchronized playback needs the window upfront)."""
    try:
        d = float(me.duration)
        if d > 0:
            return int(round(d * 1000))
    except (TypeError, ValueError):
        pass
    secs = _probed_video_seconds(getattr(me, "file", None))
    if secs and secs > 0:
        return int(round(secs * 1000))
    return DEFAULT_ITEM_DURATION_S * 1000


def _media_item_payload(me):
    """Per-item dict sent to clients in PLAY/PRELOAD. getattr guards items
    loaded from an older settings.dat that predate the newer fields. duration
    is emitted in MILLISECONDS (stored in seconds — see _duration_ms)."""
    return {"id": me.id, "file": me.file, "duration": _duration_ms(me),
            "playmode": me.playmode.name,
            "backgroundColor": getattr(me, "backgroundColor", "#000000"),
            "startEffect": getattr(me, "startEffect", None),
            "endEffect": getattr(me, "endEffect", None),
            "scriptSpan": getattr(me, "scriptSpan", "mirror")}


# Per-client URL routing for media-cache-aware clients. See spec
# 2026-06-03-media-cache-design.md. For SEGMENT items on an iPad in
# lighttpd-localhost cache mode that has the segment cached, returns
# the localhost URL so Safari fetches from local lighttpd (zero LAN
# bandwidth). For every other case -- non-SEGMENT items, cache miss,
# different cache mode -- returns the central-server URL.
#
# Accepts both real `MediaElement` instances (whose .playmode is a
# `PlayMode` enum) and dict-like / stub items (whose .playmode may
# be the string "SEGMENT" -- e.g., post-`_media_item_payload` wire
# dicts, or test stubs). Both forms work.
def _resolve_media_url(client, item):
    # Normalise playmode to its string name. PlayMode is an Enum on
    # real MediaElement instances; on wire-dicts / test stubs it's
    # already a string.
    pm = getattr(item, "playmode", None)
    pm_name = pm.name if hasattr(pm, "name") else (pm if isinstance(pm, str) else None)
    # Non-SEGMENT items (SCRIPT, IMAGE, INDIVIDUAL, etc.) are either
    # tiny or per-iPad-uncached by this design; pass through .file
    # unchanged so existing behavior is preserved.
    if pm_name != "SEGMENT":
        return getattr(item, "file", "")
    # Look for explicit seg_hash/seg_n attributes first (test stubs);
    # otherwise parse from the file path / URL using the canonical
    # seg_<HASH>_<N>.mp4 basename pattern set by the render pipeline.
    seg_hash = getattr(item, "seg_hash", None)
    seg_n = getattr(item, "seg_n", None)
    if seg_hash is None or seg_n is None:
        file_str = getattr(item, "file", "") or ""
        m = _SEG_FILE_RE.search(file_str)
        if m:
            seg_hash = m.group(1)
            seg_n = m.group(2)
    if seg_hash is None or seg_n is None:
        # Defensive: a SEGMENT item we can't extract hash+n from
        # can't be cached. Return the original file path.
        return getattr(item, "file", "")
    seg_key = f"{seg_hash}_{seg_n}"
    if (getattr(client, "cacheMode", "none") == "lighttpd-localhost"
            and seg_key in getattr(client, "cachedSegments", set())):
        return f"http://127.0.0.1:8080/seg_{seg_key}.mp4"
    # Central-server URL. clientKey is set on the Client by REGISTER;
    # tests pass it explicitly. Modern (service-worker) clients also
    # get this central URL -- their SW intercepts transparently.
    ckey = getattr(client, "clientKey", None) or "unknown"
    return f"http://192.168.1.60:3000/media/{ckey}/seg_{seg_key}.mp4"


def _build_media_elements(items):
    """Build MediaElement objects from a list of item dicts (SETPLAYLIST /
    ASSIGN_PLAYLIST share this). Maps the playmode string to the enum and
    applies field defaults."""
    elements = []
    for item in (items or []):
        me = MediaElement()
        me.id = item.get("id")
        me.file = item.get("file")
        me.duration = item.get("duration")
        _pm = item.get("playmode")
        me.playmode = (PlayMode.SEGMENT if _pm == "SEGMENT"
                       else PlayMode.SCRIPT if _pm == "SCRIPT"
                       else PlayMode.INDIVIDUAL if _pm == "INDIVIDUAL"
                       else PlayMode.FULL)
        me.backgroundColor = item.get("backgroundColor", "#000000")
        me.startEffect = item.get("startEffect")
        me.endEffect = item.get("endEffect")
        me.scriptSpan = item.get("scriptSpan", "mirror")
        elements.append(me)
    return elements


# ---------------------------------------------------------------------------
# Per-client item resolution (touches server.settings via display state)
# ---------------------------------------------------------------------------

def _per_client_items(display, key, c):
    """Per-client playlist items: renderable items (SEGMENT/INDIVIDUAL) resolve to
    THIS client's warped file when calibrated, else the plain source. Shared by
    the PLAY (GO) and PREPARE paths so both hand a client the same playable URL.

    Media-cache aware (2026-06-03): when this client is in
    cacheMode='lighttpd-localhost' AND has the segment cached locally
    (seg_<token>_<i> in client.cachedSegments), the per-iPad URL is
    rewritten to http://127.0.0.1:8080/seg_<token>_<i>.mp4 so the
    iPad's Safari fetches from its local lighttpd instead of competing
    for shared WiFi bandwidth at PLAY time. INDIVIDUAL-mode items are
    NOT cached by this design (no ind_HASH_N tracking in cachedSegments),
    so they keep the central-server URL. See spec
    docs/superpowers/specs/2026-06-03-media-cache-design.md."""
    token = display.renderedToken
    items = []
    cache_on = (getattr(c, "cacheMode", "none") == "lighttpd-localhost")
    cached = getattr(c, "cachedSegments", set()) if cache_on else set()
    for i, me in enumerate(display.mediaElements):
        if me.playmode == PlayMode.FULL:
            # Shared central device asset written by _encode_group (Task 4).
            # All clients in the group share this one file — never raw, never
            # per-client seg_/ind_ path.
            ext = ".mp4" if isVideoItem(me.file) else ".png"
            sub = "videos" if ext == ".mp4" else "images"
            f = "/media/server/" + sub + "/full_" + token + "_" + str(i) + ext
        elif _is_renderable(me) and c.measuredPerimeter is not None:
            prefix = "ind_" if me.playmode == PlayMode.INDIVIDUAL else "seg_"
            ext = ".mp4" if isVideoItem(me.file) else ".png"
            seg_key = "%s_%d" % (token, i)
            if (prefix == "seg_" and cache_on and seg_key in cached):
                # Cache hit: localhost URL bypasses central server entirely.
                f = "http://127.0.0.1:8080/seg_" + seg_key + ".mp4"
            else:
                f = "/media/" + key + "/" + prefix + token + "_" + str(i) + ext
        else:
            f = me.file  # SCRIPT animation ref, or uncalibrated fallback
        item = _media_item_payload(me)
        item["file"] = f
        # Mesh animation geometry: a SCRIPT item set to span the wall gets this
        # client's quad (normalized into the group bbox) + the global canvas size,
        # but ONLY when the client is calibrated and the group has bbox+meshGlobal.
        # Omitted otherwise -> the client goes black (mesh) or mirrors (mirror).
        # measuredPerimeter is a numpy (4,1,2) array in production -> reshape(-1,2);
        # cast coords to native float so the JSON payload has no numpy types.
        if me.playmode == PlayMode.SCRIPT and getattr(me, "scriptSpan", "mirror") == "mesh":
            if (calibration.MESH_RECTIFY
                    and getattr(display, "meshGlobalRect", None)
                    and getattr(c, "meshCellQuad", None)):
                # Homography-rectified geometry (keystone removed). Build a FRESH
                # per-item list (not the shared c.meshCellQuad object) and cast to
                # native float: the broadcast is jsonpickle-encoded with reference
                # tracking on, so handing the SAME list object to multiple mesh
                # items makes every occurrence after the first serialize as a
                # {"py/id": N} back-reference. The iPad's JSON.parse then sees an
                # object instead of a [u,v] array and mmMeshTransform throws on
                # meshQuad[3][0] -> the RAF loop dies and that screen goes black.
                # (The raw-bbox path below is immune: its comprehension already
                # builds a distinct list per item.)
                item["meshQuad"] = [[float(pt[0]), float(pt[1])] for pt in c.meshCellQuad]
                item["meshGlobal"] = list(display.meshGlobalRect)
                # Physical screen grid [cols, rows] + exact per-screen rects so
                # grid-aware animations choreograph correctly. Fresh lists per item
                # (same jsonpickle-shared-ref hazard as meshQuad above). meshCells
                # accounts for real bezel gaps so "light the screen under a point"
                # doesn't bleed into neighbors (uniform cols/rows would).
                if getattr(display, "meshGrid", None):
                    item["meshGrid"] = [int(display.meshGrid[0]), int(display.meshGrid[1])]
                if getattr(display, "meshCells", None):
                    item["meshCells"] = [[[float(pt[0]), float(pt[1])] for pt in cell]
                                         for cell in display.meshCells]
            elif (c.measuredPerimeter is not None
                    and display.boundingBox and getattr(display, "meshGlobal", None)):
                # Raw-bbox path (today's behavior). measuredPerimeter is numpy
                # (4,1,2) -> reshape(-1,2); native float for the JSON payload.
                bx, by, bw, bh = display.boundingBox
                quad = np.array(c.measuredPerimeter).reshape(-1, 2)
                item["meshQuad"] = [[float((px - bx) / float(bw)), float((py - by) / float(bh))]
                                    for (px, py) in quad]
                item["meshGlobal"] = list(display.meshGlobal)
            # else: omit -> client goes black (uncalibrated mesh)
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Broadcast helpers (per-client play/preload)
# ---------------------------------------------------------------------------

def _broadcast_per_client_play(display_id, display):
    """Send each client its own PLAY with its per-client (warped) media URLs."""
    for key, c in _group_clients(display_id):
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch,
                        "items": _per_client_items(display, key, c), "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0)}})


def _broadcast_per_client_preload(display_id, media_elements=None):
    """Send each client in a display group its own PRELOAD with per-client
    media URLs computed by _per_client_items -- the same function PLAY uses,
    so PRELOAD and PLAY are consistent. iPad-1 devices in lighttpd-localhost
    cacheMode + cached segment get localhost URLs; everyone else gets the
    central-server per-client URL (matching legacy behavior). The legacy
    `media_elements` parameter is accepted for backward compatibility but
    ignored -- we read display.mediaElements via display_id."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return
    for key, c in _group_clients(display_id):
        items = _per_client_items(display, key, c)
        broadcast_to_client(key, {"REQUEST": "PRELOAD",
                                  "PAYLOAD": {"items": items}})


# ---------------------------------------------------------------------------
# Playback orchestration (APPLY / START / STOP)
# ---------------------------------------------------------------------------

def _apply_playlist(display_id, pl):
    """Copy a saved Playlist onto a group (mediaElements, loop, PRELOAD) and
    sync display.renderedToken from the render registry so the per-client PLAY
    URLs (_per_client_items) resolve the right seg_<token> assets. Sets the
    live token to the playlist's READY token, else "" (not ready)."""
    import server
    display = server.settings.displays.setdefault(display_id, Display())
    display.mediaElements = _build_media_elements(pl.items)
    display.currentPlaylistName = getattr(pl, "name", None)
    display.loop = bool(pl.loop)
    name = getattr(pl, "name", None)
    entry = (getattr(display, "renders", {}) or {}).get(name)
    cur = render_token(display.mediaElements, display_id)
    if entry and entry.get("state") == RENDER_READY and entry.get("token") == cur:
        display.renderedToken = cur
    else:
        display.renderedToken = ""
    _broadcast_per_client_preload(display_id, display.mediaElements)


def _start_group_playback(display_id, resume_epoch=None):
    """Set the group playing now and broadcast PLAY (per-client for renderable items,
    else group-wide). No render gating here — callers ensure render readiness."""
    import server
    display = server.settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return
    now_ms = int(time.time() * 1000)
    if resume_epoch is None:
        resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
    display.playStartEpoch = resume_epoch
    display.action = PlayState.PLAY
    if _needs_per_client_delivery(display.mediaElements):
        _broadcast_per_client_play(display_id, display)
    else:
        items = [_media_item_payload(me) for me in display.mediaElements]
        broadcast_to_display_group(display_id, {
            "REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0)}})
    server._broadcast_playback_state(display_id)


def _stop_group_playback(display_id):
    """Tear down playback for a display group: set STOP, zero the playhead,
    and cancel any in-flight coordinated-start prepare (clearing prepareId,
    readyClients, armPending, prepareDeadline) so the group's next PLAY
    starts from a clean slate. Then broadcast STOP to every client in the
    group so they hide their video element."""
    import server
    display = server.settings.displays.get(display_id)
    if display:
        display.action = PlayState.STOP
        display.currentFrame = 0
        display.currentPlaylistName = None
        # cancel any in-flight coordinated-start prepare (don't leave stale state)
        display.prepareId = None
        display.readyClients = set()
        display.armPending = set()
        display.prepareDeadline = 0
    broadcast_to_display_group(display_id, {"REQUEST": "STOP", "PAYLOAD": {"displayID": display_id}})
    server._broadcast_playback_state(display_id)


# ---------------------------------------------------------------------------
# Coordinated-start helpers
# ---------------------------------------------------------------------------

def _group_online_keys(display_id):
    """Return the set of client keys belonging to display_id that are
    currently online. 'Online' = client.displayID matches AND client.isOnline
    is True (the latter is maintained by the websocket connect/disconnect
    handlers and the discovery heartbeat loop)."""
    import server
    return {k for k, c in server.settings.clients.items()
            if getattr(c, "displayID", None) == display_id and getattr(c, "isOnline", False)}


def _begin_prepare(display_id):
    """Phase 1: tell the group to buffer + hold frame 0 (don't start the clock).

    Sends PREPARE only to clients that have completed the SYN/SYNACK
    handshake (client.synced == True). Un-synced clients (freshly
    reconnected, mid-handshake) are skipped here -- they'll be picked up
    by _prepare_unsynced_clients() which polls and sends PREPARE as each
    finishes its handshake. Without this filter PREPARE would race the
    page-load on freshly-reconnected iPads and get silently dropped on
    the client side (the recv-PREPARE handler bails when sock_callback
    isn't yet wired)."""
    import server
    display = server.settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return
    display.prepareId = uuid.uuid4().hex
    display.playSeed = _mint_play_seed()
    display.readyClients = set()
    display.armPending = set()
    display.prepareDeadline = int(time.time() * 1000) + server.PREPARE_TIMEOUT_MS
    display.action = PlayState.PREPARING
    n_sent = n_skipped = 0
    for key, c in _group_clients(display_id):
        if not getattr(c, "synced", False):
            n_skipped += 1
            continue
        # Per-client PREPARE: each client must buffer/arm with ITS OWN rendered
        # segment URL, not the generic source (a renderable client handed the
        # 1080p source can't decode it -> MEDIA_ERR_SRC_NOT_SUPPORTED). Same
        # URLs as the GO.
        broadcast_to_client(key, {
            "REQUEST": "PREPARE",
            "PAYLOAD": {"prepareId": display.prepareId,
                        "items": _per_client_items(display, key, c), "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0)}})
        n_sent += 1
    if n_skipped:
        logging.info("_begin_prepare %s: PREPARE sent to %d synced; %d un-synced "
                     "will be sent PREPARE when their SYN/SYNACK completes",
                     display_id, n_sent, n_skipped)
        # Fire-and-forget the unsynced-client poll. Guarded with a running-loop
        # check: production always calls _begin_prepare inside the aiohttp loop,
        # but a synchronous caller (unit test) has no running loop, and Python
        # 3.12+ no longer auto-creates one — bare ensure_future would raise
        # RuntimeError. No loop -> the background poll is a no-op (nothing to poll).
        try:
            asyncio.get_running_loop()
            asyncio.ensure_future(_prepare_unsynced_clients(display_id,
                                                            display.prepareId))
        except RuntimeError:
            pass
    server._broadcast_playback_state(display_id)


async def _prepare_unsynced_clients(display_id, prepare_id):
    """Poll for clients that finish their SYN/SYNACK after _begin_prepare's
    initial broadcast, and send them PREPARE then. Bounded to the same
    deadline as the rest of the prepare phase -- after PREPARE_TIMEOUT_MS
    the release fires and any still-unsynced clients are released along
    with the rest of the group (they receive the PLAY broadcast and
    catch up from there)."""
    import server
    sent = set()
    while True:
        await asyncio.sleep(0.5)
        display = server.settings.displays.get(display_id)
        if not display:
            return
        if display.action != PlayState.PREPARING or display.prepareId != prepare_id:
            return  # group already released / new prepare started
        for key, c in _group_clients(display_id):
            if key in sent:
                continue
            if not getattr(c, "synced", False):
                continue
            broadcast_to_client(key, {
                "REQUEST": "PREPARE",
                "PAYLOAD": {"prepareId": prepare_id,
                            "items": _per_client_items(display, key, c),
                            "loop": display.loop,
                            "seed": getattr(display, "playSeed", 0)}})
            sent.add(key)
            logging.info("_prepare_unsynced: late PREPARE sent to %s", key)
