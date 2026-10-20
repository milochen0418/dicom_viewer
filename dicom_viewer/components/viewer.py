import reflex as rx
from reflex_components_core.core.window_events import WindowEventListener

from dicom_viewer.components.three_views import three_views
from dicom_viewer.states.dicom_state import DicomViewerState


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


def preset_item(preset: str) -> rx.Component:
    is_selected = DicomViewerState.selected_preset == preset
    return rx.el.div(
        rx.el.button(
            preset,
            on_click=lambda: DicomViewerState.apply_preset(preset),
            class_name=rx.cond(
                is_selected,
                "flex-1 text-left text-sm text-blue-200 bg-blue-600/20 border border-blue-500/40 rounded-lg px-3 py-2 transition-colors",
                "flex-1 text-left text-sm text-slate-200 bg-slate-800/60 border border-slate-700 rounded-lg px-3 py-2 hover:bg-slate-800 transition-colors",
            ),
        ),
        rx.el.button(
            rx.icon("circle-help", class_name="h-4 w-4"),
            # Shown only while pointing at "?", and it ignores the pointer, so
            # it never covers the buttons of the presets around it.
            rx.el.span(
                DicomViewerState.preset_tooltips[preset],
                rx.el.span(
                    "Click ? for the tutorial",
                    class_name="block mt-1 text-[11px] text-blue-300",
                ),
                class_name=(
                    "absolute right-full top-1/2 -translate-y-1/2 mr-2 w-52 block "
                    "text-left text-xs text-slate-200 bg-slate-900 border "
                    "border-slate-700 rounded-lg shadow-xl p-2 z-50 opacity-0 "
                    "pointer-events-none transition-opacity group-hover:opacity-100 "
                    "group-focus-visible:opacity-100"
                ),
            ),
            on_click=lambda: DicomViewerState.open_tutorial_dialog(preset),
            class_name="relative group ml-2 p-1 rounded text-slate-400 hover:text-blue-300 hover:bg-slate-800 transition-colors",
        ),
        class_name="flex items-center",
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
                        DicomViewerState.patient_name,
                        "Hidden",
                    ),
                    "user",
                ),
                metadata_row(
                    "ID",
                    rx.cond(
                        DicomViewerState.metadata_unlocked,
                        DicomViewerState.patient_id,
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
                        DicomViewerState.study_date,
                        "Hidden",
                    ),
                    "calendar",
                ),
                metadata_row("Modality", DicomViewerState.modality, "scan"),
                metadata_row("Study", DicomViewerState.study_description, "file-text"),
                metadata_row("Series", DicomViewerState.series_description, "layers"),
                class_name="mb-6",
            ),
            rx.el.div(
                control_section_header("Image Data"),
                metadata_row(
                    "Resolution",
                    f"{DicomViewerState.columns} x {DicomViewerState.rows}",
                    "maximize",
                ),
                metadata_row("Spacing", DicomViewerState.pixel_spacing, "ruler"),
                metadata_row(
                    "Thickness", DicomViewerState.slice_thickness, "align-justify"
                ),
                metadata_row("Position", DicomViewerState.image_position, "crosshair"),
                class_name="mb-6",
            ),
            class_name="p-5",
        ),
    )


