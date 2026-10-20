"""Stacking a series into a 3D volume and cutting it in three planes (MPR).

The volume is turned so its axes follow the patient (DICOM LPS: +x to the
patient's left, +y to the back, +z to the head), whatever plane the series
was scanned in. The views are then plain array slices:

- axial: seen from the feet; patient right on the screen's left, front on top
- coronal: seen from the front; patient right on the screen's left, head on top
- sagittal: seen from the patient's left; front on the screen's left, head on top

Plain Python without Reflex, so it can run in a worker thread.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import itertools
import math

import numpy as np
import pydicom

PLANES = ("axial", "coronal", "sagittal")

# Which plane's index runs along a view's (horizontal, vertical) screen axis.
# A view of one plane shows the other two as crosshair lines.
SCREEN_AXES = {
    "axial": ("sagittal", "coronal"),
    "coronal": ("sagittal", "axial"),
    "sagittal": ("coronal", "axial"),
}

_LAYOUT_TAGS = [
    "ImagePositionPatient",
    "ImageOrientationPatient",
    "PixelSpacing",
    "SliceThickness",
    "SpacingBetweenSlices",
    "Rows",
    "Columns",
    "SamplesPerPixel",
    "NumberOfFrames",
]

# Bigger series are refused rather than risk running the server out of memory.
_MAX_BYTES = 768 * 1024 * 1024
# Volumes kept for reuse; opening the same series again is then instant.
_CACHE_SIZE = 3
# Tilt from the nearest patient axis that is worth telling the user about.
_TILT_NOTE_DEGREES = 2.0


class VolumeError(Exception):
    """The series cannot be stacked into a volume; the message is for the user."""


@dataclasses.dataclass
class Layout:
    """What the headers say about stacking a series."""

    paths: list[str]  # one file per slice position, in slice order
    # Slice of every file of the series, including those not stacked.
    slice_of: dict[str, int]
    rows: int
    columns: int
    origin: np.ndarray  # position of the first pixel of the first slice (mm)
    # Unit vectors of the slice, row and column axes of the stack:
    # stack[k, r, c] lies at origin + k*spacing[0]*axes[0] + r*... + c*...
    axes: np.ndarray
    spacing: tuple[float, float, float]
    per_position: int = 1
    has_geometry: bool = True


@dataclasses.dataclass
class Volume:
    """A series as a 3D array indexed [z, y, x] in patient (LPS) order."""

    data: np.ndarray
    spacing: tuple[float, float, float]  # mm along z, y, x
    slope: float
    intercept: float
    # Stack axis feeding each patient axis, and whether it runs backwards.
    order: tuple[int, int, int]
    flipped: tuple[bool, bool, bool]
    layout: Layout
    tilt: float  # degrees between the stack's axes and the patient's

    @property
    def scan_plane(self) -> str:
        """The plane the series was scanned in (its own images)."""
        return PLANES[self.order.index(0)]

    def count(self, plane: str) -> int:
        """Slices a plane has (its index range)."""
        nz, ny, nx = self.data.shape
        return {"axial": nz, "coronal": ny, "sagittal": nx}[plane]

    def ratio(self, plane: str) -> float:
        """Width / height of a plane's picture in millimetres."""
        nz, ny, nx = self.data.shape
        sz, sy, sx = self.spacing
        width, height = {
            "axial": (nx * sx, ny * sy),
            "coronal": (nx * sx, nz * sz),
            "sagittal": (ny * sy, nz * sz),
        }[plane]
        return width / height

    def plane(self, plane: str, index: int) -> np.ndarray:
        """One slice of a plane, oriented for display (rows top to bottom)."""
        if plane == "axial":
            return self.data[index, :, :]
        if plane == "coronal":
            return self.data[::-1, index, :]
        return self.data[::-1, :, index]

    def value(self, z: int, y: int, x: int) -> float:
        return float(self.data[z, y, x]) * self.slope + self.intercept

    def _stack_index(self, zyx: tuple[int, int, int]) -> list[int]:
        stack = [0, 0, 0]
        for axis, index in enumerate(zyx):
            if self.flipped[axis]:
                index = self.data.shape[axis] - 1 - index
            stack[self.order[axis]] = index
        return stack

    def position(self, z: int, y: int, x: int) -> np.ndarray:
        """Patient coordinates (mm, LPS) of a voxel."""
        k, r, c = self._stack_index((z, y, x))
        layout = self.layout
        steps = np.array([k, r, c], dtype=float) * np.array(layout.spacing)
        return layout.origin + steps @ layout.axes

    def cursor_at(self, path: str) -> dict[str, int]:
        """Plane indices centred on the volume, on the slice of file `path` if any."""
        nz, ny, nx = self.data.shape
        zyx = [nz // 2, ny // 2, nx // 2]
        k = self.layout.slice_of.get(path)
        if k is not None:
            axis = self.order.index(0)
            zyx[axis] = self.data.shape[axis] - 1 - k if self.flipped[axis] else k
        return dict(zip(("axial", "coronal", "sagittal"), zyx))

    def path_at(self, cursor: dict[str, int]) -> str:
        """The file of the scanned slice the cursor is on."""
        zyx = (cursor["axial"], cursor["coronal"], cursor["sagittal"])
        return self.layout.paths[self._stack_index(zyx)[0]]


def cache_key(paths: list[str]) -> str:
    return hashlib.sha1("\0".join(paths).encode()).hexdigest()


_cache: collections.OrderedDict[str, Volume] = collections.OrderedDict()


def cached(key: str) -> Volume | None:
    volume = _cache.get(key)
    if volume is not None:
        _cache.move_to_end(key)
    return volume


def store(key: str, volume: Volume) -> None:
    _cache[key] = volume
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)


