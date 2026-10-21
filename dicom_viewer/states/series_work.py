"""The work behind an open series, done once on the server for everyone on it.

A series is read once (its files put in slice order, its window, its annotation layers) and
kept in this process's memory (`SERIES`). What to show is in the series' room
(:class:`~dicom_viewer.states.room.SeriesRoom`); the pictures drawn here go back into the
room, so everyone on the series gets the same ones.

Each kind of work (opening, drawing one view, drawing three, building the volume, reading
annotations) runs in at most one task per series (`_claim`). A change made while it runs is
picked up by its next round, and a drawing task stops only when what it drew matches what
the room asks for, checked under the room's lock: drags and wheel bursts coalesce instead of
piling up, and no change is left undrawn.

The room is reached through one of the tabs on the series (`with_room`): a Reflex shared
state is changed through a client linked to it, which then carries the change to the others.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import itertools
import logging
import os
import sys
from typing import Any, Callable

import numpy as np
import reflex as rx

from dicom_viewer import annotations, call, folders, series, viewers, volume
from dicom_viewer.states.room import (
    SLIDER_GROUPS,
    ItemView,
    LayerView,
    SeriesRoom,
    room_token,
)
from dicom_viewer.windowing import AUTO, DEFAULT_WINDOW
import dicom_viewer.annotations.builtin  # noqa: F401 - registers the SEG, SR, RTSTRUCT and PR adapters

# Files whose headers are read between two progress updates while opening.
_OPEN_BATCH = 16
# Series kept read; the least recently opened one nobody is looking at goes first.
_MAX_SERIES = 16

# with_room's answer when no tab is on the series.
NOBODY = object()


@dataclasses.dataclass
class Series:
    """A series as read from its folder, and what its room's pictures were drawn for."""

    folder: str
    files: list[str]  # in slice order
    refs: list  # where each file is, for the annotation layers
    window: tuple[float, float]  # Auto
    auto_source: str
    label: str
    # The slice read last, kept to draw it again with another window.
    index: int = -1
    pixels: np.ndarray | None = None
    slope: float = 1.0
    intercept: float = 0.0
    # (slice, window, drawing) of the picture in 1 view; per plane, in 3 views.
    drawn: tuple | None = None
    plane_drawn: dict[str, tuple] = dataclasses.field(default_factory=dict)
    volume_key: str = ""
    layers_key: str = ""
    # Bumped whenever the annotation layers are read again, so the pictures redraw.
    layers_rev: int = 0


SERIES: collections.OrderedDict[str, Series] = collections.OrderedDict()


def _remember(series_id: str, s: Series) -> None:
    SERIES[series_id] = s
    SERIES.move_to_end(series_id)
    watched = viewers.watching()
    for old in [sid for sid in SERIES if sid not in watched][: max(0, len(SERIES) - _MAX_SERIES)]:
        del SERIES[old]


@dataclasses.dataclass(frozen=True)
class Drawing:
    """How the panel wants annotations drawn, as the renderers take it."""

    visible: dict[str, list[str]]  # layer id -> item ids to show; [] hides the layer
    opacity: float
    labels: bool
    version: int


@dataclasses.dataclass
class _Loaded:
    """The annotation files seen for a series: per file its layer (None when it
    is not for this series) and load error, plus the layers in folder order."""

    by_path: dict[str, tuple] = dataclasses.field(default_factory=dict)
    layers: list = dataclasses.field(default_factory=list)


# Loaded layers per series and label volumes per (volume, layer). They hold
# numpy arrays, so they live here; the room keeps only ids and visibility.
# The folders are scanned again every time a series opens, so annotations
# downloaded later show up; only the objects already read are reused.
_LAYER_CACHE_SIZE = 3
_LABEL_CACHE_SIZE = 8
_layer_cache: collections.OrderedDict[str, _Loaded] = collections.OrderedDict()
_label_cache: collections.OrderedDict[tuple[str, str], np.ndarray | None] = (
    collections.OrderedDict()
)


def _cached_layers(key: str) -> _Loaded | None:
    entry = _layer_cache.get(key)
    if entry is not None:
        _layer_cache.move_to_end(key)
    return entry


def _layers_of(key: str) -> list:
    entry = _cached_layers(key) if key else None
    return entry.layers if entry else []


