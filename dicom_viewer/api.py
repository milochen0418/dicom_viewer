"""The backend's own HTTP routes (next to Reflex's).

``POST /_dicom_viewer/bye``: a tab that is closing (or reloading) says goodbye with
``navigator.sendBeacon``, since a websocket message wouldn't get out of a page that is going
away. It names the tab by its Reflex client token, which only that tab knows, so nobody can
make someone else leave.

``GET /_dicom_viewer/og/<id>`` and ``GET /_dicom_viewer/og/<id>/image.jpg``: what a link to a
series shows in a chat app (Relack), its Open Graph tags and a picture of it (see preview.py).

The paths are in backend-paths.txt, so the App Store's nginx sends them to the backend.
"""

from __future__ import annotations

import asyncio
import json
import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from dicom_viewer import folders, links, preview, viewers, volume
from dicom_viewer.states import series_work

BYE_PATH = "/_dicom_viewer/bye"
OG_PATH = "/_dicom_viewer/og/{series_id}"
OG_PICTURE_PATH = "/_dicom_viewer/og/{series_id}/image.jpg"
_tasks: set = set()


async def bye(request: Request) -> Response:
    try:
        data = json.loads((await request.body())[:4096] or b"{}")
    except ValueError:
        return Response(status_code=400)
    token = str(data.get("token") or "")
    peer = str(data.get("peer") or "")
    series_id = viewers.find(token, peer) if token and peer else ""
    if series_id:
        viewers.leave(peer)
        # Tell the others now, through one of them (outside this request).
        task = asyncio.create_task(series_work.push(series_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return Response(status_code=204)


def _public_base_url(request: Request) -> str:
    """The address the link was sent with (behind nginx, from its headers)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


async def _room_view(series_id: str) -> preview.View | None:
    """What the series' room shows in 1 view, read through a tab on it; None when nobody is
    on the series."""
    if not viewers.tokens_on(series_id):
        return None

    def read(room) -> preview.View | None:
        if room.status != "ready" or not 0 <= room.current_index < room.total_images:
            return None
        index = room.current_index
        # In 3 views: the scanned image the crosshair is on, which going back to 1 view shows.
        s = series_work.SERIES.get(series_id)
        vol = volume.cached(s.volume_key) if s is not None and room.view_mode == "mpr" else None
        if vol is not None and room.mpr_status == "ready":
            path = vol.path_at(room.plane_index)
            if path in s.files:
                index = s.files.index(path)
        # Every series opens on its first slice, an edge of the scan: the middle one shows more.
        return preview.View(index or None, room.window_center, room.window_width, room.selected_preset)

    view = await series_work.with_room(series_id, read)
    return None if view is series_work.NOBODY else view


async def og_page(request: Request) -> Response:
    series_id = request.path_params["series_id"]
    folder = await asyncio.to_thread(links.find_folder, series_id, folders.dicom_root())
    try:
        about = await asyncio.to_thread(preview.facts, folder) if folder else None
    except OSError:
        about = None
    if about is None:
        return HTMLResponse("<!doctype html><title>No such series</title>", status_code=404)
    view = await _room_view(series_id)
    base = _public_base_url(request)
    picture_url = base + OG_PICTURE_PATH.format(series_id=series_id) + preview.picture_query(view)
    body = preview.page(f"{base}/viewer/{series_id}", picture_url, about, view)
    return HTMLResponse(body, headers={"Cache-Control": "public, max-age=60"})


async def og_picture(request: Request) -> Response:
    series_id = request.path_params["series_id"]
    folder = await asyncio.to_thread(links.find_folder, series_id, folders.dicom_root())
    if folder is None:
        return Response(status_code=404)
    try:
        # A series someone has open is in slice order already.
        s = series_work.SERIES.get(series_id)
        if s is not None and s.folder == folder:
            files, auto = s.files, s.window
        else:
            files, auto = await asyncio.to_thread(preview.ordered, folder)
        if not files:
            return Response(status_code=404)
        index, center, width = preview.pick(request.query_params, len(files), auto)
        body = await asyncio.to_thread(preview.picture, files[index], center, width)
    except Exception as e:
        logging.warning(f"No preview picture of series {series_id}: {e}")
        return Response(status_code=404)
    return Response(body, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


api = Starlette(
    routes=[
        Route(BYE_PATH, bye, methods=["POST"]),
        Route(OG_PATH, og_page, methods=["GET"]),
        Route(OG_PICTURE_PATH, og_picture, methods=["GET"]),
    ]
)
