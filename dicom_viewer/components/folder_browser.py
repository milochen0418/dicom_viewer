import reflex as rx

try:
    from reflex_ddns_auth.intent import Intent
except ImportError:  # a standalone viewer: no DICOM Data Explorer to fetch images with
    Intent = None

from dicom_viewer.states.browser_state import Crumb, FolderBrowserState, FolderEntry
from dicom_viewer.states.dicom_state import DicomViewerState

def _click_count_spec(e: rx.Var) -> tuple[rx.Var[int]]:
    # 1 for a single click, 2 for the second click of a double click.
    return (rx.Var(f"{e}.detail").to(int),)


class NavButton(rx.el.Button):
    """A button whose click handler learns which click of a multi-click it got."""

    on_click: rx.EventHandler[_click_count_spec]


DICOM_BADGE = (
    "text-[10px] font-semibold tracking-wider px-1.5 py-0.5 rounded "
    "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0"
)


def _plural(count, one: str, many: str):
    return rx.cond(count == 1, f"1 {one}", f"{count} {many}")


def open_button(series_id, label: str, large: bool = False) -> rx.Component:
    """Goes to the series' own address, which can be shared."""
    return rx.el.button(
        rx.icon("play", class_name="h-4 w-4 mr-1.5 fill-current"),
        label,
        on_click=rx.redirect("/viewer/" + series_id),
        class_name=(
            "flex items-center whitespace-nowrap rounded-lg font-medium text-white "
            "bg-blue-600 hover:bg-blue-500 transition-colors shadow-lg "
            "hover:shadow-blue-500/25 disabled:opacity-50 "
            + ("px-5 py-2.5 text-sm" if large else "px-3 py-1.5 text-xs")
        ),
    )


def fetch_button(missing, label, study, large: bool = False) -> rx.Component:
    """Opens DICOM Data Explorer in a dialog to download the series an annotation needs.

    `missing` is the comma-separated SeriesInstanceUIDs; `label` names the annotation;
    `study` is its StudyInstanceUID, which a PACS needs to find the series.
    Without the DDNS libraries there is nothing to open, so nothing is shown.
    """
    if Intent is None:
        return rx.fragment()
    return rx.el.button(
        rx.icon("cloud-download", class_name="h-4 w-4 mr-1.5"),
        "Get images",
        on_click=Intent.start(
            "dicom-data-explorer",
            "dicom.fetch-series",
            on_result=FolderBrowserState.images_fetched,
            on_cancel=FolderBrowserState.fetch_cancelled,
            label="Get images",
            series=missing,
            title=label,
            study=study,
        ),
        title="Download the images this annotation belongs to, with DICOM Data Explorer",
        class_name=(
            "flex items-center whitespace-nowrap rounded-lg font-medium text-amber-100 "
            "bg-amber-700/80 hover:bg-amber-600 transition-colors shadow-lg "
            + ("px-5 py-2.5 text-sm" if large else "px-3 py-1.5 text-xs")
        ),
    )


def live_badge(series_id) -> rx.Component:
    """Who is looking at the series right now (as ReflexGrid's list shows who is editing)."""
    count = FolderBrowserState.live_count[series_id]
    return rx.cond(
        (series_id != "") & (count > 0),
        rx.el.span(
            rx.el.span(class_name="h-2 w-2 rounded-full bg-green-500 animate-pulse"),
            count.to(str) + " viewing",
            title=FolderBrowserState.live_names[series_id],
            class_name="live-badge flex items-center gap-1.5 text-[10px] font-medium px-1.5 py-0.5 rounded bg-green-500/10 text-green-300 border border-green-500/30 shrink-0",
        ),
    )


def breadcrumb(crumb: Crumb, index) -> rx.Component:
    is_current = index == FolderBrowserState.crumbs.length() - 1
    return rx.el.div(
        rx.cond(
            index > 0,
            rx.icon("chevron-right", class_name="h-4 w-4 text-slate-600 shrink-0"),
        ),
        NavButton.create(
            rx.cond(index == 0, rx.icon("house", class_name="h-4 w-4 mr-1.5 shrink-0")),
            rx.el.span(crumb.name, class_name="truncate"),
            title=crumb.path,
            on_click=lambda clicks: FolderBrowserState.open_folder(crumb.path, clicks),
            disabled=is_current,
            class_name=rx.cond(
                is_current,
                "flex items-center max-w-[18rem] px-1.5 py-1 rounded font-medium text-slate-100",
                "flex items-center max-w-[12rem] px-1.5 py-1 rounded text-blue-300 hover:text-blue-200 hover:bg-slate-800 transition-colors",
            ),
        ),
        class_name="flex items-center gap-1 min-w-0",
    )