def _store_layers(key: str, entry: _Loaded) -> None:
    _layer_cache[key] = entry
    _layer_cache.move_to_end(key)
    while len(_layer_cache) > _LAYER_CACHE_SIZE:
        _layer_cache.popitem(last=False)


def _store_labels(key: tuple[str, str], labels: np.ndarray | None) -> None:
    _label_cache[key] = labels
    _label_cache.move_to_end(key)
    while len(_label_cache) > _LABEL_CACHE_SIZE:
        _label_cache.popitem(last=False)


def _layer_view(layer) -> LayerView:
    return LayerView(
        id=layer.id,
        format=layer.format,
        name=layer.name,
        notes=list(layer.notes),
        volume_ok=bool(layer.supports_volume),
        rows=[
            ItemView(
                id=item.id,
                name=item.name,
                color="rgb(%d, %d, %d)" % tuple(item.color),
                description=item.description,
            )
            for item in layer.items
        ],
    )


def _render_slice(pixels, slope, intercept, center, width, ref, layers, drawing) -> str:
    """The windowed slice with the visible annotations on it, as a PNG data URL."""
    try:
        image = series.render_image(pixels, slope, intercept, center, width)
    except Exception as e:
        logging.exception(f"Error processing image: {e}")
        return "/placeholder.svg"
    if ref is not None and layers:
        try:
            image = annotations.render(
                image,
                ref,
                layers,
                visible=drawing.visible,
                opacity=drawing.opacity,
                show_labels=drawing.labels,
            )
        except Exception as e:
            logging.exception(f"Error drawing annotations: {e}")
    return series.encode_png(image)


def _labels_for(layer, stack, vol: volume.Volume) -> np.ndarray | None:
    """A layer's label volume turned like `vol`, or None when it cannot be shown."""
    try:
        labels = layer.label_volume(stack)
    except Exception as e:
        logging.exception(f"Error building the labels of {layer.name}: {e}")
        return None
    return None if labels is None else volume.reorient(labels, vol)


def _plane_overlays(key: str, layers, drawing) -> list[tuple]:
    """(layer, labels, hidden item ids) for the visible layers that have labels."""
    overlays = []
    for layer in layers:
        shown = drawing.visible.get(layer.id)
        if shown is None:
            shown = [item.id for item in layer.items]
        labels = _label_cache.get((key, layer.id)) if shown else None
        if labels is None:
            continue
        hidden = {item.id for item in layer.items} - set(shown)
        overlays.append((layer, labels, hidden))
    return overlays


def _render_planes(
    vol: volume.Volume,
    indices: dict[str, int],
    center: float,
    width: float,
    overlays: list[tuple] = (),
    opacity: float = 0.4,
) -> dict[str, str]:
    pictures = {}
    for plane, index in indices.items():
        try:
            image = series.render_image(
                vol.plane(plane, index), vol.slope, vol.intercept, center, width
            )
        except Exception as e:
            logging.exception(f"Error processing the {plane} view: {e}")
            pictures[plane] = "/placeholder.svg"
            continue
        for layer, labels, hidden in overlays:
            try:
                cut = volume.cut(labels, plane, index)
                image = annotations.compose(
                    image,
                    annotations.overlay_from_labels(cut, layer.items),
                    layer.items,
                    opacity=opacity,
                    hidden=hidden,
                )
            except Exception as e:
                logging.exception(f"Error drawing {layer.name} on the {plane} view: {e}")
        pictures[plane] = series.encode_png(image)
    return pictures


def format_dicom_error(error: Exception) -> str:
    message = str(error)
    lowered = message.lower()
    if (
        "transfer syntax" in lowered
        or "no handler" in lowered
        or "decode" in lowered
        or "compressed" in lowered
    ):
        return (
            "Unable to decode pixel data. This DICOM appears to use a compressed "
            "transfer syntax. Install pylibjpeg (and pylibjpeg-libjpeg) or gdcm to "
            "enable decompression, then restart the app."
        )
    return f"Error loading image: {message}"


