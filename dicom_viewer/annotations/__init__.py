"""Annotation display for the viewer: one interface over SEG, SR, RTSTRUCT, GSPS, ...

This package defines the contract (model), geometry, colours, drawing and
the adapter registry. Adapters live in their own modules and import what
they need (highdicom, pydicom); nothing here needs more than pydicom,
numpy and Pillow. See docs/annotations_interface.md.
"""

from dicom_viewer.annotations.colors import PALETTE, cielab_to_rgb, default_color, dicom_cielab_to_rgb
from dicom_viewer.annotations.compose import compose, overlay_from_labels, render
from dicom_viewer.annotations.geometry import (
    basis,
    find_slice,
    find_slice_for_point,
    normal,
    plane_distance,
    same_grid,
    same_plane,
    series_info,
    slice_ref,
    to_patient,
    to_pixels,
)
from dicom_viewer.annotations.model import (
    RGB,
    Adapter,
    BaseLayer,
    Item,
    ItemKind,
    Label,
    Layer,
    LayerError,
    Mask,
    SeriesInfo,
    Shape,
    ShapeKind,
    SliceOverlay,
    SliceRef,
)
from dicom_viewer.annotations.registry import (
    adapter_for,
    adapters,
    find_annotation_files,
    find_layer_files,
    is_annotation_file,
    layers_for_folder,
    load_layers,
    register,
    sop_class_of,
    unregister,
)

__all__ = [
    "PALETTE", "cielab_to_rgb", "default_color", "dicom_cielab_to_rgb",
    "compose", "overlay_from_labels", "render",
    "basis", "find_slice", "find_slice_for_point", "normal", "plane_distance",
    "same_grid", "same_plane", "series_info", "slice_ref", "to_patient", "to_pixels",
    "RGB", "Adapter", "BaseLayer", "Item", "ItemKind", "Label", "Layer", "LayerError",
    "Mask", "SeriesInfo", "Shape", "ShapeKind", "SliceOverlay", "SliceRef",
    "adapter_for", "adapters", "find_annotation_files", "find_layer_files", "is_annotation_file",
    "layers_for_folder", "load_layers", "register", "sop_class_of", "unregister",
]
