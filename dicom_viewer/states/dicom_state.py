import reflex as rx
import asyncio
import collections
import dataclasses
import os
import logging
import numpy as np

from dicom_viewer import annotations, folders, series, volume
import dicom_viewer.annotations.builtin  # noqa: F401 - registers the SEG, SR, RTSTRUCT and PR adapters

# Used when neither the files nor the pixels give a window.
DEFAULT_WINDOW = (40.0, 400.0)
# selected_preset while the window is the one the series opened with.
AUTO = "Auto"

# Windowing presets: (center, width) in HU.
PRESETS = {
    "Soft Tissue": (40, 400),
    "Lung": (-600, 1500),
    "Bone": (300, 1500),
    "Brain": (40, 80),
    "Abdomen": (50, 400),
    "Liver": (60, 150),
    "Mediastinum": (40, 350),
    "Spine": (30, 300),
    "Pelvis": (50, 450),
    "Head/Neck": (50, 250),
    "CTA/Vascular": (150, 600),
    "Kidney": (30, 300),
    "Pancreas": (50, 200),
    "Trauma": (50, 500),
    "Subdural": (50, 130),
    "Stroke": (35, 30),
    "Body": (40, 450),
    "Extremity/MSK": (40, 350),
    "Temporal Bone": (700, 4000),
    "Sinus": (50, 300),
    "Angio Bone Sub": (300, 1200),
    "Lung HRCT": (-700, 1200),
    "Orbits": (50, 300),
    "CTA Head/Neck": (180, 700),
    "Arterial": (150, 600),
    "Venous": (100, 500),
    "Colon/Bowel": (50, 400),
    "Adrenal": (40, 300),
    "Gallbladder": (30, 200),
    "Skin/Subcutaneous": (50, 250),
    "Cardiac": (75, 350),
}

# How the preset picker groups them, most used first.
PRESET_GROUPS = {
    "General": ["Soft Tissue", "Body", "Trauma", "Skin/Subcutaneous"],
    "Head & Neck": [
        "Brain", "Stroke", "Subdural", "Head/Neck", "Temporal Bone", "Sinus", "Orbits",
    ],
    "Chest": ["Lung", "Lung HRCT", "Mediastinum", "Cardiac"],
    "Abdomen & Pelvis": [
        "Abdomen", "Liver", "Kidney", "Pancreas", "Adrenal", "Gallbladder", "Colon/Bowel",
        "Pelvis",
    ],
    "Bone & MSK": ["Bone", "Spine", "Extremity/MSK"],
    "Vessels (CTA)": ["CTA/Vascular", "CTA Head/Neck", "Arterial", "Venous", "Angio Bone Sub"],
}

# Files whose headers are read between two progress updates while opening.
_OPEN_BATCH = 16

_SLICE_KEY_STEPS = {
    "ArrowUp": -1,
    "ArrowLeft": -1,
    "ArrowDown": 1,
    "ArrowRight": 1,
    "PageUp": -10,
    "PageDown": 10,
}


@dataclasses.dataclass
class ItemView:
    """One toggleable thing of an annotation layer, as the panel shows it."""

    id: str = ""
    name: str = ""
    color: str = ""  # CSS colour
    description: str = ""
    visible: bool = True


@dataclasses.dataclass
class LayerView:
    """One annotation object (SEG, RTSTRUCT, SR, PR) found beside the open series."""

    id: str = ""
    format: str = ""
    name: str = ""
    notes: list[str] = dataclasses.field(default_factory=list)
    volume_ok: bool = False  # can be shown in 3 views
    visible: bool = True
    rows: list[ItemView] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Drawing:
    """How the panel wants annotations drawn, as the renderers take it."""

    visible: dict[str, list[str]]  # layer id -> item ids to show; [] hides the layer
    opacity: float
    labels: bool
    version: int


@dataclasses.dataclass
class _Loaded:
    """The annotation files seen for a series: per file its layer (None when it
    is not for this series) and load error, plus the layers in folder order."""

    by_path: dict[str, tuple] = dataclasses.field(default_factory=dict)
    layers: list = dataclasses.field(default_factory=list)


# Loaded layers per series and label volumes per (volume, layer). They hold
# numpy arrays, so they live here; the state keeps only ids and visibility.
# The folders are scanned again every time a series opens, so annotations
# downloaded later show up; only the objects already read are reused.
_LAYER_CACHE_SIZE = 3
_LABEL_CACHE_SIZE = 8
_layer_cache: collections.OrderedDict[str, _Loaded] = collections.OrderedDict()
_label_cache: collections.OrderedDict[tuple[str, str], np.ndarray | None] = (
    collections.OrderedDict()
)


def _cached_layers(key: str) -> _Loaded | None:
    entry = _layer_cache.get(key)
    if entry is not None:
        _layer_cache.move_to_end(key)
    return entry


def _layers_of(key: str) -> list:
    entry = _cached_layers(key) if key else None
    return entry.layers if entry else []


def _store_layers(key: str, entry: _Loaded) -> None:
    _layer_cache[key] = entry
    _layer_cache.move_to_end(key)
    while len(_layer_cache) > _LAYER_CACHE_SIZE:
        _layer_cache.popitem(last=False)


def _store_labels(key: tuple[str, str], labels: np.ndarray | None) -> None:
    _label_cache[key] = labels
    _label_cache.move_to_end(key)
    while len(_label_cache) > _LABEL_CACHE_SIZE:
        _label_cache.popitem(last=False)


def _layer_view(layer) -> LayerView:
    return LayerView(
        id=layer.id,
        format=layer.format,
        name=layer.name,
        notes=list(layer.notes),
        volume_ok=bool(layer.supports_volume),
        rows=[
            ItemView(
                id=item.id,
                name=item.name,
                color="rgb(%d, %d, %d)" % tuple(item.color),
                description=item.description,
            )
            for item in layer.items
        ],
    )


def _render_slice(pixels, slope, intercept, center, width, ref, layers, drawing) -> str:
    """The windowed slice with the visible annotations on it, as a PNG data URL."""
    try:
        image = series.render_image(pixels, slope, intercept, center, width)
    except Exception as e:
        logging.exception(f"Error processing image: {e}")
        return "/placeholder.svg"
    if ref is not None and layers:
        try:
            image = annotations.render(
                image,
                ref,
                layers,
                visible=drawing.visible,
                opacity=drawing.opacity,
                show_labels=drawing.labels,
            )
        except Exception as e:
            logging.exception(f"Error drawing annotations: {e}")
    return series.encode_png(image)


def _labels_for(layer, stack, vol: volume.Volume) -> np.ndarray | None:
    """A layer's label volume turned like `vol`, or None when it cannot be shown."""
    try:
        labels = layer.label_volume(stack)
    except Exception as e:
        logging.exception(f"Error building the labels of {layer.name}: {e}")
        return None
    return None if labels is None else volume.reorient(labels, vol)


def _plane_overlays(key: str, layers, drawing) -> list[tuple]:
    """(layer, labels, hidden item ids) for the visible layers that have labels."""
    overlays = []
    for layer in layers:
        shown = drawing.visible.get(layer.id)
        if shown is None:
            shown = [item.id for item in layer.items]
        labels = _label_cache.get((key, layer.id)) if shown else None
        if labels is None:
            continue
        hidden = {item.id for item in layer.items} - set(shown)
        overlays.append((layer, labels, hidden))
    return overlays


