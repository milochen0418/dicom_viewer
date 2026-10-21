"""The series cut in three planes (axial, coronal, sagittal) around one point."""

import reflex as rx

from dicom_viewer.components.together import actor_badge, slider_key
from dicom_viewer.states.dicom_state import DicomViewerState
from dicom_viewer.states.room import SeriesRoom
from dicom_viewer.volume import SCREEN_AXES

# Name, colour, what it shows, and the patient side at each edge
# (top, bottom, left, right) of its picture.
_PLANES = {
    "axial": (
        "Axial",
        "#f87171",
        "Slices across the body, seen from the feet",
        ("A", "P", "R", "L"),
    ),
    "coronal": (
        "Coronal",
        "#4ade80",
        "Slices from front to back, seen from the front",
        ("S", "I", "R", "L"),
    ),
    "sagittal": (
        "Sagittal",
        "#facc15",
        "Slices from side to side, seen from the patient's left",
        ("S", "I", "A", "P"),
    ),
}


def _plane_wheel_spec(e: rx.Var) -> tuple[rx.Var[float], rx.Var[str]]:
    # Ctrl + wheel is the browser's (pinch) zoom, not slice scrolling.
    return (
        rx.Var(f"({e}.ctrlKey ? 0 : {e}.deltaY)").to(float),
        rx.Var(f"{e}.currentTarget.dataset.plane").to(str),
    )


def _plane_click_spec(
    e: rx.Var,
) -> tuple[rx.Var[float], rx.Var[float], rx.Var[str]]:
    # Where the click fell, as fractions of the picture's width and height.
    box = f"{e}.currentTarget.getBoundingClientRect()"
    return (
        rx.Var(f"(({e}.clientX - {box}.left) / {box}.width)").to(float),
        rx.Var(f"(({e}.clientY - {box}.top) / {box}.height)").to(float),
        rx.Var(f"{e}.currentTarget.dataset.plane").to(str),
    )


class PlaneArea(rx.el.Div):
    """A div for one plane; its events say which plane they came from."""

    on_wheel: rx.EventHandler[_plane_wheel_spec]
    on_click: rx.EventHandler[_plane_click_spec]


def _line(plane: str, vertical: bool) -> rx.Component:
    """Where another plane crosses this picture."""
    place = f"{SeriesRoom.crosshair[plane]}%"
    return rx.el.div(
        class_name=(
            "absolute top-0 bottom-0 w-px pointer-events-none"
            if vertical
            else "absolute left-0 right-0 h-px pointer-events-none"
        ),
        style={
            "left" if vertical else "top": place,
            "backgroundColor": _PLANES[plane][1],
            "opacity": "0.9",
        },
    )


def _edge_label(text: str, position: str) -> rx.Component:
    return rx.el.span(
        text,
        class_name=(
            f"absolute {position} text-[11px] font-semibold text-slate-300/80 "
            "pointer-events-none select-none"
        ),
    )


