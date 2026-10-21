"""Geometry every adapter needs: SliceRef from a dataset, mm <-> pixel, plane matching.

Patient coordinates are DICOM LPS millimetres. Pixel coordinates follow the
convention in model.py: (column, row), top-left corner of the top-left pixel
at (0, 0). ImagePositionPatient is the centre of that pixel, so it maps to
(0.5, 0.5).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import numpy as np

from dicom_viewer.annotations.model import SeriesInfo, SliceRef


def _floats(value, count: int) -> tuple[float, ...] | None:
    if value is None:
        return None
    try:
        numbers = tuple(float(x) for x in value)
    except (TypeError, ValueError):
        return None
    return numbers[:count] if len(numbers) >= count else None


def _functional(ds, frame_number: int | None, sequence: str, keyword: str):
    """`keyword` from an enhanced image's per-frame, else shared, functional group."""
    groups = []
    per_frame = ds.get("PerFrameFunctionalGroupsSequence")
    if per_frame is not None and frame_number and 0 < frame_number <= len(per_frame):
        groups.append(per_frame[frame_number - 1])
    shared = ds.get("SharedFunctionalGroupsSequence")
    if shared:
        groups.append(shared[0])
    for group in groups:
        inner = group.get(sequence)
        if inner:
            value = inner[0].get(keyword)
            if value is not None:
                return value
    return None


def slice_ref(ds, *, frame_number: int | None = None) -> SliceRef:
    """A SliceRef for a pydicom dataset; a header read (stop_before_pixels) is enough.

    For enhanced multi-frame images pass the 1-based `frame_number`; the
    geometry then comes from that frame's functional groups.
    """

    def tag(keyword: str, sequence: str):
        value = ds.get(keyword)
        if value is None:
            value = _functional(ds, frame_number, sequence, keyword)
        return value

    return SliceRef(
        sop_instance_uid=str(ds.get("SOPInstanceUID", "") or ""),
        series_uid=str(ds.get("SeriesInstanceUID", "") or ""),
        rows=int(ds.get("Rows", 0) or 0),
        columns=int(ds.get("Columns", 0) or 0),
        frame_of_reference_uid=str(ds.get("FrameOfReferenceUID", "") or ""),
        position=_floats(tag("ImagePositionPatient", "PlanePositionSequence"), 3),
        orientation=_floats(tag("ImageOrientationPatient", "PlaneOrientationSequence"), 6),
        pixel_spacing=_floats(tag("PixelSpacing", "PixelMeasuresSequence"), 2),
        frame_number=frame_number,
    )


def series_info(refs: Iterable[SliceRef], *, study_uid: str = "") -> SeriesInfo:
    """The SeriesInfo of the slices the viewer has open."""
    refs = list(refs)
    if not refs:
        raise ValueError("series_info needs at least one slice")
    return SeriesInfo(
        series_uid=refs[0].series_uid,
        frame_of_reference_uid=next(
            (r.frame_of_reference_uid for r in refs if r.frame_of_reference_uid), ""
        ),
        study_uid=study_uid,
        sop_instance_uids=frozenset(r.sop_instance_uid for r in refs),
    )