def toolbar() -> rx.Component:
    return rx.el.div(
        NavButton.create(
            rx.icon("arrow-up", class_name="h-4 w-4 mr-1.5"),
            "Up",
            on_click=lambda clicks: FolderBrowserState.go_up(clicks),
            disabled=~FolderBrowserState.can_go_up,
            title="Go to the parent folder",
            class_name=(
                "flex items-center px-3 py-1.5 text-sm rounded-lg bg-slate-800 "
                "hover:bg-slate-700 text-slate-200 transition-colors shrink-0 "
                "disabled:opacity-40 disabled:cursor-not-allowed"
            ),
        ),
        rx.el.nav(
            rx.foreach(FolderBrowserState.crumbs, breadcrumb),
            class_name="flex flex-wrap items-center gap-1 min-w-0 flex-1 text-sm font-mono",
        ),
        rx.el.button(
            rx.icon(
                "refresh-cw",
                class_name=rx.cond(
                    FolderBrowserState.loading | FolderBrowserState.checking,
                    "h-4 w-4 animate-spin",
                    "h-4 w-4",
                ),
            ),
            on_click=FolderBrowserState.refresh,
            title="Refresh",
            class_name="p-2 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors shrink-0",
        ),
        class_name="flex items-center gap-3 px-4 py-3 border-b border-slate-800",
    )


def message_banner(message, on_dismiss=None) -> rx.Component:
    dismiss = (
        rx.el.button(
            rx.icon("x", class_name="h-4 w-4"),
            on_click=on_dismiss,
            title="Dismiss",
            class_name="ml-2 p-1 rounded hover:bg-red-900/40",
        )
        if on_dismiss is not None
        else rx.fragment()
    )
    return rx.el.div(
        rx.icon("triangle-alert", class_name="h-4 w-4 mr-2 shrink-0"),
        rx.el.span(message, class_name="flex-1"),
        dismiss,
        class_name="flex items-center text-red-300 text-sm bg-red-900/20 p-3 rounded-lg border border-red-900/50",
    )


def here_card() -> rx.Component:
    """Shown when the current folder itself holds DICOM images."""
    return rx.cond(
        FolderBrowserState.here_dicom_count > 0,
        rx.el.div(
            rx.el.div(
                rx.icon("images", class_name="h-6 w-6 text-emerald-300"),
                class_name="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 shrink-0",
            ),
            rx.el.div(
                rx.el.div(
                    rx.el.p(
                        "This folder contains DICOM images",
                        class_name="text-sm font-semibold text-slate-100",
                    ),
                    live_badge(FolderBrowserState.here_id),
                    class_name="flex items-center gap-2",
                ),
                rx.el.p(
                    _plural(FolderBrowserState.here_dicom_count, "image", "images"),
                    rx.cond(
                        FolderBrowserState.here_annotation_count > 0,
                        rx.fragment(
                            " · ",
                            _plural(
                                FolderBrowserState.here_annotation_count,
                                "annotation",
                                "annotations",
                            ),
                        ),
                    ),
                    rx.cond(
                        FolderBrowserState.here_label != "",
                        rx.fragment(" · ", FolderBrowserState.here_label),
                    ),
                    class_name="text-xs text-slate-400 truncate mt-0.5",
                ),
                class_name="flex-1 min-w-0",
            ),
            open_button(FolderBrowserState.here_id, "Open in viewer", large=True),
            class_name="flex items-center gap-4 m-4 p-4 rounded-xl bg-slate-800/60 border border-emerald-500/30",
        ),
    )


