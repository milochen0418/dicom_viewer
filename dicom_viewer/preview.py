"""What a link to a series shows where it is sent: a title, a line about it and a picture.

/viewer/<id> is the app's page, the same for every series, so a chat app that unfurls links
(Relack) reads a series' Open Graph tags from /_dicom_viewer/og/<id> instead (api.py), as it
does for CoDoc in MD's documents. The picture is the slice the series' room shows when the link
is shared, with its window, which is what someone opening the link will find; the middle slice
while the room is still on the first one, or nobody is on the series (then in the Auto window).

Nothing about the patient goes into either: the metadata panel keeps that behind a password.

Plain Python without Reflex, so the reading and drawing can run in a worker thread.
"""

from __future__ import annotations

import collections
import dataclasses
import html
import io
import math
import os
import threading
from collections.abc import Mapping
from urllib.parse import urlencode

import pydicom
from PIL import Image

from dicom_viewer import folders, series
from dicom_viewer.windowing import DEFAULT_WINDOW, PRESETS

SITE_NAME = "DICOM Viewer"
# 3:2, the most of a picture Relack's card shows, at twice the card's size.
PICTURE_SIZE = (900, 600)
_JPEG_QUALITY = 85
_FACT_TAGS = ["Rows", "Columns", "BodyPartExamined"]

# Series put in slice order, by (folder, its mtime): reading every header is the slow part.
_ORDERS = 4
_lock = threading.Lock()
_orders: collections.OrderedDict[tuple[str, int], tuple[list[str], tuple[float, float]]] = (
    collections.OrderedDict()
)


@dataclasses.dataclass(frozen=True)
class View:
    """What the series' room shows: the slice (from 0; None for the middle one), the window
    and its preset."""

    index: int | None
    center: float
    width: float
    preset: str = ""


@dataclasses.dataclass(frozen=True)
class Facts:
    """What the card says about a series."""

    title: str
    images: int
    rows: int = 0
    columns: int = 0
    body_part: str = ""


def facts(folder: str) -> Facts | None:
    """The card's facts about the series in `folder`; None when it holds no images.

    Raises OSError when the folder cannot be read.
    """
    images = folders.image_files(folder)
    if not images:
        return None
    title = folders.series_label(images[0]) or os.path.basename(os.path.normpath(folder)) or "DICOM series"
    try:
        ds = pydicom.dcmread(images[0], stop_before_pixels=True, specific_tags=_FACT_TAGS)
        rows, columns = int(ds.get("Rows", 0) or 0), int(ds.get("Columns", 0) or 0)
        body_part = str(ds.get("BodyPartExamined", "") or "").strip()
    except Exception:
        rows = columns = 0
        body_part = ""
    return Facts(title, len(images), rows, columns, body_part)


def describe(about: Facts, view: View | None) -> str:
    """The card's line, e.g. "327 images · 512 × 512. Slice 177 of 327, Lung window. ..."."""
    parts = [f"{about.images} image{'' if about.images == 1 else 's'}"]
    if about.rows and about.columns:
        parts.append(f"{about.columns} × {about.rows}")
    if about.body_part and about.body_part.lower() not in about.title.lower():
        parts.append(about.body_part)
    text = " · ".join(parts) + "."
    shown = []
    if view is not None and view.index is not None:
        shown.append(f"Slice {view.index + 1} of {about.images}")
    if view is not None and view.preset in PRESETS:
        shown.append(f"{view.preset} window")
    if shown:
        text += " " + ", ".join(shown) + "."
    return text + " Everyone who opens the link sees the same view."


def picture_query(view: View | None) -> str:
    """What the picture's address adds to ask for the view: it stays the one shared."""
    if view is None:
        return ""
    query = {} if view.index is None else {"slice": view.index}
    return "?" + urlencode({**query, "center": f"{view.center:g}", "width": f"{view.width:g}"})


def page(url: str, picture_url: str, about: Facts, view: View | None) -> str:
    """The Open Graph page of the series at `url` (its /viewer/<id>); people go on there."""
    e = html.escape
    text = describe(about, view)
    meta = {
        "og:type": "website",
        "og:site_name": SITE_NAME,
        "og:title": about.title,
        "og:description": text,
        "og:url": url,
        "og:image": picture_url,
        "og:image:type": "image/jpeg",
        "og:image:width": str(PICTURE_SIZE[0]),
        "og:image:height": str(PICTURE_SIZE[1]),
    }
    tags = "".join(f'<meta property="{k}" content="{e(v)}"/>' for k, v in meta.items())
    return (
        "<!doctype html><html><head><meta charset='utf-8'/>"
        f"<title>{e(about.title)} | {SITE_NAME}</title>"
        f'<meta name="description" content="{e(text)}"/>'
        f'<meta name="twitter:card" content="summary_large_image"/>{tags}'
        f'<link rel="canonical" href="{e(url)}"/>'
        f'<meta http-equiv="refresh" content="0; url={e(url)}"/>'
        f'</head><body><a href="{e(url)}">{e(about.title)}</a></body></html>'
    )


def ordered(folder: str) -> tuple[list[str], tuple[float, float]]:
    """The series' images in slice order and its Auto window, as the viewer opens it.

    Raises OSError when the folder cannot be read.
    """
    key = (folder, os.stat(folder).st_mtime_ns)
    with _lock:
        if key in _orders:
            _orders.move_to_end(key)
            return _orders[key]
    keyed = series.read_sort_keys(folders.image_files(folder))
    keyed.sort(key=lambda item: item[0])
    files = [path for _, path in keyed]
    window = None
    if files:
        window = series.file_window(files[0]) or series.image_window(files[len(files) // 2])
    found = (files, tuple(window or DEFAULT_WINDOW))
    with _lock:
        _orders[key] = found
        while len(_orders) > _ORDERS:
            _orders.popitem(last=False)
    return found


def _number(text: str | None, kind: type) -> float | None:
    try:
        value = kind(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def pick(query: Mapping[str, str], count: int, auto: tuple[float, float]) -> tuple[int, float, float]:
    """(slice, center, width) the picture's address asks for; the middle slice and the Auto
    window for what it leaves out or gets wrong."""
    index = _number(query.get("slice"), int)
    if index is None or not 0 <= index < count:
        index = count // 2
    center, width = _number(query.get("center"), float), _number(query.get("width"), float)
    if center is None or width is None or width <= 0:
        center, width = auto
    return index, center, width


def picture(path: str, center: float, width: float) -> bytes:
    """The slice in `path` with the window, as large as fits on black, as a JPEG."""
    _, pixels, slope, intercept = series.read_slice(path)
    image = series.render_image(pixels, slope, intercept, center, width)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    box_width, box_height = PICTURE_SIZE
    scale = min(box_width / image.width, box_height / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    card = Image.new(image.mode, PICTURE_SIZE)
    card.paste(image.resize(size, Image.LANCZOS), ((box_width - size[0]) // 2, (box_height - size[1]) // 2))
    buffer = io.BytesIO()
    card.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()
