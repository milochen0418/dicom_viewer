"""Structured Reports with imaging measurements (TID 1500), read with highdicom.

Each ROI measurement group is an item: its region is drawn on the image it
refers to and its measurements are written beside it. Reports without
measurement groups load as empty layers with a note, so the user learns why
nothing is drawn.

highdicom is imported when an object is loaded, so the viewer runs without
it and reports the missing package on the layer instead.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable

import numpy as np

from dicom_viewer.annotations import geometry
from dicom_viewer.annotations.colors import default_color
from dicom_viewer.annotations.model import (
    BaseLayer,
    Item,
    Label,
    Layer,
    LayerError,
    SeriesInfo,
    Shape,
    SliceOverlay,
    SliceRef,
)
from dicom_viewer.annotations.registry import register
from dicom_viewer.annotations.support import enum_name, matches, text

ENHANCED_SR = "1.2.840.10008.5.1.4.1.1.88.22"
COMPREHENSIVE_SR = "1.2.840.10008.5.1.4.1.1.88.33"
COMPREHENSIVE_3D_SR = "1.2.840.10008.5.1.4.1.1.88.34"
_KINDS = {"POINT": "point", "MULTIPOINT": "point", "POLYLINE": "polyline", "CIRCLE": "circle", "ELLIPSE": "ellipse"}
# A 3D region is drawn on a slice when all its points lie this close to the plane.
_PLANE_TOLERANCE_MM = 1.0


@dataclasses.dataclass(frozen=True)
class _Region:
    item_id: str
    kind: str
    points: np.ndarray  # (n, 2) pixels, or (n, 3) mm when `in_3d`
    image_uid: str
    in_3d: bool


def _evidence(sr) -> tuple[set[str], set[str]]:
    """Series and instance UIDs the report lists as its evidence."""
    series_uids: set[str] = set()
    instance_uids: set[str] = set()
    for study in sr.get("CurrentRequestedProcedureEvidenceSequence", []):
        for series in study.get("ReferencedSeriesSequence", []):
            if series.get("SeriesInstanceUID"):
                series_uids.add(str(series.SeriesInstanceUID))
            for item in series.get("ReferencedSOPSequence", []):
                if item.get("ReferencedSOPInstanceUID"):
                    instance_uids.add(str(item.ReferencedSOPInstanceUID))
    return series_uids, instance_uids


def _region_image(roi) -> str:
    """The SOPInstanceUID a 2D region's coordinates refer to."""
    for item in roi.get("ContentSequence", []):
        if text(item, "ValueType") == "IMAGE":
            for referenced in item.get("ReferencedSOPSequence", []):
                uid = text(referenced, "ReferencedSOPInstanceUID")
                if uid:
                    return uid
    return ""


def _measurement_text(measurement) -> str:
    try:
        name = measurement.name.meaning
        unit = getattr(measurement.unit, "value", "") or ""
        value = float(measurement.value)
    except Exception:  # noqa: BLE001 - odd measurements are left out of the label
        return ""
    unit = {"mm2": "mm²", "mm3": "mm³", "cm2": "cm²", "cm3": "cm³", "1": ""}.get(unit, unit)
    return f"{name} {value:g} {unit}".strip()


