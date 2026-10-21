"""One person's side of the viewer.

Everyone on /viewer/<id> sees the series the same way: that is the series' room
(:mod:`.room`), whose pictures are drawn once for all of them (:mod:`.series_work`).
``DicomViewerState`` is a tab's own part: which series it is on and who it is there, and what
each person keeps to themselves: the side panels, the language of the descriptions, the
tutorial and the metadata password. Its event handlers change the room for everyone, saying
who did it.
"""

import asyncio
import logging
import os
import random
import secrets

import reflex as rx

from dicom_viewer import folders, links, viewers, volume
from dicom_viewer.states import series_work as work
from dicom_viewer.states.room import ItemView, LayerView, SeriesRoom, room_token  # noqa: F401
from dicom_viewer.windowing import AUTO, DEFAULT_WINDOW, PRESET_GROUPS, PRESETS  # noqa: F401

try:
    from reflex_ddns_auth import AuthState
except ImportError:  # runs without sign-in: everyone is a guest
    AuthState = None

_SLICE_KEY_STEPS = {
    "ArrowUp": -1,
    "ArrowLeft": -1,
    "ArrowDown": 1,
    "ArrowRight": 1,
    "PageUp": -10,
    "PageDown": 10,
}


class DicomViewerState(rx.State):
    """A tab on the viewer: its series and who it is there, and what is theirs alone."""

    # The series on screen ("" on the folder page, or when the address names none), its
    # folder (the folder page marks it "Last opened"), and whether the address was a series.
    series: str = ""
    series_path: str = ""
    gone: bool = False
    # Who this tab is: {"peer", "user", "name", "email", "avatar", "color"}.
    me: dict[str, str] = {}
    # The room's actor_rev when this tab came: what was done before isn't news to it.
    actor_seen: int = 0
    _peer: str = ""
    _guest: str = ""
    tooltip_language: str = "zh-TW"
    tutorial_dialog_open: bool = False
    tutorial_dialog_title: str = ""
    tutorial_dialog_url: str = ""
    # Side panels: from the lg breakpoint up they sit beside the image and
    # fold into a rail; below it they are drawers over the image, all closed
    # until `drawer` names one ("metadata" or "controls").
    show_metadata: bool = True
    show_controls: bool = True
    drawer: str = ""
    metadata_unlocked: bool = False
    metadata_password_input: str = ""
    metadata_password_error: str = ""
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

    _metadata_password: str = os.getenv("DICOM_METADATA_PASSWORD", "dicom")

    # ------------------------------------------------------------ who and where

    async def _identify(self) -> None:
        """Who this tab is: the signed-in account (reflex_ddns_auth), else a guest."""
        email = name = avatar = ""
        if AuthState is not None:
            try:
                auth = await self.get_state(AuthState)
                if not auth._auth_loaded:
                    await auth.load_auth()
                if auth.is_logged_in:
                    email, name, avatar = auth.user_email, auth.user_name or auth.user_email, auth.user_avatar
            except Exception:
                pass
        if not self._peer:
            self._peer = secrets.token_hex(6)
        if email:
            user = email
        else:
            if not self._guest:
                self._guest = str(random.randint(1000, 9999))
            user, name = f"guest-{self._guest}", f"Guest {self._guest}"
        me = {"peer": self._peer, "user": user, "name": name, "email": email, "avatar": avatar, "color": (self.me or {}).get("color", "")}
        if me != self.me:
            self.me = me

    def _join(self) -> None:
        """Puts this tab among the people on its series."""
        me = self.me
        v = viewers.join(self.series, self._peer, self.router.session.client_token, me["user"], me["name"], me.get("avatar", ""))
        if me.get("color") != v.color:
            self.me = {**me, "color": v.color}

    def _actor(self) -> dict[str, str]:
        me = self.me or {}
        return {"peer": me.get("peer", ""), "name": me.get("name", ""), "color": me.get("color", "")}

    async def _room(self) -> SeriesRoom | None:
        """The room of the series on screen; None without one (or before it is linked)."""
        if not self.series:
            return None
        room = await self.get_state(SeriesRoom)
        return room if room._linked_to == room_token(self.series) else None

    @rx.event
    async def on_load(self):
        """Opens the series in the URL and links this tab to its room."""
        path = str(self.router.url.path or "")
        series_id = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
        if not links.valid_id(series_id):
            return rx.redirect("/")
        await self._identify()
        was_on = viewers.on(self._peer)
        folder = await asyncio.to_thread(links.find_folder, series_id, folders.dicom_root())
        room = await self.get_state(SeriesRoom)
        if folder is None:
            if was_on:
                viewers.leave(self._peer)
                work.push_later(was_on)
            self.series, self.gone = "", True
            try:
                return await room._unlink()
            except Exception:  # not linked
                return None
        self.series, self.series_path, self.gone = series_id, folder, False
        # A tab back on its series (e.g. reconnecting after a restart) is found there again.
        self._join()
        if was_on and was_on != series_id:
            work.push_later(was_on)
        room = await room._link_to(room_token(series_id))
        self.actor_seen = room.actor_rev
        work.resume(room, series_id, folder)
        work.publish(room, series_id)

    @rx.event
    async def leave_series(self):
        """Leaves the series on screen (back to the folders): the others stop seeing this
        tab, and it stops hearing about the series."""
        left = viewers.leave(self._peer) if self._peer else ""
        self.series = ""
        room = await self.get_state(SeriesRoom)
        if left:
            if room._linked_to == room_token(left):
                work.publish(room, left)
            else:
                work.push_later(left)
        try:
            return await room._unlink()
        except Exception:  # not linked (e.g. after a server restart)
            return None

    @rx.event
    async def ping(self):
        """Still here: keeps this tab among the people on the series, and drops those gone."""
        room = await self._room()
        if room is None:
            return
        if not viewers.touch(self.series, self._peer):
            # Back after a restart, or after being dropped as idle.
            self._join()
        viewers.prune(self.series)
        if self.series not in work.SERIES and room.status != "error":
            work.resume(room, self.series, self.series_path)
        work.publish(room, self.series)

    # ------------------------------------------------------------ 1 view

    async def _slice_to(self, index_of, drag: bool = False):
        room = await self._room()
        if room is not None and room._go_to_slice(index_of(room), self._actor(), drag):
            work.redraw(self.series, "single")

    @rx.event
    async def next_image(self):
        await self._slice_to(lambda room: room.current_index + 1)

    @rx.event
    async def prev_image(self):
        await self._slice_to(lambda room: room.current_index - 1)

    @rx.event
    async def set_slice_index(self, value: str):
        try:
            index = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting slice index: {e}")
            return
        await self._slice_to(lambda room: index, drag=True)

    @rx.event
    async def scroll_slices(self, delta_y: float):
        """Mouse wheel over the image: down is the next slice, up the previous."""
        if delta_y:
            await self._slice_to(lambda room: room.current_index + (1 if delta_y > 0 else -1))

    @rx.event
    async def handle_slice_key(self, key: str):
        """Arrow and Page keys step through the slices; Home/End jump to the ends."""
        if key == "Escape":
            self.drawer = ""
            return
        room = await self._room()
        if room is None:
            return
        if room.view_mode == "mpr":
            plane = room.active_plane
            if key == "Home":
                self._plane_to(room, plane, 0)
            elif key == "End":
                self._plane_to(room, plane, room.plane_count.get(plane, 0) - 1)
            elif key in _SLICE_KEY_STEPS:
                self._plane_to(room, plane, room.plane_index.get(plane, 0) + _SLICE_KEY_STEPS[key])
            return
        if key == "Home":
            target = 0
        elif key == "End":
            target = room.total_images - 1
        elif key in _SLICE_KEY_STEPS:
            target = room.current_index + _SLICE_KEY_STEPS[key]
        else:
            return
        if room._go_to_slice(target, self._actor()):
            work.redraw(self.series, "single")

    # ------------------------------------------------------------ windowing

    async def _window_to(self, window_of, preset: str, drag: bool = False):
        room = await self._room()
        if room is not None and room._set_window(*window_of(room), preset, self._actor(), drag):
            work.redraw(self.series, room.view_mode)

    @rx.event
    async def update_window_width(self, value: str):
        try:
            width = float(value)
        except ValueError as e:
            logging.exception(f"Error updating window width: {e}")
            return
        await self._window_to(lambda room: (room.window_center, width), "", drag=True)

    @rx.event
    async def update_window_center(self, value: str):
        try:
            center = float(value)
        except ValueError as e:
            logging.exception(f"Error updating window center: {e}")
            return
        await self._window_to(lambda room: (center, room.window_width), "", drag=True)

    @rx.event
    async def apply_preset(self, preset_name: str):
        """Apply a predefined windowing preset."""
        if preset_name in PRESETS:
            await self._window_to(lambda room: PRESETS[preset_name], preset_name)

    @rx.event
    async def apply_auto_window(self):
        """Back to the window the series opened with."""
        await self._window_to(lambda room: room.auto_window, AUTO)

    @rx.event
    async def reset_view(self):
        """Reset zoom, pan and the window to how the series opened."""
        room = await self._room()
        if room is None:
            return
        actor = self._actor()
        zoomed = room._reset_zoom(actor)
        windowed = room._set_window(*room.auto_window, AUTO, actor)
        if windowed:
            work.redraw(self.series, room.view_mode)
        if zoomed or windowed:
            room._act(actor, "Reset the view")

    # ------------------------------------------------------------ zoom and pan

    @rx.event
    async def set_zoom(self, value: float):
        """Set the zoom level within limits."""
        room = await self._room()
        if room is not None:
            room._set_zoom(value, self._actor())

    @rx.event
    async def zoom_in(self):
        room = await self._room()
        if room is not None:
            room._set_zoom(room.zoom_level + 0.25, self._actor())

    @rx.event
    async def zoom_out(self):
        room = await self._room()
        if room is not None:
            room._set_zoom(room.zoom_level - 0.25, self._actor())

    @rx.event
    async def reset_zoom(self):
        """Reset zoom and pan settings."""
        room = await self._room()
        if room is not None:
            room._reset_zoom(self._actor())

    @rx.event
    async def pan_control(self, dx: int, dy: int):
        room = await self._room()
        if room is not None:
            room._pan(dx, dy, self._actor())

    # ------------------------------------------------------------ 3 views

    @rx.event
    async def set_view_mode(self, mode: str):
        room = await self._room()
        if room is None or not room._set_view_mode(mode, self._actor()):
            return
        if mode == "mpr":
            work.load_volume(self.series)
            return
        # Back in one view, show the scanned image the crosshair is on.
        s = work.SERIES.get(self.series)
        vol = volume.cached(s.volume_key) if s is not None else None
        if vol is not None and room.mpr_status == "ready":
            path = vol.path_at(room.plane_index)
            if path in s.files and s.files.index(path) != room.current_index:
                room.current_index = s.files.index(path)
                room._bump("slice")
        work.redraw(self.series, "single")

    def _plane_to(self, room: SeriesRoom, plane: str, index: int, drag: bool = False) -> None:
        if room._move_plane(plane, index, self._actor(), drag):
            work.update_crosshair(room, self.series)
            work.redraw(self.series, "mpr")

    @rx.event
    async def scroll_plane(self, delta_y: float, plane: str):
        """Mouse wheel over a plane: down is its next slice, up the previous."""
        room = await self._room()
        if room is not None and delta_y and plane in room.plane_index:
            self._plane_to(room, plane, room.plane_index[plane] + (1 if delta_y > 0 else -1))

    @rx.event
    async def show_plane(self, plane: str):
        """Narrow screens show one plane at a time: this picks it."""
        room = await self._room()
        if room is not None:
            room._show_plane(plane, self._actor())

    @rx.event
    async def set_plane_index(self, plane: str, value: str):
        try:
            index = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting the {plane} slice: {e}")
            return
        room = await self._room()
        if room is not None:
            self._plane_to(room, plane, index, drag=True)

    @rx.event
    async def click_plane(self, fx: float, fy: float, plane: str):
        """A click in a plane moves the crosshair there, in the other two planes."""
        room = await self._room()
        if room is not None and room._click_plane(fx, fy, plane, self._actor()):
            work.update_crosshair(room, self.series)
            work.redraw(self.series, "mpr")

    @rx.event
    async def toggle_crosshair(self):
        room = await self._room()
        if room is not None:
            room._toggle_crosshair(self._actor())

    # ------------------------------------------------------------ annotations

    @rx.event
    async def toggle_layer(self, layer_id: str):
        room = await self._room()
        if room is not None and room._toggle_layer(layer_id, self._actor()):
            work.redraw(self.series, room.view_mode)

    @rx.event
    async def toggle_item(self, layer_id: str, item_id: str):
        room = await self._room()
        if room is not None and room._toggle_item(layer_id, item_id, self._actor()):
            work.redraw(self.series, room.view_mode)

    @rx.event
    async def set_annotation_opacity(self, value: str):
        try:
            percent = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting the annotation opacity: {e}")
            return
        room = await self._room()
        if room is not None and room._set_opacity(percent, self._actor(), drag=True):
            work.redraw(self.series, room.view_mode)

    @rx.event
    async def toggle_annotation_labels(self):
        room = await self._room()
        if room is not None:
            room._toggle_labels(self._actor())
            work.redraw(self.series, room.view_mode)

    @rx.event
    async def rescan_layers(self):
        """Look again for annotation files beside this series, e.g. after downloading one."""
        room = await self._room()
        if room is None or room.layers_status == "loading":
            return
        room._act(self._actor(), "Looked for annotations again")
        work.rescan(self.series)

    # ------------------------------------------------------------ this person's own

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

    @rx.event
    def set_tooltip_language(self, value: str):
        if value in self._preset_tutorial_links:
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
    async def open_tutorial_dialog(self):
        """The tutorial of the preset in use; Auto and Manual open its start."""
        room = await self._room()
        selected = room.selected_preset if room is not None else AUTO
        preset = selected if selected in PRESETS else ""
        self.tutorial_dialog_title = preset or "Windowing"
        self.tutorial_dialog_url = self._get_tutorial_link(preset)
        self.tutorial_dialog_open = True

    @rx.event
    def close_tutorial_dialog(self):
        self.tutorial_dialog_open = False
        self.tutorial_dialog_title = ""
        self.tutorial_dialog_url = ""
