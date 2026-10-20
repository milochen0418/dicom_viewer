"""RT Structure Sets (RTSTRUCT): contours in patient millimetres, drawn as outlines.

Read with pydicom only. A contour is drawn on the image it names
(ContourImageSequence), else on any slice whose plane it lies in; the
three-plane view gets the contours filled in.
"""

from __future__ import annotations

import collections
import dataclasses
from collections.abc import Iterable, Sequence

import numpy as np

from dicom_viewer.annotations import geometry
from dicom_viewer.annotations.colors import default_color
from dicom_viewer.annotations.model import (
    BaseLayer,
    Item,
    Layer,
    SeriesInfo,
    Shape,
    SliceOverlay,
    SliceRef,
)
from dicom_viewer.annotations.registry import register
from dicom_viewer.annotations.support import matches, polygon_mask, read_dataset, text

RT_STRUCTURE_SET_STORAGE = "1.2.840.10008.5.1.4.1.1.481.3"
_KINDS = {
    "CLOSED_PLANAR": "polygon",
    "CLOSEDPLANAR_XOR": "polygon",
    "OPEN_PLANAR": "polyline",
    "OPEN_NONPLANAR": "polyline",
    "POINT": "point",
}
_OTHER_SERIES_NOTE = "Made for another series; drawn on the images its contours lie in."


@dataclasses.dataclass(frozen=True)
class Contour:
    item_id: str
    kind: str
    points: np.ndarray  # (n, 3) mm
    image_uid: str  # "" when the contour names no image


def _plane_tolerance(contours: Sequence[Contour]) -> float:
    """Half the gap between the contour planes, so a slice matches one plane only."""
    planar = [c for c in contours if len(c.points) >= 3]
    if not planar:
        return 0.5
    first = planar[0].points
    normal = np.cross(first[1] - first[0], first[2] - first[0])
    if np.linalg.norm(normal) == 0:
        return 0.5
    normal /= np.linalg.norm(normal)
    heights = sorted({round(float(c.points[0] @ normal), 2) for c in planar})
    gaps = np.diff(heights)
    if len(gaps) == 0:
        return 0.5
    return float(min(2.5, max(0.1, np.median(gaps) / 2.0)))


