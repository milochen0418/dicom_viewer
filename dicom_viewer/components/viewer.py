import reflex as rx
from reflex_components_core.core.window_events import WindowEventListener

from dicom_viewer.components.call_views import call_button, incoming_call
from dicom_viewer.components.three_views import three_views
from dicom_viewer.components.together import (
    actor_badge,
    page_hooks,
    presence,
    share_button,
    slider_key,
)
from dicom_viewer.states.dicom_state import AUTO, PRESET_GROUPS, DicomViewerState
from dicom_viewer.states.room import SeriesRoom


def _wheel_spec(e: rx.Var) -> tuple[rx.Var[float]]:
    # Ctrl + wheel is the browser's (pinch) zoom, not slice scrolling.
    return (rx.Var(f"({e}.ctrlKey ? 0 : {e}.deltaY)").to(float),)


def _slice_key_spec(e: rx.Var) -> tuple[rx.Var[str]]:
    # Keys typed into the password box or used on a slider, and shortcuts with
    # modifiers, are not slice navigation.
    return (
        rx.Var(
            f"(({e}.metaKey || {e}.ctrlKey || {e}.altKey || "
            f"['INPUT', 'TEXTAREA', 'SELECT'].includes({e}.target?.tagName)) "
            f"? '' : {e}.key)"
        ).to(str),
    )


class WheelArea(rx.el.Div):
    """A div that reports mouse-wheel movement."""

    on_wheel: rx.EventHandler[_wheel_spec]


class SliceKeys(WindowEventListener):
    """Page-wide key listener that leaves form fields alone."""

    on_key_down: rx.EventHandler[_slice_key_spec]


def side_panel(
    side: str,
    title: str,
    docked_open: rx.Var[bool],
    on_toggle: rx.EventHandler,
    width: str,
    *body: rx.Component,
) -> rx.Component:
    """A column beside the image (lg and up) or a drawer over it (narrower).

    Folded on a wide screen it leaves a rail where it was, so the way back
    is in the same place as the way out.
    """
    edge = "left" if side == "metadata" else "right"
    icon_button = "p-1.5 rounded-md border border-slate-700 text-slate-300 hover:text-white hover:bg-slate-800 hover:border-slate-600 transition-colors"
    buttons = rx.fragment(
        rx.el.button(
            rx.icon("x", class_name="h-4 w-4"),
            on_click=DicomViewerState.set_drawer(""),
            title="Close",
            class_name=f"lg:hidden {icon_button}",
        ),
        rx.el.button(
            rx.icon(f"panel-{edge}-close", class_name="h-4 w-4"),
            on_click=on_toggle,
            title=f"Hide {title.lower()}",
            class_name=f"hidden lg:inline-flex {icon_button}",
        ),
    )
    heading = rx.el.h2(title, class_name="text-sm font-semibold text-slate-100")
    return rx.el.aside(
        rx.el.button(
            rx.icon(f"panel-{edge}-open", class_name="h-4 w-4 shrink-0"),
            rx.el.span(title, style={"writingMode": "vertical-rl"}),
            on_click=on_toggle,
            title=f"Show {title.lower()}",
            class_name=rx.cond(
                docked_open,
                "hidden",
                "hidden lg:flex flex-1 flex-col items-center gap-3 py-4 text-xs font-semibold uppercase tracking-wider text-slate-400 hover:text-blue-300 hover:bg-slate-800/60 transition-colors",
            ),
        ),
        rx.el.div(
            # The buttons sit on the side facing the image.
            rx.el.div(
                *((heading, buttons) if edge == "left" else (buttons, heading)),
                class_name="flex items-center gap-2 px-4 py-3 border-b border-slate-800 shrink-0"
                + (" justify-between" if edge == "left" else ""),
            ),
            rx.el.div(*body, class_name="flex-1 min-h-0 overflow-y-auto custom-scrollbar"),
            class_name=rx.cond(
                docked_open,
                "flex flex-col flex-1 min-h-0",
                "flex flex-col flex-1 min-h-0 lg:hidden",
            ),
        ),
        class_name=[
            f"fixed inset-y-0 {edge}-0 z-50 w-[min(20rem,88vw)] flex flex-col shrink-0 "
            f"bg-slate-900 border-slate-800 {'border-r' if edge == 'left' else 'border-l'} "
            "shadow-2xl transition-[transform,visibility] duration-200 "
            "lg:static lg:z-auto lg:shadow-none lg:transform-none lg:visible lg:transition-none",
            rx.cond(
                DicomViewerState.drawer == side,
                "",
                f"{'-translate-x-full' if edge == 'left' else 'translate-x-full'} invisible",
            ),
            rx.cond(docked_open, width, "lg:w-11"),
        ],
    )


