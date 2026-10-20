"""Drawing a SliceOverlay onto a picture: the one place colours meet pixels.

Works on PIL images at the slice's native resolution, before the viewer
encodes the PNG, so it serves the single view and the three-plane cuts alike.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from dicom_viewer.annotations.model import Item, Layer, Mask, Shape, SliceOverlay, SliceRef

_ELLIPSE_SEGMENTS = 72


def _item_map(items: Mapping[str, Item] | Iterable[Item]) -> dict[str, Item]:
    if isinstance(items, Mapping):
        return dict(items)
    return {item.id: item for item in items}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1 has no sized default font
        return ImageFont.load_default()


def _ellipse_outline(points: np.ndarray) -> list[tuple[float, float]]:
    """Points around an ellipse given both ends of its major axis, then of its minor axis."""
    major, minor = points[1] - points[0], points[3] - points[2]
    centre = (points[0] + points[1]) / 2.0
    a, b = np.linalg.norm(major) / 2.0, np.linalg.norm(minor) / 2.0
    angle = math.atan2(major[1], major[0])
    t = np.linspace(0.0, 2.0 * math.pi, _ELLIPSE_SEGMENTS, endpoint=False)
    x, y = a * np.cos(t), b * np.sin(t)
    xs = centre[0] + x * math.cos(angle) - y * math.sin(angle)
    ys = centre[1] + x * math.sin(angle) + y * math.cos(angle)
    return list(zip(xs.tolist(), ys.tolist()))


def _draw_shape(draw: ImageDraw.ImageDraw, shape: Shape, color, width: int) -> None:
    # ImageDraw addresses pixel centres with integers; our convention puts
    # centres at .5, so shift once here.
    xy = shape.points - 0.5
    points = [tuple(p) for p in xy.tolist()]
    if shape.kind == "point":
        (x, y), radius = points[0], max(2.0, width + 1.0)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
    elif shape.kind == "polyline":
        if shape.closed:
            points.append(points[0])
        draw.line(points, fill=color, width=width, joint="curve")
    elif shape.kind == "polygon":
        draw.polygon(points, outline=color, width=width)
    elif shape.kind == "circle":
        (cx, cy), (px, py) = points[0], points[1]
        radius = math.hypot(px - cx, py - cy)
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=color, width=width)
    elif shape.kind == "ellipse":
        draw.polygon(_ellipse_outline(xy), outline=color, width=width)


def compose(
    image: Image.Image,
    overlay: SliceOverlay,
    items: Mapping[str, Item] | Iterable[Item],
    *,
    opacity: float = 0.4,
    line_width: int = 2,
    show_labels: bool = True,
    label_size: int = 12,
    hidden: Iterable[str] = (),
) -> Image.Image:
    """A new RGB picture: `image` with `overlay` drawn on it.

    `items` gives each primitive's colour by item id; primitives whose item
    is unknown or listed in `hidden` are skipped. Masks are blended with
    `opacity`, shapes outlined `line_width` pixels wide, labels drawn with a
    dark halo. Masks must have the picture's (rows, columns) shape.
    """
    colors = _item_map(items)
    hidden = set(hidden)

    def color_of(item_id: str):
        item = colors.get(item_id)
        return None if item is None or item_id in hidden else tuple(item.color)

    rgb = image.convert("RGB")
    width, height = rgb.size
    if overlay.masks:
        pixels = np.asarray(rgb, dtype=np.float32)
        for mask in overlay.masks:
            color = color_of(mask.item_id)
            if color is None:
                continue
            if mask.pixels.shape != (height, width):
                raise ValueError(
                    f"Mask for item {mask.item_id!r} has shape {mask.pixels.shape}, "
                    f"the picture is {(height, width)}"
                )
            tint = np.array(color, dtype=np.float32)
            pixels[mask.pixels] = pixels[mask.pixels] * (1.0 - opacity) + tint * opacity
        rgb = Image.fromarray(np.rint(np.clip(pixels, 0, 255)).astype(np.uint8), "RGB")
    if overlay.shapes or (show_labels and overlay.labels):
        draw = ImageDraw.Draw(rgb)
        for shape in overlay.shapes:
            color = color_of(shape.item_id)
            if color is not None:
                _draw_shape(draw, shape, color, line_width)
        if show_labels and overlay.labels:
            font = _font(label_size)
            for label in overlay.labels:
                color = color_of(label.item_id)
                if color is None:
                    continue
                x, y = label.anchor
                draw.text(
                    (x - 0.5, y - 0.5), label.text, fill=color, font=font,
                    stroke_width=2, stroke_fill=(0, 0, 0),
                )
    return rgb


def overlay_from_labels(labels: np.ndarray, items: Sequence[Item]) -> SliceOverlay:
    """Masks for one cut of a label volume: value v becomes a Mask of items[v - 1]."""
    labels = np.asarray(labels)
    if labels.ndim != 2:
        raise ValueError(f"labels must be 2-D (rows, columns), got shape {labels.shape}")
    overlay = SliceOverlay()
    for value in np.unique(labels):
        index = int(value)
        if index <= 0:
            continue
        if index > len(items):
            logging.warning("Label %d has no item (layer has %d items)", index, len(items))
            continue
        overlay.masks.append(Mask(items[index - 1].id, labels == index))
    return overlay


def render(
    image: Image.Image,
    ref: SliceRef,
    layers: Iterable[Layer],
    *,
    visible: Mapping[str, Iterable[str] | None] | None = None,
    **options,
) -> Image.Image:
    """`image` with every layer drawn on it in order; `options` go to compose().

    `visible` maps a layer id to the item ids to show. A layer missing from
    it, or mapped to None, shows all its items; one mapped to an empty
    collection is hidden.
    """
    for layer in layers:
        wanted = None
        if visible is not None and layer.id in visible:
            wanted = visible[layer.id]
            if wanted is not None:
                wanted = list(wanted)
                if not wanted:
                    continue
        overlay = layer.overlay(ref, wanted)
        if not overlay.is_empty():
            image = compose(image, overlay, layer.items, **options)
    return image