class RtStructLayer(BaseLayer):
    supports_volume = True

    def __init__(self, path: str, ds) -> None:
        names: dict[int, str] = {}
        frame_of_reference = ""
        for roi in ds.get("StructureSetROISequence", []):
            number = int(roi.get("ROINumber", 0) or 0)
            names[number] = text(roi, "ROIName") or f"ROI {number}"
            frame_of_reference = frame_of_reference or text(roi, "ReferencedFrameOfReferenceUID")
        self._series: set[str] = set()
        self._instances: set[str] = set()
        for referenced in ds.get("ReferencedFrameOfReferenceSequence", []):
            frame_of_reference = frame_of_reference or text(referenced, "FrameOfReferenceUID")
            for study in referenced.get("RTReferencedStudySequence", []):
                for series in study.get("RTReferencedSeriesSequence", []):
                    if series.get("SeriesInstanceUID"):
                        self._series.add(str(series.SeriesInstanceUID))
                    for image in series.get("ContourImageSequence", []):
                        if image.get("ReferencedSOPInstanceUID"):
                            self._instances.add(str(image.ReferencedSOPInstanceUID))
        self._frame_of_reference = frame_of_reference
        items: list[Item] = []
        contours: list[Contour] = []
        for index, roi_contour in enumerate(ds.get("ROIContourSequence", [])):
            number = int(roi_contour.get("ReferencedROINumber", index) or 0)
            item_id = str(number)
            color = roi_contour.get("ROIDisplayColor")
            try:
                color = tuple(int(c) for c in color) if color is not None else None
            except (TypeError, ValueError):
                color = None
            if color is None or len(color) != 3:
                color = default_color(index)
            items.append(Item(item_id, names.get(number, f"ROI {number}"), color, "contour"))
            for contour in roi_contour.get("ContourSequence", []):
                data = np.asarray(contour.get("ContourData", []), dtype=float)
                if data.size < 3 or data.size % 3:
                    continue
                points = data.reshape(-1, 3)
                kind = _KINDS.get(text(contour, "ContourGeometricType", "CLOSED_PLANAR"), "polygon")
                if kind == "polygon" and len(points) < 3:
                    kind = "polyline" if len(points) == 2 else "point"
                image_uid = ""
                images = contour.get("ContourImageSequence", [])
                if images:
                    image_uid = text(images[0], "ReferencedSOPInstanceUID")
                    if image_uid:
                        self._instances.add(image_uid)
                contours.append(Contour(item_id, kind, points, image_uid))
        self._contours = contours
        self._by_image: dict[str, list[Contour]] = collections.defaultdict(list)
        for contour in contours:
            if contour.image_uid:
                self._by_image[contour.image_uid].append(contour)
        self._tolerance = _plane_tolerance(contours)
        name = (
            text(ds, "StructureSetLabel")
            or text(ds, "StructureSetName")
            or text(ds, "SeriesDescription")
            or "Structure set"
        )
        super().__init__(
            layer_id=str(ds.SOPInstanceUID),
            format="RTSTRUCT",
            name=name,
            source_path=path,
            items=items,
        )

    def applies_to(self, series: SeriesInfo) -> bool:
        if matches(series, series_uids=self._series, instance_uids=self._instances):
            return True
        if self._frame_of_reference and self._frame_of_reference == series.frame_of_reference_uid:
            if _OTHER_SERIES_NOTE not in self.notes:
                self.notes.append(_OTHER_SERIES_NOTE)
            return True
        return False

    def _on_plane(self, ref: SliceRef, contour: Contour) -> bool:
        return abs(float(geometry.plane_distance(ref, contour.points[0])[0])) <= self._tolerance

    def contours_on(self, ref: SliceRef) -> list[Contour]:
        """The contours to draw on a slice: those naming it, else those lying in its plane."""
        found = list(self._by_image.get(ref.sop_instance_uid, []))
        if not ref.has_geometry:
            return found
        found += [c for c in self._contours if not c.image_uid and self._on_plane(ref, c)]
        if not found:
            found = [c for c in self._contours if c.image_uid and self._on_plane(ref, c)]
        return found

    def overlay(self, ref: SliceRef, item_ids: Iterable[str] | None = None) -> SliceOverlay:
        out = SliceOverlay()
        if not ref.has_geometry:
            return out
        wanted = {item.id for item in self.selected(item_ids)}
        for contour in self.contours_on(ref):
            if contour.item_id not in wanted:
                continue
            points = geometry.to_pixels(ref, contour.points)
            if contour.kind == "point":
                out.shapes.extend(Shape(contour.item_id, "point", [p]) for p in points)
            else:
                out.shapes.append(
                    Shape(contour.item_id, contour.kind, points, closed=contour.kind == "polygon")
                )
        return out

    def label_volume(self, refs: Sequence[SliceRef]) -> np.ndarray | None:
        if not refs or not all(ref.has_geometry for ref in refs):
            return None
        shape = refs[0].shape
        if any(ref.shape != shape for ref in refs):
            return None
        index_of = {item.id: index + 1 for index, item in enumerate(self.items)}
        dtype = np.uint8 if len(self.items) < 256 else np.uint16
        labels = np.zeros((len(refs), *shape), dtype=dtype)
        for k, ref in enumerate(refs):
            masks: dict[str, np.ndarray] = {}
            for contour in self.contours_on(ref):
                if contour.kind != "polygon":
                    continue
                mask = polygon_mask(geometry.to_pixels(ref, contour.points), shape)
                masks[contour.item_id] = masks[contour.item_id] | mask if contour.item_id in masks else mask
            # Bigger structures first, so the small ones stay visible where they overlap.
            for item_id in sorted(masks, key=lambda i: -int(masks[i].sum())):
                labels[k][masks[item_id]] = index_of[item_id]
        return labels


class RtStructAdapter:
    name = "rtstruct"
    sop_class_uids = frozenset({RT_STRUCTURE_SET_STORAGE})

    def load(self, path: str) -> Layer:
        return RtStructLayer(path, read_dataset(path, "structure set"))


register(RtStructAdapter())
