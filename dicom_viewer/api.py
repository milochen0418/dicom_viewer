"""The backend's own HTTP routes (next to Reflex's).

``POST /_dicom_viewer/bye``: a tab that is closing (or reloading) says goodbye with
``navigator.sendBeacon``, since a websocket message wouldn't get out of a page that is going
away. It names the tab by its Reflex client token, which only that tab knows, so nobody can
make someone else leave. The path is in backend-paths.txt, so the App Store's nginx sends it
to the backend.
"""

from __future__ import annotations

import asyncio
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from dicom_viewer import viewers
from dicom_viewer.states import series_work

BYE_PATH = "/_dicom_viewer/bye"
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


api = Starlette(routes=[Route(BYE_PATH, bye, methods=["POST"])])