def viewer_sidebar() -> rx.Component:
    """The controls sidebar."""
    return side_panel(
        "controls",
        "Controls",
        DicomViewerState.show_controls,
        DicomViewerState.toggle_controls,
        "lg:w-80",
        rx.el.div(
            control_section_header("Zoom & Pan"),
            rx.el.div(
                rx.el.div(
                    rx.el.button(
                        rx.icon("minus", class_name="h-4 w-4"),
                        on_click=DicomViewerState.zoom_out,
                        disabled=DicomViewerState.zoom_level <= 0.25,
                        class_name="p-2 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 disabled:opacity-50",
                    ),
                    rx.el.span(
                        f"{(DicomViewerState.zoom_level * 100).to_string()}%",
                        class_name="text-sm font-mono text-blue-400 w-16 text-center",
                    ),
                    rx.el.button(
                        rx.icon("plus", class_name="h-4 w-4"),
                        on_click=DicomViewerState.zoom_in,
                        disabled=DicomViewerState.zoom_level >= 4.0,
                        class_name="p-2 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 disabled:opacity-50",
                    ),
                    class_name="flex items-center justify-between mb-4 bg-slate-900/50 p-2 rounded-lg border border-slate-800",
                ),
                rx.el.div(
                    rx.el.button(
                        rx.icon("arrow-up", class_name="h-5 w-5"),
                        on_click=lambda: DicomViewerState.pan_control(0, 50),
                        class_name="p-2 bg-slate-800 hover:bg-slate-700 rounded-t-lg mx-auto block text-slate-300",
                    ),
                    rx.el.div(
                        rx.el.button(
                            rx.icon("arrow-left", class_name="h-5 w-5"),
                            on_click=lambda: DicomViewerState.pan_control(50, 0),
                            class_name="p-2 bg-slate-800 hover:bg-slate-700 rounded-l-lg text-slate-300",
                        ),
                        rx.el.button(
                            rx.icon("maximize", class_name="h-4 w-4"),
                            on_click=DicomViewerState.reset_zoom,
                            class_name="p-2 bg-slate-700 hover:bg-slate-600 text-blue-400",
                            title="Reset View",
                        ),
                        rx.el.button(
                            rx.icon("arrow-right", class_name="h-5 w-5"),
                            on_click=lambda: DicomViewerState.pan_control(-50, 0),
                            class_name="p-2 bg-slate-800 hover:bg-slate-700 rounded-r-lg text-slate-300",
                        ),
                        class_name="flex justify-center gap-1 my-1",
                    ),
                    rx.el.button(
                        rx.icon("arrow-down", class_name="h-5 w-5"),
                        on_click=lambda: DicomViewerState.pan_control(0, -50),
                        class_name="p-2 bg-slate-800 hover:bg-slate-700 rounded-b-lg mx-auto block text-slate-300",
                    ),
                    class_name="mb-6",
                ),
                rx.el.button(
                    "Reset All",
                    on_click=DicomViewerState.reset_view,
                    class_name="w-full py-2 px-4 bg-slate-800 hover:bg-red-900/30 text-slate-400 hover:text-red-400 text-xs font-medium uppercase tracking-wider rounded-lg transition-colors border border-slate-800 hover:border-red-900/50",
                ),
                class_name="flex flex-col",
            ),
            class_name="p-6",
        ),
        rx.el.div(
            control_section_header("Windowing"),
            rx.el.div(
                rx.el.div(
                    rx.el.label(
                        "Selected Preset",
                        class_name="block text-[11px] font-medium text-slate-500",
                    ),
                    rx.el.span(
                        rx.cond(
                            DicomViewerState.selected_preset != "",
                            DicomViewerState.selected_preset,
                            "Manual",
                        ),
                        class_name="text-xs text-blue-300 font-mono",
                    ),
                    class_name="flex items-center justify-between mb-3",
                ),
                rx.el.div(
                    rx.el.label(
                        "Level (Center)",
                        class_name="text-xs font-medium text-slate-300",
                    ),
                    rx.el.span(
                        DicomViewerState.window_center.to_string(),
                        class_name="text-xs text-blue-400 font-mono",
                    ),
                    class_name="flex justify-between mb-2",
                ),
                rx.el.input(
                    type="range",
                    min="-1000",
                    max="3000",
                    key=DicomViewerState.window_slider_key,
                    default_value=DicomViewerState.window_center,
                    on_change=DicomViewerState.update_window_center,
                    class_name="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer hover:bg-slate-600 transition-colors accent-blue-500",
                ),
                class_name="mb-4",
            ),
            rx.el.div(
                rx.el.div(
                    rx.el.label(
                        "Width", class_name="text-xs font-medium text-slate-300"
                    ),
                    rx.el.span(
                        DicomViewerState.window_width.to_string(),
                        class_name="text-xs text-blue-400 font-mono",
                    ),
                    class_name="flex justify-between mb-2",
                ),
                rx.el.input(
                    type="range",
                    min="1",
                    max="4000",
                    key=DicomViewerState.window_slider_key,
                    default_value=DicomViewerState.window_width,
                    on_change=DicomViewerState.update_window_width,
                    class_name="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer hover:bg-slate-600 transition-colors accent-blue-500",
                ),
            ),
            rx.el.div(
                rx.el.label(
                    "Preset",
                    class_name="block text-xs font-medium text-slate-400 mb-1.5",
                ),
                rx.el.div(
                    rx.el.label(
                        "Tooltip Language",
                        class_name="block text-[11px] font-medium text-slate-500 mb-2",
                    ),
                    rx.el.select(
                        rx.el.option("English", value="en"),
                        rx.el.option("繁中", value="zh-TW"),
                        rx.el.option("简中", value="zh-CN"),
                        rx.el.option("Español", value="es"),
                        value=DicomViewerState.tooltip_language,
                        on_change=DicomViewerState.set_tooltip_language,
                        class_name="w-full bg-slate-900 text-slate-200 text-xs rounded-lg border border-slate-700 px-3 py-2 outline-none focus:ring-1 focus:ring-blue-500 mb-4 appearance-none",
                    ),
                ),
                rx.el.div(
                    rx.foreach(DicomViewerState.preset_options, preset_item),
                    class_name="grid gap-2",
                ),
                class_name="mt-4",
            ),
            class_name="p-6 border-t border-slate-800",
        ),
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
                DicomViewerState.view_mode == mode,
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
                DicomViewerState.total_images > 0,
                rx.el.div(
                    rx.el.p(
                        rx.cond(
                            DicomViewerState.series_label != "",
                            DicomViewerState.series_label,
                            DicomViewerState.series_name,
                        ),
                        class_name="text-sm font-medium text-slate-100 truncate",
                    ),
                    rx.el.p(
                        DicomViewerState.series_name,
                        title=DicomViewerState.series_path,
                        class_name="font-mono text-xs text-slate-400 truncate",
                    ),
                    class_name="min-w-0 border-l border-slate-700 pl-3 sm:pl-4",
                ),
            ),
            class_name="flex items-center gap-3 sm:gap-4 min-w-0",
        ),
        rx.cond(
            DicomViewerState.total_images > 0,
            rx.el.div(
                view_mode_switch(),
                drawer_button("metadata", "info", "Metadata"),
                drawer_button("controls", "sliders-horizontal", "Controls"),
                rx.cond(
                    DicomViewerState.view_mode == "single",
                    rx.el.div(
                        rx.icon("layers", class_name="h-4 w-4 mr-2 text-blue-400"),
                        rx.el.span(
                            DicomViewerState.current_position_text,
                            class_name="font-mono text-sm text-slate-300",
                        ),
                        title="Slice / slices in this series",
                        class_name="hidden lg:flex items-center bg-slate-800 px-3 py-1.5 rounded-full border border-slate-700",
                    ),
                ),
                class_name="flex items-center gap-2 sm:gap-3 lg:gap-6 shrink-0",
            ),
        ),
        class_name="bg-slate-900 border-b border-slate-800 px-2 sm:px-4 py-2 sm:py-3 flex justify-between items-center gap-2 sm:gap-4 z-10 shrink-0",
    )