def here_annotation_card() -> rx.Component:
    """Shown when the current folder holds annotations but no images."""
    return rx.cond(
        (FolderBrowserState.here_dicom_count == 0) & (FolderBrowserState.here_annotation_count > 0),
        rx.el.div(
            rx.el.div(
                rx.icon("layers", class_name="h-6 w-6 text-amber-300"),
                class_name="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 shrink-0",
            ),
            rx.el.div(
                rx.el.p(
                    "This folder holds annotations for images kept elsewhere",
                    class_name="text-sm font-semibold text-slate-100",
                ),
                rx.el.p(
                    _plural(FolderBrowserState.here_annotation_count, "annotation", "annotations"),
                    rx.cond(
                        FolderBrowserState.here_label != "",
                        rx.fragment(" · ", FolderBrowserState.here_label),
                    ),
                    rx.cond(
                        FolderBrowserState.here_missing != "",
                        " · the images are not downloaded yet",
                        rx.cond(
                            FolderBrowserState.here_opens != "",
                            " · press Open images to see them with it",
                            " · the images are in a folder beside this one: go up and open them",
                        ),
                    ),
                    class_name="text-xs text-slate-400 truncate mt-0.5",
                ),
                class_name="flex-1 min-w-0",
            ),
            rx.el.div(
                rx.cond(
                    FolderBrowserState.here_opens != "",
                    open_button(FolderBrowserState.here_opens_id, "Open images", large=True),
                ),
                rx.cond(
                    FolderBrowserState.here_missing != "",
                    fetch_button(FolderBrowserState.here_missing, FolderBrowserState.here_label, FolderBrowserState.here_study, large=True),
                ),
                class_name="flex items-center gap-2 shrink-0",
            ),
            class_name="flex items-center gap-4 m-4 p-4 rounded-xl bg-slate-800/60 border border-amber-500/30",
        ),
    )


def entry_details(entry: FolderEntry) -> rx.Component:
    return rx.el.p(
        rx.cond(
            ~entry.checked,
            rx.el.span("Checking…", class_name="animate-pulse"),
            rx.cond(
                entry.error != "",
                rx.el.span(entry.error, class_name="text-red-400"),
                rx.cond(
                    entry.dicom_count > 0,
                    rx.fragment(
                        _plural(entry.dicom_count, "image", "images"),
                        rx.cond(
                            entry.annotation_count > 0,
                            rx.fragment(
                                " · ",
                                _plural(entry.annotation_count, "annotation", "annotations"),
                            ),
                        ),
                        # The title shows the series label, so name the folder here.
                        rx.cond(entry.label != "", rx.fragment(" · ", entry.name)),
                        rx.cond(
                            entry.folder_count > 0,
                            rx.fragment(
                                " · ", _plural(entry.folder_count, "folder", "folders")
                            ),
                        ),
                    ),
                    rx.cond(
                        entry.annotation_count > 0,
                        # Annotations of images kept elsewhere: open those images to see them.
                        rx.fragment(
                            _plural(entry.annotation_count, "annotation", "annotations"),
                            rx.cond(entry.label != "", rx.fragment(" · ", entry.label)),
                            rx.cond(
                                entry.missing != "",
                                " · the images are not downloaded yet",
                                rx.cond(
                                    entry.opens != "",
                                    " · press Open to see it on its images",
                                    " · shown with the images they belong to",
                                ),
                            ),
                        ),
                        rx.cond(
                            entry.folder_count > 0,
                            _plural(entry.folder_count, "folder", "folders"),
                            "No DICOM images",
                        ),
                    ),
                ),
            ),
        ),
        class_name="text-xs text-slate-400 truncate mt-0.5",
    )


def folder_row(entry: FolderEntry) -> rx.Component:
    has_dicom = entry.dicom_count > 0
    # Series folders are often named by UID; their label says more.
    show_label = has_dicom & (entry.label != "")
    return rx.el.div(
        NavButton.create(
            rx.cond(
                has_dicom,
                rx.icon("images", class_name="h-5 w-5 text-emerald-300 shrink-0"),
                rx.icon("folder", class_name="h-5 w-5 text-slate-400 shrink-0"),
            ),
            rx.el.div(
                rx.el.div(
                    rx.el.span(
                        rx.cond(show_label, entry.label, entry.name),
                        class_name=rx.cond(
                            show_label,
                            "truncate text-sm font-medium text-slate-100",
                            "truncate font-mono text-sm text-slate-100",
                        ),
                    ),
                    rx.cond(has_dicom, rx.el.span("DICOM", class_name=DICOM_BADGE)),
                    live_badge(rx.cond(has_dicom, entry.series_id, entry.opens_id)),
                    rx.cond(
                        entry.path == DicomViewerState.series_path,
                        rx.el.span(
                            "Last opened",
                            class_name="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 shrink-0",
                        ),
                    ),
                    class_name="flex items-center gap-2 min-w-0",
                ),
                entry_details(entry),
                class_name="flex-1 min-w-0",
            ),
            rx.cond(
                ~has_dicom,
                rx.icon("chevron-right", class_name="h-4 w-4 text-slate-500 shrink-0"),
            ),
            title=entry.name,
            on_click=lambda clicks: FolderBrowserState.open_folder(entry.path, clicks),
            class_name="flex flex-1 min-w-0 items-center gap-3 px-4 py-3 text-left",
        ),
        rx.cond(
            has_dicom,
            rx.el.div(open_button(entry.series_id, "Open"), class_name="pr-4 shrink-0"),
            rx.cond(
                (entry.opens != "") | (entry.missing != ""),
                rx.el.div(
                    # Downloaded images: open them, the annotation comes along.
                    rx.cond(entry.opens != "", open_button(entry.opens_id, "Open")),
                    rx.cond(entry.missing != "", fetch_button(entry.missing, entry.label, entry.study)),
                    class_name="flex items-center gap-2 pr-4 shrink-0",
                ),
            ),
        ),
        class_name="flex items-center border-b border-slate-800 last:border-b-0 hover:bg-slate-800/50 transition-colors",
    )


