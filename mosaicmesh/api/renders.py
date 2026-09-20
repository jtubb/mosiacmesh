"""GET /api/renders — fleet-wide snapshot of the per-(playlist, group) render
registry plus the queue depth. Read-only; drives the admin Render Status panel.
Follows the project {success, ...} response convention."""
from aiohttp import web

from mosaicmesh import render as _render
from mosaicmesh import render_queue

__all__ = ["api_renders_list"]


async def api_renders_list(request):
    """GET /api/renders -> {success, renders: [...], queueDepth: N}.

    `?assets=1` adds an assetsPresent flag per entry. Opt-in: it costs a listdir
    per client directory, so the UI's routine poll must not ask for it -- use it
    when diagnosing a READY entry whose files may have gone missing.
    """
    want_assets = str(request.query.get("assets", "")).lower() in ("1", "true", "yes")
    return web.json_response({
        "success": True,
        "renders": _render.renders_snapshot(with_assets=want_assets),
        "queueDepth": render_queue.queue_depth(),
    })