def slice_bar() -> rx.Component:
    """Slice navigation right under the image."""
    step_button = "p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-300 transition-colors shrink-0"
    return rx.el.div(
        rx.el.button(
            rx.icon("chevron-left", class_name="h-5 w-5"),
            on_click=DicomViewerState.prev_image,
            disabled=DicomViewerState.current_index == 0,
            title="Previous slice (↑)",
            class_name=step_button,
        ),
        rx.el.input(
            type="range",
            min="0",
            max=DicomViewerState.slider_max,
            key=DicomViewerState.slice_slider_key,
            default_value=DicomViewerState.current_index,
            on_change=DicomViewerState.set_slice_index,
            class_name="flex-1 min-w-0 h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500",
        ),
        rx.el.button(
            rx.icon("chevron-right", class_name="h-5 w-5"),
            on_click=DicomViewerState.next_image,
            disabled=DicomViewerState.current_index
            == DicomViewerState.total_images - 1,
            title="Next slice (↓)",
            class_name=step_button,
        ),
        rx.el.span(
            DicomViewerState.current_position_text,
            class_name="font-mono text-sm text-slate-300 w-20 text-right shrink-0",
        ),
        rx.el.span(
            rx.icon("mouse", class_name="h-3.5 w-3.5 mr-1.5"),
            "Scroll or ↑ ↓ to change slice",
            class_name="hidden lg:flex items-center text-xs text-slate-500 whitespace-nowrap",
        ),
        class_name="flex items-center gap-3 px-4 py-3 bg-slate-900 border-t border-slate-800 shrink-0",
    )


def empty_viewer() -> rx.Component:
    return rx.el.div(
        rx.icon("images", class_name="h-12 w-12 text-slate-600 mb-4"),
        rx.el.p("No images are open", class_name="text-lg text-slate-200 font-medium"),
        rx.el.p(
            "Choose a folder with DICOM images first.",
            class_name="text-sm text-slate-500 mt-1",
        ),
        rx.el.button(
            rx.icon("folder-open", class_name="h-4 w-4 mr-2"),
            "Choose a folder",
            on_click=rx.redirect("/"),
            class_name="mt-6 flex items-center px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium",
        ),
        class_name="flex-1 flex flex-col items-center justify-center text-center",
    )


def viewer_layout() -> rx.Component:
    """The main viewer layout with image and controls."""
    return rx.el.div(
        SliceKeys.create(on_key_down=DicomViewerState.handle_slice_key),
        top_bar(),
        rx.cond(
            DicomViewerState.error_message != "",
            rx.el.div(
                rx.icon("triangle-alert", class_name="h-4 w-4 mr-2 text-red-400"),
                DicomViewerState.error_message,
                class_name="m-4 flex items-center text-red-400 text-sm bg-red-900/20 p-3 rounded-lg border border-red-900/50",
            ),
        ),
        rx.cond(
            DicomViewerState.total_images > 0,
            rx.el.div(
                metadata_panel(),
                rx.el.div(
                    rx.cond(
                        DicomViewerState.view_mode == "mpr",
                        three_views(),
                        rx.fragment(
                            WheelArea.create(
                                rx.image(
                                    src=DicomViewerState.current_image_base64,
                                    style={
                                        "transform": f"translate({DicomViewerState.pan_x}px, {DicomViewerState.pan_y}px) scale({DicomViewerState.zoom_level})",
                                        "transition": "transform 0.1s ease-out",
                                    },
                                    class_name="max-h-full max-w-full object-contain pointer-events-none select-none",
                                    alt="DICOM Image",
                                ),
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
            ),
            empty_viewer(),
        ),
        tutorial_dialog(),
        class_name="flex flex-col h-screen w-screen bg-slate-950 text-slate-100 font-['Inter'] overflow-hidden",
    )