def drawer_backdrop() -> rx.Component:
    return rx.cond(
        DicomViewerState.drawer != "",
        rx.el.div(
            on_click=DicomViewerState.set_drawer(""),
            class_name="fixed inset-0 z-40 bg-black/60 lg:hidden",
        ),
    )


def control_section_header(title: str) -> rx.Component:
    return rx.el.h3(
        title,
        class_name="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4",
    )


def tutorial_dialog() -> rx.Component:
    return rx.el.dialog(
        rx.el.div(
            rx.el.div(
                rx.el.h3(
                    DicomViewerState.tutorial_dialog_title,
                    class_name="text-base font-semibold text-slate-100",
                ),
                rx.el.button(
                    rx.icon("x", class_name="h-4 w-4"),
                    on_click=DicomViewerState.close_tutorial_dialog,
                    class_name="p-1 rounded hover:bg-slate-800 text-slate-300",
                ),
                class_name="flex items-center justify-between mb-3",
            ),
            rx.cond(
                DicomViewerState.tutorial_dialog_url != "",
                rx.el.div(
                    rx.el.iframe(
                        src=DicomViewerState.tutorial_dialog_url,
                        class_name="w-full h-96 rounded-lg border border-slate-800",
                    ),
                    rx.el.a(
                        "Open in new tab",
                        href=DicomViewerState.tutorial_dialog_url,
                        target="_blank",
                        rel="noreferrer",
                        class_name="text-xs text-blue-400 hover:text-blue-300 mt-2 inline-block",
                    ),
                    class_name="space-y-2",
                ),
                rx.el.p(
                    "No tutorial link configured for this preset.",
                    class_name="text-sm text-slate-400",
                ),
            ),
            class_name="bg-slate-950 rounded-xl border border-slate-800 p-4 w-[560px] max-w-[90vw]",
        ),
        open=DicomViewerState.tutorial_dialog_open,
        # Fixed in the middle of the screen, so it is not caught inside the
        # Controls panel when that panel is a drawer.
        class_name="fixed inset-0 m-auto z-[60] h-fit max-h-[90vh] overflow-auto p-0 bg-transparent border-0 shadow-2xl",
    )


def zoom_pan_section() -> rx.Component:
    """Zoom and Reset All beside the pan pad, so the section is only as tall as the pad."""
    square = "h-8 w-8 flex items-center justify-center bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors disabled:opacity-50"

    def pan(icon: str, dx: int, dy: int, label: str, place: str) -> rx.Component:
        return rx.el.button(
            rx.icon(icon, class_name="h-4 w-4"),
            on_click=DicomViewerState.pan_control(dx, dy),
            title=label,
            aria_label=label,
            class_name=f"{square} {place}",
        )

    return rx.el.div(
        control_section_header("Zoom & Pan"),
        rx.el.div(
            rx.el.div(
                rx.el.div(
                    rx.el.button(
                        rx.icon("minus", class_name="h-4 w-4"),
                        on_click=DicomViewerState.zoom_out,
                        disabled=SeriesRoom.zoom_level <= 0.25,
                        title="Zoom out",
                        aria_label="Zoom out",
                        class_name=f"{square} rounded-md shrink-0",
                    ),
                    rx.el.span(
                        f"{(SeriesRoom.zoom_level * 100).to_string()}%",
                        class_name="flex-1 min-w-0 h-8 flex items-center justify-center text-sm font-mono text-blue-400 bg-slate-900/50 border border-slate-800 rounded-md",
                    ),
                    rx.el.button(
                        rx.icon("plus", class_name="h-4 w-4"),
                        on_click=DicomViewerState.zoom_in,
                        disabled=SeriesRoom.zoom_level >= 4.0,
                        title="Zoom in",
                        aria_label="Zoom in",
                        class_name=f"{square} rounded-md shrink-0",
                    ),
                    class_name="flex items-center gap-1",
                ),
                rx.el.button(
                    "Reset All",
                    on_click=DicomViewerState.reset_view,
                    title="Reset zoom, pan and the window",
                    class_name="h-8 w-full px-2 bg-slate-800 hover:bg-red-900/30 text-slate-400 hover:text-red-400 text-xs font-medium uppercase tracking-wider rounded-lg transition-colors border border-slate-800 hover:border-red-900/50",
                ),
                # Zoom lines up with the pad's top row, Reset All with its bottom row.
                class_name="flex-1 min-w-0 flex flex-col justify-between",
            ),
            rx.el.div(
                pan("arrow-up", 0, 50, "Pan up", "col-start-2 rounded-t-lg"),
                pan("arrow-left", 50, 0, "Pan left", "col-start-1 rounded-l-lg"),
                rx.el.button(
                    rx.icon("maximize", class_name="h-4 w-4"),
                    on_click=DicomViewerState.reset_zoom,
                    title="Reset View",
                    aria_label="Reset View",
                    class_name="h-8 w-8 flex items-center justify-center bg-slate-700 hover:bg-slate-600 text-blue-400 transition-colors",
                ),
                pan("arrow-right", -50, 0, "Pan right", "rounded-r-lg"),
                pan("arrow-down", 0, -50, "Pan down", "col-start-2 rounded-b-lg"),
                class_name="grid grid-cols-[repeat(3,2rem)] gap-1 shrink-0",
            ),
            class_name="flex gap-4",
        ),
        class_name="p-6",
    )


