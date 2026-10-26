"""Server-rendered pages; browser styles and navigation live in static/."""

from html import escape
from pathlib import Path
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from .service import _db, _settings, _html, _browse_url
from .storage import FEEDBACK


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel="icon" type="image/png" href="/pocket-manager-icon.png">
<link rel="apple-touch-icon" href="/pocket-manager-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-title" content="Pocket Media Manager">
<meta name="theme-color" content="#f7f7f4">
<link rel="stylesheet" href="/static/styles.css"></head><body><main>{body}</main>
<script src="/static/app.js" defer></script></body></html>""",
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


# Button kinds: "" (neutral), "primary" (one per screen), "quiet", "pill", "compact", "danger".
def _form(action: str, label: str, fields: dict | None = None, kind: str = "") -> str:
    inputs = "".join(
        f'<input type="hidden" name="{escape(str(key), quote=True)}" '
        f'value="{escape(str(value), quote=True)}">'
        for key, value in (fields or {}).items()
    )
    return f'<form method="post" action="{escape(action, quote=True)}">{inputs}<button class="{kind}">{escape(label)}</button></form>'


def _link(url: str, label: str, kind: str = "") -> str:
    return f'<a class="button {kind}" href="{escape(url, quote=True)}">{escape(label)}</a>'


def _select_all_row() -> str:
    # app.js ticks or clears every checkbox on the page.
    return (
        '<div class="row"><button class="compact" type="button" data-select="all">Select All</button>'
        '<button class="compact" type="button" data-select="none">Deselect All</button></div>'
    )


def _media_label(relative_path: str) -> str:
    """Top-level folder as a tag, then the file name (file names are unique in the library)."""
    folder = relative_path.split("/", 1)[0] if "/" in relative_path else "Media root"
    return (
        f'<span><span class="tag">{escape(folder)}</span> {escape(Path(relative_path).name)}</span>'
    )


def _error(request: Request, code: int, message: str, error_code: str | None = None):
    if _html(request):
        pending = getattr(request.state, "pending_encounter", None)
        navigation = (
            _link("/feedback/" + pending["id"], "Back to Feedback", "primary")
            if pending
            else _link("/", "Home")
            + _link("/explore", "Explore")
            + _form("/library/refresh", "Refresh Library")
        )
        return _page(
            "Unable to continue",
            f"<h1>Unable to continue</h1><p>{escape(message)}</p>" + navigation,
            code,
        )
    return JSONResponse(
        {"ok": False, "error": {"code": error_code or "invalid_request", "message": message}},
        status_code=code,
    )


def _feedback_page(request: Request, encounter: dict, launch: dict | None = None) -> HTMLResponse:
    identifier = encounter["id"]
    if encounter["resolution"] != "open":
        return _page(
            "Feedback saved",
            "<h1>Feedback saved</h1><p>This encounter is already closed.</p>"
            + _link(encounter["context"]["return_path"], "Continue", "primary")
            + _link("/", "Home"),
        )
    media = _db(request).media(encounter["media_id"])
    favorite = media["favorite"]
    body = f'<h1>Feedback</h1><p>{escape(Path(encounter["relative_path"]).name)}</p>'
    body += "<p>Close this feedback to continue on any device. Choosing No feedback is fine.</p>"
    body += f'<span hidden data-feedback-path="/feedback/{identifier}"></span>'
    error = launch.get("launch_error") if launch else encounter["launch_error"]
    if error:
        body += f'<p class="error">{escape(error)}</p>'
    if launch and launch.get("player_url"):
        body += f'<span hidden data-player-url="{escape(launch["player_url"], quote=True)}"></span>'
    player_label = "mpv (PC)" if encounter["context"]["player"] == "mpv" else "VLC (Phone)"
    body += '<div class="feedback-actions">'
    body += _form(f"/encounters/{identifier}/play", "Open Player — " + player_label)
    if not media["not_for_me"]:
        body += _form(
            "/feedback/favorite",
            "★ Favorite" if favorite else "☆ Favorite",
            {"encounter_id": identifier, "favorite": "0" if favorite else "1"},
            "favorite-toggle is-on" if favorite else "favorite-toggle",
        )
    body += "</div>"
    if media["not_for_me"]:
        body += "<p>Allow recommendations again before marking this clip as Favorite.</p>"
    for preference, label in (
        ("less_often", "Restore normal frequency"),
        ("not_for_me", "Allow recommendations again"),
    ):
        if media[preference]:
            body += _form(
                "/feedback/restore",
                label,
                {"encounter_id": identifier, "preference": preference},
                "compact",
            )
    body += '<h2 class="section-label">How was it?</h2><div class="choice-grid lead">'
    for value, label in FEEDBACK.items():
        kind = "choice choice-not-for-me" if value == "not_for_me" else "choice"
        body += _form("/feedback/" + value, label, {"encounter_id": identifier}, kind)
    body += '</div><div class="quiet-row">'
    body += _form("/feedback/no_feedback", "No feedback", {"encounter_id": identifier}, "quiet")
    if not _db(request).blocked(encounter["media_id"]):
        body += _form("/feedback/other", "Something Else", {"encounter_id": identifier}, "quiet")
    body += "</div>"
    return _page("Feedback", body)


def _line_icon(*paths: str) -> str:
    drawing = "".join(f'<path d="{path}"/>' for path in paths)
    return f"""<span class="explore-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"
stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
focusable="false">{drawing}</svg></span>"""


FOLDER_ICON = _line_icon("M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9H3Z")
SHUFFLE_ICON = _line_icon("M16 4h4v4", "M4 20 20 4", "M20 16v4h-4", "M15 15l5 5", "M4 4l5 5")
CHECKLIST_ICON = _line_icon(
    "M3.5 6.5 5 8l2.5-2.5", "M3.5 13.5 5 15l2.5-2.5", "M11 7h10", "M11 14h10", "M11 20h10"
)
STREAM_ICON = _line_icon(
    "M4 8h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z", "M8 3l4 4 4-4"
)
PLAY_ICON = """<span class="explore-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"
focusable="false"><path d="M8 5v14l11-7Z"/></svg></span>"""


def _explore_section(key: str, title: str, cards: list[str]) -> str:
    return (
        f'<section class="explore-section" aria-labelledby="explore-{key}">'
        f'<h2 class="explore-heading" id="explore-{key}">{title} '
        f'<span class="explore-count">({len(cards)})</span></h2>{"".join(cards)}</section>'
    )


def _explore_folder(name: str, path: str, mode: str = "explore") -> str:
    url = escape(_browse_url(mode, path), quote=True)
    return f"""<a class="button explore-card explore-folder" href="{url}">
{FOLDER_ICON}
<span class="explore-content"><span class="explore-name">{escape(name)}</span></span>
<span class="explore-chevron" aria-hidden="true">›</span></a>"""


def _home_card(url: str, icon: str, name: str, detail: str) -> str:
    return f"""<a class="button explore-card" href="{escape(url, quote=True)}">
{icon}
<span class="explore-content"><span class="explore-name">{escape(name)}</span>
<span class="explore-detail">{escape(detail)}</span></span>
<span class="explore-chevron" aria-hidden="true">›</span></a>"""


def _select_folder(name: str, clips: int, checked: bool) -> str:
    # The real checkbox stays in the label so tapping the card toggles it; CSS styles the state.
    state = " checked" if checked else ""
    count = f"{clips} clip" + ("" if clips == 1 else "s")
    return f"""<label class="explore-card select-card">
<input class="select-input" type="checkbox" name="folders" value="{escape(name, quote=True)}"{state}>
{FOLDER_ICON}
<span class="explore-content"><span class="explore-name">{escape(name)}</span>
<span class="explore-detail">{count}</span></span>
<span class="select-mark" aria-hidden="true">✓</span></label>"""


def _explore_file(media: dict) -> str:
    tone = "is-not-for-me" if media["not_for_me"] else "is-favorite" if media["favorite"] else ""
    badges = ""
    if media["favorite"] and not media["not_for_me"]:
        badges += '<span class="explore-badge badge-favorite"><span aria-hidden="true">★</span>Favorite</span>'
    if media["not_for_me"]:
        badges += '<span class="explore-badge badge-not-for-me">Not for me</span>'
    if badges:
        badges = f'<span class="explore-badges">{badges}</span>'
    return f"""<form method="post" action="/explore/play">
<input type="hidden" name="media_id" value="{media['id']}">
<button type="submit" class="explore-card explore-file {tone}">
{PLAY_ICON}
<span class="explore-content"><span class="explore-name">{escape(Path(media['relative_path']).name)}</span>
{badges}</span></button></form>"""


def _stream_file(name: str, path: str) -> str:
    # app.js handles data-stream-path clicks; Stream has no feedback, so no form or badges.
    return f"""<button type="button" class="explore-card explore-file" data-stream-path="{escape(path, quote=True)}">
{PLAY_ICON}
<span class="explore-content"><span class="explore-name">{escape(name)}</span></span></button>"""