def _floats(value, count: int) -> list[float] | None:
    try:
        numbers = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    return numbers[:count] if len(numbers) >= count else None


def read_layout(paths: list[str]) -> Layout:
    """Read the headers of `paths` (in slice order) and plan the stack.

    Raises VolumeError when the images cannot form one volume.
    """
    headers = [
        pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_LAYOUT_TAGS)
        for path in paths
    ]
    if not headers:
        raise VolumeError("This folder has no images.")
    first = headers[0]
    if int(first.get("SamplesPerPixel", 1) or 1) != 1:
        raise VolumeError("3 views work only with grayscale images.")
    if any(int(ds.get("NumberOfFrames", 1) or 1) > 1 for ds in headers):
        raise VolumeError(
            "This series keeps its images as frames inside one file, "
            "which 3 views cannot read yet."
        )
    shape = (int(first.get("Rows", 0)), int(first.get("Columns", 0)))
    if any((int(ds.get("Rows", 0)), int(ds.get("Columns", 0))) != shape for ds in headers):
        raise VolumeError(
            "The images in this series have different sizes, so they cannot be stacked."
        )

    pixel_spacing = _floats(first.get("PixelSpacing"), 2) or [1.0, 1.0]
    orientations = [_floats(ds.get("ImageOrientationPatient"), 6) for ds in headers]
    positions = [_floats(ds.get("ImagePositionPatient"), 3) for ds in headers]
    has_geometry = all(o is not None for o in orientations) and all(
        p is not None for p in positions
    )

    if has_geometry:
        orientation = np.array(orientations[0])
        if any(np.abs(np.array(o) - orientation).max() > 1e-3 for o in orientations):
            raise VolumeError(
                "The images in this series are not parallel, so they cannot be stacked."
            )
        row_dir = orientation[:3] / np.linalg.norm(orientation[:3])
        col_dir = orientation[3:] / np.linalg.norm(orientation[3:])
        normal = np.cross(row_dir, col_dir)
        # Keep the first file at each position: several time points or echoes
        # of one position would otherwise be stacked as extra slices.
        keys = [round(float(np.dot(normal, position)), 2) for position in positions]
        seen = collections.Counter(keys)
        first: dict[float, int] = {}
        for i, key in enumerate(keys):
            first.setdefault(key, i)
        chosen = sorted(first.values(), key=lambda i: keys[i])
        slice_number = {keys[i]: k for k, i in enumerate(chosen)}
        slice_of = {path: slice_number[key] for path, key in zip(paths, keys)}
        gaps = np.diff([float(np.dot(normal, positions[i])) for i in chosen])
        slice_spacing = float(np.median(gaps)) if len(gaps) else 0.0
        origin = np.array(positions[chosen[0]])
        axes = np.array([normal, col_dir, row_dir])
        per_position = max(seen.values())
    else:
        # Without positions, assume evenly spaced axial slices in file order.
        chosen = list(range(len(headers)))
        slice_of = {path: k for k, path in enumerate(paths)}
        slice_spacing = 0.0
        origin = np.zeros(3)
        axes = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
        per_position = 1

    if len(chosen) < 2:
        raise VolumeError("3 views need at least 2 images at different positions.")
    if slice_spacing <= 0:
        for tag in ("SpacingBetweenSlices", "SliceThickness"):
            value = _floats([first.get(tag)], 1)
            if value and value[0] > 0:
                slice_spacing = value[0]
                break
        else:
            slice_spacing = 1.0

    if shape[0] * shape[1] * len(chosen) * 4 > _MAX_BYTES:
        raise VolumeError("This series is too large for 3 views.")

    return Layout(
        paths=[paths[i] for i in chosen],
        slice_of=slice_of,
        rows=shape[0],
        columns=shape[1],
        origin=origin,
        axes=axes,
        spacing=(slice_spacing, pixel_spacing[0], pixel_spacing[1]),
        per_position=per_position,
        has_geometry=has_geometry,
    )


