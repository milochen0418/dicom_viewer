"""Who is looking at which series, one entry per browser tab, in this process's memory.

As with the people on a ReflexGrid workbook: a tab joins when it opens /viewer/<id>, pings
every few seconds, and leaves when it goes back to the folders or closes; a tab that stops
pinging is dropped after IDLE seconds. Several tabs of one person count once in `watching`.

Plain Python without Reflex.
"""

from __future__ import annotations

import dataclasses
import threading
import time

# The colours people get on a series, in this order (ReflexGrid's).
COLORS = ["#E8710A", "#188038", "#9334E6", "#D93025", "#E52592", "#12B5CB", "#B06000", "#3949AB", "#00897B", "#1A73E8"]
# A tab that hasn't pinged for this long has left (it pings every 5 s; hidden tabs are throttled).
IDLE = 30.0


@dataclasses.dataclass
class Viewer:
    peer: str  # the tab
    token: str  # its Reflex client token
    user: str  # the person: an e-mail, or guest-NNNN
    name: str
    avatar: str = ""
    color: str = ""
    last_seen: float = 0.0


_lock = threading.Lock()
# Series id -> peer -> viewer, in the order they came.
_on: dict[str, dict[str, Viewer]] = {}


def _color_for(tabs: dict[str, Viewer], user: str) -> str:
    """The person's colour on the series: their other tabs' one, else a free one."""
    used: dict[str, str] = {}
    for v in tabs.values():
        if v.color:
            used.setdefault(v.user, v.color)
    if user in used:
        return used[user]
    taken = set(used.values())
    return next((c for c in COLORS if c not in taken), COLORS[len(used) % len(COLORS)])


def join(series_id: str, peer: str, token: str, user: str, name: str, avatar: str = "", now: float | None = None) -> Viewer:
    """Puts a tab on a series, or finds it there again; it leaves any other series."""
    now = time.time() if now is None else now
    with _lock:
        for other in [sid for sid, tabs in _on.items() if sid != series_id and peer in tabs]:
            _drop(other, peer)
        tabs = _on.setdefault(series_id, {})
        v = tabs.get(peer)
        if v is None:
            v = tabs[peer] = Viewer(peer, token, user, name, avatar)
        else:
            v.token, v.user, v.name, v.avatar = token, user, name, avatar
        if not v.color:
            v.color = _color_for({k: t for k, t in tabs.items() if k != peer}, user)
        v.last_seen = now
        return dataclasses.replace(v)


def _drop(series_id: str, peer: str) -> bool:
    tabs = _on.get(series_id)
    if not tabs or tabs.pop(peer, None) is None:
        return False
    if not tabs:
        del _on[series_id]
    return True


def leave(peer: str) -> str:
    """Takes a tab off its series; returns that series' id ("" when it was on none)."""
    with _lock:
        for series_id in [sid for sid, tabs in _on.items() if peer in tabs]:
            _drop(series_id, peer)
            return series_id
    return ""


def on(peer: str) -> str:
    """The series a tab is on ("" when none)."""
    with _lock:
        return next((sid for sid, tabs in _on.items() if peer in tabs), "")


def touch(series_id: str, peer: str, now: float | None = None) -> bool:
    """The tab is still there; False when it isn't on the series (any more)."""
    with _lock:
        v = _on.get(series_id, {}).get(peer)
        if v is None:
            return False
        v.last_seen = time.time() if now is None else now
        return True


def prune(series_id: str, idle: float = IDLE, now: float | None = None) -> list[str]:
    """Drops the tabs not seen for `idle` seconds; returns their peers."""
    now = time.time() if now is None else now
    with _lock:
        gone = [p for p, v in _on.get(series_id, {}).items() if now - v.last_seen > idle]
        for peer in gone:
            _drop(series_id, peer)
        return gone


def find(token: str, peer: str) -> str:
    """The series the tab `peer` is on, when `token` is that tab's; else ""."""
    with _lock:
        for series_id, tabs in _on.items():
            v = tabs.get(peer)
            if v is not None and v.token == token:
                return series_id
    return ""


def tabs(series_id: str) -> list[dict[str, str]]:
    """One entry per tab on the series: {"id", "user", "name", "color", "avatar"}."""
    with _lock:
        return [
            {"id": v.peer, "user": v.user, "name": v.name, "color": v.color, "avatar": v.avatar}
            for v in _on.get(series_id, {}).values()
        ]


def tokens_on(series_id: str) -> list[str]:
    """Client tokens of the tabs on the series, most recently seen first."""
    with _lock:
        on = sorted(_on.get(series_id, {}).values(), key=lambda v: v.last_seen, reverse=True)
        return [v.token for v in on]


def users_on(series_id: str) -> list[str]:
    with _lock:
        return sorted({v.user for v in _on.get(series_id, {}).values()} - {""})


def watching() -> dict[str, list[dict[str, str]]]:
    """The people on each series: {series id: [{"user", "name", "color"}]}, one per person."""
    out: dict[str, list[dict[str, str]]] = {}
    with _lock:
        for series_id, on in _on.items():
            people: dict[str, dict[str, str]] = {}
            for v in on.values():
                people.setdefault(v.user or v.peer, {"user": v.user, "name": v.name, "color": v.color})
            if people:
                out[series_id] = list(people.values())
    return out
