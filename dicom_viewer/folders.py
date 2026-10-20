"""Folder browsing helpers: list folders and tell which ones hold DICOM images.

Plain Python without Reflex, so the slow parts can run in a worker thread.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys
from pathlib import Path

import pydicom

_SHARED_DICOM_DIR = "/Users/Shared/DICOM"

# Part 10 DICOM files carry "DICM" right after a 128-byte preamble. pydicom
# rejects files without it (the viewer never passes force=True), so this cheap
# check accepts exactly the files the viewer can open.
_PREAMBLE_LENGTH = 128
_DICOM_PREFIX = b"DICM"

_LABEL_TAGS = [
    "Modality",
    "SeriesDescription",
    "StudyDescription",
    "BodyPartExamined",
    "SeriesInstanceUID",
]


def dicom_root() -> str:
    """The folder the browser starts in. Users cannot go above it."""
    configured = os.getenv("PUBLIC_DICOM_DIR", "").strip()
    if configured:
        return os.path.normpath(configured)
    if sys.platform == "darwin" or Path(_SHARED_DICOM_DIR).is_dir():
        return _SHARED_DICOM_DIR
    return str(Path.home())


def is_inside(path: str, root: str) -> bool:
    """Whether `path` is `root` or below it (".." is resolved, symlinks are not)."""
    path, root = os.path.normpath(path), os.path.normpath(root)
    if not (os.path.isabs(path) and os.path.isabs(root)):
        return False
    return os.path.commonpath([path, root]) == root


def natural_key(name: str) -> list:
    """Sort key that puts "series2" before "series10"."""
    return [
        int(part) if part.isdecimal() else part.lower()
        for part in re.split(r"(\d+)", name)
    ]


def is_dicom_file(path: str) -> bool:
    try:
        with open(path, "rb") as file:
            file.seek(_PREAMBLE_LENGTH)
            return file.read(len(_DICOM_PREFIX)) == _DICOM_PREFIX
    except OSError:
        return False


@dataclasses.dataclass
class Listing:
    """Visible folders and files; hidden ones (.item/, .tmp/, .DS_Store) are skipped."""

    folders: list[str]
    files: list[str]


def list_folder(path: str) -> Listing:
    """Raises OSError when the folder cannot be read."""
    folders: list[str] = []
    files: list[str] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    folders.append(entry.name)
                elif entry.is_file():
                    files.append(entry.name)
            except OSError:
                continue
    folders.sort(key=natural_key)
    files.sort(key=natural_key)
    return Listing(folders, files)


def dicom_files(path: str, listing: Listing | None = None) -> list[str]:
    """Full paths of the DICOM files directly inside `path`, in name order."""
    names = (listing or list_folder(path)).files
    candidates = (os.path.join(path, name) for name in names)
    return [candidate for candidate in candidates if is_dicom_file(candidate)]


def annotation_files(files: list[str]) -> list[str]:
    """The files among `files` that hold annotations (SEG, RTSTRUCT, SR, PR, ...).

    Cheap: only when the first file is an annotation are the others checked,
    which fits the one-series-per-folder layout; a flat folder mixing images
    and annotations is split by `image_files` instead.
    """
    from dicom_viewer.annotations import builtin, registry  # noqa: F401 - registers the adapters

    if not files or not registry.is_annotation_file(files[0]):
        return []
    return [path for path in files if registry.is_annotation_file(path)]


def image_files(path: str, listing: Listing | None = None) -> list[str]:
    """The DICOM images directly inside `path`: every file no annotation adapter claims."""
    from dicom_viewer.annotations import builtin, registry  # noqa: F401 - registers the adapters

    return [
        candidate
        for candidate in dicom_files(path, listing)
        if not registry.is_annotation_file(candidate)
    ]


def series_facts(file_path: str) -> tuple[str, str]:
    """(label, SeriesInstanceUID) of a file's series, e.g. ("CT · CHEST 1.25MM", "1.2…")."""
    try:
        ds = pydicom.dcmread(
            file_path, stop_before_pixels=True, specific_tags=_LABEL_TAGS
        )
    except Exception:
        return "", ""

    def text(keyword: str) -> str:
        return str(ds.get(keyword, "") or "").strip()

    description = (
        text("SeriesDescription") or text("StudyDescription") or text("BodyPartExamined")
    )
    label = " · ".join(part for part in (text("Modality"), description) if part)
    return label, text("SeriesInstanceUID")


def series_label(file_path: str) -> str:
    """Short description of a file's series, e.g. "CT · CHEST 1.25MM"."""
    return series_facts(file_path)[0]


def present_series(parent: str) -> dict[str, str]:
    """The image series in the folders directly inside `parent`: SeriesInstanceUID -> folder."""
    found: dict[str, str] = {}
    try:
        listing = list_folder(parent)
    except OSError:
        return found
    for name in listing.folders:
        folder = os.path.join(parent, name)
        try:
            files = dicom_files(folder)
        except OSError:
            continue
        if files and not annotation_files(files[:1]):
            uid = series_facts(files[0])[1]
            if uid:
                found.setdefault(uid, folder)
    return found


@dataclasses.dataclass
class FolderSummary:
    folder_count: int = 0
    dicom_count: int = 0  # images
    annotation_count: int = 0
    label: str = ""
    series_uid: str = ""  # of the images; "" for a folder without images
    # Series the annotations need, for a folder that holds annotations only.
    references: list[str] = dataclasses.field(default_factory=list)
    error: str = ""


def summarize_folder(path: str, listing: Listing | None = None) -> FolderSummary:
    """Counts of subfolders, images and annotations, plus a label for the series."""
    try:
        listing = listing or list_folder(path)
        files = dicom_files(path, listing)
    except PermissionError:
        return FolderSummary(error="No permission to open this folder")
    except OSError as error:
        return FolderSummary(error=f"Cannot open: {error.strerror or error}")
    annotations = annotation_files(files)
    images = [path for path in files if path not in set(annotations)]
    label, series_uid = series_facts((images or annotations)[0]) if files else ("", "")
    references: set[str] = set()
    if annotations and not images:
        from dicom_viewer.annotations import registry

        for path in annotations:
            references |= registry.needed_series(path)
    return FolderSummary(
        folder_count=len(listing.folders),
        dicom_count=len(images),
        annotation_count=len(annotations),
        label=label,
        series_uid=series_uid if images else "",
        references=sorted(references),
    )


def describe_error(error: OSError) -> str:
    if isinstance(error, PermissionError):
        return (
            "Permission denied when opening this folder. On macOS, grant "
            "Terminal/VS Code access to it or enable Full Disk Access in "
            "System Settings > Privacy & Security."
        )
    if isinstance(error, FileNotFoundError):
        return "This folder no longer exists."
    return f"Cannot open this folder: {error.strerror or error}"