def windowing_section() -> rx.Component:
    """Auto, the presets and the Level / Width sliders, in little height.

    The presets sit in one grouped dropdown (the phone's own picker on a
    phone), and the line under it says what the window in use is for, in the
    language chosen beside the heading.
    """
    preset = SeriesRoom.selected_preset
    is_auto = preset == AUTO
    is_preset = (preset != "") & (preset != AUTO)
    box = "h-9 text-sm rounded-lg border transition-colors"
    chosen = "text-blue-200 bg-blue-950 border-blue-500/50"
    not_chosen = "text-slate-200 bg-slate-800 border-slate-700 hover:border-slate-600"
    picker = f"{box} w-full pl-3 pr-8 appearance-none cursor-pointer outline-none focus:ring-1 focus:ring-blue-500 truncate [&_option]:text-slate-200"
    return rx.el.div(
        rx.el.div(
            control_section_header("Windowing"),
            rx.el.label(
                rx.icon("languages", class_name="h-3.5 w-3.5 shrink-0"),
                rx.el.select(
                    rx.el.option("English", value="en"),
                    rx.el.option("繁中", value="zh-TW"),
                    rx.el.option("简中", value="zh-CN"),
                    rx.el.option("Español", value="es"),
                    value=DicomViewerState.tooltip_language,
                    on_change=DicomViewerState.set_tooltip_language,
                    aria_label="Language",
                    style={"colorScheme": "dark"},
                    class_name="bg-transparent text-xs text-slate-300 outline-none cursor-pointer appearance-none [&_option]:bg-slate-900",
                ),
                title="Language of the descriptions and tutorials",
                class_name="flex items-center gap-1.5 mb-4 px-2 py-1 rounded-md border border-slate-700 text-slate-400 hover:border-slate-600 focus-within:ring-1 focus-within:ring-blue-500 cursor-pointer",
            ),
            class_name="flex items-center justify-between",
        ),
        rx.el.div(
            rx.el.button(
                rx.icon("sparkles", class_name="h-4 w-4 mr-1.5"),
                "Auto",
                on_click=DicomViewerState.apply_auto_window,
                title="The window the series opened with: the one its files suggest, or one worked out from the image",
                aria_pressed=is_auto,
                class_name=rx.cond(
                    is_auto,
                    f"{box} flex items-center px-3 font-medium shrink-0 {chosen}",
                    f"{box} flex items-center px-3 font-medium shrink-0 {not_chosen}",
                ),
            ),
            rx.el.div(
                rx.el.select(
                    rx.el.option("Presets…", value="", disabled=True, hidden=True),
                    *[
                        rx.el.optgroup(
                            *[rx.el.option(name, value=name) for name in names],
                            label=group,
                        )
                        for group, names in PRESET_GROUPS.items()
                    ],
                    value=rx.cond(is_preset, preset, ""),
                    on_change=DicomViewerState.apply_preset,
                    title="Presets for common tissues",
                    aria_label="Preset",
                    style={"colorScheme": "dark"},
                    class_name=rx.cond(
                        is_preset,
                        f"{picker} {chosen}",
                        f"{picker} {not_chosen} text-slate-400",
                    ),
                ),
                rx.icon(
                    "chevron-down",
                    class_name="absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none",
                ),
                class_name="relative flex-1 min-w-0",
            ),
            class_name="flex gap-2",
        ),
        rx.el.div(
            rx.el.p(
                SeriesRoom.window_descriptions[DicomViewerState.tooltip_language],
                class_name="text-xs text-slate-400 leading-relaxed",
            ),
            rx.el.button(
                rx.icon("circle-help", class_name="h-4 w-4"),
                on_click=DicomViewerState.open_tutorial_dialog,
                title="Tutorial",
                aria_label="Tutorial",
                class_name="p-1 -mr-1 -mt-0.5 rounded text-slate-400 hover:text-blue-300 hover:bg-slate-800 transition-colors shrink-0",
            ),
            class_name="flex items-start justify-between gap-2 mt-2 mb-4",
        ),
        rx.el.div(
            rx.el.div(
                rx.el.label(
                    "Level (Center)",
                    class_name="text-xs font-medium text-slate-300",
                ),
                rx.el.span(
                    SeriesRoom.window_center.to_string(),
                    class_name="text-xs text-blue-400 font-mono",
                ),
                class_name="flex justify-between mb-2",
            ),
            rx.el.input(
                type="range",
                min="-1000",
                max="3000",
                key=slider_key("window"),
                default_value=SeriesRoom.window_center,
                on_change=DicomViewerState.update_window_center,
                class_name="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer hover:bg-slate-600 transition-colors accent-blue-500",
            ),
            class_name="mb-4",
        ),
        rx.el.div(
            rx.el.div(
                rx.el.label("Width", class_name="text-xs font-medium text-slate-300"),
                rx.el.span(
                    SeriesRoom.window_width.to_string(),
                    class_name="text-xs text-blue-400 font-mono",
                ),
                class_name="flex justify-between mb-2",
            ),
            rx.el.input(
                type="range",
                min="1",
                max="4000",
                key=slider_key("window"),
                default_value=SeriesRoom.window_width,
                on_change=DicomViewerState.update_window_width,
                class_name="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer hover:bg-slate-600 transition-colors accent-blue-500",
            ),
        ),
        class_name="p-6 border-t border-slate-800",
    )


