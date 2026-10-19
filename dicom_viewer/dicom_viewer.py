import reflex as rx
from dicom_viewer.states.browser_state import FolderBrowserState
from dicom_viewer.components.folder_browser import folder_page
from dicom_viewer.components.viewer import viewer_layout


def header() -> rx.Component:
    """Application header."""
    return rx.el.header(
        rx.el.div(
            rx.icon("activity", class_name="h-6 w-6 text-blue-400 mr-3"),
            rx.el.h1(
                "DICOM Viewer",
                class_name="text-xl font-bold text-slate-100 tracking-tight",
            ),
            class_name="flex items-center",
        ),
        class_name="bg-slate-900 border-b border-slate-800 px-6 py-4 shadow-sm sticky top-0 z-40",
    )


def index() -> rx.Component:
    return rx.el.div(
        header(),
        rx.el.main(folder_page(), class_name="flex-1 bg-slate-950 min-h-screen"),
        class_name="flex flex-col min-h-screen font-['Inter'] bg-slate-950 text-slate-100",
    )


app = rx.App(
    head_components=[
        rx.el.link(rel="preconnect", href="https://fonts.googleapis.com"),
        rx.el.link(rel="preconnect", href="https://fonts.gstatic.com", cross_origin=""),
        rx.el.link(
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap",
            rel="stylesheet",
        ),
    ],
)
app.add_page(index, route="/", on_load=FolderBrowserState.on_load)
app.add_page(viewer_layout, route="/viewer")
