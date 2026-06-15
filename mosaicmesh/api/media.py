"""REST endpoints for the shared media library.

GET /api/media        - list /media/server/{images,videos} + video durations
POST /upload/{dest}   - accept multipart media uploads

Pure relocation from server.py per PR-2 of the spec. Behavior is
identical; the only changes are:
  - get_video_duration (used by api_media) stays in server.py for now
    because other code paths use it; we call it via server.get_video_duration.
  - calibrate / processImage / processVideo likewise stay in server.py;
    upload_handler calls them via server.<name>(...).
Both accesses use lazy `import server` as the first body line
(established pattern from earlier Task moves).
"""
import json
import logging
import os

from aiohttp import web

__all__ = [
    "api_media",
    "api_media_delete",
    "upload_handler",
]


# Allowed `kind` subdirs under media/server/. Used by api_media_delete
# to compute the on-disk path AND to reject anything else (e.g. a
# request that tried to traverse into a per-client `media/<key>/...`
# directory).
_MEDIA_SUBDIRS = ("images", "videos")


def _disk_path_from_url(url):
    """Map a /media/server/{kind}/{filename} URL to its on-disk path.

    Returns None if the URL doesn't fit the expected shape OR contains
    a path traversal (the `..` segment is the obvious one; we also
    reject absolute paths after the leading slash + any backslash, in
    case a Windows-uploaded filename slipped past upload validation).
    """
    if not isinstance(url, str):
        return None
    prefix = "/media/server/"
    if not url.startswith(prefix):
        return None
    rest = url[len(prefix):]
    parts = rest.split("/")
    if len(parts) != 2:
        return None
    sub, name = parts
    if sub not in _MEDIA_SUBDIRS:
        return None
    if not name or name.startswith(".") or ".." in name or "\\" in name:
        return None
    return os.path.join("media", "server", sub, name)


def _playlist_refs_for_media(url):
    """Return [playlistName, ...] for every playlist whose .items[] has
    .file == url. Used to populate the 409 refs payload so the UI can
    surface "in use by N playlists" before forcing the operator to
    delete those references first."""
    import server
    refs = []
    for p in server.settings.playlists.values():
        for item in getattr(p, "items", []) or []:
            if isinstance(item, dict) and item.get("file") == url:
                refs.append(p.name)
                break
    return refs


async def api_media(request):
    """List the shared media library under media/server/{images,videos}, plus
    per-video durations (seconds) so the playlist editor can offer 'full length'."""
    import server

    def _list(sub):
        d = os.path.join("media", "server", sub)
        if not os.path.isdir(d):
            return []
        return ["/media/server/" + sub + "/" + f
                for f in sorted(os.listdir(d))
                if os.path.isfile(os.path.join(d, f))]
    videos = _list("videos")
    durations = {}
    for url in videos:
        disk = os.path.join("media", "server", "videos", os.path.basename(url))
        d = await server.get_video_duration(disk)
        if d is not None:
            durations[url] = round(d, 1)
    body = json.dumps({"images": _list("images"), "videos": videos,
                       "videoDurations": durations})
    return web.Response(text=body, content_type="application/json")


async def api_media_delete(request):
    """DELETE /api/media — body {url:"/media/server/{images|videos}/foo.mp4"}.

    Pre-check: 409 + {refs:[playlistName,...]} if any playlist's
    items[] references the URL. The operator must remove those
    references first; there is intentionally no force-delete since
    losing referenced media silently has broken playback before.

    Success: removes the file off disk + returns 204. The file is the
    only side effect — playlists/schedules unaffected.
    """
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False, "error": f"Invalid JSON: {e}"}, status=400)
    url = body.get("url")
    disk = _disk_path_from_url(url)
    if disk is None:
        return web.json_response({"success": False,
                                  "error": "url must be /media/server/{images|videos}/{filename}"},
                                 status=400)
    if not os.path.isfile(disk):
        return web.json_response({"success": False,
                                  "error": f"file not found: {url}"},
                                 status=404)
    refs = _playlist_refs_for_media(url)
    if refs:
        return web.json_response({
            "success": False,
            "error": f"media is in use by {len(refs)} playlist(s)",
            "refs": refs,
        }, status=409)
    try:
        os.remove(disk)
    except OSError as e:
        return web.json_response({"success": False,
                                  "error": f"could not delete: {e}"},
                                 status=500)
    logging.info("DELETE /api/media %s", url)
    return web.Response(status=204)


async def upload_handler(request):
    """POST /upload/{dest} — accept a single multipart file upload and route
    it to the appropriate processor (calibrate / image / video) based on the
    URL dest segment. All three processors live in server.py; we lazy-import
    server and call them as server.<name>(path, filename)."""
    import server
    logging.debug("UPLOAD_HANDLER")
    uploadDest = request.match_info.get('dest')
    logging.debug(uploadDest)
    reader = await request.multipart()
    # reader.next() will `yield` the fields of your form
    field = await reader.next()
    logging.debug(field.name)
    filename = field.filename
    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    path = os.path.join('cache')
    if not os.path.exists(path):
        os.mkdir(path)
    with open(os.path.join(path, filename), 'wb') as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)

    response = "none"
    ct = 'application/octet-stream'

    if(uploadDest == "calibrate"):
        response, ct = server.calibrate(os.path.join(path, filename))
    elif(uploadDest == "image"):
        response, ct = server.processImage(path, filename)
    elif(uploadDest == "video"):
        response, ct = server.processVideo(path, filename)
    return web.Response(body=response, content_type=ct)
