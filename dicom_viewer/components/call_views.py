"""Call controls (from ReflexGrid): call everyone on the series, incoming calls, the call's
heartbeat."""

from __future__ import annotations

import reflex as rx

from dicom_viewer.call import CALLS_ENABLED, CallState
from dicom_viewer.states.dicom_state import DicomViewerState
from dicom_viewer.states.room import SeriesRoom


def call_button() -> rx.Component:
    """Call everyone on the series, or join its call while one is in progress."""
    if not CALLS_ENABLED:
        return rx.fragment()
    button = "flex items-center gap-1.5 p-2 lg:px-3 lg:py-1.5 rounded-lg text-sm font-medium transition-colors shrink-0"
    return rx.cond(
        SeriesRoom.call_count > 0,
        rx.el.button(
            rx.icon("phone-call", class_name="h-4 w-4"),
            rx.el.span("Join call · ", SeriesRoom.call_count, class_name="whitespace-nowrap"),
            on_click=CallState.join_call,
            id="call-join",
            title="Join the call on this series",
            class_name=f"{button} bg-green-600 hover:bg-green-500 text-white",
        ),
        rx.el.button(
            rx.icon("phone", class_name="h-4 w-4"),
            rx.el.span("Call everyone", class_name="hidden lg:inline whitespace-nowrap"),
            on_click=CallState.join_call,
            id="call-everyone",
            title="Call everyone on this series",
            aria_label="Call everyone",
            class_name=f"{button} bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700",
        ),
    )


def incoming_call() -> rx.Component:
    """Someone called everyone on the series: accept or decline."""
    if not CALLS_ENABLED:
        return rx.fragment()
    me = DicomViewerState.me["user"]
    rung = (
        (SeriesRoom.call_ringing_from != "")
        & SeriesRoom.call_invited.contains(me)
        & ~SeriesRoom.call_responded.contains(me)
        & (DicomViewerState.series != "")
    )
    round_button = "p-2.5 rounded-full flex cursor-pointer border-0"
    # Above the intent dialog (z-index 1000), so a call can be answered from inside another.
    return rx.cond(
        rung,
        rx.el.div(
            rx.el.div(
                rx.icon("phone-incoming", class_name="h-5 w-5 text-white"),
                class_name="h-10 w-10 rounded-full bg-green-500 flex items-center justify-center shrink-0 animate-pulse",
            ),
            rx.el.div(
                rx.el.span("Incoming call", class_name="text-[11px] font-semibold uppercase tracking-wider text-green-400"),
                rx.el.span(SeriesRoom.call_ringing_from, class_name="text-sm font-bold text-slate-100 truncate"),
                rx.el.span("is calling everyone on this series", class_name="text-xs text-slate-400"),
                class_name="flex flex-col min-w-0 flex-1",
            ),
            rx.el.button(
                rx.icon("phone-off", class_name="h-4 w-4"),
                on_click=CallState.decline_call,
                title="Decline",
                aria_label="Decline",
                class_name=f"{round_button} bg-red-900/60 text-red-300 hover:bg-red-900",
            ),
            rx.el.button(
                rx.icon("phone", class_name="h-4 w-4"),
                on_click=CallState.join_call,
                title="Accept",
                aria_label="Accept",
                class_name=f"{round_button} bg-green-600 text-white hover:bg-green-500",
            ),
            role="alertdialog",
            aria_label="Incoming call",
            class_name="fixed bottom-6 right-4 sm:right-6 z-[1001] w-[min(20rem,calc(100vw-2rem))] flex items-center gap-3 p-3 rounded-2xl bg-slate-900 border border-slate-700 shadow-2xl",
        ),
    )


_CALL_HEARTBEAT_JS = """
if (window.__dvCallHb) clearInterval(window.__dvCallHb);
window.__dvCallHb = setInterval(function () {
    var el = document.getElementById('call-heartbeat-trigger');
    if (!el) { clearInterval(window.__dvCallHb); window.__dvCallHb = null; return; }
    el.click();
}, 3000);
"""


@rx.memo
def call_keepalive() -> rx.Component:
    """While in a call, keep its heartbeat going on every page.

    The call runs in the app-wide intent dialogs, so it outlives the series page; the
    heartbeat stops with the tab, which then drops out of the call.
    """
    return rx.cond(
        CallState.current_series != "",
        rx.el.button(
            id="call-heartbeat-trigger",
            on_click=CallState.heartbeat,
            on_mount=rx.call_script(_CALL_HEARTBEAT_JS),
            tab_index=-1,
            aria_hidden="true",
            class_name="sr-only",
        ),
    )
