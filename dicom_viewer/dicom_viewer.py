"""DICOM Viewer.

Pages: ``/`` is the folder page; ``/viewer/<id>`` is a series at its own address, looked at
together by everyone who opens it: what one of them does to the view, everyone sees.
"""

import reflex as rx

try:
    from reflex_ddns_auth.intent import install_intent_host
except ImportError:  # a standalone viewer, without the DDNS libraries
    install_intent_host = None

from dicom_viewer.api import api
from dicom_viewer.states.browser_state import FolderBrowserState
from dicom_viewer.states.dicom_state import AuthState, DicomViewerState
from dicom_viewer.components.call_views import call_keepalive
from dicom_viewer.components.folder_browser import folder_page
from dicom_viewer.components.together import STYLE, account
from dicom_viewer.components.viewer import viewer_layout


def header() -> rx.Component:
    """Application header."""
    return rx.el.header(
        rx.el.div(
            rx.el.div(
                rx.icon("activity", class_name="h-6 w-6 text-blue-400 mr-3"),
                rx.el.h1(
                    "DICOM Viewer",
                    class_name="text-xl font-bold text-slate-100 tracking-tight",
                ),
                class_name="flex items-center",
            ),
            account(),
            class_name="flex items-center justify-between gap-4",
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
        rx.el.style(STYLE),
    ],
    # Our own backend routes (a closing tab's goodbye), next to Reflex's.
    api_transformer=api,
)
# Dialogs of other apps (DICOM Data Explorer fetching images, calls) live above every page,
# so a call goes on when you go back to the folders.
if install_intent_host is not None:
    install_intent_host(app)
    app.extra_app_wraps[(8, "DicomViewerCallKeepalive")] = lambda stateful: rx.fragment(call_keepalive())

_auth_on_load = [AuthState.load_auth] if AuthState is not None else []

app.add_page(index, route="/", on_load=[*_auth_on_load, DicomViewerState.leave_series, FolderBrowserState.on_load])
app.add_page(viewer_layout, route="/viewer/[series_id]", on_load=[*_auth_on_load, DicomViewerState.on_load])
# The old address of the viewer, before series had their own.
app.add_page(rx.fragment, route="/viewer", on_load=rx.redirect("/"))