def metadata_row(label: str, value: str, icon_name: str) -> rx.Component:
    """Row for a single metadata item."""
    return rx.el.div(
        rx.el.div(
            rx.icon(icon_name, class_name="h-3.5 w-3.5 mr-2 text-slate-500"),
            rx.el.span(label, class_name="text-xs text-slate-400 font-medium"),
            class_name="flex items-center mb-1",
        ),
        rx.el.p(value, class_name="text-sm text-slate-200 font-mono pl-5.5 break-all"),
        class_name="mb-3",
    )


def metadata_panel() -> rx.Component:
    """Left sidebar displaying DICOM metadata."""
    return side_panel(
        "metadata",
        "Metadata",
        DicomViewerState.show_metadata,
        DicomViewerState.toggle_metadata,
        "lg:w-72",
        rx.el.div(
            rx.el.div(
                control_section_header("Patient Info"),
                metadata_row(
                    "Name",
                    rx.cond(
                        DicomViewerState.metadata_unlocked,
                        SeriesRoom.patient_name,
                        "Hidden",
                    ),
                    "user",
                ),
                metadata_row(
                    "ID",
                    rx.cond(
                        DicomViewerState.metadata_unlocked,
                        SeriesRoom.patient_id,
                        "Hidden",
                    ),
                    "id-card",
                ),
                rx.cond(
                    DicomViewerState.metadata_unlocked,
                    rx.el.button(
                        "Lock",
                        on_click=DicomViewerState.lock_metadata,
                        class_name="text-xs text-slate-400 hover:text-white mt-2",
                    ),
                    rx.el.div(
                        rx.el.label(
                            "Unlock protected fields",
                            class_name="block text-xs font-medium text-slate-400 mb-2",
                        ),
                        rx.el.div(
                            rx.el.input(
                                type="password",
                                placeholder="Password",
                                value=DicomViewerState.metadata_password_input,
                                on_change=DicomViewerState.update_metadata_password,
                                class_name="flex-1 bg-slate-800 text-slate-200 text-sm rounded-lg border border-slate-700 px-3 py-2 outline-none focus:ring-1 focus:ring-blue-500",
                            ),
                            rx.el.button(
                                "Unlock",
                                on_click=DicomViewerState.unlock_metadata,
                                class_name="ml-2 px-3 py-2 text-xs rounded-lg bg-blue-600 hover:bg-blue-500 text-white",
                            ),
                            class_name="flex items-center",
                        ),
                        rx.cond(
                            DicomViewerState.metadata_password_error != "",
                            rx.el.p(
                                DicomViewerState.metadata_password_error,
                                class_name="text-xs text-red-400 mt-2",
                            ),
                            rx.el.span(),
                        ),
                        class_name="mt-2",
                    ),
                ),
                class_name="mb-6",
            ),
            rx.el.div(
                control_section_header("Study Details"),
                metadata_row(
                    "Date",
                    rx.cond(
                        DicomViewerState.metadata_unlocked,
                        SeriesRoom.study_date,
                        "Hidden",
                    ),
                    "calendar",
                ),
                metadata_row("Modality", SeriesRoom.modality, "scan"),
                metadata_row("Study", SeriesRoom.study_description, "file-text"),
                metadata_row("Series", SeriesRoom.series_description, "layers"),
                class_name="mb-6",
            ),
            rx.el.div(
                control_section_header("Image Data"),
                metadata_row(
                    "Resolution",
                    f"{SeriesRoom.columns} x {SeriesRoom.rows}",
                    "maximize",
                ),
                metadata_row("Spacing", SeriesRoom.pixel_spacing, "ruler"),
                metadata_row(
                    "Thickness", SeriesRoom.slice_thickness, "align-justify"
                ),
                metadata_row("Position", SeriesRoom.image_position, "crosshair"),
                class_name="mb-6",
            ),
            class_name="p-5",
        ),
    )