class SrLayer(BaseLayer):
    def __init__(self, path: str, sr, report) -> None:
        self._series, self._instances = _evidence(sr)
        self._regions: list[_Region] = []
        items: list[Item] = []
        groups = []
        if report is not None:
            groups = list(report.get_planar_roi_measurement_groups()) + list(
                report.get_volumetric_roi_measurement_groups()
            )
        for index, group in enumerate(groups):
            item_id = str(getattr(group, "tracking_uid", "") or "") or f"group-{index}"
            finding = getattr(group, "finding_type", None)
            name = (
                (finding.meaning if finding is not None else "")
                or str(getattr(group, "tracking_identifier", "") or "")
                or f"Finding {index + 1}"
            )
            measurements = [_measurement_text(m) for m in group.get_measurements()]
            description = "; ".join(m for m in measurements if m)
            rois = getattr(group, "roi", None)
            if rois is None:
                rois = []
            elif not isinstance(rois, (list, tuple)):
                rois = [rois]
            for roi in rois:
                kind = _KINDS.get(enum_name(getattr(roi, "graphic_type", "")))
                value = getattr(roi, "value", None)
                if kind is None or value is None:
                    continue
                points = np.asarray(value, dtype=float)
                in_3d = points.ndim == 2 and points.shape[1] == 3
                if points.ndim != 2 or points.shape[1] not in (2, 3):
                    continue
                self._regions.append(
                    _Region(item_id, kind, points, "" if in_3d else _region_image(roi), in_3d)
                )
            self._instances |= {r.image_uid for r in self._regions if r.image_uid}
            items.append(Item(item_id, name, default_color(index), "measurement", description=description))
        notes = []
        if not groups:
            notes.append("This report has no imaging measurement groups to draw.")
        title = ""
        try:
            title = sr.ConceptNameCodeSequence[0].CodeMeaning
        except Exception:  # noqa: BLE001 - a missing title is fine
            pass
        super().__init__(
            layer_id=str(sr.SOPInstanceUID),
            format="SR",
            name=text(sr, "SeriesDescription") or title or "Structured report",
            source_path=path,
            items=items,
            notes=notes,
        )

    def applies_to(self, series: SeriesInfo) -> bool:
        return matches(series, series_uids=self._series, instance_uids=self._instances)

    def overlay(self, ref: SliceRef, item_ids: Iterable[str] | None = None) -> SliceOverlay:
        out = SliceOverlay()
        wanted = {item.id: item for item in self.selected(item_ids)}
        labelled: set[str] = set()
        for region in self._regions:
            if region.item_id not in wanted:
                continue
            if region.in_3d:
                if not ref.has_geometry:
                    continue
                if np.abs(geometry.plane_distance(ref, region.points)).max() > _PLANE_TOLERANCE_MM:
                    continue
                points = geometry.to_pixels(ref, region.points)
            else:
                if region.image_uid != ref.sop_instance_uid:
                    continue
                points = region.points
            if region.kind == "point":
                out.shapes.extend(Shape(region.item_id, "point", [p]) for p in points)
            else:
                closed = region.kind == "polyline" and len(points) > 2 and np.allclose(points[0], points[-1])
                out.shapes.append(Shape(region.item_id, region.kind, points, closed=closed))
            if region.item_id not in labelled:
                item = wanted[region.item_id]
                caption = item.name if not item.description else f"{item.name}: {item.description}"
                anchor = points.mean(axis=0)
                out.labels.append(Label(region.item_id, caption, (float(anchor[0]), float(anchor[1]))))
                labelled.add(region.item_id)
        return out


def _report(hd, sr):
    """The TID 1500 measurement report of an SR, or None when it is another kind of report."""
    for attempt in (lambda: hd.sr.MeasurementReport.from_sequence([sr]),):
        try:
            return attempt()
        except Exception as error:  # noqa: BLE001 - not a measurement report
            logging.info("SR %s is not a measurement report: %s", sr.SOPInstanceUID, error)
    return None


class SrAdapter:
    name = "sr"
    sop_class_uids = frozenset({ENHANCED_SR, COMPREHENSIVE_SR, COMPREHENSIVE_3D_SR})

    def load(self, path: str) -> Layer:
        try:
            import highdicom as hd
        except ImportError as error:
            raise LayerError(
                "Structured reports need the highdicom package; install it and restart the app."
            ) from error
        try:
            sr = hd.sr.srread(path)
        except Exception as error:  # noqa: BLE001 - reported to the user as is
            raise LayerError(f"Cannot read structured report: {error}") from error
        return SrLayer(path, sr, _report(hd, sr))


register(SrAdapter())
