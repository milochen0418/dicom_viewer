"""What shows that a series is looked at together (as in ReflexGrid's title bar): who is here,
the link to share, the account, the badge saying who did the last thing, the keys of the
shared sliders, and the page's heartbeat and goodbye."""

import reflex as rx

from dicom_viewer.api import BYE_PATH
from dicom_viewer.states.dicom_state import AuthState, DicomViewerState
from dicom_viewer.states.room import SeriesRoom

# This tab, as the room names it.
ME = DicomViewerState.me["peer"]


def slider_key(group: str) -> rx.Var:
    """The React key of a group of shared sliders (see ``room.slider_keys_after``): theirs
    for everyone, except for the person dragging one of them."""
    return rx.cond(
        SeriesRoom.slider_by[group] == ME,
        SeriesRoom.slider_hold[group],
        SeriesRoom.slider_key[group],
    )


def peer_avatar(p) -> rx.Component:
    """Someone on the series, ringed in their colour; the ring pulses when they act."""
    acting = (SeriesRoom.actor_rev > DicomViewerState.actor_seen) & (SeriesRoom.actor["peer"] == p["id"])
    face = rx.cond(
        p["avatar"] != "",
        rx.image(src=p["avatar"], alt=p["name"], class_name="h-6 w-6 rounded-full object-cover"),
        rx.el.span(
            p["initials"],
            class_name="h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
            style={"backgroundColor": p["color"]},
        ),
    )
    return rx.el.span(
        face,
        # A new key restarts the pulse.
        key=rx.cond(acting, p["id"].to(str) + "-" + SeriesRoom.actor_rev.to_string(), p["id"].to(str)),
        title=p["name"],
        custom_attrs={"data-name": p["name"]},
        class_name=rx.cond(acting, "dv-person dv-acting", "dv-person"),
        style={"color": p["color"], "borderColor": p["color"]},
    )


def presence() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.foreach(SeriesRoom.people, peer_avatar),
            class_name="flex items-center pl-1.5",
        ),
        rx.el.span(
            SeriesRoom.people_count.to(str) + " here",
            id="people-count",
            class_name="text-xs text-slate-400 whitespace-nowrap",
        ),
        id="presence",
        class_name="flex items-center gap-2 min-w-0",
    )


def share_button() -> rx.Component:
    return rx.el.button(
        rx.icon("link", class_name="h-4 w-4"),
        on_click=[
            rx.call_script("navigator.clipboard && navigator.clipboard.writeText(window.location.href)"),
            rx.toast.success("Link copied: anyone with it can look at this series with you.", position="bottom-center"),
        ],
        id="share-link",
        title="Copy the link to share this series",
        aria_label="Copy link",
        class_name="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition-colors shrink-0",
    )


def identity() -> rx.Component:
    """Who you are here, and signing in or out (with reflex_ddns_auth)."""
    link = "text-xs whitespace-nowrap hover:underline"
    guest = rx.el.div(
        rx.el.span(DicomViewerState.me["name"], id="me-name", class_name="text-xs text-slate-200 whitespace-nowrap"),
        *([rx.el.a("Sign in", href=AuthState.login_url, class_name=f"{link} text-blue-300")] if AuthState is not None else []),
        class_name="flex items-center gap-2",
    )
    if AuthState is None:
        return guest
    signed_in = rx.el.div(
        rx.el.span(AuthState.user_name, id="me-name", class_name="text-xs text-slate-200 whitespace-nowrap max-w-[9rem] truncate"),
        rx.el.a("Sign out", href=AuthState.logout_url, class_name=f"{link} text-slate-400"),
        class_name="flex items-center gap-2",
    )
    return rx.cond(AuthState.is_logged_in, signed_in, guest)


