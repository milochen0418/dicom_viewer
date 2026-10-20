"""Softcopy Presentation States (GSPS and its colour variants): graphics and text.

Read with pydicom only. Each graphic layer of the presentation state is an
item. Graphics in PIXEL units are drawn as they are; DISPLAY units are
mapped through the displayed area the state selects for the image.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

import numpy as np

from dicom_viewer.annotations.colors import default_color, dicom_cielab_to_rgb
from dicom_viewer.annotations.model import (
    BaseLayer,
    Item,
    Label,
    Layer,
    SeriesInfo,
    Shape,
    SliceOverlay,
    SliceRef,
)
from dicom_viewer.annotations.registry import register
from dicom_viewer.annotations.support import (
    matches,
    read_dataset,
    referenced_instances,
    referenced_series,
    text,
)

GRAYSCALE_SOFTCOPY_PRESENTATION_STATE = "1.2.840.10008.5.1.4.1.1.11.1"
COLOR_SOFTCOPY_PRESENTATION_STATE = "1.2.840.10008.5.1.4.1.1.11.2"
PSEUDO_COLOR_SOFTCOPY_PRESENTATION_STATE = "1.2.840.10008.5.1.4.1.1.11.3"
BLENDING_SOFTCOPY_PRESENTATION_STATE = "1.2.840.10008.5.1.4.1.1.11.4"
XA_XRF_GRAYSCALE_SOFTCOPY_PRESENTATION_STATE = "1.2.840.10008.5.1.4.1.1.11.5"
_KINDS = {
    "POINT": "point",
    "POLYLINE": "polyline",
    "INTERPOLATED": "polyline",
    "CIRCLE": "circle",
    "ELLIPSE": "ellipse",
}


@dataclasses.dataclass(frozen=True)
class _Graphic:
    kind: str
    units: str  # PIXEL or DISPLAY
    points: np.ndarray  # (n, 2)


@dataclasses.dataclass(frozen=True)
class _Text:
    text: str
    units: str
    anchor: tuple[float, float]


@dataclasses.dataclass(frozen=True)
class _Annotation:
    """One GraphicAnnotationSequence item: graphics and text for some images."""

    item_id: str
    images: frozenset[str]  # empty: every image the state applies to
    graphics: tuple[_Graphic, ...]
    texts: tuple[_Text, ...]


@dataclasses.dataclass(frozen=True)
class _Area:
    images: frozenset[str]
    top_left: tuple[float, float]  # 1-based pixel indices (column, row)
    bottom_right: tuple[float, float]


def _image_uids(sequence) -> frozenset[str]:
    return frozenset(
        str(item.ReferencedSOPInstanceUID)
        for item in sequence or []
        if "ReferencedSOPInstanceUID" in item
    )


def _pair(value) -> tuple[float, float] | None:
    try:
        numbers = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    return (numbers[0], numbers[1]) if len(numbers) >= 2 else None


class GspsLayer(BaseLayer):
    def __init__(self, path: str, ds) -> None:
        self._series = referenced_series(ds)
        self._instances = referenced_instances(ds)
        colors: dict[str, tuple[int, int, int]] = {}
        order: dict[str, int] = {}
        for layer in ds.get("GraphicLayerSequence", []):
            name = text(layer, "GraphicLayer")
            lab = layer.get("GraphicLayerRecommendedDisplayCIELabValue")
            if lab is not None:
                colors[name] = dicom_cielab_to_rgb(lab)
            order[name] = int(layer.get("GraphicLayerOrder", len(order)) or 0)
        annotations: list[_Annotation] = []
        names: list[str] = []
        for annotation in ds.get("GraphicAnnotationSequence", []):
            name = text(annotation, "GraphicLayer", "Annotations")
            if name not in names:
                names.append(name)
            graphics = []
            for graphic in annotation.get("GraphicObjectSequence", []):
                kind = _KINDS.get(text(graphic, "GraphicType"))
                data = np.asarray(graphic.get("GraphicData", []), dtype=float)
                if kind is None or data.size < 2 or data.size % 2:
                    continue
                points = data.reshape(-1, 2)
                if kind == "point":
                    points = points[:1]
                graphics.append(_Graphic(kind, text(graphic, "GraphicAnnotationUnits", "PIXEL"), points))
            texts = []
            for item in annotation.get("TextObjectSequence", []):
                value = text(item, "UnformattedTextValue")
                anchor = _pair(item.get("AnchorPoint"))
                units = text(item, "AnchorPointAnnotationUnits", "PIXEL")
                if anchor is None:
                    anchor = _pair(item.get("BoundingBoxTopLeftHandCorner"))
                    units = text(item, "BoundingBoxAnnotationUnits", "PIXEL")
                if value and anchor is not None:
                    texts.append(_Text(value, units, anchor))
            annotations.append(
                _Annotation(
                    name,
                    _image_uids(annotation.get("ReferencedImageSequence")),
                    tuple(graphics),
                    tuple(texts),
                )
            )
        self._annotations = annotations
        self._areas = [
            _Area(_image_uids(area.get("ReferencedImageSequence")), top_left, bottom_right)
            for area in ds.get("DisplayedAreaSelectionSequence", [])
            if (top_left := _pair(area.get("DisplayedAreaTopLeftHandCorner"))) is not None
            and (bottom_right := _pair(area.get("DisplayedAreaBottomRightHandCorner"))) is not None
        ]
        names.sort(key=lambda name: order.get(name, len(order)))
        items = [
            Item(name, name, colors.get(name, default_color(index)), "graphic")
            for index, name in enumerate(names)
        ]
        super().__init__(
            layer_id=str(ds.SOPInstanceUID),
            format="PR",
            name=text(ds, "ContentLabel") or text(ds, "ContentDescription") or "Presentation state",
            source_path=path,
            items=items,
        )

    def applies_to(self, series: SeriesInfo) -> bool:
        return matches(series, series_uids=self._series, instance_uids=self._instances)

    def _area_for(self, ref: SliceRef) -> _Area | None:
        for area in self._areas:
            if ref.sop_instance_uid in area.images:
                return area
        return next((area for area in self._areas if not area.images), None)

    def _to_pixels(self, ref: SliceRef, points: np.ndarray, units: str) -> np.ndarray | None:
        if units == "PIXEL":
            return points
        if units != "DISPLAY":
            return None
        area = self._area_for(ref)
        if area is None:
            return None
        top_left = np.array(area.top_left) - 1.0  # 1-based index -> corner of the pixel
        size = np.array(area.bottom_right) - np.array(area.top_left) + 1.0
        return top_left + points * size

    def _covers(self, annotation: _Annotation, ref: SliceRef) -> bool:
        if annotation.images:
            return ref.sop_instance_uid in annotation.images
        return not self._instances or ref.sop_instance_uid in self._instances

    def overlay(self, ref: SliceRef, item_ids: Iterable[str] | None = None) -> SliceOverlay:
        out = SliceOverlay()
        wanted = {item.id for item in self.selected(item_ids)}
        for annotation in self._annotations:
            if annotation.item_id not in wanted or not self._covers(annotation, ref):
                continue
            for graphic in annotation.graphics:
                points = self._to_pixels(ref, graphic.points, graphic.units)
                if points is not None:
                    out.shapes.append(Shape(annotation.item_id, graphic.kind, points))
            for item in annotation.texts:
                anchor = self._to_pixels(ref, np.array([item.anchor]), item.units)
                if anchor is not None:
                    out.labels.append(Label(annotation.item_id, item.text, tuple(anchor[0])))
        return out


class GspsAdapter:
    name = "gsps"
    sop_class_uids = frozenset(
        {
            GRAYSCALE_SOFTCOPY_PRESENTATION_STATE,
            COLOR_SOFTCOPY_PRESENTATION_STATE,
            PSEUDO_COLOR_SOFTCOPY_PRESENTATION_STATE,
            BLENDING_SOFTCOPY_PRESENTATION_STATE,
            XA_XRF_GRAYSCALE_SOFTCOPY_PRESENTATION_STATE,
        }
    )

    def load(self, path: str) -> Layer:
        return GspsLayer(path, read_dataset(path, "presentation state"))


register(GspsAdapter())