def annotation_item(layer: rx.Var, item: rx.Var) -> rx.Component:
    """One segment, ROI, graphic layer or finding, with its checkbox and colour."""
    return rx.el.label(
        rx.el.input(
            type="checkbox",
            checked=item.visible,
            on_change=lambda _value: DicomViewerState.toggle_item(layer.id, item.id),
            class_name="accent-blue-500 shrink-0",
        ),
        rx.el.span(
            class_name="h-2.5 w-2.5 rounded-full shrink-0 border border-black/40",
            style={"backgroundColor": item.color},
        ),
        rx.el.span(item.name, title=item.name, class_name="text-sm text-slate-200 truncate"),
        rx.cond(
            item.description != "",
            rx.el.span(
                item.description,
                class_name="text-[11px] text-slate-500 truncate ml-auto shrink-0 max-w-[40%]",
            ),
        ),
        class_name="flex items-center gap-2 pl-6 py-0.5 cursor-pointer select-none",
    )


def annotation_layer(layer: rx.Var) -> rx.Component:
    """One annotation object: its format badge, name, items and notes."""
    return rx.el.div(
        rx.el.label(
            rx.el.input(
                type="checkbox",
                checked=layer.visible,
                on_change=lambda _value: DicomViewerState.toggle_layer(layer.id),
                class_name="accent-blue-500 shrink-0",
            ),
            rx.el.span(
                layer.format,
                class_name="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-slate-800 text-blue-300 shrink-0",
            ),
            rx.el.span(layer.name, title=layer.name, class_name="text-sm text-slate-100 truncate"),
            rx.cond(
                (SeriesRoom.view_mode == "mpr") & ~layer.volume_ok,
                rx.el.span(
                    "1 view only",
                    title="This kind of annotation is drawn on the scanned images only.",
                    class_name="text-[10px] text-amber-300 ml-auto shrink-0",
                ),
            ),
            class_name="flex items-center gap-2 cursor-pointer select-none",
        ),
        rx.foreach(layer.rows, lambda item: annotation_item(layer, item)),
        rx.foreach(
            layer.notes,
            lambda note: rx.el.p(
                rx.icon("info", class_name="h-3.5 w-3.5 mr-1.5 mt-0.5 shrink-0"),
                note,
                class_name="flex text-xs text-amber-300/90 bg-amber-900/20 border border-amber-900/40 rounded-lg p-2 mt-1 ml-6",
            ),
        ),
        class_name="mb-3",
    )