def plane_view(plane: str) -> rx.Component:
    title, color, description, (top, bottom, left, right) = _PLANES[plane]
    horizontal, vertical = SCREEN_AXES[plane]
    ratio = SeriesRoom.plane_ratio[plane]
    return rx.el.div(
        rx.el.div(
            rx.el.span(
                class_name="h-2.5 w-2.5 rounded-full shrink-0",
                style={"backgroundColor": color},
            ),
            rx.el.span(
                title,
                title=description,
                class_name="text-xs font-semibold text-slate-200 w-14 shrink-0",
            ),
            rx.el.input(
                type="range",
                min="0",
                max=SeriesRoom.plane_count[plane] - 1,
                key=slider_key("plane"),
                default_value=SeriesRoom.plane_index[plane],
                on_change=lambda value: DicomViewerState.set_plane_index(plane, value),
                title=f"{title} slice",
                class_name="flex-1 min-w-0 h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500",
            ),
            rx.el.span(
                SeriesRoom.plane_position_text[plane],
                class_name="font-mono text-xs text-slate-400 w-20 text-right shrink-0",
            ),
            class_name="flex items-center gap-2 px-3 py-1.5 bg-slate-900 shrink-0",
        ),
        PlaneArea.create(
            PlaneArea.create(
                rx.image(
                    src=getattr(SeriesRoom, f"{plane}_image"),
                    alt=f"{title} view",
                    class_name="w-full h-full pointer-events-none select-none",
                ),
                rx.cond(
                    SeriesRoom.show_crosshair,
                    rx.fragment(_line(horizontal, True), _line(vertical, False)),
                ),
                custom_attrs={"data-plane": plane},
                on_click=DicomViewerState.click_plane,
                # The largest box of the picture's real-world shape that fits.
                style={
                    "width": f"min(100cqw, calc(100cqh * {ratio}))",
                    "aspectRatio": ratio,
                    "transform": f"translate({SeriesRoom.pan_x}px, {SeriesRoom.pan_y}px) scale({SeriesRoom.zoom_level})",
                    "transition": "transform 0.1s ease-out",
                },
                class_name="relative shrink-0 cursor-crosshair",
            ),
            _edge_label(top, "top-1 left-1/2 -translate-x-1/2"),
            _edge_label(bottom, "bottom-1 left-1/2 -translate-x-1/2"),
            _edge_label(left, "left-1.5 top-1/2 -translate-y-1/2"),
            _edge_label(right, "right-1.5 top-1/2 -translate-y-1/2"),
            actor_badge(plane),
            custom_attrs={"data-plane": plane},
            on_wheel=DicomViewerState.scroll_plane.throttle(60),
            style={"containerType": "size"},
            class_name="flex-1 min-h-0 relative flex items-center justify-center overflow-hidden bg-black select-none",
        ),
        # Outline drawn above the picture marks the plane the keys move.
        rx.cond(
            SeriesRoom.active_plane == plane,
            rx.el.div(
                class_name="hidden md:block absolute inset-0 border border-blue-500/70 pointer-events-none"
            ),
        ),
        # Below md only the plane picked in plane_tabs() is shown.
        class_name=rx.cond(
            SeriesRoom.active_plane == plane,
            "relative flex flex-col min-h-0 min-w-0 bg-black",
            "relative hidden md:flex flex-col min-h-0 min-w-0 bg-black",
        ),
    )


def plane_tabs() -> rx.Component:
    """Too narrow for all three views: pick the one to show."""

    def tab(plane: str) -> rx.Component:
        title, color, description, _ = _PLANES[plane]
        return rx.el.button(
            rx.el.span(
                class_name="h-2 w-2 rounded-full shrink-0",
                style={"backgroundColor": color},
            ),
            title,
            on_click=DicomViewerState.show_plane(plane),
            title=description,
            class_name=rx.cond(
                SeriesRoom.active_plane == plane,
                "flex-1 flex items-center justify-center gap-2 py-1.5 rounded-md bg-slate-700 text-slate-100 text-sm font-medium",
                "flex-1 flex items-center justify-center gap-2 py-1.5 rounded-md text-slate-400 hover:text-slate-100 text-sm font-medium",
            ),
        )

    return rx.el.div(
        *(tab(plane) for plane in _PLANES),
        class_name="md:hidden flex gap-1 p-1 bg-slate-900 border-b border-slate-800 shrink-0",
    )


def _info_row(label: str, value: rx.Var) -> rx.Component:
    return rx.el.div(
        rx.el.span(label, class_name="text-xs text-slate-400"),
        rx.el.span(value, class_name="text-sm text-slate-100 font-mono"),
        class_name="flex flex-col gap-0.5",
    )