def _metadata(ds) -> dict[str, Any]:
    """What the metadata panel shows of a file."""

    def get_val(tag, default="N/A"):
        return str(ds.get(tag, default))

    pos = ds.get("ImagePositionPatient", None)
    spacing = ds.get("PixelSpacing", None)
    thickness = ds.get("SliceThickness", None)
    return {
        "patient_name": get_val("PatientName"),
        "patient_id": get_val("PatientID"),
        "study_date": get_val("StudyDate"),
        "modality": get_val("Modality"),
        "study_description": get_val("StudyDescription"),
        "series_description": get_val("SeriesDescription"),
        "image_position": f"[{', '.join([f'{x:.1f}' for x in pos])}]" if pos else "N/A",
        "pixel_spacing": f"{spacing[0]:.3f} x {spacing[1]:.3f} mm" if spacing else "N/A",
        "slice_thickness": f"{thickness} mm" if thickness else "N/A",
        "rows": int(ds.get("Rows", 0)),
        "columns": int(ds.get("Columns", 0)),
    }


def crosshair_info(vol: volume.Volume, plane_index, modality: str) -> tuple[str, str]:
    """The crosshair's position in patient millimetres, and the value there."""
    z, y, x = (plane_index[plane] for plane in volume.PLANES)
    px, py, pz = vol.position(z, y, x)
    position = (
        f"{'L' if px >= 0 else 'R'} {abs(px):.1f} · "
        f"{'P' if py >= 0 else 'A'} {abs(py):.1f} · "
        f"{'S' if pz >= 0 else 'I'} {abs(pz):.1f} mm"
    )
    value = vol.value(z, y, x)
    text = f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
    return position, f"{text} HU" if modality == "CT" else text


def update_crosshair(room: SeriesRoom, series_id: str) -> None:
    s = SERIES.get(series_id)
    vol = volume.cached(s.volume_key) if s is not None else None
    if vol is None:
        return
    position, value = crosshair_info(vol, room.plane_index, room.modality)
    if room.crosshair_position != position:
        room.crosshair_position = position
    if room.crosshair_value != value:
        room.crosshair_value = value


def _drawing(room: SeriesRoom, s: Series) -> Drawing:
    """What the panel asks for, as the renderers take it."""
    visible = {
        layer.id: ([item.id for item in layer.rows if item.visible] if layer.visible else [])
        for layer in room.layers
    }
    return Drawing(visible, room.annotation_opacity / 100.0, room.show_annotation_labels, s.layers_rev)


# ------------------------------------------------------------ the room


def publish(room: SeriesRoom, series_id: str) -> None:
    """Shows who is on the series, and its call, to everyone on it (only what changed)."""
    peers = viewers.tabs(series_id)
    if list(room.peers) != peers:
        room.peers = peers
    here = {p["id"] for p in peers}
    if any(peer not in here for peer in room.pointers):
        room.pointers = {peer: p for peer, p in room.pointers.items() if peer in here}
    call.sync_room(room, series_id)


async def with_room(series_id: str, fn: Callable[[SeriesRoom], Any]) -> Any:
    """`fn(room)` on the series' room, through one of the tabs on the series; NOBODY when
    no tab is on it. `fn` runs under the room's lock and must not await."""
    from reflex.utils.prerequisites import get_app

    from dicom_viewer.states.dicom_state import DicomViewerState

    app = get_app().app
    for token in viewers.tokens_on(series_id):
        try:
            context = app.modify_state(rx.BaseStateToken(ident=token, cls=DicomViewerState))
            root = await context.__aenter__()
        except Exception as e:  # that tab's state can't be had: try the next one
            logging.warning(f"Cannot reach a tab on series {series_id}: {e}")
            continue
        found, result = False, None
        try:
            tab = await root.get_state(DicomViewerState)
            if tab.series == series_id:
                room = await root.get_state(SeriesRoom)
                if room._linked_to == room_token(series_id):
                    found, result = True, fn(room)
        except BaseException as error:
            if not await context.__aexit__(type(error), error, error.__traceback__):
                raise
            continue
        await context.__aexit__(None, None, None)
        if found:
            return result
    return NOBODY


async def push(series_id: str) -> None:
    """Shows who is on the series to everyone on it, from outside their own events (e.g. a
    tab that closed)."""
    await with_room(series_id, lambda room: publish(room, series_id))