def folder_list() -> rx.Component:
    return rx.cond(
        FolderBrowserState.entries.length() > 0,
        rx.el.div(
            rx.el.div(
                rx.el.span("Folders"),
                rx.el.span(
                    FolderBrowserState.entries.length(),
                    class_name="px-1.5 rounded bg-slate-800 text-slate-400",
                ),
                rx.cond(
                    FolderBrowserState.checking,
                    rx.el.span(
                        "Looking for DICOM images…",
                        class_name="ml-auto normal-case tracking-normal animate-pulse",
                    ),
                ),
                class_name="flex items-center gap-2 px-4 pt-4 pb-2 text-xs font-semibold uppercase tracking-wider text-slate-500",
            ),
            rx.el.div(
                rx.foreach(FolderBrowserState.entries, folder_row),
                class_name="mx-4 mb-4 rounded-lg border border-slate-800 bg-slate-950/60 overflow-hidden",
            ),
        ),
    )


def empty_state() -> rx.Component:
    return rx.cond(
        FolderBrowserState.is_empty,
        rx.el.div(
            rx.icon("folder-x", class_name="h-10 w-10 text-slate-600 mb-3"),
            rx.el.p("This folder is empty", class_name="text-slate-300 font-medium"),
            rx.el.p(
                "It has no subfolders and no DICOM images.",
                class_name="text-sm text-slate-500 mt-1",
            ),
            class_name="flex flex-col items-center text-center py-12",
        ),
    )


_REFRESH_JS = """
if (window.__dvListRefresh) clearInterval(window.__dvListRefresh);
window.__dvListRefresh = setInterval(function () {
    var el = document.getElementById('live-refresh');
    if (!el) { clearInterval(window.__dvListRefresh); window.__dvListRefresh = null; return; }
    el.click();
}, 8000);
"""


def folder_page() -> rx.Component:
    """Step 1: find a folder with DICOM images and open it."""
    return rx.el.div(
        rx.el.div(
            rx.el.h2("Choose a DICOM folder", class_name="text-2xl font-semibold text-slate-100"),
            rx.el.p(
                "Click a folder to go inside. Folders marked ",
                rx.el.span("DICOM", class_name=DICOM_BADGE),
                " contain images: press Open to view them.",
                class_name="text-sm text-slate-400 mt-2 flex flex-wrap items-center gap-1",
            ),
            class_name="mb-6",
        ),
        rx.el.div(
            toolbar(),
            rx.cond(
                FolderBrowserState.error != "",
                rx.el.div(message_banner(FolderBrowserState.error), class_name="m-4"),
            ),
            rx.el.div(
                here_card(),
        here_annotation_card(),
                folder_list(),
                empty_state(),
                class_name=rx.cond(
                    FolderBrowserState.loading,
                    "min-h-[8rem] opacity-50 pointer-events-none transition-opacity delay-200",
                    "min-h-[8rem] transition-opacity",
                ),
            ),
            rx.el.div(
                "Data folder: ",
                rx.el.code(FolderBrowserState.root, class_name="text-slate-400"),
                class_name="px-4 py-3 border-t border-slate-800 text-xs text-slate-500 truncate",
            ),
            class_name="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden",
        ),
        rx.el.button(
            id="live-refresh",
            on_click=FolderBrowserState.refresh_live,
            on_mount=rx.call_script(_REFRESH_JS),
            tab_index=-1,
            aria_hidden="true",
            class_name="sr-only",
        ),
        class_name="max-w-3xl mx-auto w-full px-4 py-10",
    )
