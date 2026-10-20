"""State of the folder page: the folder shown and which subfolders hold DICOM."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time

import reflex as rx

from dicom_viewer import folders
from dicom_viewer.states.dicom_state import DicomViewerState

# Subfolder checks reach the page at most this often (seconds).
_PUBLISH_INTERVAL = 0.3


@dataclasses.dataclass
class FolderEntry:
    """One subfolder row."""

    name: str = ""
    path: str = ""
    checked: bool = False  # the fields below are filled in
    folder_count: int = 0
    dicom_count: int = 0  # images
    annotation_count: int = 0
    label: str = ""
    series_uid: str = ""
    # Series the annotations need that are not downloaded, comma-separated.
    missing: str = ""
    missing_count: int = 0
    # The folder of the (downloaded) images the annotations belong to.
    opens: str = ""
    error: str = ""


@dataclasses.dataclass
class Crumb:
    """One step of the breadcrumb, from the root down to the current folder."""

    name: str = ""
    path: str = ""


def _present(
    summaries: dict[str, folders.FolderSummary], here_uid: str = "", here_path: str = ""
) -> dict[str, str]:
    """The image series in the checked folders, and in the folder itself: SeriesInstanceUID -> folder."""
    found = {s.series_uid: path for path, s in summaries.items() if s.series_uid}
    if here_uid and here_path:
        found.setdefault(here_uid, here_path)
    return found


def _rows(
    folder: str,
    names: list[str],
    summaries: dict[str, folders.FolderSummary],
    present: dict[str, str] | None = None,
) -> list[FolderEntry]:
    """`present` maps the series on disk to their folders; None while folders are still being checked."""
    rows = []
    for name in names:
        path = os.path.join(folder, name)
        summary = summaries.get(path)
        if summary is None:
            rows.append(FolderEntry(name=name, path=path))
            continue
        missing: list[str] = []
        opens = ""
        if present is not None and summary.annotation_count and not summary.dicom_count:
            missing = [uid for uid in summary.references if uid not in present]
            opens = next((present[uid] for uid in summary.references if uid in present), "")
        rows.append(
            FolderEntry(
                name=name,
                path=path,
                checked=True,
                folder_count=summary.folder_count,
                dicom_count=summary.dicom_count,
                annotation_count=summary.annotation_count,
                label=summary.label,
                series_uid=summary.series_uid,
                missing=",".join(missing),
                missing_count=len(missing),
                opens=opens,
                error=summary.error,
            )
        )
    return rows


class FolderBrowserState(rx.State):
    """Browse the DICOM root; each folder shows whether it holds DICOM images."""

    root: str = folders.dicom_root()
    path: str = ""
    crumbs: list[Crumb] = []
    entries: list[FolderEntry] = []
    here_dicom_count: int = 0
    here_annotation_count: int = 0
    here_label: str = ""
    here_missing: str = ""  # series this folder's annotations need, not downloaded
    here_opens: str = ""  # the folder of the images this folder's annotations belong to
    loading: bool = False
    checking: bool = False
    error: str = ""
    # Reopen the last folder, also in a new tab or after a restart.
    last_folder: str = rx.LocalStorage("", name="dicom_viewer_last_folder")
    _generation: int = 0
    # A series the explorer just fetched: opened once the listing shows it.
    _open_after: str = ""

    @rx.var
    def can_go_up(self) -> bool:
        return self.path not in ("", self.root)

    @rx.var
    def is_empty(self) -> bool:
        return (
            not self.loading
            and not self.error
            and not self.entries
            and self.here_dicom_count == 0
            and self.here_annotation_count == 0
        )

    @rx.event
    def on_load(self):
        return FolderBrowserState.browse_to(self.path or self.last_folder or self.root)

    @rx.event
    def open_folder(self, path: str, clicks: int = 1):
        # The second click of a double click would land on whatever row of the
        # opened folder is under the pointer, so only single clicks navigate.
        if clicks <= 1:
            return FolderBrowserState.browse_to(path)

    @rx.event
    def go_up(self, clicks: int = 1):
        if clicks <= 1 and self.can_go_up:
            return FolderBrowserState.browse_to(os.path.dirname(self.path))

    @rx.event
    def refresh(self):
        return FolderBrowserState.browse_to(self.path or self.root)

    @rx.event
    def images_fetched(self, data: dict):
        """The explorer's dialog finished: list the folders again and open the series it fetched."""
        series = data.get("downloaded") or data.get("series") or []
        if isinstance(series, str):
            series = [uid for uid in series.split(",") if uid]
        self._open_after = str(series[0]) if series else ""
        # Fetched from inside an annotation folder, the images land beside it.
        target = self.path or self.root
        if self.here_annotation_count and not self.here_dicom_count and self.can_go_up:
            target = os.path.dirname(self.path)
        return FolderBrowserState.browse_to(target)

    @rx.event
    def fetch_cancelled(self, data: dict):
        if str(data.get("reason", "")) == "unavailable":
            return rx.toast.error(
                "DICOM Data Explorer is not installed, so the images cannot be fetched from here."
            )
        return None

    def _target(self, path: str) -> str:
        """`path` if it is a folder inside the root, else its nearest such parent."""
        root = os.path.normpath(self.root)
        target = os.path.normpath(path) if path else root
        if not folders.is_inside(target, root):
            return root
        while target != root and not os.path.isdir(target):
            target = os.path.dirname(target)
        return target

    def _crumbs(self, target: str) -> list[Crumb]:
        root = os.path.normpath(self.root)
        crumbs = [Crumb(name=os.path.basename(root) or root, path=root)]
        relative = os.path.relpath(target, root)
        if relative != ".":
            current = root
            for part in relative.split(os.sep):
                current = os.path.join(current, part)
                crumbs.append(Crumb(name=part, path=current))
        return crumbs

    def _error_text(self, error: OSError, target: str) -> str:
        if isinstance(error, FileNotFoundError) and target == os.path.normpath(self.root):
            return (
                f"The DICOM folder {target} does not exist. Set PUBLIC_DICOM_DIR "
                "to the folder that holds your DICOM files."
            )
        return folders.describe_error(error)

    @rx.event(background=True)
    async def browse_to(self, path: str):
        """Show `path`, then check its subfolders for DICOM images one by one."""
        async with self:
            self._generation += 1
            generation = self._generation
            target = self._target(path)
            self.path = target
            self.crumbs = self._crumbs(target)
            self.here_dicom_count = 0
            self.here_annotation_count = 0
            self.here_label = ""
            self.here_missing = ""
            self.here_opens = ""
            self.loading = True
            self.error = ""
        try:
            listing = await asyncio.to_thread(folders.list_folder, target)
            here = await asyncio.to_thread(folders.summarize_folder, target, listing)
        except OSError as error:
            async with self:
                if generation == self._generation:
                    self.entries = []
                    self.loading = False
                    self.checking = False
                    self.error = self._error_text(error, target)
            return
        here_missing: list[str] = []
        here_opens = ""
        parent = os.path.dirname(target)
        if (
            here.annotation_count
            and not here.dicom_count
            and here.references
            and folders.is_inside(parent, os.path.normpath(self.root))
        ):
            # Annotations only: are the images they need in a folder beside this one?
            present = await asyncio.to_thread(folders.present_series, parent)
            here_missing = [uid for uid in here.references if uid not in present]
            here_opens = next((present[uid] for uid in here.references if uid in present), "")
        async with self:
            if generation != self._generation:
                return
            self.entries = _rows(target, listing.folders, {})
            self.here_dicom_count = here.dicom_count
            self.here_annotation_count = here.annotation_count
            self.here_label = here.label
            self.here_missing = ",".join(here_missing)
            self.here_opens = here_opens
            self.loading = False
            self.checking = bool(listing.folders)
            self.last_folder = target

        summaries: dict[str, folders.FolderSummary] = {}
        published = time.monotonic()
        for name in listing.folders:
            subfolder = os.path.join(target, name)
            summaries[subfolder] = await asyncio.to_thread(
                folders.summarize_folder, subfolder
            )
            if time.monotonic() - published >= _PUBLISH_INTERVAL:
                async with self:
                    if generation != self._generation:
                        return
                    self.entries = _rows(target, listing.folders, summaries)
                published = time.monotonic()
        open_path = ""
        async with self:
            if generation == self._generation:
                self.entries = _rows(
                    target, listing.folders, summaries, _present(summaries, here.series_uid, target)
                )
                self.checking = False
                wanted, self._open_after = self._open_after, ""
                if wanted:
                    open_path = next(
                        (e.path for e in self.entries if e.series_uid == wanted), ""
                    )
        if open_path:
            yield DicomViewerState.open_series(open_path)
