"""Addresses of series that can be shared: /viewer/<id>.

The id is the start of a hash of the folder's path below the DICOM root, so a link needs no
store, keeps working after a restart and names the same folder on another host with the same
folders. It stops working when the folder is moved or renamed.

Plain Python without Reflex, so the folder walk can run in a worker thread.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading

_ID_RE = re.compile(r"^[0-9a-f]{10}$")

_lock = threading.Lock()
# (root, id) -> folder, for the folders seen so far.
_known: dict[tuple[str, str], str] = {}


def valid_id(series_id: str) -> bool:
    return bool(series_id) and _ID_RE.match(series_id) is not None


def id_of(folder: str, root: str) -> str:
    """The id of `folder`, a folder below `root` or `root` itself."""
    root = os.path.normpath(root)
    relative = os.path.relpath(os.path.normpath(folder), root).replace(os.sep, "/")
    series_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:10]
    with _lock:
        _known[(root, series_id)] = os.path.normpath(folder)
    return series_id


def find_folder(series_id: str, root: str) -> str | None:
    """The folder whose id is `series_id`, or None when no folder below `root` has it.

    Hidden folders (.item/, .tmp/) are skipped, as on the folder page.
    """
    if not valid_id(series_id):
        return None
    root = os.path.normpath(root)
    with _lock:
        known = _known.get((root, series_id))
    if known is not None and os.path.isdir(known):
        return known
    seen: set[str] = set()
    for folder, subfolders, _files in os.walk(root, followlinks=True):
        real = os.path.realpath(folder)
        if real in seen:  # a link back up the tree
            subfolders[:] = []
            continue
        seen.add(real)
        subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
        if id_of(folder, root) == series_id:
            return os.path.normpath(folder)
    return None
