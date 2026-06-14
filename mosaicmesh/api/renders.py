"""GET /api/renders — fleet-wide snapshot of the per-(playlist, group) render
registry plus the queue depth. Read-only; drives the admin Render Status panel.
Follows the project {success, ...} response convention."""
from aiohttp import web

from mosaicmesh import render as _render
from mosaicmesh import render_queue

__all__ = ["api_renders_list"]


async def api_renders_list(request):
    """GET /api/renders -> {success, renders: [...], queueDepth: N}."""
    return web.json_response({
        "success": True,
        "renders": _render.renders_snapshot(),
        "queueDepth": render_queue.queue_depth(),
    })
