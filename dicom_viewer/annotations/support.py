"""Helpers the adapters share: reading objects, matching references, rasterising."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pydicom

from dicom_viewer.annotations.model import LayerError, SeriesInfo


def read_dataset(path: str, what: str, *, pixels: bool = False) -> pydicom.Dataset:
    """The DICOM object at `path`, or a LayerError naming `what` for the user."""
    try:
        return pydicom.dcmread(path, stop_before_pixels=not pixels)
    except Exception as error:  # noqa: BLE001 - every read failure is reported the same way
        raise LayerError(f"Cannot read {what}: {error}") from error


def text(ds, keyword: str, default: str = "") -> str:
    """A string attribute, stripped; `default` when missing or empty."""
    value = ds.get(keyword)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def enum_name(value) -> str:
    """"SEMIAUTOMATIC" from highdicom's enums or plain strings."""
    return str(getattr(value, "value", value) or "")


def referenced_series(ds) -> set[str]:
    """SeriesInstanceUIDs in the object's ReferencedSeriesSequence."""
    return {
        str(item.SeriesInstanceUID)
        for item in ds.get("ReferencedSeriesSequence", [])
        if "SeriesInstanceUID" in item
    }


def referenced_instances(ds) -> set[str]:
    """SOPInstanceUIDs the object names through its ReferencedSeriesSequence."""
    found: set[str] = set()
    for series in ds.get("ReferencedSeriesSequence", []):
        for keyword in ("ReferencedInstanceSequence", "ReferencedImageSequence"):
            for item in series.get(keyword, []):
                uid = item.get("ReferencedSOPInstanceUID")
                if uid:
                    found.add(str(uid))
    return found


def matches(
    series: SeriesInfo,
    *,
    series_uids: Iterable[str] = (),
    instance_uids: Iterable[str] = (),
) -> bool:
    """Whether the object names the open series, or any of its images."""
    if series.series_uid and series.series_uid in set(series_uids):
        return True
    return bool(series.sop_instance_uids and set(instance_uids) & series.sop_instance_uids)


def polygon_mask(points_px, shape: tuple[int, int]) -> np.ndarray:
    """The pixels inside a closed polygon (pixel coordinates), as a bool (rows, columns) array.

    A pixel counts as inside when its centre is (even-odd rule), so a polygon
    along pixel corners covers exactly the pixels it encloses.
    """
    rows, columns = shape
    mask = np.zeros((rows, columns), dtype=bool)
    points = np.asarray(points_px, dtype=float)
    if points.ndim != 2 or len(points) < 3:
        return mask
    x, y = points[:, 0], points[:, 1]
    x_next, y_next = np.roll(x, -1), np.roll(y, -1)
    for row in range(rows):
        centre = row + 0.5
        # Edges that cross this scanline; the half-open test counts a vertex once.
        crossing = ((y <= centre) & (y_next > centre)) | ((y_next <= centre) & (y > centre))
        if not crossing.any():
            continue
        t = (centre - y[crossing]) / (y_next[crossing] - y[crossing])
        xs = np.sort(x[crossing] + t * (x_next[crossing] - x[crossing]))
        for left, right in zip(xs[0::2], xs[1::2]):
            # Pixels whose centre c + 0.5 lies in [left, right).
            first = max(0, int(np.ceil(left - 0.5)))
            last = min(columns, int(np.ceil(right - 0.5)))
            if last > first:
                mask[row, first:last] = True
    return mask
