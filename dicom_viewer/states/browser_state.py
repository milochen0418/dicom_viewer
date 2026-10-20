"""State of the folder page: the folder shown and which subfolders hold DICOM."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time

import reflex as rx

from dicom_viewer import folders

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
    error: str = ""


@dataclasses.dataclass
class Crumb:
    """One step of the breadcrumb, from the root down to the current folder."""

    name: str = ""
    path: str = ""


def _rows(
    folder: str, names: list[str], summaries: dict[str, folders.FolderSummary]
) -> list[FolderEntry]:
    rows = []
    for name in names:
        path = os.path.join(folder, name)
        summary = summaries.get(path)
        if summary is None:
            rows.append(FolderEntry(name=name, path=path))
        else:
            rows.append(
                FolderEntry(
                    name=name,
                    path=path,
                    checked=True,
                    folder_count=summary.folder_count,
                    dicom_count=summary.dicom_count,
                    annotation_count=summary.annotation_count,
                    label=summary.label,
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
    loading: bool = False
    checking: bool = False
    error: str = ""
    # Reopen the last folder, also in a new tab or after a restart.
    last_folder: str = rx.LocalStorage("", name="dicom_viewer_last_folder")
    _generation: int = 0

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
        async with self:
            if generation != self._generation:
                return
            self.entries = _rows(target, listing.folders, {})
            self.here_dicom_count = here.dicom_count
            self.here_annotation_count = here.annotation_count
            self.here_label = here.label
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
        async with self:
            if generation == self._generation:
                self.entries = _rows(target, listing.folders, summaries)
                self.checking = False