def account() -> rx.Component:
    """The account on the folder page's header."""
    if AuthState is None:
        return rx.fragment()
    return rx.cond(
        AuthState.is_logged_in,
        rx.el.div(
            rx.cond(
                AuthState.user_avatar != "",
                rx.image(src=AuthState.user_avatar, class_name="h-7 w-7 rounded-full"),
            ),
            rx.el.span(AuthState.user_name, class_name="text-sm text-slate-200"),
            rx.el.a("Sign out", href=AuthState.logout_url, class_name="text-xs text-slate-400 hover:underline"),
            class_name="flex items-center gap-2",
        ),
        rx.el.div(
            rx.icon("user", class_name="h-4 w-4 text-slate-500"),
            rx.el.span("Guest", class_name="text-sm text-slate-400"),
            rx.el.a(
                "Sign in",
                href=AuthState.login_url,
                class_name="text-xs px-2.5 py-1 rounded-md bg-blue-600/20 text-blue-300 hover:bg-blue-600/30",
            ),
            class_name="flex items-center gap-2",
        ),
    )


def actor_badge(plane: str = "") -> rx.Component:
    """Who did the last thing, over the picture, fading out; not shown to that person.

    In 3 views each plane has one: it shows what was done to that plane, and what was done
    to all of them (the window, zoom) on the plane the keys move.
    """
    actor = SeriesRoom.actor
    # Only what was done since this tab came, by someone else.
    show = (SeriesRoom.actor_rev > DicomViewerState.actor_seen) & (actor["peer"] != "") & (actor["peer"] != ME)
    if plane:
        show = show & (
            (actor["plane"] == plane) | ((actor["plane"] == "") & (SeriesRoom.active_plane == plane))
        )
    return rx.cond(
        show,
        rx.el.div(
            rx.el.span(class_name="h-2 w-2 rounded-full shrink-0", style={"backgroundColor": actor["color"]}),
            rx.el.span(actor["name"], class_name="font-semibold truncate max-w-[10rem]"),
            rx.el.span("·", class_name="text-slate-500"),
            rx.el.span(actor["text"], class_name="truncate"),
            # A new key restarts the fade.
            key=SeriesRoom.actor_rev.to_string(),
            class_name="dv-actor actor-badge absolute top-2 left-2 z-10 max-w-[calc(100%-1rem)] flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-900/85 border text-xs text-slate-100 pointer-events-none shadow-lg",
            style={"borderColor": actor["color"]},
        ),
    )


_HOOKS_JS = f"""
if (window.__dvPing) clearInterval(window.__dvPing);
window.__dvPing = setInterval(function () {{
    var el = document.getElementById('dv-ping');
    if (!el) {{ clearInterval(window.__dvPing); window.__dvPing = null; return; }}
    el.click();
}}, 5000);
if (!window.__dvBye) {{
    // Closing or reloading the tab: the others stop seeing it at once (otherwise it takes
    // the server a few missed pings to notice). A beacon, because a websocket message doesn't
    // get out of a page that is going away; it goes to the backend the way Reflex's own
    // events do (behind a proxy, on the page's host).
    window.__dvBye = function () {{
        var el = document.getElementById('dv-ping');
        var peer = el && el.dataset.peer;
        if (!peer || !navigator.sendBeacon) return;
        try {{
            var url = new URL('{BYE_PATH}', getBackendURL(env.PING));
            navigator.sendBeacon(url.toString(), JSON.stringify({{ token: getToken(), peer: peer }}));
        }} catch (e) {{}}
    }};
    window.addEventListener('pagehide', window.__dvBye);
}}
"""


def page_hooks() -> rx.Component:
    """Still here: a ping every 5 s keeps this tab among the people on the series."""
    return rx.el.button(
        id="dv-ping",
        on_click=DicomViewerState.ping,
        on_mount=rx.call_script(_HOOKS_JS),
        custom_attrs={"data-peer": ME},
        tab_index=-1,
        aria_hidden="true",
        class_name="sr-only",
    )


STYLE = """
/* Who did the last thing: shown, then faded out (a new key starts it again). */
@keyframes dv-fade { 0%, 75% { opacity: 1; } 100% { opacity: 0; } }
.dv-actor { animation: dv-fade 4s ease-out forwards; }
/* Someone on the series; their ring pulses (in their colour) when they act. */
.dv-person { display: flex; border: 2px solid; border-radius: 9999px; background: #0f172a; margin-left: -6px; }
@keyframes dv-ring { from { box-shadow: 0 0 0 0 currentColor; } to { box-shadow: 0 0 0 6px transparent; } }
.dv-acting { animation: dv-ring 0.9s ease-out 3; }
"""