def _render_planes(
    vol: volume.Volume,
    indices: dict[str, int],
    center: float,
    width: float,
    overlays: list[tuple] = (),
    opacity: float = 0.4,
) -> dict[str, str]:
    pictures = {}
    for plane, index in indices.items():
        try:
            image = series.render_image(
                vol.plane(plane, index), vol.slope, vol.intercept, center, width
            )
        except Exception as e:
            logging.exception(f"Error processing the {plane} view: {e}")
            pictures[plane] = "/placeholder.svg"
            continue
        for layer, labels, hidden in overlays:
            try:
                cut = volume.cut(labels, plane, index)
                image = annotations.compose(
                    image,
                    annotations.overlay_from_labels(cut, layer.items),
                    layer.items,
                    opacity=opacity,
                    hidden=hidden,
                )
            except Exception as e:
                logging.exception(f"Error drawing {layer.name} on the {plane} view: {e}")
        pictures[plane] = series.encode_png(image)
    return pictures


class DicomViewerState(rx.State):
    """State of the open series and how it is displayed."""
    series_path: str = ""
    series_name: str = ""
    series_label: str = ""
    total_images: int = 0
    _series_files: list[str] = []
    # The Auto window, and where it came from: "file", "image" or "default".
    _series_window: tuple[float, float] = DEFAULT_WINDOW
    auto_source: str = "file"
    # Bumped when a different series is opened, so in-flight image work for
    # the previous one is dropped.
    _series_version: int = 0
    opening: bool = False
    opening_done: int = 0
    opening_total: int = 0
    open_error: str = ""
    current_index: int = 0
    # The sliders are uncontrolled so they can be dragged; changing these keys
    # remounts them at the state value after a change made by other means.
    slice_slider_key: int = 0
    window_slider_key: int = 0
    error_message: str = ""
    current_image_base64: str = "/placeholder.svg"
    window_center: float = DEFAULT_WINDOW[0]
    window_width: float = DEFAULT_WINDOW[1]
    zoom_level: float = 1.0
    pan_x: int = 0
    pan_y: int = 0
    # AUTO, a preset name, or "" when the window was set with the sliders.
    selected_preset: str = AUTO
    tooltip_language: str = "zh-TW"
    tutorial_dialog_open: bool = False
    tutorial_dialog_title: str = ""
    tutorial_dialog_url: str = ""
    patient_name: str = "N/A"
    patient_id: str = "N/A"
    study_date: str = "N/A"
    modality: str = "N/A"
    study_description: str = "N/A"
    series_description: str = "N/A"
    image_position: str = "N/A"
    pixel_spacing: str = "N/A"
    slice_thickness: str = "N/A"
    rows: int = 0
    columns: int = 0
    # Side panels: from the lg breakpoint up they sit beside the image and
    # fold into a rail; below it they are drawers over the image, all closed
    # until `drawer` names one ("metadata" or "controls").
    show_metadata: bool = True
    show_controls: bool = True
    drawer: str = ""
    metadata_unlocked: bool = False
    metadata_password_input: str = ""
    metadata_password_error: str = ""
    _cached_pixel_array: np.ndarray | None = None
    _cached_rescale_slope: float = 1.0
    _cached_rescale_intercept: float = 0.0
    _cached_index: int = -1
    _refreshing: bool = False
    # "single" shows one scanned image; "mpr" shows the series cut in three
    # planes (axial, coronal, sagittal) that share one crosshair point.
    view_mode: str = "single"
    mpr_status: str = ""  # "", "loading", "ready" or "error"
    mpr_done: int = 0
    mpr_total: int = 0
    mpr_error: str = ""
    mpr_notes: list[str] = []
    # Per plane: the slice shown, how many there are, picture width / height.
    plane_index: dict[str, int] = {plane: 0 for plane in volume.PLANES}
    plane_count: dict[str, int] = {plane: 0 for plane in volume.PLANES}
    plane_ratio: dict[str, float] = {plane: 1.0 for plane in volume.PLANES}
    # Separate vars, so redrawing one plane does not resend the other two.
    axial_image: str = "/placeholder.svg"
    coronal_image: str = "/placeholder.svg"
    sagittal_image: str = "/placeholder.svg"
    active_plane: str = "axial"
    show_crosshair: bool = True
    plane_slider_key: int = 0
    crosshair_position: str = ""
    crosshair_value: str = ""
    _volume_key: str = ""
    # (volume, slice, window) each plane picture was drawn for.
    _plane_drawn: dict[str, tuple] = {}
    _mpr_refreshing: bool = False
    # Annotation objects (SEG, RTSTRUCT, SR, PR) found beside the series:
    # what the panel lists, and what the user switched off.
    layers: list[LayerView] = []
    layer_errors: list[str] = []
    layers_status: str = ""  # "", "loading" or "ready"
    annotation_opacity: int = 40  # percent
    show_annotation_labels: bool = True
    annotation_slider_key: int = 0
    _layers_key: str = ""
    # Bumped whenever visibility or opacity changes, so the pictures redraw.
    _layers_version: int = 0
    # Where each file of _series_files is, for the annotation layers.
    _series_refs: list = []
    _default_tutorial_links: dict[str, str] = {
        # Use same-origin static assets so the tutorial can be embedded in an iframe.
        # (Many external sites, including Wikipedia, block iframe embedding.)
        "en": "/tutorials/windowing_en.html",
        "zh-TW": "/tutorials/windowing_zh_tw.html",
        "zh-CN": "/tutorials/windowing_zh_cn.html",
        "es": "/tutorials/windowing_es.html",
    }
    _preset_tutorial_links: dict[str, dict[str, str]] = {
        "en": {
            "Soft Tissue": "/tutorials/windowing_en.html#soft-tissue",
            "Lung": "/tutorials/windowing_en.html#lung",
            "Bone": "/tutorials/windowing_en.html#bone",
            "Brain": "/tutorials/windowing_en.html#brain",
            "Abdomen": "/tutorials/windowing_en.html#abdomen",
            "Liver": "/tutorials/windowing_en.html#liver",
            "Mediastinum": "/tutorials/windowing_en.html#mediastinum",
            "Spine": "/tutorials/windowing_en.html#spine",
            "Pelvis": "/tutorials/windowing_en.html#pelvis",
            "Head/Neck": "/tutorials/windowing_en.html#head-neck",
            "CTA/Vascular": "/tutorials/windowing_en.html#cta-vascular",
            "Kidney": "/tutorials/windowing_en.html#kidney",
            "Pancreas": "/tutorials/windowing_en.html#pancreas",
            "Trauma": "/tutorials/windowing_en.html#trauma",
            "Subdural": "/tutorials/windowing_en.html#subdural",
            "Stroke": "/tutorials/windowing_en.html#stroke",
            "Body": "/tutorials/windowing_en.html#body",
            "Extremity/MSK": "/tutorials/windowing_en.html#extremity-msk",
            "Temporal Bone": "/tutorials/windowing_en.html#temporal-bone",
            "Sinus": "/tutorials/windowing_en.html#sinus",
            "Angio Bone Sub": "/tutorials/windowing_en.html#angio-bone-sub",
            "Lung HRCT": "/tutorials/windowing_en.html#lung-hrct",
            "Orbits": "/tutorials/windowing_en.html#orbits",
            "CTA Head/Neck": "/tutorials/windowing_en.html#cta-head-neck",
            "Arterial": "/tutorials/windowing_en.html#arterial",
            "Venous": "/tutorials/windowing_en.html#venous",
            "Colon/Bowel": "/tutorials/windowing_en.html#colon-bowel",
            "Adrenal": "/tutorials/windowing_en.html#adrenal",
            "Gallbladder": "/tutorials/windowing_en.html#gallbladder",
            "Skin/Subcutaneous": "/tutorials/windowing_en.html#skin-subcutaneous",
            "Cardiac": "/tutorials/windowing_en.html#cardiac",
        },
        "zh-TW": {
            "Soft Tissue": "/tutorials/windowing_zh_tw.html#soft-tissue",
            "Lung": "/tutorials/windowing_zh_tw.html#lung",
            "Bone": "/tutorials/windowing_zh_tw.html#bone",
            "Brain": "/tutorials/windowing_zh_tw.html#brain",
            "Abdomen": "/tutorials/windowing_zh_tw.html#abdomen",
            "Liver": "/tutorials/windowing_zh_tw.html#liver",
            "Mediastinum": "/tutorials/windowing_zh_tw.html#mediastinum",
            "Spine": "/tutorials/windowing_zh_tw.html#spine",
            "Pelvis": "/tutorials/windowing_zh_tw.html#pelvis",
            "Head/Neck": "/tutorials/windowing_zh_tw.html#head-neck",
            "CTA/Vascular": "/tutorials/windowing_zh_tw.html#cta-vascular",
            "Kidney": "/tutorials/windowing_zh_tw.html#kidney",
            "Pancreas": "/tutorials/windowing_zh_tw.html#pancreas",
            "Trauma": "/tutorials/windowing_zh_tw.html#trauma",
            "Subdural": "/tutorials/windowing_zh_tw.html#subdural",
            "Stroke": "/tutorials/windowing_zh_tw.html#stroke",
            "Body": "/tutorials/windowing_zh_tw.html#body",
            "Extremity/MSK": "/tutorials/windowing_zh_tw.html#extremity-msk",
            "Temporal Bone": "/tutorials/windowing_zh_tw.html#temporal-bone",
            "Sinus": "/tutorials/windowing_zh_tw.html#sinus",
            "Angio Bone Sub": "/tutorials/windowing_zh_tw.html#angio-bone-sub",
            "Lung HRCT": "/tutorials/windowing_zh_tw.html#lung-hrct",
            "Orbits": "/tutorials/windowing_zh_tw.html#orbits",
            "CTA Head/Neck": "/tutorials/windowing_zh_tw.html#cta-head-neck",
            "Arterial": "/tutorials/windowing_zh_tw.html#arterial",
            "Venous": "/tutorials/windowing_zh_tw.html#venous",
            "Colon/Bowel": "/tutorials/windowing_zh_tw.html#colon-bowel",
            "Adrenal": "/tutorials/windowing_zh_tw.html#adrenal",
            "Gallbladder": "/tutorials/windowing_zh_tw.html#gallbladder",
            "Skin/Subcutaneous": "/tutorials/windowing_zh_tw.html#skin-subcutaneous",
            "Cardiac": "/tutorials/windowing_zh_tw.html#cardiac",
        },
        "zh-CN": {
                "Soft Tissue": "/tutorials/windowing_zh_cn.html#soft-tissue",
                "Lung": "/tutorials/windowing_zh_cn.html#lung",
                "Bone": "/tutorials/windowing_zh_cn.html#bone",
                "Brain": "/tutorials/windowing_zh_cn.html#brain",
                "Abdomen": "/tutorials/windowing_zh_cn.html#abdomen",
                "Liver": "/tutorials/windowing_zh_cn.html#liver",
                "Mediastinum": "/tutorials/windowing_zh_cn.html#mediastinum",
                "Spine": "/tutorials/windowing_zh_cn.html#spine",
                "Pelvis": "/tutorials/windowing_zh_cn.html#pelvis",
                "Head/Neck": "/tutorials/windowing_zh_cn.html#head-neck",
                "CTA/Vascular": "/tutorials/windowing_zh_cn.html#cta-vascular",
                "Kidney": "/tutorials/windowing_zh_cn.html#kidney",
                "Pancreas": "/tutorials/windowing_zh_cn.html#pancreas",
                "Trauma": "/tutorials/windowing_zh_cn.html#trauma",
                "Subdural": "/tutorials/windowing_zh_cn.html#subdural",
                "Stroke": "/tutorials/windowing_zh_cn.html#stroke",
                "Body": "/tutorials/windowing_zh_cn.html#body",
                "Extremity/MSK": "/tutorials/windowing_zh_cn.html#extremity-msk",
                "Temporal Bone": "/tutorials/windowing_zh_cn.html#temporal-bone",
                "Sinus": "/tutorials/windowing_zh_cn.html#sinus",
                "Angio Bone Sub": "/tutorials/windowing_zh_cn.html#angio-bone-sub",
                "Lung HRCT": "/tutorials/windowing_zh_cn.html#lung-hrct",
                "Orbits": "/tutorials/windowing_zh_cn.html#orbits",
                "CTA Head/Neck": "/tutorials/windowing_zh_cn.html#cta-head-neck",
                "Arterial": "/tutorials/windowing_zh_cn.html#arterial",
                "Venous": "/tutorials/windowing_zh_cn.html#venous",
                "Colon/Bowel": "/tutorials/windowing_zh_cn.html#colon-bowel",
                "Adrenal": "/tutorials/windowing_zh_cn.html#adrenal",
                "Gallbladder": "/tutorials/windowing_zh_cn.html#gallbladder",
                "Skin/Subcutaneous": "/tutorials/windowing_zh_cn.html#skin-subcutaneous",
                "Cardiac": "/tutorials/windowing_zh_cn.html#cardiac",
            },
            "es": {
                "Soft Tissue": "/tutorials/windowing_es.html#soft-tissue",
                "Lung": "/tutorials/windowing_es.html#lung",
                "Bone": "/tutorials/windowing_es.html#bone",
                "Brain": "/tutorials/windowing_es.html#brain",
                "Abdomen": "/tutorials/windowing_es.html#abdomen",
                "Liver": "/tutorials/windowing_es.html#liver",
                "Mediastinum": "/tutorials/windowing_es.html#mediastinum",
                "Spine": "/tutorials/windowing_es.html#spine",
                "Pelvis": "/tutorials/windowing_es.html#pelvis",
                "Head/Neck": "/tutorials/windowing_es.html#head-neck",
                "CTA/Vascular": "/tutorials/windowing_es.html#cta-vascular",
                "Kidney": "/tutorials/windowing_es.html#kidney",
                "Pancreas": "/tutorials/windowing_es.html#pancreas",
                "Trauma": "/tutorials/windowing_es.html#trauma",
                "Subdural": "/tutorials/windowing_es.html#subdural",
                "Stroke": "/tutorials/windowing_es.html#stroke",
                "Body": "/tutorials/windowing_es.html#body",
                "Extremity/MSK": "/tutorials/windowing_es.html#extremity-msk",
                "Temporal Bone": "/tutorials/windowing_es.html#temporal-bone",
                "Sinus": "/tutorials/windowing_es.html#sinus",
                "Angio Bone Sub": "/tutorials/windowing_es.html#angio-bone-sub",
                "Lung HRCT": "/tutorials/windowing_es.html#lung-hrct",
                "Orbits": "/tutorials/windowing_es.html#orbits",
                "CTA Head/Neck": "/tutorials/windowing_es.html#cta-head-neck",
                "Arterial": "/tutorials/windowing_es.html#arterial",
                "Venous": "/tutorials/windowing_es.html#venous",
                "Colon/Bowel": "/tutorials/windowing_es.html#colon-bowel",
                "Adrenal": "/tutorials/windowing_es.html#adrenal",
                "Gallbladder": "/tutorials/windowing_es.html#gallbladder",
                "Skin/Subcutaneous": "/tutorials/windowing_es.html#skin-subcutaneous",
                "Cardiac": "/tutorials/windowing_es.html#cardiac",
            },
    }

    _preset_descriptions: dict[str, dict[str, str]] = {
        "en": {
            "Soft Tissue": "Good for most organs and muscles.",
            "Lung": "Best for seeing lungs and airways.",
            "Bone": "Makes bones and fractures easy to see.",
            "Brain": "Standard view for brain tissue.",
            "Abdomen": "General view for belly organs.",
            "Liver": "Clearer view of the liver.",
            "Mediastinum": "Shows the middle chest area.",
            "Spine": "Helps check the spine and canal.",
            "Pelvis": "General view of pelvic organs.",
            "Head/Neck": "Shows soft tissues in head/neck.",
            "CTA/Vascular": "Shows blood vessels with contrast.",
            "Kidney": "Helps see kidneys clearly.",
            "Pancreas": "Better view of the pancreas.",
            "Trauma": "Wide view for injuries.",
            "Subdural": "Helps see bleeding under the skull.",
            "Stroke": "Narrow brain view for early stroke.",
            "Body": "Wide soft-tissue view for whole body.",
            "Extremity/MSK": "For arms/legs soft tissues.",
            "Temporal Bone": "Very wide view for ear bones.",
            "Sinus": "For sinuses around the nose.",
            "Angio Bone Sub": "Shows vessels after bone is removed.",
            "Lung HRCT": "Extra detail for lungs.",
            "Orbits": "For the eye sockets.",
            "CTA Head/Neck": "Vessels in head and neck.",
            "Arterial": "Artery phase vessels.",
            "Venous": "Vein phase vessels.",
            "Colon/Bowel": "For colon and bowel walls.",
            "Adrenal": "For adrenal glands.",
            "Gallbladder": "For gallbladder and bile ducts.",
            "Skin/Subcutaneous": "For skin and fat layer.",
            "Cardiac": "For heart soft tissues.",
        },
        "zh-TW": {
            "Soft Tissue": "看大部分器官與肌肉。",
            "Lung": "看肺部與氣道。",
            "Bone": "讓骨頭和骨折更清楚。",
            "Brain": "腦部常用視窗。",
            "Abdomen": "看腹部器官。",
            "Liver": "看肝臟更清楚。",
            "Mediastinum": "看胸部中間區域。",
            "Spine": "看脊椎與椎管。",
            "Pelvis": "看骨盆器官。",
            "Head/Neck": "看頭頸部軟組織。",
            "CTA/Vascular": "看打顯影劑的血管。",
            "Kidney": "看腎臟更清楚。",
            "Pancreas": "看胰臟更清楚。",
            "Trauma": "看受傷的大範圍。",
            "Subdural": "看頭骨下出血。",
            "Stroke": "看早期中風。",
            "Body": "全身軟組織大範圍。",
            "Extremity/MSK": "看四肢軟組織。",
            "Temporal Bone": "看耳朵附近骨頭。",
            "Sinus": "看鼻竇。",
            "Angio Bone Sub": "扣掉骨頭後看血管。",
            "Lung HRCT": "更細的肺部細節。",
            "Orbits": "看眼眶。",
            "CTA Head/Neck": "看頭頸部血管。",
            "Arterial": "動脈期血管。",
            "Venous": "靜脈期血管。",
            "Colon/Bowel": "看腸道和腸壁。",
            "Adrenal": "看腎上腺。",
            "Gallbladder": "看膽囊和膽道。",
            "Skin/Subcutaneous": "看皮膚和皮下脂肪。",
            "Cardiac": "看心臟軟組織。",
        },
        "zh-CN": {
            "Soft Tissue": "看大部分器官和肌肉。",
            "Lung": "看肺部和气道。",
            "Bone": "让骨头和骨折更清楚。",
            "Brain": "脑部常用视窗。",
            "Abdomen": "看腹部器官。",
            "Liver": "看肝脏更清楚。",
            "Mediastinum": "看胸部中间区域。",
            "Spine": "看脊柱与椎管。",
            "Pelvis": "看骨盆器官。",
            "Head/Neck": "看头颈部软组织。",
            "CTA/Vascular": "看打显影剂的血管。",
            "Kidney": "看肾脏更清楚。",
            "Pancreas": "看胰腺更清楚。",
            "Trauma": "看受伤的大范围。",
            "Subdural": "看颅骨下出血。",
            "Stroke": "看早期中风。",
            "Body": "全身软组织大范围。",
            "Extremity/MSK": "看四肢软组织。",
            "Temporal Bone": "看耳朵附近骨头。",
            "Sinus": "看鼻窦。",
            "Angio Bone Sub": "去掉骨头后看血管。",
            "Lung HRCT": "更细的肺部细节。",
            "Orbits": "看眼眶。",
            "CTA Head/Neck": "看头颈部血管。",
            "Arterial": "动脉期血管。",
            "Venous": "静脉期血管。",
            "Colon/Bowel": "看肠道和肠壁。",
            "Adrenal": "看肾上腺。",
            "Gallbladder": "看胆囊和胆道。",
            "Skin/Subcutaneous": "看皮肤和皮下脂肪。",
            "Cardiac": "看心脏软组织。",
        },
        "es": {
            "Soft Tissue": "Para la mayoría de órganos y músculos.",
            "Lung": "Para ver pulmones y vías aéreas.",
            "Bone": "Hace más claros los huesos y fracturas.",
            "Brain": "Vista estándar del cerebro.",
            "Abdomen": "Vista general del abdomen.",
            "Liver": "Para ver el hígado con más detalle.",
            "Mediastinum": "Muestra el centro del pecho.",
            "Spine": "Para revisar la columna y el canal.",
            "Pelvis": "Vista general de la pelvis.",
            "Head/Neck": "Para tejidos de cabeza y cuello.",
            "CTA/Vascular": "Para vasos con contraste.",
            "Kidney": "Para ver los riñones.",
            "Pancreas": "Para ver el páncreas.",
            "Trauma": "Vista amplia para lesiones.",
            "Subdural": "Para ver sangrado bajo el cráneo.",
            "Stroke": "Vista estrecha para ictus temprano.",
            "Body": "Vista amplia de tejidos blandos.",
            "Extremity/MSK": "Para tejidos de brazos y piernas.",
            "Temporal Bone": "Vista muy amplia del temporal.",
            "Sinus": "Para los senos paranasales.",
            "Angio Bone Sub": "Vasos con hueso removido.",
            "Lung HRCT": "Más detalle del pulmón.",
            "Orbits": "Para las órbitas de los ojos.",
            "CTA Head/Neck": "Vasos de cabeza y cuello.",
            "Arterial": "Vasos en fase arterial.",
            "Venous": "Vasos en fase venosa.",
            "Colon/Bowel": "Para colon e intestino.",
            "Adrenal": "Para glándulas suprarrenales.",
            "Gallbladder": "Para vesícula y vía biliar.",
            "Skin/Subcutaneous": "Para piel y grasa.",
            "Cardiac": "Para tejidos del corazón.",
        },
    }

    # What Auto (by auto_source) and a window set by hand mean.
    _window_notes: dict[str, dict[str, str]] = {
        "en": {
            "file": "The window the series' files suggest.",
            "image": "Worked out from the image's own values.",
            "default": "A soft-tissue window; the series suggests none.",
            "manual": "Set by hand with the sliders below.",
        },
        "zh-TW": {
            "file": "使用這個序列檔案建議的視窗。",
            "image": "依影像本身的數值自動算出。",
            "default": "序列沒有建議視窗，先用軟組織視窗。",
            "manual": "用下方滑桿手動調整。",
        },
        "zh-CN": {
            "file": "使用这个序列文件建议的视窗。",
            "image": "根据图像本身的数值自动算出。",
            "default": "序列没有建议视窗，先用软组织视窗。",
            "manual": "用下方滑块手动调整。",
        },
        "es": {
            "file": "La ventana que sugieren los archivos de la serie.",
            "image": "Calculada a partir de los valores de la imagen.",
            "default": "Ventana de tejidos blandos; la serie no sugiere ninguna.",
            "manual": "Ajustada a mano con los controles de abajo.",
        },
    }

    _metadata_password: str = os.getenv("DICOM_METADATA_PASSWORD", "dicom")

    @rx.var
    def current_position_text(self) -> str:
        if not self.total_images:
            return "0 / 0"
        return f"{self.current_index + 1} / {self.total_images}"

    @rx.var
    def slider_max(self) -> int:
        return max(0, self.total_images - 1)

    @rx.var
    def opening_percent(self) -> int:
        if not self.opening_total:
            return 0
        return round(100 * self.opening_done / self.opening_total)

    @rx.event
    def dismiss_open_error(self):
        self.open_error = ""

    @rx.event(background=True)
    async def open_series(self, folder: str):
        """Read the DICOM files in `folder`, order the slices and show the viewer."""
        async with self:
            if self.opening:
                return
            self.opening = True
            self.opening_done = 0
            self.opening_total = 0
            self.open_error = ""
        try:
            if not folders.is_inside(folder, folders.dicom_root()):
                async with self:
                    self.open_error = "This folder is outside the DICOM folder."
                return
            try:
                files = await asyncio.to_thread(folders.image_files, folder)
                async with self:
                    self.opening_total = len(files)
                keyed = []
                for start in range(0, len(files), _OPEN_BATCH):
                    batch = files[start : start + _OPEN_BATCH]
                    keyed += await asyncio.to_thread(series.read_slices, batch)
                    async with self:
                        self.opening_done = start + len(batch)
            except OSError as e:
                logging.exception(f"Error opening {folder}: {e}")
                async with self:
                    self.open_error = folders.describe_error(e)
                return
            if not keyed:
                async with self:
                    self.open_error = "No readable DICOM images in this folder."
                return
            keyed.sort(key=lambda item: item[0])
            ordered = [path for _, path, _ in keyed]
            refs = [ref for _, _, ref in keyed]
            window = await asyncio.to_thread(series.file_window, ordered[0])
            source = "file"
            if window is None:
                # Nothing suggested: work one out from a slice in the middle.
                window = await asyncio.to_thread(
                    series.image_window, ordered[len(ordered) // 2]
                )
                source = "image"
            label = await asyncio.to_thread(folders.series_label, ordered[0])
            async with self:
                self._series_version += 1
                version = self._series_version
                self._series_files = ordered
                self._series_refs = refs
                self._series_window = window or DEFAULT_WINDOW
                self.auto_source = source if window else "default"
                self.series_path = folder
                self.series_name = os.path.basename(os.path.normpath(folder))
                self.series_label = label
                self.total_images = len(ordered)
                self.current_index = 0
                self.slice_slider_key += 1
                self.error_message = ""
                self.current_image_base64 = "/placeholder.svg"
                self._cached_index = -1
                self._cached_pixel_array = None
                self.zoom_level = 1.0
                self.pan_x = 0
                self.pan_y = 0
                self.metadata_unlocked = False
                self.metadata_password_input = ""
                self.metadata_password_error = ""
                self.view_mode = "single"
                self.mpr_status = ""
                self.mpr_error = ""
                self._volume_key = ""
                self.layers = []
                self.layer_errors = []
                self.layers_status = ""
                self._layers_key = ""
                self._set_window(*self._series_window, AUTO)
            # Show the first slice before switching pages.
            await self._refresh()
            yield rx.redirect("/viewer")
            async with self:
                self.opening = False
            # The annotations beside the series come after the pictures.
            await self._load_layers(folder, version)
        finally:
            async with self:
                self.opening = False

    @rx.event(background=True)
    async def refresh_image(self):
        async with self:
            mode = self.view_mode
        if mode == "mpr":
            await self._refresh_planes()
        else:
            await self._refresh()

    async def _refresh(self):
        """Bring the picture up to date with the slice and window in the state.

        Call from a background event. A call made while a refresh is running
        returns at once: the running loop sees its change and stops only when
        the picture matches the latest values, so drags and wheel bursts
        coalesce instead of piling up.
        """
        async with self:
            if self._refreshing:
                return
            self._refreshing = True
        done = False
        try:
            while True:
                async with self:
                    version = self._series_version
                    index = self.current_index
                    center, width = self.window_center, self.window_width
                    if not 0 <= index < len(self._series_files):
                        self._refreshing = False
                        done = True
                        return
                    path = self._series_files[index]
                    cached = self._cached_index == index
                    pixels = self._cached_pixel_array
                    slope = self._cached_rescale_slope
                    intercept = self._cached_rescale_intercept
                    ref = self._series_refs[index] if index < len(self._series_refs) else None
                    layers = _layers_of(self._layers_key)
                    drawing = self._drawing()
                ds = None
                if not cached:
                    try:
                        ds, pixels, slope, intercept = await asyncio.to_thread(
                            series.read_slice, path
                        )
                    except Exception as e:
                        logging.exception(f"Error loading image {path}: {e}")
                        async with self:
                            if self._series_version == version:
                                self.error_message = self._format_dicom_error(e)
                                self.current_image_base64 = "/placeholder.svg"
                                self._cached_index = -1
                                self._cached_pixel_array = None
                                if self.current_index == index:
                                    self._refreshing = False
                                    done = True
                                    return
                        continue
                image = await asyncio.to_thread(
                    _render_slice, pixels, slope, intercept, center, width, ref, layers, drawing
                )
                async with self:
                    if self._series_version != version:
                        continue
                    if ds is not None:
                        self._cached_index = index
                        self._cached_pixel_array = pixels
                        self._cached_rescale_slope = slope
                        self._cached_rescale_intercept = intercept
                        self._extract_metadata(ds)
                        self.error_message = ""
                    self.current_image_base64 = image
                    if (
                        self.current_index,
                        self.window_center,
                        self.window_width,
                        self._layers_version,
                    ) == (index, center, width, drawing.version):
                        self._refreshing = False
                        done = True
                        return
        finally:
            if not done:
                async with self:
                    self._refreshing = False

    def _extract_metadata(self, ds):
        """Extract metadata from DICOM dataset."""

        @rx.event
        def get_val(tag, default="N/A"):
            return str(ds.get(tag, default))

        self.patient_name = get_val("PatientName")
        self.patient_id = get_val("PatientID")
        self.study_date = get_val("StudyDate")
        self.modality = get_val("Modality")
        self.study_description = get_val("StudyDescription")
        self.series_description = get_val("SeriesDescription")
        pos = ds.get("ImagePositionPatient", None)
        self.image_position = (
            f"[{', '.join([f'{x:.1f}' for x in pos])}]" if pos else "N/A"
        )
        spacing = ds.get("PixelSpacing", None)
        self.pixel_spacing = (
            f"{spacing[0]:.3f} x {spacing[1]:.3f} mm" if spacing else "N/A"
        )
        thickness = ds.get("SliceThickness", None)
        self.slice_thickness = f"{thickness} mm" if thickness else "N/A"
        self.rows = int(ds.get("Rows", 0))
        self.columns = int(ds.get("Columns", 0))

    @rx.event
    def toggle_metadata(self):
        self.show_metadata = not self.show_metadata

    @rx.event
    def toggle_controls(self):
        self.show_controls = not self.show_controls

    @rx.event
    def set_drawer(self, side: str):
        self.drawer = side if side in ("metadata", "controls") else ""

    @rx.event
    def update_metadata_password(self, value: str):
        self.metadata_password_input = value
        if self.metadata_password_error:
            self.metadata_password_error = ""

    @rx.event
    def unlock_metadata(self):
        if self.metadata_password_input == self._metadata_password:
            self.metadata_unlocked = True
            self.metadata_password_input = ""
            self.metadata_password_error = ""
        else:
            self.metadata_password_error = "Invalid password."

    @rx.event
    def lock_metadata(self):
        self.metadata_unlocked = False
        self.metadata_password_input = ""
        self.metadata_password_error = ""

    def _format_dicom_error(self, error: Exception) -> str:
        message = str(error)
        lowered = message.lower()
        if (
            "transfer syntax" in lowered
            or "no handler" in lowered
            or "decode" in lowered
            or "compressed" in lowered
        ):
            return (
                "Unable to decode pixel data. This DICOM appears to use a compressed "
                "transfer syntax. Install pylibjpeg (and pylibjpeg-libjpeg) or gdcm to "
                "enable decompression, then restart the app."
            )
        return f"Error loading image: {message}"

    def _set_window(
        self, center: float, width: float, preset: str, from_slider: bool = False
    ):
        """Change the window; refresh_image redraws."""
        self.window_center = float(center)
        self.window_width = float(width)
        self.selected_preset = preset
        if not from_slider:
            self.window_slider_key += 1
        return DicomViewerState.refresh_image

    @rx.event
    def update_window_width(self, value: str):
        try:
            width = float(value)
        except ValueError as e:
            logging.exception(f"Error updating window width: {e}")
            return
        return self._set_window(self.window_center, width, "", from_slider=True)

    @rx.event
    def update_window_center(self, value: str):
        try:
            center = float(value)
        except ValueError as e:
            logging.exception(f"Error updating window center: {e}")
            return
        return self._set_window(center, self.window_width, "", from_slider=True)

    def _go_to_slice(self, index: int, from_slider: bool = False):
        """Move to slice `index` (clamped); refresh_image redraws."""
        if not self.total_images:
            return None
        index = max(0, min(self.total_images - 1, index))
        if index == self.current_index:
            return None
        self.current_index = index
        if not from_slider:
            self.slice_slider_key += 1
        return DicomViewerState.refresh_image

    @rx.event
    def next_image(self):
        return self._go_to_slice(self.current_index + 1)

    @rx.event
    def prev_image(self):
        return self._go_to_slice(self.current_index - 1)

    @rx.event
    def set_slice_index(self, value: str):
        try:
            index = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting slice index: {e}")
            return
        return self._go_to_slice(index, from_slider=True)

    @rx.event
    def scroll_slices(self, delta_y: float):
        """Mouse wheel over the image: down is the next slice, up the previous."""
        if delta_y:
            return self._go_to_slice(self.current_index + (1 if delta_y > 0 else -1))

    @rx.event
    def handle_slice_key(self, key: str):
        """Arrow and Page keys step through the slices; Home/End jump to the ends."""
        if key == "Escape":
            self.drawer = ""
            return None
        if self.view_mode == "mpr":
            plane = self.active_plane
            index = self.plane_index.get(plane, 0)
            if key == "Home":
                return self._move_plane(plane, 0)
            if key == "End":
                return self._move_plane(plane, self.plane_count.get(plane, 0) - 1)
            if key in _SLICE_KEY_STEPS:
                return self._move_plane(plane, index + _SLICE_KEY_STEPS[key])
            return None
        if key == "Home":
            return self._go_to_slice(0)
        if key == "End":
            return self._go_to_slice(self.total_images - 1)
        if key in _SLICE_KEY_STEPS:
            return self._go_to_slice(self.current_index + _SLICE_KEY_STEPS[key])

    @rx.var
    def mpr_percent(self) -> int:
        if not self.mpr_total:
            return 0
        return round(100 * self.mpr_done / self.mpr_total)

    @rx.var
    def plane_position_text(self) -> dict[str, str]:
        return {
            plane: f"{self.plane_index.get(plane, 0) + 1} / {self.plane_count.get(plane, 0)}"
            for plane in volume.PLANES
        }

    @rx.var
    def crosshair(self) -> dict[str, float]:
        """Where each plane crosses the other views, in % of their width or height.

        Only the axial index is drawn upside down (head at the top).
        """
        place = {}
        for plane in volume.PLANES:
            count = max(1, self.plane_count.get(plane, 0))
            index = self.plane_index.get(plane, 0)
            if plane == "axial":
                index = count - 1 - index
            place[plane] = (index + 0.5) / count * 100
        return place

    @rx.event
    def set_view_mode(self, mode: str):
        if mode == self.view_mode or mode not in ("single", "mpr"):
            return None
        self.view_mode = mode
        if mode == "mpr":
            return DicomViewerState.load_volume
        # Back in one view, show the scanned image the crosshair is on.
        vol = volume.cached(self._volume_key)
        if vol is not None and self.mpr_status == "ready":
            path = vol.path_at(self.plane_index)
            if path in self._series_files:
                self._go_to_slice(self._series_files.index(path))
        return DicomViewerState.refresh_image

    @rx.event(background=True)
    async def load_volume(self):
        await self._load_volume()

    async def _load_volume(self, rebuild: bool = False):
        """Stack the open series into a volume (or reuse it) and draw the planes.

        `rebuild` is for a volume dropped from the cache while in use: its
        crosshair stays where it was.
        """
        async with self:
            if self.mpr_status == "loading" or not self._series_files:
                return
            files = list(self._series_files)
            version = self._series_version
            # Start on the image shown in one view, or in the middle of the
            # volume while one view is still on the first image.
            current_path = files[self.current_index] if self.current_index else ""
            key = volume.cache_key(files)
            vol = volume.cached(key)
            if vol is None:
                self.mpr_status = "loading"
                self.mpr_done = 0
                self.mpr_total = 0
                self.mpr_error = ""
        if vol is None:
            try:
                layout = await asyncio.to_thread(volume.read_layout, files)
                async with self:
                    self.mpr_total = len(layout.paths)
                slices = []
                for start in range(0, len(layout.paths), _OPEN_BATCH):
                    batch = layout.paths[start : start + _OPEN_BATCH]
                    slices += await asyncio.to_thread(volume.read_pixels, batch)
                    async with self:
                        if self._series_version != version:
                            self.mpr_status = ""
                            return
                        self.mpr_done = start + len(batch)
                vol = await asyncio.to_thread(volume.assemble, layout, slices)
            except Exception as e:
                if not isinstance(e, volume.VolumeError):
                    logging.exception(f"Error building the volume: {e}")
                async with self:
                    if self._series_version == version:
                        self.mpr_status = "error"
                        self.mpr_error = (
                            str(e)
                            if isinstance(e, volume.VolumeError)
                            else self._format_dicom_error(e)
                        )
                    else:
                        self.mpr_status = ""
                return
            volume.store(key, vol)
        async with self:
            if self._series_version != version:
                self.mpr_status = ""
                return
            cursor = vol.cursor_at(current_path)
            if self._volume_key != key:
                self._volume_key = key
                self._plane_drawn = {}
                self.plane_count = {plane: vol.count(plane) for plane in volume.PLANES}
                self.plane_ratio = {plane: vol.ratio(plane) for plane in volume.PLANES}
                self.plane_index = cursor
                self.active_plane = vol.scan_plane
                self.mpr_notes = volume.notes(vol)
            elif not rebuild:
                # Seen before: follow one view's image, keep the other planes.
                self.plane_index[vol.scan_plane] = cursor[vol.scan_plane]
            self.plane_slider_key += 1
            self._update_crosshair_info(vol)
            self.mpr_status = "ready"
        await self._load_labels(key, vol, version)
        await self._refresh_planes()

    async def _refresh_planes(self):
        """Bring the three plane pictures up to date; coalesces like _refresh."""
        async with self:
            if self._mpr_refreshing:
                return
            self._mpr_refreshing = True
        done = missing = False
        try:
            while True:
                async with self:
                    key = self._volume_key
                    window = (self.window_center, self.window_width)
                    drawing = self._drawing()
                    layers = _layers_of(self._layers_key)
                    wanted = {
                        plane: (key, self.plane_index[plane], *window, drawing.version)
                        for plane in volume.PLANES
                    }
                    stale = [
                        plane
                        for plane in volume.PLANES
                        if self._plane_drawn.get(plane) != wanted[plane]
                    ]
                    vol = volume.cached(key)
                    if self.view_mode != "mpr" or self.mpr_status != "ready" or not stale:
                        self._mpr_refreshing = False
                        done = True
                        return
                    if vol is None:
                        # Dropped from the cache to make room for other volumes.
                        self.mpr_status = ""
                        self._mpr_refreshing = False
                        done = missing = True
                        break
                overlays = _plane_overlays(key, layers, drawing)
                images = await asyncio.to_thread(
                    _render_planes,
                    vol,
                    {p: wanted[p][1] for p in stale},
                    *window,
                    overlays,
                    drawing.opacity,
                )
                async with self:
                    if self._volume_key != key:
                        continue
                    drawn = dict(self._plane_drawn)
                    for plane, image in images.items():
                        setattr(self, f"{plane}_image", image)
                        drawn[plane] = wanted[plane]
                    self._plane_drawn = drawn
        finally:
            if not done:
                async with self:
                    self._mpr_refreshing = False
        if missing:
            await self._load_volume(rebuild=True)

    async def _load_layers(self, folder: str, version: int):
        """Find the annotation objects for the open series and list them in the panel."""
        async with self:
            if self._series_version != version:
                return
            files = list(self._series_files)
            refs = list(self._series_refs)
            self.layers_status = "loading"
        key = volume.cache_key(files)
        entry = _cached_layers(key) or _Loaded()
        errors: list[tuple[str, str]] = []
        try:
            paths = await asyncio.to_thread(annotations.find_layer_files, folder)
        except Exception as e:
            logging.exception(f"Error looking for annotations in {folder}: {e}")
            paths = list(entry.by_path)
            errors.append((folder, str(e)))
        new = [path for path in paths if path not in entry.by_path]
        if new:
            failures: list[tuple[str, str]] = []
            try:
                info = annotations.series_info(refs)
                loaded = await asyncio.to_thread(annotations.load_layers, info, new, failures)
            except Exception as e:
                logging.exception(f"Error reading the annotations in {folder}: {e}")
                loaded = []
                errors.append((folder, str(e)))
            by_path = {layer.source_path: layer for layer in loaded}
            failed = dict(failures)
            for path in new:
                entry.by_path[path] = (by_path.get(path), failed.get(path, ""))
        found = [entry.by_path[path] for path in paths if path in entry.by_path]
        entry.layers = [layer for layer, _ in found if layer is not None]
        errors += [(path, entry.by_path[path][1]) for path in paths
                   if path in entry.by_path and entry.by_path[path][1]]
        _store_layers(key, entry)
        layers = entry.layers
        async with self:
            if self._series_version != version:
                return
            self._layers_key = key
            self.layers = [_layer_view(layer) for layer in layers]
            self.layer_errors = [
                f"{os.path.basename(path)}: {message}" for path, message in errors
            ]
            self.layers_status = "ready"
            self._layers_version += 1
            self.annotation_slider_key += 1
            mode, volume_key, mpr_status = self.view_mode, self._volume_key, self.mpr_status
        if not layers:
            return
        if mode == "mpr" and mpr_status == "ready":
            vol = volume.cached(volume_key)
            if vol is not None:
                await self._load_labels(volume_key, vol, version)
            await self._refresh_planes()
        elif mode == "single":
            await self._refresh()

    @rx.event(background=True)
    async def rescan_layers(self):
        """Look again for annotation files beside the series, e.g. after downloading one."""
        async with self:
            folder, version = self.series_path, self._series_version
            if not folder or self.layers_status == "loading":
                return
        await self._load_layers(folder, version)

    async def _load_labels(self, key: str, vol: volume.Volume, version: int):
        """Build the label volumes of the layers that 3 views can show (kept per volume)."""
        async with self:
            if self._series_version != version:
                return
            layers = _layers_of(self._layers_key)
            refs_by_path = dict(zip(self._series_files, self._series_refs))
        stack = [refs_by_path.get(path) for path in vol.layout.paths]
        if not layers or any(ref is None for ref in stack):
            return
        for layer in layers:
            if not layer.supports_volume or (key, layer.id) in _label_cache:
                continue
            labels = await asyncio.to_thread(_labels_for, layer, stack, vol)
            _store_labels((key, layer.id), labels)

    def _drawing(self) -> Drawing:
        """What the panel asks for, as the renderers take it."""
        visible = {
            layer.id: ([item.id for item in layer.rows if item.visible] if layer.visible else [])
            for layer in self.layers
        }
        return Drawing(
            visible,
            self.annotation_opacity / 100.0,
            self.show_annotation_labels,
            self._layers_version,
        )

    def _set_layers(self, layer_id: str, visible: bool | None = None, item_id: str = ""):
        """Copy the layer views with one layer, or one of its items, switched."""
        updated = []
        for layer in self.layers:
            if layer.id == layer_id:
                rows = [
                    ItemView(item.id, item.name, item.color, item.description,
                             (not item.visible) if item.id == item_id else item.visible)
                    for item in layer.rows
                ]
                layer = LayerView(
                    layer.id, layer.format, layer.name, list(layer.notes), layer.volume_ok,
                    layer.visible if visible is None else visible, rows,
                )
            updated.append(layer)
        self.layers = updated
        self._layers_version += 1

    @rx.event
    def toggle_layer(self, layer_id: str):
        current = next((layer.visible for layer in self.layers if layer.id == layer_id), None)
        if current is None:
            return None
        self._set_layers(layer_id, visible=not current)
        return DicomViewerState.refresh_image

    @rx.event
    def toggle_item(self, layer_id: str, item_id: str):
        self._set_layers(layer_id, item_id=item_id)
        return DicomViewerState.refresh_image

    @rx.event
    def set_annotation_opacity(self, value: str):
        try:
            percent = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting the annotation opacity: {e}")
            return None
        self.annotation_opacity = max(0, min(100, percent))
        self._layers_version += 1
        return DicomViewerState.refresh_image

    @rx.event
    def toggle_annotation_labels(self):
        self.show_annotation_labels = not self.show_annotation_labels
        self._layers_version += 1
        return DicomViewerState.refresh_image

    def _update_crosshair_info(self, vol: volume.Volume | None = None):
        vol = vol or volume.cached(self._volume_key)
        if vol is None:
            return
        z, y, x = (self.plane_index[plane] for plane in volume.PLANES)
        px, py, pz = vol.position(z, y, x)
        self.crosshair_position = (
            f"{'L' if px >= 0 else 'R'} {abs(px):.1f} · "
            f"{'P' if py >= 0 else 'A'} {abs(py):.1f} · "
            f"{'S' if pz >= 0 else 'I'} {abs(pz):.1f} mm"
        )
        value = vol.value(z, y, x)
        text = f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
        self.crosshair_value = f"{text} HU" if self.modality == "CT" else text

    def _move_plane(self, plane: str, index: int, from_slider: bool = False):
        """Show slice `index` (clamped) of `plane`; refresh_image redraws."""
        count = self.plane_count.get(plane, 0)
        if not count:
            return None
        self.active_plane = plane
        index = max(0, min(count - 1, index))
        if index == self.plane_index[plane]:
            return None
        self.plane_index[plane] = index
        if not from_slider:
            self.plane_slider_key += 1
        self._update_crosshair_info()
        return DicomViewerState.refresh_image

    @rx.event
    def scroll_plane(self, delta_y: float, plane: str):
        """Mouse wheel over a plane: down is its next slice, up the previous."""
        if delta_y and plane in self.plane_index:
            step = 1 if delta_y > 0 else -1
            return self._move_plane(plane, self.plane_index[plane] + step)

    @rx.event
    def show_plane(self, plane: str):
        """Narrow screens show one plane at a time: this picks it."""
        if plane in self.plane_index:
            self.active_plane = plane

    @rx.event
    def set_plane_index(self, plane: str, value: str):
        try:
            index = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting the {plane} slice: {e}")
            return
        return self._move_plane(plane, index, from_slider=True)

    @rx.event
    def click_plane(self, fx: float, fy: float, plane: str):
        """A click in a plane moves the crosshair there, in the other two planes."""
        if plane not in volume.SCREEN_AXES or not (0 <= fx <= 1 and 0 <= fy <= 1):
            return None
        self.active_plane = plane
        horizontal, vertical = volume.SCREEN_AXES[plane]
        changed = False
        for other, fraction in ((horizontal, fx), (vertical, fy)):
            count = self.plane_count.get(other, 0)
            index = min(count - 1, int(fraction * count))
            if other == "axial":
                index = count - 1 - index
            if count and index != self.plane_index[other]:
                self.plane_index[other] = index
                changed = True
        if not changed:
            return None
        self.plane_slider_key += 1
        self._update_crosshair_info()
        return DicomViewerState.refresh_image

    @rx.event
    def toggle_crosshair(self):
        self.show_crosshair = not self.show_crosshair

    @rx.event
    def set_zoom(self, value: float):
        """Set the zoom level within limits."""
        self.zoom_level = max(0.25, min(4.0, value))

    @rx.event
    def zoom_in(self):
        """Increase zoom level."""
        self.set_zoom(self.zoom_level + 0.25)

    @rx.event
    def zoom_out(self):
        """Decrease zoom level."""
        self.set_zoom(self.zoom_level - 0.25)

    @rx.event
    def reset_zoom(self):
        """Reset zoom and pan settings."""
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0

    @rx.event
    def pan_control(self, dx: int, dy: int):
        """Update pan position."""
        self.pan_x += dx
        self.pan_y += dy

    @rx.event
    def apply_preset(self, preset_name: str):
        """Apply a predefined windowing preset."""
        if preset_name in PRESETS:
            center, width = PRESETS[preset_name]
            return self._set_window(center, width, preset_name)

    @rx.event
    def apply_auto_window(self):
        """Back to the window the series opened with."""
        return self._set_window(*self._series_window, AUTO)

    @rx.event
    def reset_view(self):
        """Reset zoom, pan and the window to how the series opened."""
        self.reset_zoom()
        return self._set_window(*self._series_window, AUTO)

    @rx.event
    def set_tooltip_language(self, value: str):
        if value in self._preset_descriptions:
            self.tooltip_language = value

    def _get_tutorial_link(self, preset: str) -> str:
        links = self._preset_tutorial_links.get(self.tooltip_language, {})
        if preset in links:
            return links[preset]
        fallback_links = self._preset_tutorial_links.get("en", {})
        if preset in fallback_links:
            return fallback_links[preset]
        return self._default_tutorial_links.get(
            self.tooltip_language, self._default_tutorial_links["en"]
        )

    @rx.event
    def open_tutorial_dialog(self):
        """The tutorial of the preset in use; Auto and Manual open its start."""
        preset = self.selected_preset if self.selected_preset in PRESETS else ""
        self.tutorial_dialog_title = preset or "Windowing"
        self.tutorial_dialog_url = self._get_tutorial_link(preset)
        self.tutorial_dialog_open = True

    @rx.event
    def close_tutorial_dialog(self):
        self.tutorial_dialog_open = False
        self.tutorial_dialog_title = ""
        self.tutorial_dialog_url = ""

    @rx.var
    def window_description(self) -> str:
        """What the window in use is for, in the chosen language."""
        language = self.tooltip_language
        if self.selected_preset in PRESETS:
            descriptions = self._preset_descriptions.get(
                language, self._preset_descriptions["en"]
            )
            return descriptions.get(self.selected_preset, "")
        notes = self._window_notes.get(language, self._window_notes["en"])
        return notes[self.auto_source if self.selected_preset == AUTO else "manual"]
