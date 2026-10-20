"""Which adapter reads which DICOM object, and loading the layers for one series.

Adapters register themselves by SOP class. A file no adapter claims is not
an annotation as far as the viewer is concerned, so the folder scan can keep
treating it as an image without knowing every image SOP class.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

import pydicom

from dicom_viewer import folders
from dicom_viewer.annotations.model import Adapter, Layer, LayerError, SeriesInfo

_adapters: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    """Make `adapter` the reader for its SOP classes (replacing earlier ones); returns it."""
    if not adapter.sop_class_uids:
        raise ValueError(f"Adapter {adapter.name!r} claims no SOP class")
    for uid in adapter.sop_class_uids:
        _adapters[str(uid)] = adapter
    return adapter


def unregister(adapter: Adapter) -> None:
    for uid, registered in list(_adapters.items()):
        if registered is adapter:
            del _adapters[uid]


def adapters() -> list[Adapter]:
    """The registered adapters, each once, in registration order."""
    unique: list[Adapter] = []
    for adapter in _adapters.values():
        if all(adapter is not seen for seen in unique):
            unique.append(adapter)
    return unique


def adapter_for(sop_class_uid: str) -> Adapter | None:
    return _adapters.get(str(sop_class_uid))


def sop_class_of(path: str) -> str:
    """The file's SOPClassUID, or "" when it is not a readable DICOM file."""
    try:
        ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=["SOPClassUID"])
    except Exception:
        return ""
    return str(ds.get("SOPClassUID", "") or "")


def is_annotation_file(path: str) -> bool:
    """Whether a registered adapter reads this file."""
    return adapter_for(sop_class_of(path)) is not None


def find_annotation_files(folders_to_scan: Iterable[str], *, thorough: bool = True) -> list[str]:
    """Files directly inside each folder that a registered adapter can read.

    `thorough` reads every DICOM file's SOP class. With it off, only the
    first file of a folder is read and the folder is skipped when that file
    is not an annotation: cheap and right for the one-series-per-folder
    layout collections use, where a folder holds either images or one
    annotation object. A flat folder mixing both needs `thorough`.
    """
    found: list[str] = []
    for folder in folders_to_scan:
        try:
            files = folders.dicom_files(folder)
        except OSError as error:
            logging.warning("Cannot scan %s for annotations: %s", folder, error)
            continue
        if not files:
            continue
        if not thorough and not is_annotation_file(files[0]):
            continue
        found.extend(path for path in files if is_annotation_file(path))
    return found


def load_layers(
    series: SeriesInfo,
    paths: Iterable[str],
    errors: list[tuple[str, str]] | None = None,
) -> list[Layer]:
    """Load the objects at `paths` and keep the layers that annotate `series`.

    A file that fails to load is skipped and logged; when `errors` is given,
    (path, message) is appended to it for the UI to show.
    """
    layers: list[Layer] = []
    for path in paths:
        adapter = adapter_for(sop_class_of(path))
        if adapter is None:
            continue
        try:
            layer = adapter.load(path)
        except LayerError as error:
            message = str(error)
        except Exception as error:  # noqa: BLE001 - one bad file must not hide the others
            logging.exception("Adapter %s failed on %s", adapter.name, path)
            message = f"{adapter.name}: {error}"
        else:
            if layer.applies_to(series):
                layers.append(layer)
            continue
        logging.warning("Skipping %s: %s", path, message)
        if errors is not None:
            errors.append((path, message))
    return layers


def find_layer_files(folder: str, *, siblings: bool = True) -> list[str]:
    """Annotation files for the series in `folder`: in it and, with `siblings`, beside it.

    The folder itself is scanned thoroughly; sibling folders the cheap way
    (first file only), and only while their parent is still inside the
    viewer's DICOM root. Cheap enough to run every time a series opens.
    """
    folder = os.path.normpath(folder)
    paths = find_annotation_files([folder], thorough=True)
    if siblings:
        parent = os.path.dirname(folder)
        if folders.is_inside(parent, folders.dicom_root()):
            try:
                names = folders.list_folder(parent).folders
            except OSError as error:
                logging.warning("Cannot list %s for sibling annotations: %s", parent, error)
                names = []
            others = [os.path.join(parent, name) for name in names]
            paths += find_annotation_files(
                [other for other in others if other != folder], thorough=False
            )
    return paths


def layers_for_folder(
    series: SeriesInfo,
    folder: str,
    *,
    siblings: bool = True,
    errors: list[tuple[str, str]] | None = None,
) -> list[Layer]:
    """Layers for the series in `folder`: `find_layer_files` then `load_layers`."""
    return load_layers(series, find_layer_files(folder, siblings=siblings), errors)
