"""The common interface between DICOM annotation objects and the viewer.

Whatever DICOM object an annotation comes from (SEG, SR, RTSTRUCT, GSPS, ...),
the viewer only ever sees these:

    Adapter       reads one kind of DICOM object (chosen by SOP class) into a Layer
    Layer         one loaded object: its Items and, for any slice the viewer
                  shows, a SliceOverlay drawn in that slice's own pixel grid
    Item          one toggleable thing in a layer: a segment, an ROI, a group
                  of GSPS graphics, an SR finding
    SliceOverlay  masks, shapes and text labels, already in pixel coordinates

Pixel coordinate convention, shared by DICOM GSPS "PIXEL" units, SR SCOORD
graphic data and highdicom's transformers: a point is (column, row) as
floats; the top-left corner of the top-left pixel is (0, 0), so that pixel's
centre is (0.5, 0.5) and the bottom-right corner of the image is
(columns, rows). Raster masks use the matching array indices: mask[r, c]
covers the square [c, c + 1) x [r, r + 1).

Full contract: docs/annotations_interface.md.

Plain Python without Reflex, so layers can be built and queried in worker
threads.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol, runtime_checkable

import numpy as np

ShapeKind = Literal["polyline", "polygon", "point", "circle", "ellipse"]
ItemKind = Literal["mask", "contour", "graphic", "measurement"]
RGB = tuple[int, int, int]

# Fewest points each shape kind needs; the layout follows DICOM graphic objects.
_MIN_POINTS = {"polyline": 2, "polygon": 3, "point": 1, "circle": 2, "ellipse": 4}


class LayerError(Exception):
    """An adapter cannot read an object; the message is for the user."""


@dataclasses.dataclass(frozen=True)
class SeriesInfo:
    """The image series the viewer has open; what a layer matches itself against."""

    series_uid: str
    frame_of_reference_uid: str = ""
    study_uid: str = ""
    sop_instance_uids: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True)
class SliceRef:
    """One image, or one frame of a multi-frame image, the viewer is about to draw.

    `position`, `orientation` and `pixel_spacing` are ImagePositionPatient,
    ImageOrientationPatient and PixelSpacing (between rows, between columns)
    as floats. They are None when the image carries no geometry; only
    matching by UID is possible then.
    """

    sop_instance_uid: str
    series_uid: str
    rows: int
    columns: int
    frame_of_reference_uid: str = ""
    position: tuple[float, float, float] | None = None
    orientation: tuple[float, float, float, float, float, float] | None = None
    pixel_spacing: tuple[float, float] | None = None
    frame_number: int | None = None  # 1-based, multi-frame images only

    @property
    def has_geometry(self) -> bool:
        return (
            self.position is not None
            and self.orientation is not None
            and self.pixel_spacing is not None
        )

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.columns)


@dataclasses.dataclass
class Mask:
    """A raster region of one item on one slice: True where the item covers the pixel.

    `pixels` has the slice's shape (rows, columns); it is converted to bool.
    """

    item_id: str
    pixels: np.ndarray

    def __post_init__(self) -> None:
        pixels = np.asarray(self.pixels, dtype=bool)
        if pixels.ndim != 2:
            raise ValueError(
                f"Mask.pixels must be 2-D (rows, columns), got shape {pixels.shape}"
            )
        self.pixels = pixels


@dataclasses.dataclass
class Shape:
    """A vector drawing of one item on one slice, in pixel coordinates.

    `points` is an (n, 2) float array of (column, row). The layout per kind
    follows DICOM graphic objects (GSPS and SR SCOORD):

    - polyline: 2+ points joined in order; `closed` also joins last to first
    - polygon: 3+ points, always closed; the inside is the region
    - point: 1 point
    - circle: 2 points, the centre and any point on the circumference
    - ellipse: 4 points, both ends of the major axis, then both ends of the
      minor axis
    """

    item_id: str
    kind: ShapeKind
    points: np.ndarray
    closed: bool = False

    def __post_init__(self) -> None:
        if self.kind not in _MIN_POINTS:
            raise ValueError(f"Unknown shape kind {self.kind!r}")
        points = np.asarray(self.points, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2:
            raise ValueError(
                f"Shape.points must be (n, 2) of (column, row), got shape {points.shape}"
            )
        if len(points) < _MIN_POINTS[self.kind]:
            raise ValueError(
                f"A {self.kind} needs at least {_MIN_POINTS[self.kind]} points, "
                f"got {len(points)}"
            )
        self.points = points
        if self.kind == "polygon":
            self.closed = True


@dataclasses.dataclass
class Label:
    """Text of one item anchored at a pixel coordinate (column, row)."""

    item_id: str
    text: str
    anchor: tuple[float, float]


@dataclasses.dataclass
class SliceOverlay:
    """Everything one or more layers draw on one slice. Empty when nothing applies."""

    masks: list[Mask] = dataclasses.field(default_factory=list)
    shapes: list[Shape] = dataclasses.field(default_factory=list)
    labels: list[Label] = dataclasses.field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.masks or self.shapes or self.labels)

    def item_ids(self) -> set[str]:
        return {p.item_id for p in (*self.masks, *self.shapes, *self.labels)}

    def extend(self, other: SliceOverlay) -> SliceOverlay:
        """Append another overlay's primitives (drawn after this one's); returns self."""
        self.masks.extend(other.masks)
        self.shapes.extend(other.shapes)
        self.labels.extend(other.labels)
        return self


@dataclasses.dataclass(frozen=True)
class Item:
    """One toggleable thing in a layer, as the UI lists it.

    `id` is unique within its layer and is what primitives refer to. `color`
    is (r, g, b) 0-255, the adapter's default; the UI may override it.
    """

    id: str
    name: str
    color: RGB
    kind: ItemKind
    description: str = ""


@runtime_checkable
class Layer(Protocol):
    """One loaded annotation object. Adapters return these; the viewer queries them.

    Attributes are fixed after loading. `overlay` may be called many times
    from a worker thread and should be cheap after the first call: cache
    decoded frames or rasterised contours inside the layer.
    """

    id: str
    format: str  # "SEG", "SR", "RTSTRUCT", "PR", ... for a badge in the UI
    name: str
    source_path: str
    items: Sequence[Item]
    notes: Sequence[str]  # things the user should know, e.g. "drawn only on matching slices"
    supports_volume: bool

    def applies_to(self, series: SeriesInfo) -> bool:
        """Whether this layer annotates `series`: by referenced UIDs, else frame of reference."""

    def overlay(
        self, ref: SliceRef, item_ids: Iterable[str] | None = None
    ) -> SliceOverlay:
        """What to draw on `ref`, limited to `item_ids` when given (None means every item).

        Returns an empty overlay when nothing in the layer lies on that slice.
        """

    def label_volume(self, refs: Sequence[SliceRef]) -> np.ndarray | None:
        """Labels for a stack of slices, for the three-plane (MPR) view.

        `refs` are the slices in stack order. The result is an integer array
        of shape (len(refs), rows, columns): 0 is background and value v is
        items[v - 1]. Where items overlap, the later item wins, so adapters
        put smaller structures after the ones that contain them. None when
        the layer cannot be shown as a volume (`supports_volume` is False).
        """


@runtime_checkable
class Adapter(Protocol):
    """Reads one family of DICOM objects into layers. Register one per SOP class family."""

    name: str
    sop_class_uids: frozenset[str]

    def load(self, path: str) -> Layer:
        """Read the object at `path`. Raise LayerError with a message for the user when it cannot."""


class BaseLayer:
    """Optional base for layers: stores the attributes, defaults to no volume support."""

    supports_volume: bool = False

    def __init__(
        self,
        *,
        layer_id: str,
        format: str,
        name: str,
        source_path: str,
        items: Sequence[Item],
        notes: Sequence[str] = (),
    ) -> None:
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate item ids in layer {layer_id!r}")
        self.id = layer_id
        self.format = format
        self.name = name
        self.source_path = source_path
        self.items = list(items)
        self.notes = list(notes)
        self._items_by_id = {item.id: item for item in self.items}

    def item(self, item_id: str) -> Item:
        return self._items_by_id[item_id]

    def selected(self, item_ids: Iterable[str] | None) -> list[Item]:
        """The items to draw: all of them for None, else those listed (unknown ids are ignored)."""
        if item_ids is None:
            return list(self.items)
        wanted = set(item_ids)
        return [item for item in self.items if item.id in wanted]

    def applies_to(self, series: SeriesInfo) -> bool:
        raise NotImplementedError

    def overlay(
        self, ref: SliceRef, item_ids: Iterable[str] | None = None
    ) -> SliceOverlay:
        raise NotImplementedError

    def label_volume(self, refs: Sequence[SliceRef]) -> np.ndarray | None:
        return None
