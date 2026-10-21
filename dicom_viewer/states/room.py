"""What everybody on a series shares, pushed to all of them at once.

``SeriesRoom`` is a Reflex ``SharedState``: every tab on /viewer/<id> links its room to the
series (``room_token``), so what one person does to the view (the slice, 1 view or 3 views,
the crosshair, the window, zoom and pan, the annotations shown) reaches everyone on it, and
the pictures, drawn once on the server (:mod:`.series_work`), reach all of them. The room
also says who is here, who did the last thing (``actor``), and the series' call.

The series' files, pixels and volumes are not in it: they live once in the server's memory.
What is each person's own (the side panels, the language of the descriptions, the tutorial,
the metadata password) stays in their ``DicomViewerState``.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from dicom_viewer import volume, windowing
from dicom_viewer.windowing import AUTO, DEFAULT_WINDOW

LANGUAGES = ("en", "zh-TW", "zh-CN", "es")
# Slider groups: one key remounts all the sliders of a group (see `slider_keys_after`).
SLIDER_GROUPS = ("slice", "window", "plane", "opacity")
_PLANE_TITLES = {"axial": "Axial", "coronal": "Coronal", "sagittal": "Sagittal"}


def room_token(series_id: str) -> str:
    """The shared-state token of a series (Reflex link tokens can't contain ``_``)."""
    return f"series-{series_id}"


def slider_keys_after(key: int, hold: int, by: str, peer: str, drag: bool) -> tuple[int, int, str]:
    """The (key, hold, by) of a slider group after its value changed.

    The sliders are uncontrolled, so they can be dragged; a new React key remounts them at the
    room's value. Everyone's sliders take ``key``, except the person dragging one of the group
    (``by``), whose sliders keep ``hold``, the key they had when the drag began: a remount
    would end their drag.
    """
    if not drag:
        return key + 1, hold, ""
    if by != peer:
        hold, by = key, peer
    return key + 1, hold, by


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


class SeriesRoom(rx.SharedState):
    # The series: "loading" while its files are read, then "ready" (or "error").
    status: str = ""
    opening_done: int = 0
    opening_total: int = 0
    open_error: str = ""
    series_path: str = ""
    series_name: str = ""
    series_label: str = ""
    total_images: int = 0
    # The window it opened with (Auto), and where that came from: "file", "image" or "default".
    auto_window: list[float] = list(DEFAULT_WINDOW)
    auto_source: str = "file"

    # 1 view: the slice on screen, its picture, and its file's metadata.
    current_index: int = 0
    current_image_base64: str = "/placeholder.svg"
    error_message: str = ""
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

    # How everyone sees it.
    window_center: float = DEFAULT_WINDOW[0]
    window_width: float = DEFAULT_WINDOW[1]
    # AUTO, a preset name, or "" when the window was set with the sliders.
    selected_preset: str = AUTO
    zoom_level: float = 1.0
    pan_x: int = 0
    pan_y: int = 0
    # "single" shows one scanned image; "mpr" shows the series cut in three planes (axial,
    # coronal, sagittal) that share one crosshair point.
    view_mode: str = "single"

    # 3 views.
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
    # The plane the keys move (framed in blue), and the one a phone shows.
    active_plane: str = "axial"
    show_crosshair: bool = True
    crosshair_position: str = ""
    crosshair_value: str = ""

    # Annotation objects (SEG, RTSTRUCT, SR, PR) found beside the series: what the panel
    # lists, and what was switched off.
    layers: list[LayerView] = []
    layer_errors: list[str] = []
    layers_status: str = ""  # "", "loading" or "ready"
    annotation_opacity: int = 40  # percent
    show_annotation_labels: bool = True

    # Slider keys per group (see `slider_keys_after`).
    slider_key: dict[str, int] = {group: 0 for group in SLIDER_GROUPS}
    slider_hold: dict[str, int] = {group: 0 for group in SLIDER_GROUPS}
    slider_by: dict[str, str] = {group: "" for group in SLIDER_GROUPS}

    # Who did the last thing: {"peer", "name", "color", "text", "plane"}; every action bumps
    # `actor_rev`, which restarts the badge that shows it.
    actor: dict[str, str] = {}
    actor_rev: int = 0

    # One entry per tab on the series: {"id", "user", "name", "color", "avatar"}.
    peers: list[dict[str, str]] = []
    # The series' call: how many are in it, who is ringing everyone (while it rings), the
    # people it rings (those here when it started), and those who joined or declined.
    call_count: int = 0
    call_ringing_from: str = ""
    call_invited: list[str] = []
    call_responded: list[str] = []

    # ------------------------------------------------------------ computed

    @rx.var
    def people(self) -> list[dict[str, str]]:
        """One entry per person: several tabs of one person count once."""
        out: list[dict[str, str]] = []
        seen = set()
        for p in self.peers:
            user = p.get("user") or p.get("id")
            if user in seen:
                continue
            seen.add(user)
            name = p.get("name", "") or "?"
            initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
            out.append({"id": p.get("id", ""), "user": user, "name": name, "initials": initials, "color": p.get("color", ""), "avatar": p.get("avatar", "")})
        return out

    @rx.var
    def people_count(self) -> int:
        return len(self.people)

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

    @rx.var
    def window_descriptions(self) -> dict[str, str]:
        """What the window in use is for, in each language (everyone reads their own)."""
        return {lang: windowing.describe(self.selected_preset, self.auto_source, lang) for lang in LANGUAGES}

    # ------------------------------------------------------------ changes
    # Called by a tab's event handlers on its linked room. `actor` is that person:
    # {"peer", "name", "color"}. Each returns whether anything changed.

    def _act(self, actor: dict, text: str, plane: str = "") -> None:
        self.actor = {"peer": actor.get("peer", ""), "name": actor.get("name", ""), "color": actor.get("color", ""), "text": text, "plane": plane}
        self.actor_rev += 1

    def _bump(self, group: str, peer: str = "", drag: bool = False) -> None:
        key, hold, by = slider_keys_after(self.slider_key[group], self.slider_hold[group], self.slider_by[group], peer, drag)
        self.slider_key[group] = key
        if self.slider_hold[group] != hold:
            self.slider_hold[group] = hold
        if self.slider_by[group] != by:
            self.slider_by[group] = by

    def _go_to_slice(self, index: int, actor: dict, drag: bool = False) -> bool:
        if not self.total_images:
            return False
        index = max(0, min(self.total_images - 1, index))
        if index == self.current_index:
            return False
        self.current_index = index
        self._bump("slice", actor.get("peer", ""), drag)
        self._act(actor, f"Slice {index + 1} / {self.total_images}")
        return True

    def _set_window(self, center: float, width: float, preset: str, actor: dict, drag: bool = False) -> bool:
        center, width = float(center), float(width)
        if (center, width, preset) == (self.window_center, self.window_width, self.selected_preset):
            return False
        self.window_center, self.window_width, self.selected_preset = center, width, preset
        self._bump("window", actor.get("peer", ""), drag)
        if preset == AUTO:
            text = "Auto window"
        elif preset:
            text = f"{preset} window"
        else:
            text = f"Level {center:g} · Width {width:g}"
        self._act(actor, text)
        return True

    def _set_zoom(self, value: float, actor: dict) -> bool:
        value = max(0.25, min(4.0, value))
        if value == self.zoom_level:
            return False
        self.zoom_level = value
        self._act(actor, f"Zoom {round(value * 100)}%")
        return True

    def _pan(self, dx: int, dy: int, actor: dict) -> None:
        self.pan_x += dx
        self.pan_y += dy
        self._act(actor, "Pan")

    def _reset_zoom(self, actor: dict) -> bool:
        if (self.zoom_level, self.pan_x, self.pan_y) == (1.0, 0, 0):
            return False
        self.zoom_level, self.pan_x, self.pan_y = 1.0, 0, 0
        self._act(actor, "Reset zoom and pan")
        return True

    def _set_view_mode(self, mode: str, actor: dict) -> bool:
        if mode == self.view_mode or mode not in ("single", "mpr"):
            return False
        self.view_mode = mode
        self._act(actor, "3 views" if mode == "mpr" else "1 view")
        return True

    def _move_plane(self, plane: str, index: int, actor: dict, drag: bool = False) -> bool:
        """Show slice `index` (clamped) of `plane`."""
        count = self.plane_count.get(plane, 0)
        if not count:
            return False
        if self.active_plane != plane:
            self.active_plane = plane
        index = max(0, min(count - 1, index))
        if index == self.plane_index[plane]:
            return False
        self.plane_index[plane] = index
        self._bump("plane", actor.get("peer", ""), drag)
        self._act(actor, f"{_PLANE_TITLES[plane]} {index + 1} / {count}", plane)
        return True

    def _click_plane(self, fx: float, fy: float, plane: str, actor: dict) -> bool:
        """A click in a plane moves the crosshair there, in the other two planes."""
        if plane not in volume.SCREEN_AXES or not (0 <= fx <= 1 and 0 <= fy <= 1):
            return False
        if self.active_plane != plane:
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
        if changed:
            self._bump("plane", actor.get("peer", ""))
            self._act(actor, "Moved the crosshair", plane)
        return changed

    def _show_plane(self, plane: str, actor: dict) -> bool:
        if plane not in self.plane_index or plane == self.active_plane:
            return False
        self.active_plane = plane
        self._act(actor, f"{_PLANE_TITLES[plane]} view", plane)
        return True

    def _toggle_crosshair(self, actor: dict) -> None:
        self.show_crosshair = not self.show_crosshair
        self._act(actor, "Crosshair lines " + ("on" if self.show_crosshair else "off"))

    def _set_layers(self, layer_id: str, visible: bool | None = None, item_id: str = "") -> str:
        """Copy the layer views with one layer, or one of its items, switched; returns what
        was switched, as the person reads it."""
        updated = []
        said = ""
        for layer in self.layers:
            if layer.id == layer_id:
                rows = []
                for item in layer.rows:
                    shown = (not item.visible) if item.id == item_id else item.visible
                    if item.id == item_id:
                        said = f"{item.name} {'shown' if shown else 'hidden'}"
                    rows.append(ItemView(item.id, item.name, item.color, item.description, shown))
                shown = layer.visible if visible is None else visible
                if visible is not None:
                    said = f"{layer.name} {'shown' if shown else 'hidden'}"
                layer = LayerView(layer.id, layer.format, layer.name, list(layer.notes), layer.volume_ok, shown, rows)
            updated.append(layer)
        self.layers = updated
        return said

    def _toggle_layer(self, layer_id: str, actor: dict) -> bool:
        current = next((layer.visible for layer in self.layers if layer.id == layer_id), None)
        if current is None:
            return False
        self._act(actor, self._set_layers(layer_id, visible=not current))
        return True

    def _toggle_item(self, layer_id: str, item_id: str, actor: dict) -> bool:
        said = self._set_layers(layer_id, item_id=item_id)
        if not said:
            return False
        self._act(actor, said)
        return True

    def _set_opacity(self, percent: int, actor: dict, drag: bool = False) -> bool:
        percent = max(0, min(100, percent))
        if percent == self.annotation_opacity:
            return False
        self.annotation_opacity = percent
        self._bump("opacity", actor.get("peer", ""), drag)
        self._act(actor, f"Annotation opacity {percent}%")
        return True

    def _toggle_labels(self, actor: dict) -> None:
        self.show_annotation_labels = not self.show_annotation_labels
        self._act(actor, "Measurement text " + ("on" if self.show_annotation_labels else "off"))