def basis(ref: SliceRef) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(origin, row direction, column direction, normal) of a slice in mm; unit vectors.

    The row direction is where the column index grows (first triplet of
    ImageOrientationPatient); the column direction is where the row index
    grows (second triplet). Raises ValueError when the slice has no geometry.
    """
    if not ref.has_geometry:
        raise ValueError(f"Slice {ref.sop_instance_uid or '?'} has no geometry")
    origin = np.array(ref.position, dtype=float)
    row_dir = np.array(ref.orientation[:3], dtype=float)
    col_dir = np.array(ref.orientation[3:6], dtype=float)
    for vector in (row_dir, col_dir):
        length = np.linalg.norm(vector)
        if length == 0:
            raise ValueError(f"Slice {ref.sop_instance_uid or '?'} has a zero orientation vector")
        vector /= length
    return origin, row_dir, col_dir, np.cross(row_dir, col_dir)


def normal(ref: SliceRef) -> np.ndarray:
    return basis(ref)[3]


def to_pixels(ref: SliceRef, points_mm) -> np.ndarray:
    """Patient points (n, 3) mm -> pixel coordinates (n, 2) (column, row) on `ref`.

    Points off the plane are projected onto it; use plane_distance to check first.
    """
    origin, row_dir, col_dir, _ = basis(ref)
    points = np.atleast_2d(np.asarray(points_mm, dtype=float))
    if points.shape[-1] != 3:
        raise ValueError(f"points_mm must be (n, 3), got shape {points.shape}")
    offsets = points - origin
    row_spacing, col_spacing = ref.pixel_spacing
    cols = offsets @ row_dir / col_spacing + 0.5
    rows = offsets @ col_dir / row_spacing + 0.5
    return np.column_stack([cols, rows])


def to_patient(ref: SliceRef, points_px) -> np.ndarray:
    """Pixel coordinates (n, 2) (column, row) on `ref` -> patient points (n, 3) mm."""
    origin, row_dir, col_dir, _ = basis(ref)
    points = np.atleast_2d(np.asarray(points_px, dtype=float))
    if points.shape[-1] != 2:
        raise ValueError(f"points_px must be (n, 2), got shape {points.shape}")
    row_spacing, col_spacing = ref.pixel_spacing
    cols = (points[:, 0] - 0.5) * col_spacing
    rows = (points[:, 1] - 0.5) * row_spacing
    return origin + np.outer(cols, row_dir) + np.outer(rows, col_dir)


def plane_distance(ref: SliceRef, points_mm) -> np.ndarray:
    """Signed distance (mm) of each point from the slice plane, along its normal."""
    origin, _, _, plane_normal = basis(ref)
    points = np.atleast_2d(np.asarray(points_mm, dtype=float))
    return (points - origin) @ plane_normal


def _same_frame(a: SliceRef, b: SliceRef) -> bool:
    return not (
        a.frame_of_reference_uid
        and b.frame_of_reference_uid
        and a.frame_of_reference_uid != b.frame_of_reference_uid
    )


def _parallel(a: SliceRef, b: SliceRef, angle_tolerance_deg: float) -> bool:
    cosine = abs(float(np.dot(normal(a), normal(b))))
    return cosine >= math.cos(math.radians(angle_tolerance_deg))


def same_plane(
    a: SliceRef, b: SliceRef, *, tolerance_mm: float = 0.5, angle_tolerance_deg: float = 1.0
) -> bool:
    """Whether two slices lie in one plane: same frame of reference, parallel, within `tolerance_mm`."""
    if not (a.has_geometry and b.has_geometry and _same_frame(a, b)):
        return False
    if not _parallel(a, b, angle_tolerance_deg):
        return False
    return abs(float(plane_distance(a, b.position)[0])) <= tolerance_mm


def same_grid(
    a: SliceRef, b: SliceRef, *, tolerance_mm: float = 0.5, angle_tolerance_deg: float = 1.0
) -> bool:
    """Whether two slices share one pixel grid, so a mask of one fits the other unchanged."""
    if not same_plane(a, b, tolerance_mm=tolerance_mm, angle_tolerance_deg=angle_tolerance_deg):
        return False
    if a.shape != b.shape:
        return False
    if not np.allclose(a.pixel_spacing, b.pixel_spacing, rtol=1e-3, atol=1e-4):
        return False
    if not np.allclose(a.orientation, b.orientation, atol=1e-3):
        return False
    return float(np.linalg.norm(np.subtract(a.position, b.position))) <= tolerance_mm


def grid_offset(
    frame: SliceRef, ref: SliceRef, *, tolerance_mm: float = 0.5, angle_tolerance_deg: float = 1.0
) -> tuple[int, int] | None:
    """(rows, columns) from the top-left pixel of `ref` to that of `frame`, when `frame`'s
    pixel grid is part of `ref`'s: one plane, the same orientation and spacing, shifted by
    whole pixels (within `tolerance_mm`). None otherwise. A mask cropped to its object's
    box this way fits the image without resampling; (0, 0) for the same grid."""
    if not same_plane(frame, ref, tolerance_mm=tolerance_mm, angle_tolerance_deg=angle_tolerance_deg):
        return None
    if not np.allclose(frame.pixel_spacing, ref.pixel_spacing, rtol=1e-3, atol=1e-4):
        return None
    if not np.allclose(frame.orientation, ref.orientation, atol=1e-3):
        return None
    origin, row_dir, col_dir, _ = basis(ref)
    shift = np.subtract(frame.position, origin)
    between_rows, between_columns = ref.pixel_spacing
    rows, columns = float(shift @ col_dir) / between_rows, float(shift @ row_dir) / between_columns
    whole = round(rows), round(columns)
    off = math.hypot((rows - whole[0]) * between_rows, (columns - whole[1]) * between_columns)
    return whole if off <= tolerance_mm else None


def find_slice(
    target: SliceRef,
    candidates: Sequence[SliceRef],
    *,
    tolerance_mm: float = 0.5,
    angle_tolerance_deg: float = 1.0,
) -> SliceRef | None:
    """The candidate in the same plane as `target`, nearest first; None when none is within tolerance."""
    if not target.has_geometry:
        return None
    best, best_distance = None, None
    for candidate in candidates:
        if not (candidate.has_geometry and _same_frame(target, candidate)):
            continue
        if not _parallel(target, candidate, angle_tolerance_deg):
            continue
        distance = abs(float(plane_distance(candidate, target.position)[0]))
        if distance <= tolerance_mm and (best_distance is None or distance < best_distance):
            best, best_distance = candidate, distance
    return best


def find_slice_for_point(
    point_mm, candidates: Sequence[SliceRef], *, tolerance_mm: float = 0.5
) -> SliceRef | None:
    """The candidate whose plane passes nearest a patient point, within `tolerance_mm`; else None.

    For contours and 3D coordinates that do not say which image they lie on.
    """
    best, best_distance = None, None
    for candidate in candidates:
        if not candidate.has_geometry:
            continue
        distance = abs(float(plane_distance(candidate, point_mm)[0]))
        if distance <= tolerance_mm and (best_distance is None or distance < best_distance):
            best, best_distance = candidate, distance
    return best