def annotations_panel() -> rx.Component:
    """The annotation layers found beside the series: SEG, RTSTRUCT, SR, PR."""
    return rx.el.div(
        rx.el.div(
            control_section_header("Annotations"),
            rx.el.button(
                rx.icon("refresh-cw", class_name="h-3.5 w-3.5"),
                on_click=DicomViewerState.rescan_layers,
                disabled=SeriesRoom.layers_status == "loading",
                title="Look again for annotation files beside this series",
                class_name="p-1 mb-4 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800 disabled:opacity-40",
            ),
            class_name="flex items-center justify-between",
        ),
        rx.match(
            SeriesRoom.layers_status,
            (
                "loading",
                rx.el.p(
                    rx.icon("loader-circle", class_name="h-3.5 w-3.5 mr-1.5 animate-spin"),
                    "Looking for annotations…",
                    class_name="flex items-center text-xs text-slate-500",
                ),
            ),
            (
                "ready",
                rx.cond(
                    SeriesRoom.layers.length() > 0,
                    rx.fragment(
                        rx.el.div(
                            rx.el.div(
                                rx.el.label(
                                    "Opacity", class_name="text-xs font-medium text-slate-300"
                                ),
                                rx.el.span(
                                    f"{SeriesRoom.annotation_opacity}%",
                                    class_name="text-xs text-blue-400 font-mono",
                                ),
                                class_name="flex justify-between mb-2",
                            ),
                            rx.el.input(
                                type="range",
                                min="0",
                                max="100",
                                key=slider_key("opacity"),
                                default_value=SeriesRoom.annotation_opacity,
                                on_change=DicomViewerState.set_annotation_opacity,
                                class_name="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer hover:bg-slate-600 transition-colors accent-blue-500",
                            ),
                            class_name="mb-3",
                        ),
                        rx.el.label(
                            rx.el.input(
                                type="checkbox",
                                checked=SeriesRoom.show_annotation_labels,
                                on_change=DicomViewerState.toggle_annotation_labels,
                                class_name="accent-blue-500",
                            ),
                            "Show measurement text",
                            class_name="flex items-center gap-2 text-sm text-slate-300 cursor-pointer select-none mb-4",
                        ),
                        rx.foreach(SeriesRoom.layers, annotation_layer),
                    ),
                    rx.el.p(
                        "No annotations were found beside this series.",
                        class_name="text-xs text-slate-500",
                    ),
                ),
            ),
            rx.fragment(),
        ),
        rx.foreach(
            SeriesRoom.layer_errors,
            lambda error: rx.el.p(
                rx.icon("triangle-alert", class_name="h-3.5 w-3.5 mr-1.5 mt-0.5 shrink-0"),
                error,
                class_name="flex text-xs text-red-400 bg-red-900/20 border border-red-900/50 rounded-lg p-2 mt-2 break-all",
            ),
        ),
        class_name="p-6 border-t border-slate-800",
    )


def viewer_sidebar() -> rx.Component:
    """The controls sidebar."""
    return side_panel(
        "controls",
        "Controls",
        DicomViewerState.show_controls,
        DicomViewerState.toggle_controls,
        "lg:w-80",
        zoom_pan_section(),
        windowing_section(),
        annotations_panel(),
    )


def view_mode_switch() -> rx.Component:
    def option(mode: str, icon: str, label: str, hint: str) -> rx.Component:
        return rx.el.button(
            rx.icon(icon, class_name="h-4 w-4 sm:mr-1.5"),
            rx.el.span(label, class_name="hidden sm:inline"),
            on_click=DicomViewerState.set_view_mode(mode),
            title=hint,
            aria_label=label,
            class_name=rx.cond(
                SeriesRoom.view_mode == mode,
                "flex items-center px-2 sm:px-3 py-1 rounded-md bg-blue-600 text-white text-sm font-medium",
                "flex items-center px-2 sm:px-3 py-1 rounded-md text-slate-400 hover:text-slate-100 text-sm font-medium",
            ),
        )

    return rx.el.div(
        option("single", "square", "1 view", "One scanned image at a time"),
        option(
            "mpr",
            "layout-grid",
            "3 views",
            "The series cut three ways: axial, coronal and sagittal (MPR)",
        ),
        class_name="flex items-center gap-0.5 p-0.5 bg-slate-800 rounded-lg border border-slate-700",
    )