def push_later(series_id: str) -> None:
    """`push`, once the event running now is done."""
    _spawn(push(series_id))


# ------------------------------------------------------------ tasks

_jobs: dict[tuple[str, str], int] = {}
_tickets = itertools.count(1)
_tasks: set[asyncio.Task] = set()


def _claim(series_id: str, job: str) -> int:
    """A ticket for running `job` on the series, or 0 when it is running already."""
    if (series_id, job) in _jobs:
        return 0
    ticket = _jobs[(series_id, job)] = next(_tickets)
    return ticket


def _release(series_id: str, job: str, ticket: int) -> None:
    if _jobs.get((series_id, job)) == ticket:
        del _jobs[(series_id, job)]


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def resume(room: SeriesRoom, series_id: str, folder: str) -> None:
    """A tab came to the series (its room linked): get done whatever it still needs, e.g.
    after a restart, or work left when everybody had gone."""
    s = SERIES.get(series_id)
    if s is None:
        if room.status != "loading":
            room.status = "loading"
            room.opening_done = room.opening_total = 0
            room.open_error = ""
        if (series_id, "open") not in _jobs:
            _spawn(_open(series_id, folder))
        return
    SERIES.move_to_end(series_id)
    if room.status != "ready" or room.series_path != s.folder:
        show(series_id, apply_ready(room, s))
        return
    if room.view_mode == "mpr" and room.mpr_status != "ready":
        load_volume(series_id)
    else:
        redraw(series_id, room.view_mode)
    if room.layers_status != "ready":
        rescan(series_id)


def redraw(series_id: str, mode: str) -> None:
    """The room asks for other pictures: draw them (in 1 view or 3 views)."""
    _spawn(_render(series_id) if mode == "single" else _render_planes_task(series_id))


def load_volume(series_id: str) -> None:
    _spawn(_load_volume(series_id))


def rescan(series_id: str) -> None:
    """Look again for annotation files beside the series, e.g. after downloading one."""
    _spawn(_load_layers(series_id))


def show(series_id: str, mode: str) -> None:
    """Draws a series just shown in its room, then reads its annotations."""
    _spawn(_show(series_id, mode))


async def _show(series_id: str, mode: str) -> None:
    if mode == "mpr":
        # The crosshair stays where it was (e.g. after a restart).
        await _load_volume(series_id, rebuild=True)
    else:
        await _render(series_id)
    await _load_layers(series_id)


def apply_ready(room: SeriesRoom, s: Series) -> str:
    """Shows a series that has been read in its room; returns the view mode to draw.

    A room that showed this series before (the server restarted) keeps its view.
    """
    same = room.series_path == s.folder and room.total_images == len(s.files)
    room.series_path = s.folder
    room.series_name = os.path.basename(os.path.normpath(s.folder))
    room.series_label = s.label
    room.total_images = len(s.files)
    room.auto_window = list(s.window)
    room.auto_source = s.auto_source
    room.open_error = ""
    if not same:
        room.current_index = 0
        room.current_image_base64 = "/placeholder.svg"
        room.error_message = ""
        room.window_center, room.window_width = s.window
        room.selected_preset = AUTO
        room.zoom_level, room.pan_x, room.pan_y = 1.0, 0, 0
        room.view_mode = "single"
        room.plane_index = {plane: 0 for plane in volume.PLANES}
        room.plane_count = {plane: 0 for plane in volume.PLANES}
        room.plane_ratio = {plane: 1.0 for plane in volume.PLANES}
        room.active_plane = "axial"
        room.show_crosshair = True
        room.layers = []
        room.layer_errors = []
        room.annotation_opacity = 40
        room.show_annotation_labels = True
        room.actor = {}
        for group in SLIDER_GROUPS:
            room._bump(group)
    # Built again in this process when needed.
    room.mpr_status = ""
    room.layers_status = ""
    room.status = "ready"
    return room.view_mode


def _failed(message: str) -> Callable[[SeriesRoom], None]:
    def put(room: SeriesRoom) -> None:
        room.status = "error"
        room.open_error = message

    return put


def _progress(done: int, total: int) -> Callable[[SeriesRoom], None]:
    def put(room: SeriesRoom) -> None:
        if room.status != "loading":
            room.status = "loading"
        room.opening_done, room.opening_total = done, total
        if room.open_error:
            room.open_error = ""

    return put


