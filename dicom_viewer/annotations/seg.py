"""DICOM Segmentation objects (SEG), read with highdicom.

Each segment is an item. A slice gets the frames that name it as their
source image; a slice of another series on the same grid gets the frames
whose plane and pixel grid match it. Decoded masks are cached per slice.

highdicom is imported when an object is loaded, so the viewer runs without
it and reports the missing package on the layer instead. It is used to parse
the object only: highdicom keeps a per-thread SQLite index for its frame
lookups, and the viewer draws from worker threads, so the frame table is
read once at load time and frames are decoded with pydicom afterwards.
"""

from __future__ import annotations

import collections
import logging
import threading
from collections.abc import Iterable, Sequence

import numpy as np
from pydicom.pixels import pixel_array

from dicom_viewer.annotations import geometry
from dicom_viewer.annotations.colors import default_color, dicom_cielab_to_rgb
from dicom_viewer.annotations.model import (
    BaseLayer,
    Item,
    Layer,
    LayerError,
    Mask,
    SeriesInfo,
    SliceOverlay,
    SliceRef,
)
from dicom_viewer.annotations.registry import register
from dicom_viewer.annotations.support import enum_name, text

SEGMENTATION_STORAGE = "1.2.840.10008.5.1.4.1.1.66.4"
LABEL_MAP_SEGMENTATION_STORAGE = "1.2.840.10008.5.1.4.1.1.66.7"
# Decoded slices kept per layer; the viewer walks a series in order.
_CACHE_SIZE = 64
_OTHER_SERIES_NOTE = "Made for another series; drawn on the images that share its grid."


def _segment_of(frame) -> int:
    identification = frame.get("SegmentIdentificationSequence")
    if identification:
        return int(identification[0].get("ReferencedSegmentNumber", 0) or 0)
    return 0


def _sources_of(frame) -> list[str]:
    """SOPInstanceUIDs of the images a frame was derived from."""
    found = []
    for derivation in frame.get("DerivationImageSequence", []):
        for source in derivation.get("SourceImageSequence", []):
            uid = source.get("ReferencedSOPInstanceUID")
            if uid:
                found.append(str(uid))
    return found


def _frame_table(seg) -> tuple[dict[str, list[tuple[int, int]]], list[tuple[SliceRef, int]]]:
    """Per source image, its (frame index, segment) pairs; and every frame's (geometry, segment)."""
    by_source: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    refs = []
    for index, frame in enumerate(seg.get("PerFrameFunctionalGroupsSequence", [])):
        segment = _segment_of(frame)
        for uid in _sources_of(frame):
            by_source[uid].append((index, segment))
        refs.append((geometry.slice_ref(seg, frame_number=index + 1), segment))
    return dict(by_source), refs