def drawer_button(side: str, icon: str, label: str) -> rx.Component:
    """Opens a side panel as a drawer; only below lg, where the panels fold away."""
    return rx.el.button(
        rx.icon(icon, class_name="h-4 w-4 md:mr-2"),
        rx.el.span(label, class_name="hidden md:inline"),
        on_click=DicomViewerState.set_drawer(side),
        title=label,
        aria_label=label,
        class_name="lg:hidden flex items-center p-2 md:px-3 md:py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium transition-colors",
    )


def top_bar() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.button(
                rx.icon("arrow-left", class_name="h-4 w-4 sm:mr-2"),
                rx.el.span("Back to folders", class_name="hidden sm:inline"),
                on_click=rx.redirect("/"),
                title="Back to folders",
                aria_label="Back to folders",
                class_name="flex items-center p-2 sm:px-3 sm:py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition-colors text-sm font-medium shrink-0",
            ),
            rx.cond(
                SeriesRoom.total_images > 0,
                rx.el.div(
                    rx.el.p(
                        rx.cond(
                            SeriesRoom.series_label != "",
                            SeriesRoom.series_label,
                            SeriesRoom.series_name,
                        ),
                        class_name="text-sm font-medium text-slate-100 truncate",
                    ),
                    rx.el.p(
                        SeriesRoom.series_name,
                        title=SeriesRoom.series_path,
                        class_name="font-mono text-xs text-slate-400 truncate",
                    ),
                    class_name="min-w-0 border-l border-slate-700 pl-3 sm:pl-4",
                ),
            ),
            class_name="flex flex-1 items-center gap-3 sm:gap-4 min-w-0",
        ),
        rx.cond(
            SeriesRoom.total_images > 0,
            rx.el.div(
                view_mode_switch(),
                drawer_button("metadata", "info", "Metadata"),
                drawer_button("controls", "sliders-horizontal", "Controls"),
                class_name="flex items-center gap-2 sm:gap-3 lg:gap-6 shrink-0",
            ),
        ),
        rx.cond(DicomViewerState.series != "", people_bar()),
        class_name="bg-slate-900 border-b border-slate-800 px-2 sm:px-4 py-2 sm:py-3 flex flex-wrap justify-between items-center gap-2 sm:gap-4 z-10 shrink-0",
    )


def people_bar() -> rx.Component:
    """Who is here, the call and the link (as in ReflexGrid's title bar). Narrower than lg
    it is a second row of the top bar."""
    return rx.el.div(
        presence(),
        rx.el.div(
            call_button(),
            share_button(),
            class_name="flex items-center gap-2 shrink-0",
        ),
        class_name="order-last lg:order-none basis-full lg:basis-auto flex items-center justify-between lg:justify-end gap-2 sm:gap-3 lg:pl-4 lg:border-l lg:border-slate-700",
    )


def slice_bar() -> rx.Component:
    """Slice navigation right under the image."""
    step_button = "p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 transition-colors shrink-0"
    return rx.el.div(
        rx.el.button(
            rx.icon("chevron-left", class_name="h-5 w-5"),
            on_click=DicomViewerState.prev_image,
            disabled=SeriesRoom.current_index == 0,
            title="Previous slice (↑)",
            class_name=step_button,
        ),
        rx.el.input(
            type="range",
            min="0",
            max=SeriesRoom.slider_max,
            key=slider_key("slice"),
            default_value=SeriesRoom.current_index,
            on_change=DicomViewerState.set_slice_index,
            id="slice-slider",
            class_name="flex-1 min-w-0 h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500",
        ),
        rx.el.button(
            rx.icon("chevron-right", class_name="h-5 w-5"),
            on_click=DicomViewerState.next_image,
            disabled=SeriesRoom.current_index
            == SeriesRoom.total_images - 1,
            title="Next slice (↓)",
            class_name=step_button,
        ),
        rx.el.span(
            SeriesRoom.current_position_text,
            class_name="font-mono text-sm text-slate-300 w-20 text-right shrink-0",
        ),
        rx.el.span(
            rx.icon("mouse", class_name="h-3.5 w-3.5 mr-1.5"),
            "Scroll or ↑ ↓ to change slice",
            class_name="hidden lg:flex items-center text-xs text-slate-500 whitespace-nowrap",
        ),
        class_name="flex items-center gap-3 px-4 py-3 bg-slate-900 border-t border-slate-800 shrink-0",
    )


