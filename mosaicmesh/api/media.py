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
import os

from aiohttp import web

__all__ = [
    "api_media",
    "upload_handler",
]


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


async def upload_handler(request):
    """POST /upload/{dest} — accept a single multipart file upload and route
    it to the appropriate processor (calibrate / image / video) based on the
    URL dest segment. All three processors live in server.py; we lazy-import
    server and call them as server.<name>(path, filename)."""
    import server
    import logging
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
