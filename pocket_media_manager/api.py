"""HTTP routes for the local web app."""

from pathlib import Path
from urllib.parse import urlencode
from html import escape
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from . import state
from .health import make_health
from .streaming import media_response
from .storage import Conflict, FEEDBACK, OTHER_FEEDBACK
from .service import (
    _db,
    _settings,
    _html,
    _remember_player,
    _recommend,
    _encounter,
    _relative_folder,
    _browse_url,
    _stream_files,
)
from .presentation import _page, _form, _link, _feedback_page, _explore_folder, _explore_file
from .playback import _launch, _launch_mpv, build_vlc_url

router = APIRouter()
APP_ICON_PATH = Path(__file__).resolve().parents[1] / "pocket-manager-icon.png"


@router.get("/health")
def health() -> dict:
    return make_health()


@router.get("/pocket-manager-icon.png")
@router.get("/favicon.ico")
def app_icon() -> FileResponse:
    return FileResponse(APP_ICON_PATH, media_type="image/png")


@router.get("/manifest.webmanifest")
def web_manifest() -> dict:
    return {
        "name": "Pocket Media Manager",
        "short_name": "Pocket Media",
        "start_url": "/",
        "display": "standalone",
        "theme_color": "#f7f7f4",
        "icons": [{"src": "/pocket-manager-icon.png", "sizes": "128x128", "type": "image/png"}],
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    player = request.state.browser["player"]
    body = '<h1>Pocket Media Manager</h1><div class="player-toggle">'
    for value, label in (("vlc", "VLC (Phone)"), ("mpv", "mpv (PC)")):
        body += f'<button type="button" class="secondary{" active" if player == value else ""}" data-player="{value}">{label}</button>'
    body += '</div><div class="stack">'
    body += _form("/recommend", "Recommend", {"player": player}, "")
    for url, label in (
        ("/select", "Recommend with Selections"),
        ("/explore", "Explore"),
        ("/stream", "Stream"),
    ):
        body += (
            f'<form method="get" action="{url}"><button class="secondary">{label}</button></form>'
        )
    for url, label in (
        ("/feedback/addressed", "Address Other Feedback"),
        ("/cleanup", "Clean Up"),
        ("/reset", "Reset Preferences"),
    ):
        body += _link(url, label)
    body += _form("/library/refresh", "Refresh Library")
    body += "</div>"
    return _page("Pocket Media Manager", body)


@router.post("/settings/player")
def remember_player(request: Request, player: str = Form(...)) -> dict:
    return {"player": _remember_player(request, player)}


@router.post("/library/refresh")
def refresh_library(request: Request):
    count = _db(request).refresh_catalog()
    _db(request).backup(daily=True)
    return RedirectResponse("/", 303) if _html(request) else {"media_count": count}


@router.post("/recommend", response_class=HTMLResponse)
def recommend(request: Request, player: str | None = Form(None)):
    _remember_player(request, player)
    encounter = _recommend(request)
    return _feedback_page(request, encounter, _launch(request, encounter))


@router.post("/next")
def next_video(request: Request, player: str | None = Form(None)) -> dict:
    _remember_player(request, player)
    encounter = _recommend(request)
    result = _launch(request, encounter)
    base = _settings(request).public_base_url
    result["feedback"] = {
        key: f"{base}/feedback/{key}" for key in (*FEEDBACK, "no_feedback", "other", "favorite")
    }
    result["feedback_page"] = f"{base}/feedback/{encounter['id']}"
    return result


@router.get("/select", response_class=HTMLResponse)
def select_folders(request: Request):
    rows = _db(request).media_rows()
    folders = sorted(
        {row["relative_path"].split("/", 1)[0] for row in rows if "/" in row["relative_path"]},
        key=str.lower,
    )
    selected = set(request.state.browser["folders"])
    body = '<h1>Recommend with Selections</h1><div class="toolbar row">'
    body += '<button form="selection-form">Recommend</button>' + _link("/", "Home") + "</div>"
    body += f"<p>Folders in <em>{escape(str(_settings(request).media_root))}</em></p>"
    body += '<div class="row"><button class="secondary" type="button" data-select="all">Select All</button><button class="secondary" type="button" data-select="none">Deselect All</button></div>'
    body += '<form id="selection-form" method="post" action="/recommend/selected" class="stack">'
    for folder in folders:
        checked = " checked" if folder in selected else ""
        body += f'<label class="check"><input type="checkbox" name="folders" value="{escape(folder, quote=True)}"{checked}>{escape(folder)}</label>'
    body += "</form>" + _form("/select/cancel", "Cancel and Clear Selections")
    if not folders:
        body += "<p>No eligible folders found. Refresh Library after adding media.</p>"
    return _page("Select Folders", body)


@router.post("/select/cancel")
def cancel_selections(request: Request):
    _db(request).update_browser(request.state.browser_id, folders=[])
    return RedirectResponse("/", 303)


@router.post("/recommend/selected", response_class=HTMLResponse)
def recommend_selected(request: Request, folders: list[str] = Form(default=[])):
    if not folders:
        raise ValueError("Select at least one folder")
    allowed = {
        row["relative_path"].split("/", 1)[0]
        for row in _db(request).media_rows()
        if "/" in row["relative_path"]
    }
    folders = list(dict.fromkeys(folder for folder in folders if folder in allowed))
    if not folders:
        raise ValueError("Select at least one available folder")
    _db(request).update_browser(request.state.browser_id, folders=folders)
    encounter = _recommend(request, folders)
    return _feedback_page(request, encounter, _launch(request, encounter))


@router.get("/explore", response_class=HTMLResponse)
def explore(request: Request, path: str | None = None):
    relative = _relative_folder(request, path)
    prefix = relative + "/" if relative else ""
    folders, files = set(), []
    for row in _db(request).media_rows():
        if not row["relative_path"].startswith(prefix):
            continue
        remainder = row["relative_path"][len(prefix) :]
        if "/" in remainder:
            folders.add(remainder.split("/", 1)[0])
        else:
            files.append(row)
    body = '<h1>Explore</h1><div class="toolbar row">' + _link("/", "Home")
    if relative:
        body += _link(
            _browse_url("explore", relative.rsplit("/", 1)[0] if "/" in relative else ""), "Back"
        )
    body += (
        '</div><p><span class="tag">'
        + escape(relative or str(_settings(request).media_root))
        + "</span></p>"
    )
    if folders:
        body += f'<section class="explore-section" aria-labelledby="explore-folders"><h2 class="explore-heading" id="explore-folders">Folders <span class="explore-count">({len(folders)})</span></h2>'
        for folder in sorted(folders, key=str.lower):
            body += _explore_folder(folder, prefix + folder)
        body += "</section>"
    if files:
        body += f'<section class="explore-section" aria-labelledby="explore-clips"><h2 class="explore-heading" id="explore-clips">Clips <span class="explore-count">({len(files)})</span></h2>'
        for row in files:
            body += _explore_file(row)
        body += "</section>"
    if not folders and not files:
        body += "<p>No eligible media here. Refresh Library after adding files.</p>"
    return _page("Explore", body)


@router.post("/explore/play", response_class=HTMLResponse)
def play_explored(request: Request, media_id: int = Form(...)):
    database = _db(request)
    media = database.media(media_id)
    database.verify_media(media_id)
    if database.blocked(media_id):
        raise ValueError("This file has outstanding Something Else feedback")
    parent = media["relative_path"].rsplit("/", 1)[0] if "/" in media["relative_path"] else ""
    encounter = _encounter(request, media, "explore", _browse_url("explore", parent))
    return _feedback_page(request, encounter, _launch(request, encounter))


@router.get("/stream", response_class=HTMLResponse)
def stream_browse(request: Request, path: str | None = None):
    relative = _relative_folder(request, path, stream=True)
    root = (_settings(request).media_root / _settings(request).stream_folder).resolve()
    current = root / relative
    body = '<h1>Stream</h1><div class="toolbar row">' + _link("/", "Home")
    if relative:
        body += _link(
            _browse_url("stream", relative.rsplit("/", 1)[0] if "/" in relative else ""), "Back"
        )
    body += (
        '</div><p><span class="tag">'
        + escape(relative or str(root))
        + '</span></p><p id="stream-status" aria-live="polite"></p><div class="stack">'
    )
    if current.is_dir():
        descendants = _stream_files(request, current)
        folders = sorted(
            {item.relative_to(current).parts[0] for item in descendants if item.parent != current},
            key=str.lower,
        )
        for folder in folders:
            body += _link(
                _browse_url("stream", (relative + "/" if relative else "") + folder), folder
            )
        for item in sorted(
            (item for item in descendants if item.parent == current), key=lambda p: p.name.lower()
        ):
            value = escape(item.relative_to(root).as_posix(), quote=True)
            body += f'<button class="secondary" type="button" data-stream-path="{value}">{escape(item.name)}</button>'
    else:
        body += "<p>Create the configured Stream folder and put direct-play media there.</p>"
    body += "</div>"
    return _page("Stream", body)


@router.post("/stream/play")
def stream_play(request: Request, path: str = Form(...)) -> dict:
    relative = _relative_folder(request, path, stream=True)
    target = (_settings(request).media_root / _settings(request).stream_folder / relative).resolve()
    if not target.is_file() or target.suffix.lower() not in _settings(request).supported_extensions:
        raise ValueError("Stream file not found or unsupported")
    with _db(request).idle():
        if request.state.browser["player"] == "mpv":
            _launch_mpv(_settings(request), target)
            return {"launched": True}
        token = state.create_stream_token(target)
        return {"player_url": build_vlc_url(f"{_settings(request).public_base_url}/stream/{token}")}


@router.get("/stream/{token}")
@router.head("/stream/{token}")
def stream_media(request: Request, token: str):
    path = state.get_stream_path(token)
    settings = _settings(request)
    if (
        path is None
        or not path.resolve().is_relative_to(settings.media_root)
        or path.resolve().is_relative_to(settings.metadata_root)
        or not path.is_file()
    ):
        raise HTTPException(404, "Playback link expired or file unavailable")
    return media_response(settings.media_root, settings.metadata_root, path, request)


@router.post("/encounters/{encounter_id}/play", response_class=HTMLResponse)
def replay_encounter(request: Request, encounter_id: str):
    encounter = _db(request).encounter(encounter_id)
    if encounter["resolution"] != "open":
        raise Conflict("This encounter is already closed; choose the clip again through Explore")
    return _feedback_page(request, encounter, _launch(request, encounter))


@router.post("/feedback/favorite")
def favorite_feedback(request: Request, encounter_id: str = Form(...), favorite: bool = Form(...)):
    _db(request).preference_for_encounter(encounter_id, "favorite", favorite)
    return (
        RedirectResponse("/feedback/" + encounter_id, 303)
        if _html(request)
        else {"favorite": favorite}
    )


@router.post("/feedback/restore")
def restore_preference(
    request: Request, encounter_id: str = Form(...), preference: str = Form(...)
):
    if preference not in {"less_often", "not_for_me"}:
        raise ValueError("Only frequency or recommendation eligibility can be restored here")
    _db(request).preference_for_encounter(encounter_id, preference, False)
    return (
        RedirectResponse("/feedback/" + encounter_id, 303)
        if _html(request)
        else {"preference": preference, "enabled": False}
    )


@router.post("/feedback/other")
def begin_other(request: Request, encounter_id: str = Form(...)):
    encounter = _db(request).encounter(encounter_id)
    if encounter["resolution"] != "open" or _db(request).blocked(encounter["media_id"]):
        raise Conflict("This encounter is closed or already has Something Else feedback")
    return RedirectResponse("/feedback/other?" + urlencode({"encounter_id": encounter_id}), 303)


@router.get("/feedback/other", response_class=HTMLResponse)
def other_page(request: Request, encounter_id: str):
    encounter = _db(request).encounter(encounter_id)
    if encounter["resolution"] != "open":
        raise Conflict("This encounter is already closed")
    body = (
        "<h1>Describe Change Required</h1><p>"
        + escape(encounter["relative_path"])
        + '</p><div class="stack">'
    )
    for value, label in OTHER_FEEDBACK.items():
        body += _form(
            "/feedback/other/save",
            label,
            {"encounter_id": encounter_id, "feedback_type": value},
            "",
        )
    body += "</div>" + _link("/feedback/" + encounter_id, "Cancel")
    return _page("Describe Change Required", body)


@router.post("/feedback/other/save")
def save_other(request: Request, encounter_id: str = Form(...), feedback_type: str = Form(...)):
    return _feedback_response(request, _db(request).submit_other(encounter_id, feedback_type))


@router.get("/feedback/addressed", response_class=HTMLResponse)
def addressed_page(request: Request):
    tasks = _db(request).tasks()
    body = "<h1>Address Other Feedback</h1>" + _link("/", "Home")
    body += '<form method="post" action="/feedback/addressed" class="stack">'
    for value, label in OTHER_FEEDBACK.items():
        group = [task for task in tasks if task["type"] == value]
        if not group:
            continue
        body += f"<h2>{label}</h2>"
        for task in group:
            path = task["relative_path"]
            folder = path.split("/", 1)[0] if "/" in path else "Media root"
            body += f'<label class="check"><input type="checkbox" name="task_ids" value="{task["id"]}"><span><span class="tag">{escape(folder)}</span> {escape(Path(path).name)}</span></label>'
    body += (
        "<button>Save Addressed Items</button></form>"
        if tasks
        else "</form><p>No outstanding tasks.</p>"
    )
    return _page("Address Other Feedback", body)


@router.post("/feedback/addressed")
def save_addressed(request: Request, task_ids: list[int] = Form(default=[])):
    _db(request).resolve_tasks(task_ids)
    return RedirectResponse("/", 303) if _html(request) else {"ok": True}


@router.get("/feedback/{encounter_id}", response_class=HTMLResponse)
def resume_feedback(request: Request, encounter_id: str):
    return _feedback_page(request, _db(request).encounter(encounter_id))


@router.post("/feedback/{feedback}")
def submit_feedback(request: Request, feedback: str, encounter_id: str = Form(...)):
    return _feedback_response(request, _db(request).submit_feedback(encounter_id, feedback))


@router.get("/cleanup", response_class=HTMLResponse)
def cleanup_page(request: Request):
    rows = _db(request).media_rows(unavailable=True)
    body = "<h1>Clean Up</h1><p>Remove selected unavailable records and their encounter history. Media files are never deleted. Outstanding maintenance tasks are kept.</p>"
    body += _link("/", "Cancel") + '<form method="post" action="/cleanup" class="stack">'
    for row in rows:
        body += f'<label class="check"><input type="checkbox" name="media_ids" value="{row["id"]}">{escape(row["relative_path"])}</label>'
    body += (
        "<button>Clean Selected Records</button></form>"
        if rows
        else "</form><p>No unavailable records. Refresh Library to check for deleted files.</p>"
    )
    return _page("Clean Up", body)


@router.post("/cleanup")
def cleanup_records(request: Request, media_ids: list[int] = Form(default=[])):
    _db(request).cleanup(media_ids)
    return RedirectResponse("/", 303) if _html(request) else {"ok": True}


@router.get("/reset", response_class=HTMLResponse)
def reset_page():
    return _page(
        "Reset Preferences",
        "<h1>Reset Preferences?</h1><p>This clears all Favorites, frequency preferences, disinterest, and encounters. The media catalog and Something Else tasks remain. A database backup is saved first.</p>"
        + _form("/reset", "Reset Preferences", kind="danger")
        + _link("/", "Cancel"),
    )


@router.post("/reset")
def reset_preferences(request: Request):
    _db(request).reset_preferences()
    state.clear_state()
    return RedirectResponse("/", 303) if _html(request) else {"ok": True}


@router.get("/last")
def last(request: Request):
    encounter = _db(request).pending_encounter() or _db(request).latest_encounter()
    if not encounter:
        raise KeyError("No encounter has been made yet")
    return {"encounter": encounter, "media": _db(request).media(encounter["media_id"])}


def _feedback_response(request: Request, encounter: dict) -> dict | RedirectResponse:
    if _html(request):
        return RedirectResponse(encounter["context"]["return_path"], 303)
    return {
        "ok": True,
        "encounter_id": encounter["id"],
        "feedback": encounter["feedback"],
        "resolution": encounter["resolution"],
    }
