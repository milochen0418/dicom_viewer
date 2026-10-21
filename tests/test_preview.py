"""Link previews: a series' Open Graph page and its picture (what Relack shows of a link)."""

from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from PIL import Image

from dicom_viewer import api, links, preview, viewers, volume
from dicom_viewer.states import series_work
from tests import synth

# Slice i holds the value 10 * (i + 1); the files suggest the window 50 / 100.
COUNT = 4


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "dicom"
    ct = root / "study" / "ct"
    ct.mkdir(parents=True)
    # dz < 0: ct0.dcm is the top slice, so slice order is the reverse of name order.
    for index, ds in enumerate(synth.ct_series(ct, count=COUNT, rows=16, columns=24, dz=-1.0, write=False)):
        ds.PixelData = np.full((16, 24), 10 * (index + 1), dtype=np.uint16).tobytes()
        synth.save(ds, ct / f"ct{index}.dcm")
    monkeypatch.setenv("PUBLIC_DICOM_DIR", str(root))
    links._known.clear()
    preview._orders.clear()
    yield {"root": str(root), "id": links.id_of(str(ct), str(root)), "study": links.id_of(str(root / "study"), str(root))}
    links._known.clear()
    preview._orders.clear()
    series_work.SERIES.clear()


class _Client:
    """Requests to the backend's own routes, without a server."""

    def get(self, path: str, **kwargs) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=api.api)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get(path, **kwargs)

        return asyncio.run(send())


@pytest.fixture
def client():
    return _Client()


def _brightness(body: bytes) -> int:
    image = Image.open(io.BytesIO(body))
    assert image.size == preview.PICTURE_SIZE
    return image.getpixel((image.width // 2, image.height // 2))


def _value(value: float, center: float = 50, width: float = 100) -> int:
    """The grey a value gets with the window."""
    return round(np.clip((value - (center - width / 2)) / width, 0, 1) * 255)


def test_page_has_the_series_tags_and_nothing_about_the_patient(tree, client):
    response = client.get(f"/_dicom_viewer/og/{tree['id']}", headers={"x-forwarded-proto": "https", "host": "dicom-viewer.example.com"})
    assert response.status_code == 200
    body = response.text
    assert '<meta property="og:title" content="CT · Synthetic CT"/>' in body
    assert '<meta property="og:site_name" content="DICOM Viewer"/>' in body
    assert "4 images · 24 × 16. Everyone who opens the link sees the same view." in body
    assert f'<meta property="og:url" content="https://dicom-viewer.example.com/viewer/{tree["id"]}"/>' in body
    # Nobody is on the series: the picture is its middle slice with the Auto window.
    assert f'content="https://dicom-viewer.example.com/_dicom_viewer/og/{tree["id"]}/image.jpg"' in body
    assert "Test^Patient" not in body and "20260101" not in body


def test_page_tells_the_view_of_the_room(tree, client, monkeypatch):
    async def view(series_id):
        assert series_id == tree["id"]
        return preview.View(index=3, center=-600.0, width=1500.0, preset="Lung")

    monkeypatch.setattr(api, "_room_view", view)
    body = client.get(f"/_dicom_viewer/og/{tree['id']}").text
    assert "Slice 4 of 4, Lung window." in body
    assert f'/_dicom_viewer/og/{tree["id"]}/image.jpg?slice=3&amp;center=-600&amp;width=1500"' in body


def test_page_of_a_room_on_the_first_slice_shows_the_middle_one(tree, client, monkeypatch):
    async def view(series_id):
        return preview.View(index=None, center=40.0, width=400.0, preset="Auto")

    monkeypatch.setattr(api, "_room_view", view)
    body = client.get(f"/_dicom_viewer/og/{tree['id']}").text
    assert "4 images · 24 × 16. Everyone who opens the link sees the same view." in body
    assert f'/_dicom_viewer/og/{tree["id"]}/image.jpg?center=40&amp;width=400"' in body


def test_page_is_not_found_without_images(tree, client):
    assert client.get(f"/_dicom_viewer/og/{tree['study']}").status_code == 404
    assert client.get("/_dicom_viewer/og/0123456789").status_code == 404
    assert client.get("/_dicom_viewer/og/..").status_code == 404


def test_picture_is_the_middle_slice_with_the_auto_window(tree, client):
    response = client.get(f"/_dicom_viewer/og/{tree['id']}/image.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    # In slice order: ct3, ct2, ct1, ct0; the middle one (index 2) is ct1, which holds 20.
    assert abs(_brightness(response.content) - _value(20)) <= 2


def test_picture_shows_the_view_it_asks_for(tree, client):
    body = client.get(f"/_dicom_viewer/og/{tree['id']}/image.jpg?slice=0&center=30&width=40").content
    assert abs(_brightness(body) - _value(40, 30, 40)) <= 2  # ct3
    # What it gets wrong falls back to the middle slice and the Auto window.
    body = client.get(f"/_dicom_viewer/og/{tree['id']}/image.jpg?slice=9&center=nan&width=-1").content
    assert abs(_brightness(body) - _value(20)) <= 2


def test_picture_uses_the_open_series(tree, client):
    # Slice numbers are the room's, i.e. the order the open series has (here: name order).
    folder = links.find_folder(tree["id"], tree["root"])
    files = [os.path.join(folder, f"ct{i}.dcm") for i in range(COUNT)]
    series_work.SERIES[tree["id"]] = series_work.Series(
        folder=folder, files=files, refs=[], window=(20.0, 20.0), auto_source="file", label=""
    )
    body = client.get(f"/_dicom_viewer/og/{tree['id']}/image.jpg?slice=1").content
    assert abs(_brightness(body) - _value(20, 20, 20)) <= 2  # ct1 with the series' window


def test_picture_is_not_found_for_an_unknown_series(tree, client):
    assert client.get("/_dicom_viewer/og/0123456789/image.jpg").status_code == 404
    assert client.get(f"/_dicom_viewer/og/{tree['study']}/image.jpg").status_code == 404


def test_room_view_is_the_slice_on_screen(monkeypatch):
    room = SimpleNamespace(
        status="ready", current_index=2, total_images=4, window_center=-600.0, window_width=1500.0,
        selected_preset="Lung", view_mode="single", mpr_status="", plane_index={"axial": 1, "coronal": 0, "sagittal": 0},
    )

    async def with_room(series_id, fn):
        return fn(room)

    monkeypatch.setattr(viewers, "tokens_on", lambda series_id: ["a tab"])
    monkeypatch.setattr(series_work, "with_room", with_room)
    assert asyncio.run(api._room_view("abc")) == preview.View(2, -600.0, 1500.0, "Lung")
    room.current_index = 0  # where every series opens
    assert asyncio.run(api._room_view("abc")) == preview.View(None, -600.0, 1500.0, "Lung")
    # In 3 views: the scanned image the crosshair is on.
    room.view_mode, room.mpr_status = "mpr", "ready"
    monkeypatch.setitem(series_work.SERIES, "abc", series_work.Series(
        folder="/x", files=["a", "b", "c", "d"], refs=[], window=(0, 1), auto_source="file", label="", volume_key="k"
    ))
    monkeypatch.setitem(volume._cache, "k", SimpleNamespace(path_at=lambda cursor: "abcd"[cursor["axial"] + 2]))
    assert asyncio.run(api._room_view("abc")).index == 3
    room.status = "loading"
    assert asyncio.run(api._room_view("abc")) is None
    monkeypatch.setattr(viewers, "tokens_on", lambda series_id: [])
    assert asyncio.run(api._room_view("abc")) is None