def crosshair_panel() -> rx.Component:
    # Below md it is a short strip under the one visible view.
    return rx.el.div(
        rx.el.h3(
            "Crosshair",
            class_name="hidden md:block text-xs font-semibold text-slate-500 uppercase tracking-wider",
        ),
        rx.el.div(
            _info_row("Position", SeriesRoom.crosshair_position),
            _info_row("Value", SeriesRoom.crosshair_value),
            class_name="flex flex-wrap gap-x-6 gap-y-2 md:flex-col md:gap-4",
        ),
        rx.el.label(
            rx.el.input(
                type="checkbox",
                checked=SeriesRoom.show_crosshair,
                on_change=DicomViewerState.toggle_crosshair,
                class_name="accent-blue-500",
            ),
            "Show crosshair lines",
            class_name="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none",
        ),
        rx.foreach(
            SeriesRoom.mpr_notes,
            lambda note: rx.el.p(
                rx.icon("info", class_name="h-3.5 w-3.5 mr-1.5 mt-0.5 shrink-0"),
                note,
                class_name="flex text-xs text-amber-300/90 bg-amber-900/20 border border-amber-900/40 rounded-lg p-2",
            ),
        ),
        rx.el.ul(
            rx.el.li("Click a view to move the crosshair there."),
            rx.el.li("Scroll over a view, or use ↑ ↓, to move through its slices."),
            rx.el.li("The blue frame marks the view the keys move."),
            class_name="hidden md:block text-xs text-slate-500 space-y-1 list-disc pl-4 mt-auto",
        ),
        class_name="flex flex-col gap-3 md:gap-4 p-3 md:p-4 bg-slate-900 overflow-y-auto custom-scrollbar min-h-0",
    )


def _loading() -> rx.Component:
    return rx.el.div(
        rx.icon("loader-circle", class_name="h-8 w-8 text-blue-400 animate-spin mb-4"),
        rx.el.p("Building the 3 views…", class_name="text-slate-200 font-medium"),
        rx.el.div(
            rx.el.div(
                class_name="h-full bg-blue-500 transition-all",
                style={"width": f"{SeriesRoom.mpr_percent}%"},
            ),
            class_name="w-64 h-1.5 bg-slate-800 rounded-full overflow-hidden mt-4",
        ),
        rx.el.p(
            rx.cond(
                SeriesRoom.mpr_total > 0,
                f"Reading image {SeriesRoom.mpr_done} of {SeriesRoom.mpr_total}",
                "Reading the image positions",
            ),
            class_name="text-xs text-slate-500 mt-2 font-mono",
        ),
        class_name="flex-1 flex flex-col items-center justify-center",
    )


def _error() -> rx.Component:
    return rx.el.div(
        rx.icon("triangle-alert", class_name="h-8 w-8 text-amber-400 mb-4"),
        rx.el.p("3 views are not available", class_name="text-slate-200 font-medium"),
        rx.el.p(
            SeriesRoom.mpr_error,
            class_name="text-sm text-slate-400 mt-2 max-w-md text-center",
        ),
        rx.el.button(
            rx.icon("square", class_name="h-4 w-4 mr-2"),
            "Back to 1 view",
            on_click=DicomViewerState.set_view_mode("single"),
            class_name="mt-6 flex items-center px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium",
        ),
        class_name="flex-1 flex flex-col items-center justify-center p-6",
    )


def three_views() -> rx.Component:
    return rx.match(
        SeriesRoom.mpr_status,
        (
            "ready",
            rx.el.div(
                plane_tabs(),
                rx.el.div(
                    plane_view("axial"),
                    plane_view("coronal"),
                    plane_view("sagittal"),
                    crosshair_panel(),
                    class_name="flex-1 min-h-0 grid grid-cols-1 grid-rows-[minmax(0,1fr)_auto] md:grid-cols-2 md:grid-rows-2 gap-px bg-slate-800",
                ),
                class_name="flex-1 min-h-0 flex flex-col",
            ),
        ),
        ("error", _error()),
        _loading(),
    )