async def _open(series_id: str, folder: str) -> None:
    """Read the DICOM files in `folder`, order the slices and show them in the room."""
    ticket = _claim(series_id, "open")
    if not ticket:
        return
    try:
        if series_id in SERIES:
            return
        if not folders.is_inside(folder, folders.dicom_root()):
            await with_room(series_id, _failed("This folder is outside the DICOM folder."))
            return
        try:
            files = await asyncio.to_thread(folders.image_files, folder)
            await with_room(series_id, _progress(0, len(files)))
            keyed = []
            for start in range(0, len(files), _OPEN_BATCH):
                batch = files[start : start + _OPEN_BATCH]
                keyed += await asyncio.to_thread(series.read_slices, batch)
                await with_room(series_id, _progress(start + len(batch), len(files)))
        except OSError as e:
            logging.exception(f"Error opening {folder}: {e}")
            await with_room(series_id, _failed(folders.describe_error(e)))
            return
        if not keyed:
            await with_room(series_id, _failed("No readable DICOM images in this folder."))
            return
        keyed.sort(key=lambda item: item[0])
        ordered = [path for _, path, _ in keyed]
        refs = [ref for _, _, ref in keyed]
        window = await asyncio.to_thread(series.file_window, ordered[0])
        source = "file"
        if window is None:
            # Nothing suggested: work one out from a slice in the middle.
            window = await asyncio.to_thread(series.image_window, ordered[len(ordered) // 2])
            source = "image"
        label = await asyncio.to_thread(folders.series_label, ordered[0])
        s = Series(
            folder=folder,
            files=ordered,
            refs=refs,
            window=tuple(window or DEFAULT_WINDOW),
            auto_source=source if window else "default",
            label=label,
        )
        _remember(series_id, s)
        mode = await with_room(series_id, lambda room: apply_ready(room, s))
    finally:
        _release(series_id, "open", ticket)
    if mode is not NOBODY:
        await _show(series_id, mode)


async def _render(series_id: str) -> None:
    """Bring the room's picture in 1 view up to date with its slice, window and annotations."""
    ticket = _claim(series_id, "render")
    if not ticket:
        return
    try:
        while True:
            s = SERIES.get(series_id)
            if s is None:
                return

            def wanted(room: SeriesRoom):
                if room.status != "ready" or room.view_mode != "single" or not 0 <= room.current_index < len(s.files):
                    want = None
                else:
                    want = (room.current_index, room.window_center, room.window_width, _drawing(room, s))
                if want is None or want == s.drawn:
                    # Done, decided under the room's lock: a change after this starts a new round.
                    _release(series_id, "render", ticket)
                    return None
                return want

            want = await with_room(series_id, wanted)
            if want is None or want is NOBODY:
                return
            index, center, width, drawing = want
            ds = None
            pixels, slope, intercept = s.pixels, s.slope, s.intercept
            if s.index != index:
                try:
                    ds, pixels, slope, intercept = await asyncio.to_thread(series.read_slice, s.files[index])
                except Exception as e:
                    logging.exception(f"Error loading image {s.files[index]}: {e}")
                    message = format_dicom_error(e)

                    def failed(room: SeriesRoom) -> None:
                        room.error_message = message
                        room.current_image_base64 = "/placeholder.svg"

                    s.index, s.pixels, s.drawn = -1, None, want
                    await with_room(series_id, failed)
                    continue
            ref = s.refs[index] if index < len(s.refs) else None
            image = await asyncio.to_thread(
                _render_slice, pixels, slope, intercept, center, width, ref, _layers_of(s.layers_key), drawing
            )
            meta = _metadata(ds) if ds is not None else None
            if ds is not None:
                s.index, s.pixels, s.slope, s.intercept = index, pixels, slope, intercept

            def put(room: SeriesRoom) -> None:
                room.current_image_base64 = image
                if meta is not None:
                    for name, value in meta.items():
                        if getattr(room, name) != value:
                            setattr(room, name, value)
                    if room.error_message:
                        room.error_message = ""

            s.drawn = want
            await with_room(series_id, put)
    finally:
        _release(series_id, "render", ticket)


async def _render_planes_task(series_id: str) -> None:
    """Bring the room's three plane pictures up to date; coalesces like _render."""
    ticket = _claim(series_id, "planes")
    if not ticket:
        return
    missing = False
    try:
        while True:
            s = SERIES.get(series_id)
            if s is None:
                return
            key = s.volume_key
            vol = volume.cached(key)

            def wanted(room: SeriesRoom):
                nonlocal missing
                if room.view_mode != "mpr" or room.mpr_status != "ready":
                    _release(series_id, "planes", ticket)
                    return None
                drawing = _drawing(room, s)
                window = (room.window_center, room.window_width)
                want = {plane: (key, room.plane_index[plane], *window, drawing) for plane in volume.PLANES}
                stale = [plane for plane in volume.PLANES if s.plane_drawn.get(plane) != want[plane]]
                if not stale:
                    _release(series_id, "planes", ticket)
                    return None
                if vol is None:
                    # Dropped from the cache to make room for other volumes: build it again.
                    room.mpr_status = ""
                    missing = True
                    return None
                return want, stale, window, drawing

            got = await with_room(series_id, wanted)
            if got is None or got is NOBODY:
                return
            want, stale, window, drawing = got
            overlays = _plane_overlays(key, _layers_of(s.layers_key), drawing)
            images = await asyncio.to_thread(
                _render_planes,
                vol,
                {plane: want[plane][1] for plane in stale},
                *window,
                overlays,
                drawing.opacity,
            )
            for plane in images:
                s.plane_drawn[plane] = want[plane]

            def put(room: SeriesRoom) -> None:
                for plane, image in images.items():
                    setattr(room, f"{plane}_image", image)

            await with_room(series_id, put)
    finally:
        _release(series_id, "planes", ticket)
    if missing:
        await _load_volume(series_id, rebuild=True)


async def _load_volume(series_id: str, rebuild: bool = False) -> None:
    """Stack the series into a volume (or reuse it) and draw the planes.

    `rebuild` is for a volume dropped from the cache while in use: its
    crosshair stays where it was.
    """
    ticket = _claim(series_id, "volume")
    if not ticket:
        return
    try:
        s = SERIES.get(series_id)
        if s is None:
            return
        files = s.files
        key = volume.cache_key(files)
        vol = volume.cached(key)

        def start(room: SeriesRoom) -> str:
            # Start on the image shown in one view, or in the middle of the
            # volume while one view is still on the first image.
            if vol is None:
                room.mpr_status = "loading"
                room.mpr_done = 0
                room.mpr_total = 0
                room.mpr_error = ""
            return files[room.current_index] if 0 < room.current_index < len(files) else ""

        current_path = await with_room(series_id, start)
        if current_path is NOBODY:
            return
        if vol is None:
            try:
                layout = await asyncio.to_thread(volume.read_layout, files)
                await with_room(series_id, lambda room: setattr(room, "mpr_total", len(layout.paths)))
                slices = []
                for begin in range(0, len(layout.paths), _OPEN_BATCH):
                    batch = layout.paths[begin : begin + _OPEN_BATCH]
                    slices += await asyncio.to_thread(volume.read_pixels, batch)
                    if SERIES.get(series_id) is not s:  # read again meanwhile
                        return
                    done = begin + len(batch)
                    await with_room(series_id, lambda room: setattr(room, "mpr_done", done))
                vol = await asyncio.to_thread(volume.assemble, layout, slices)
            except Exception as e:
                if not isinstance(e, volume.VolumeError):
                    logging.exception(f"Error building the volume: {e}")
                message = str(e) if isinstance(e, volume.VolumeError) else format_dicom_error(e)

                def failed(room: SeriesRoom) -> None:
                    room.mpr_status = "error"
                    room.mpr_error = message

                await with_room(series_id, failed)
                return
            volume.store(key, vol)

        def ready(room: SeriesRoom) -> None:
            counts = {plane: vol.count(plane) for plane in volume.PLANES}
            cursor = vol.cursor_at(current_path)
            s.volume_key = key
            s.plane_drawn = {}
            if dict(room.plane_count) != counts:
                # A volume new to this room.
                room.plane_count = counts
                room.plane_ratio = {plane: vol.ratio(plane) for plane in volume.PLANES}
                room.plane_index = cursor
                room.active_plane = vol.scan_plane
            elif not rebuild:
                # Seen before: follow one view's image, keep the other planes.
                room.plane_index[vol.scan_plane] = cursor[vol.scan_plane]
            room.mpr_notes = volume.notes(vol)
            room._bump("plane")
            update_crosshair(room, series_id)
            room.mpr_status = "ready"

        if await with_room(series_id, ready) is NOBODY:
            return
    finally:
        _release(series_id, "volume", ticket)
    await _load_labels(series_id, key, vol)
    await _render_planes_task(series_id)


async def _load_layers(series_id: str) -> None:
    """Find the annotation objects for the series and list them in the room's panel."""
    ticket = _claim(series_id, "layers")
    if not ticket:
        return
    try:
        s = SERIES.get(series_id)
        if s is None:
            return

        def start(room: SeriesRoom) -> None:
            room.layers_status = "loading"

        if await with_room(series_id, start) is NOBODY:
            return
        folder = s.folder
        key = volume.cache_key(s.files)
        entry = _cached_layers(key) or _Loaded()
        errors: list[tuple[str, str]] = []
        try:
            paths = await asyncio.to_thread(annotations.find_layer_files, folder)
        except Exception as e:
            logging.exception(f"Error looking for annotations in {folder}: {e}")
            paths = list(entry.by_path)
            errors.append((folder, str(e)))
        new = [path for path in paths if path not in entry.by_path]
        if new:
            failures: list[tuple[str, str]] = []
            try:
                info = annotations.series_info(s.refs)
                loaded = await asyncio.to_thread(annotations.load_layers, info, new, failures)
            except Exception as e:
                logging.exception(f"Error reading the annotations in {folder}: {e}")
                loaded = []
                errors.append((folder, str(e)))
            by_path = {layer.source_path: layer for layer in loaded}
            failed = dict(failures)
            for path in new:
                entry.by_path[path] = (by_path.get(path), failed.get(path, ""))
        found = [entry.by_path[path] for path in paths if path in entry.by_path]
        entry.layers = [layer for layer, _ in found if layer is not None]
        annotations.spread(entry.layers)
        errors += [(path, entry.by_path[path][1]) for path in paths
                   if path in entry.by_path and entry.by_path[path][1]]
        _store_layers(key, entry)
        s.layers_key = key
        s.layers_rev += 1
        views = [_layer_view(layer) for layer in entry.layers]
        messages = [f"{os.path.basename(path)}: {message}" for path, message in errors]

        def put(room: SeriesRoom) -> tuple[str, str]:
            # What was switched off stays off (after a restart, or a rescan).
            before = {layer.id: layer for layer in room.layers}
            for view in views:
                old = before.get(view.id)
                if old is not None:
                    view.visible = old.visible
                    shown = {item.id: item.visible for item in old.rows}
                    for item in view.rows:
                        item.visible = shown.get(item.id, True)
            room.layers = views
            room.layer_errors = messages
            room.layers_status = "ready"
            room._bump("opacity")
            return room.view_mode, room.mpr_status

        got = await with_room(series_id, put)
    finally:
        _release(series_id, "layers", ticket)
    if got is NOBODY or not entry.layers:
        return
    mode, mpr_status = got
    if mode == "mpr" and mpr_status == "ready":
        vol = volume.cached(s.volume_key)
        if vol is not None:
            await _load_labels(series_id, s.volume_key, vol)
        await _render_planes_task(series_id)
    elif mode == "single":
        await _render(series_id)


async def _load_labels(series_id: str, key: str, vol: volume.Volume) -> None:
    """Build the label volumes of the layers that 3 views can show (kept per volume)."""
    s = SERIES.get(series_id)
    if s is None:
        return
    layers = _layers_of(s.layers_key)
    refs_by_path = dict(zip(s.files, s.refs))
    stack = [refs_by_path.get(path) for path in vol.layout.paths]
    if not layers or any(ref is None for ref in stack):
        return
    for layer in layers:
        if not layer.supports_volume or (key, layer.id) in _label_cache:
            continue
        labels = await asyncio.to_thread(_labels_for, layer, stack, vol)
        _store_labels((key, layer.id), labels)