class SegLayer(BaseLayer):
    supports_volume = True

    def __init__(self, path: str, seg) -> None:
        self._seg = seg
        self._lock = threading.Lock()
        self._masks: collections.OrderedDict[str, np.ndarray | None] = collections.OrderedDict()
        self._frames: np.ndarray | None = None
        self._by_source, self._frame_refs = _frame_table(seg)
        sources = seg.get_source_image_uids()
        self._source_series = {str(series) for _, series, _ in sources}
        self._source_instances = {str(instance) for _, _, instance in sources}
        self._frame_of_reference = text(seg, "FrameOfReferenceUID")
        self._shape = (int(seg.Rows), int(seg.Columns))
        self._numbers = [int(n) for n in seg.segment_numbers]
        self._kind = enum_name(seg.segmentation_type)
        maximum = int(seg.get("MaximumFractionalValue", 255) or 255)
        # Raw frames: 0/1 for binary, 0..maximum for fractional, segment numbers for label maps.
        self._raw_threshold = maximum / 2.0 if self._kind == "FRACTIONAL" else 1.0
        items = []
        for index, number in enumerate(self._numbers):
            description = seg.get_segment_description(number)
            lab = description.get("RecommendedDisplayCIELabValue")
            color = dicom_cielab_to_rgb(lab) if lab is not None else default_color(index)
            algorithm = enum_name(getattr(description, "algorithm_type", ""))
            label = text(description, "SegmentLabel") or f"Segment {number}"
            items.append(
                Item(str(number), label, color, "mask", description=algorithm.replace("_", " ").capitalize())
            )
        name = text(seg, "SeriesDescription") or text(seg, "ContentLabel") or "Segmentation"
        notes = []
        if self._kind == "FRACTIONAL":
            notes.append("Fractional segmentation: pixels at or above half the maximum value are shown.")
        super().__init__(
            layer_id=str(seg.SOPInstanceUID),
            format="SEG",
            name=name,
            source_path=path,
            items=items,
            notes=notes,
        )

    def applies_to(self, series: SeriesInfo) -> bool:
        if series.series_uid in self._source_series or (
            series.sop_instance_uids & self._source_instances
        ):
            return True
        if self._frame_of_reference and self._frame_of_reference == series.frame_of_reference_uid:
            if _OTHER_SERIES_NOTE not in self.notes:
                self.notes.append(_OTHER_SERIES_NOTE)
            return True
        return False

    def _frame(self, index: int) -> np.ndarray:
        """Frame `index` (0-based) decoded: 0/1, 0..maximum, or segment numbers."""
        with self._lock:
            if self._frames is not None:
                return self._frames[index]
        try:
            return np.asarray(pixel_array(self._seg, index=index)).reshape(self._shape)
        except Exception:  # noqa: BLE001 - fall back to decoding everything once
            with self._lock:
                if self._frames is None:
                    self._frames = np.asarray(self._seg.pixel_array).reshape((-1, *self._shape))
                return self._frames[index]

    def _masks_of(self, frames: list[tuple[int, int]]) -> np.ndarray:
        """(rows, columns, segments) bool from (frame index, segment) pairs."""
        masks = np.zeros((*self._shape, len(self._numbers)), dtype=bool)
        for index, segment in frames:
            frame = self._frame(index)
            if self._kind == "LABELMAP":
                for position, number in enumerate(self._numbers):
                    masks[..., position] |= frame == number
            elif segment in self._numbers:
                masks[..., self._numbers.index(segment)] |= frame >= self._raw_threshold
        return masks

    def _by_position(self, ref: SliceRef) -> np.ndarray | None:
        """Frames whose plane and grid match `ref`, for images the object does not name."""
        if not ref.has_geometry:
            return None
        matching = [
            (index, segment)
            for index, (frame_ref, segment) in enumerate(self._frame_refs)
            if geometry.same_grid(frame_ref, ref)
        ]
        return self._masks_of(matching) if matching else None

    def masks_for(self, ref: SliceRef) -> np.ndarray | None:
        """(rows, columns, segments) bool for a slice; None when no frame lies on it."""
        key = ref.sop_instance_uid or repr(ref.position)
        with self._lock:
            if key in self._masks:
                self._masks.move_to_end(key)
                return self._masks[key]
        masks = None
        try:
            frames = self._by_source.get(ref.sop_instance_uid)
            if frames:
                masks = self._masks_of(frames)
            else:
                masks = self._by_position(ref)
        except Exception as error:  # noqa: BLE001 - a bad frame must not take the viewer down
            logging.warning("SEG %s: cannot decode the frames for %s: %s", self.id, key, error)
        if masks is not None and not masks.any():
            masks = None
        with self._lock:
            self._masks[key] = masks
            while len(self._masks) > _CACHE_SIZE:
                self._masks.popitem(last=False)
        return masks

    def overlay(self, ref: SliceRef, item_ids: Iterable[str] | None = None) -> SliceOverlay:
        out = SliceOverlay()
        if ref.shape != self._shape:
            return out
        masks = self.masks_for(ref)
        if masks is None:
            return out
        for item in self.selected(item_ids):
            mask = masks[..., self._numbers.index(int(item.id))]
            if mask.any():
                out.masks.append(Mask(item.id, mask))
        return out

    def label_volume(self, refs: Sequence[SliceRef]) -> np.ndarray | None:
        if not refs or any(ref.shape != self._shape for ref in refs):
            return None
        dtype = np.uint8 if len(self._numbers) < 256 else np.uint16
        labels = np.zeros((len(refs), *self._shape), dtype=dtype)
        for k, ref in enumerate(refs):
            masks = self.masks_for(ref)
            if masks is None:
                continue
            areas = masks.reshape(-1, masks.shape[-1]).sum(axis=0)
            # Bigger segments first, so the small ones stay visible where they overlap.
            for index in sorted(range(len(areas)), key=lambda i: -int(areas[i])):
                labels[k][masks[..., index]] = index + 1
        return labels


class SegAdapter:
    name = "seg"
    sop_class_uids = frozenset({SEGMENTATION_STORAGE, LABEL_MAP_SEGMENTATION_STORAGE})

    def load(self, path: str) -> Layer:
        try:
            import highdicom as hd
        except ImportError as error:
            raise LayerError(
                "Segmentations need the highdicom package; install it and restart the app."
            ) from error
        try:
            seg = hd.seg.segread(path)
        except Exception as error:  # noqa: BLE001 - reported to the user as is
            raise LayerError(f"Cannot read segmentation: {error}") from error
        return SegLayer(path, seg)


register(SegAdapter())