def read_pixels(paths: list[str]) -> list[tuple[np.ndarray, float, float]]:
    """(pixels, rescale slope, rescale intercept) of each file."""
    slices = []
    for path in paths:
        ds = pydicom.dcmread(path)
        pixels = ds.pixel_array
        if pixels.ndim != 2:
            raise VolumeError("3 views work only with grayscale images.")
        slope = float(getattr(ds, "RescaleSlope", 1.0))
        intercept = float(getattr(ds, "RescaleIntercept", 0.0))
        slices.append((pixels, slope, intercept))
    return slices


def assemble(layout: Layout, slices: list[tuple[np.ndarray, float, float]]) -> Volume:
    """Stack the slices read for `layout` and turn them to patient axes."""
    stack = np.stack([pixels for pixels, _, _ in slices])
    slopes = np.array([slope for _, slope, _ in slices])
    intercepts = np.array([intercept for _, _, intercept in slices])
    if np.all(slopes == slopes[0]) and np.all(intercepts == intercepts[0]):
        slope, intercept = float(slopes[0]), float(intercepts[0])
    else:
        # Rescaling differs per slice; apply it now so any cut can be windowed.
        stack = stack.astype(np.float32) * slopes[:, None, None].astype(np.float32)
        stack += intercepts[:, None, None].astype(np.float32)
        slope, intercept = 1.0, 0.0

    # Give each patient axis (z, y, x) the stack axis that points most along it.
    patient_component = (2, 1, 0)
    weight = np.abs(layout.axes)
    order = max(
        itertools.permutations(range(3)),
        key=lambda p: sum(weight[p[i], patient_component[i]] for i in range(3)),
    )
    data = stack.transpose(order)
    flipped = []
    for axis in range(3):
        backwards = bool(layout.axes[order[axis], patient_component[axis]] < 0)
        if backwards:
            data = np.flip(data, axis=axis)
        flipped.append(backwards)
    alignment = min(weight[order[i], patient_component[i]] for i in range(3))
    tilt = math.degrees(math.acos(min(1.0, float(alignment))))
    return Volume(
        data=data,
        spacing=tuple(layout.spacing[order[i]] for i in range(3)),
        slope=slope,
        intercept=intercept,
        order=tuple(order),
        flipped=tuple(flipped),
        layout=layout,
        tilt=tilt,
    )


def notes(volume: Volume) -> list[str]:
    """Things about the volume worth telling the user."""
    found = []
    layout = volume.layout
    if not layout.has_geometry:
        found.append(
            "These images carry no position information, so the 3 views assume "
            "evenly spaced axial slices."
        )
    if layout.per_position > 1:
        found.append(
            f"This series has {layout.per_position} images at each position "
            "(for example several time points). The 3 views use the first one."
        )
    if volume.tilt >= _TILT_NOTE_DEGREES:
        found.append(
            f"This series was scanned tilted by about {volume.tilt:.0f}°, so the "
            "views are close to, but not exactly, axial, coronal and sagittal."
        )
    return found