def message_screen(icon: str, title: str, text, id: str) -> rx.Component:
    return rx.el.div(
        rx.icon(icon, class_name="h-12 w-12 text-slate-600 mb-4"),
        rx.el.p(title, class_name="text-lg text-slate-200 font-medium"),
        rx.el.p(text, class_name="text-sm text-slate-500 mt-1 max-w-md"),
        rx.el.button(
            rx.icon("folder-open", class_name="h-4 w-4 mr-2"),
            "Back to folders",
            on_click=rx.redirect("/"),
            class_name="mt-6 flex items-center px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium",
        ),
        id=id,
        class_name="flex-1 flex flex-col items-center justify-center text-center p-6",
    )


def opening_screen() -> rx.Component:
    """The series is being read (for everyone on it)."""
    return rx.el.div(
        rx.el.div(
            rx.el.div(
                rx.el.div(
                    class_name="animate-spin rounded-full h-5 w-5 border-2 border-slate-600 border-t-blue-400"
                ),
                rx.el.span("Opening images…", class_name="font-medium text-slate-100"),
                class_name="flex items-center gap-3",
            ),
            rx.el.div(
                rx.el.div(
                    class_name="h-full bg-blue-500 transition-all",
                    style={"width": f"{SeriesRoom.opening_percent}%"},
                ),
                class_name="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden mt-4",
            ),
            rx.el.p(
                rx.cond(
                    SeriesRoom.opening_total > 0,
                    f"Reading {SeriesRoom.opening_done} of {SeriesRoom.opening_total} files",
                    "Looking for DICOM files…",
                ),
                class_name="text-xs text-slate-400 mt-2 font-mono",
            ),
            class_name="w-80 bg-slate-900 border border-slate-700 rounded-xl p-5 shadow-2xl",
        ),
        id="opening",
        class_name="flex-1 flex items-center justify-center",
    )


def viewer_body() -> rx.Component:
    """The image and the panels."""
    return rx.el.div(
        metadata_panel(),
        rx.el.div(
            rx.cond(
                SeriesRoom.view_mode == "mpr",
                three_views(),
                rx.fragment(
                    WheelArea.create(
                        rx.image(
                            src=SeriesRoom.current_image_base64,
                            style={
                                "transform": f"translate({SeriesRoom.pan_x}px, {SeriesRoom.pan_y}px) scale({SeriesRoom.zoom_level})",
                                "transition": "transform 0.1s ease-out",
                            },
                            class_name="max-h-full max-w-full object-contain pointer-events-none select-none",
                            alt="DICOM Image",
                        ),
                        actor_badge(),
                        on_wheel=DicomViewerState.scroll_slices.throttle(60),
                        class_name="flex-1 min-h-0 bg-black relative flex items-center justify-center overflow-hidden w-full",
                    ),
                    slice_bar(),
                ),
            ),
            class_name="flex flex-col flex-1 min-w-0",
        ),
        viewer_sidebar(),
        drawer_backdrop(),
        class_name="flex flex-1 overflow-hidden min-h-0",
    )


def viewer_layout() -> rx.Component:
    """A series at its own address, looked at together by everyone who opens it."""
    return rx.el.div(
        SliceKeys.create(on_key_down=DicomViewerState.handle_slice_key),
        top_bar(),
        rx.cond(
            SeriesRoom.error_message != "",
            rx.el.div(
                rx.icon("triangle-alert", class_name="h-4 w-4 mr-2 text-red-400"),
                SeriesRoom.error_message,
                class_name="m-4 flex items-center text-red-400 text-sm bg-red-900/20 p-3 rounded-lg border border-red-900/50",
            ),
        ),
        rx.cond(
            DicomViewerState.gone,
            message_screen(
                "folder-x",
                "This series isn't here any more",
                "Its folder was moved, renamed or deleted, or the link is not complete.",
                "series-gone",
            ),
            rx.match(
                SeriesRoom.status,
                (
                    "ready",
                    rx.cond(SeriesRoom.total_images > 0, viewer_body(), opening_screen()),
                ),
                (
                    "error",
                    message_screen("triangle-alert", "The images can't be opened", SeriesRoom.open_error, "open-error"),
                ),
                opening_screen(),
            ),
        ),
        tutorial_dialog(),
        rx.cond(DicomViewerState.series != "", page_hooks()),
        incoming_call(),
        class_name="flex flex-col h-screen w-screen bg-slate-950 text-slate-100 font-['Inter'] overflow-hidden",
    )
