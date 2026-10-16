from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import asynccontextmanager, suppress
from html import escape
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import recommender, state
from .config import Settings, get_settings
from .storage import Conflict, Database, FEEDBACK, OTHER_FEEDBACK


logger = logging.getLogger("pocket_media_manager")
APP_ICON_PATH = Path(__file__).resolve().parents[1] / "pocket-manager-icon.png"
COOKIE_NAME = "pocket_browser"


async def _daily_backups(database: Database) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await run_in_threadpool(database.backup, daily=True)
        except (OSError, sqlite3.Error):
            logger.exception("Automatic backup failed; will retry on the next hourly check")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    database = Database(settings)
    database.initialize()
    handler = RotatingFileHandler(settings.metadata_root / "logs" / "helper.log",
                                  maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    app.state.settings = settings
    app.state.database = database
    backup_task = None
    try:
        count = await run_in_threadpool(database.refresh_catalog)
        await run_in_threadpool(database.backup, daily=True)
        logger.info("Catalog ready: %s media files", count)
        backup_task = asyncio.create_task(_daily_backups(database))
        yield
    finally:
        if backup_task:
            backup_task.cancel()
            with suppress(asyncio.CancelledError):
                await backup_task
        logger.removeHandler(handler)
        handler.close()
        state.clear_state()


app = FastAPI(title="Pocket Media Manager", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def browser_context(request: Request, call_next):
    if (request.url.path in {"/health", "/pocket-manager-icon.png", "/favicon.ico", "/manifest.webmanifest"}
            or (request.url.path.startswith("/stream/") and request.url.path != "/stream/play")):
        return await call_next(request)
    browser_id = request.cookies.get(COOKIE_NAME, "")
    new_cookie = re.fullmatch(r"[0-9a-f]{32}", browser_id) is None
    if new_cookie:
        browser_id = uuid.uuid4().hex
    request.state.browser_id = browser_id
    try:
        request.state.browser = await run_in_threadpool(_db(request).browser, browser_id)
        response = await call_next(request)
    except (OSError, sqlite3.Error) as error:
        logger.exception("Storage is unavailable")
        response = _error(request, 503, f"Media storage is unavailable: {error}")
    if new_cookie:
        response.set_cookie(COOKIE_NAME, browser_id, max_age=365 * 24 * 3600,
                            httponly=True, samesite="lax")
    return response


def _db(request: Request) -> Database:
    return request.app.state.database


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel="icon" type="image/png" href="/pocket-manager-icon.png">
<link rel="apple-touch-icon" href="/pocket-manager-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-title" content="Pocket Media Manager">
<meta name="theme-color" content="#f7f7f4">
<style>
body {{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;
  min-height:100vh;display:grid;place-items:center;background:#f7f7f4;color:#181818}}
main {{width:min(28rem,calc(100vw - 2rem));display:grid;gap:1rem;padding:1rem 0}}
h1 {{font-size:1.45rem;margin:0}} h2 {{font-size:1.05rem;margin:.5rem 0}}
p {{margin:0;line-height:1.45;color:#555}} form {{margin:0}}
.stack {{display:grid;gap:.7rem}} .row {{display:grid;grid-template-columns:1fr 1fr;gap:.7rem}}
.toolbar {{position:sticky;top:0;z-index:1;background:#f7f7f4;padding:.3rem 0}}
button,a.button {{display:block;width:100%;box-sizing:border-box;border:1px solid #181818;
  border-radius:6px;padding:.85rem 1rem;background:#181818;color:white;font:inherit;
  text-align:center;text-decoration:none;cursor:pointer;overflow-wrap:anywhere}}
button.secondary,a.secondary {{background:white;color:#181818}}
button.danger {{background:#9d2020;border-color:#9d2020}}
.check {{display:flex;align-items:center;gap:.55rem;line-height:1.4}}
.tag {{display:inline-block;border:1px solid #bcbcb5;border-radius:999px;padding:.08rem .45rem;font-size:.85rem}}
.notice {{border:1px solid #bcbcb5;border-radius:6px;padding:.8rem;display:grid;gap:.6rem}}
.error {{color:#9d2020}} .player-toggle {{display:flex;gap:.5rem}}
.player-toggle button {{padding:.5rem}} .player-toggle .active {{background:#181818;color:white}}
.explore-section {{display:grid;gap:.65rem}}
.explore-heading {{display:flex;align-items:center;gap:.5rem;margin:.4rem 0 .1rem}}
.explore-count {{font-size:.8rem;font-weight:400;color:#626962}}
a.explore-card,button.explore-card {{display:flex;align-items:center;gap:.85rem;text-align:left;
  border:1px solid #d1d6cf;border-left-width:4px;border-radius:12px;padding:1rem;
  background:#fff;color:#242824;line-height:1.4;box-shadow:0 1px 2px #18181808;
  transition:box-shadow .12s ease}}
.explore-card:hover {{box-shadow:0 3px 12px #18181812}}
.explore-card:focus-visible {{outline:3px solid #365989;outline-offset:3px}}
.explore-icon {{display:grid;place-items:center;flex:0 0 2.25rem;height:2.25rem;
  border-radius:8px;background:#f0f2ee}}
.explore-icon svg {{width:1.25rem;height:1.25rem}}
.explore-content {{display:grid;gap:.25rem;flex:1;min-width:0}}
.explore-name {{font-weight:600;overflow-wrap:anywhere}}
.explore-detail {{font-size:.83rem;color:#626962}}
.explore-chevron {{font-size:1.6rem;line-height:1;flex-shrink:0}}
a.explore-folder {{background:#edf2f9;border-color:#b7c8df;border-left-color:#6c89af;color:#243e61}}
.explore-folder .explore-icon {{background:#dce7f5}}
.explore-folder .explore-detail {{color:#52647c}}
button.explore-file.is-favorite {{background:#edf8f0;border-color:#83b795;
  border-left-color:#42855b;color:#173d27}}
.is-favorite .explore-icon {{background:#d8eddf}}
.is-favorite .explore-detail {{color:#526056}}
button.explore-file.is-not-for-me {{background:#fff0ef;border-color:#d5a19c;
  border-left-color:#b64940;color:#6d2622}}
.is-not-for-me .explore-icon {{background:#f7dad6}}
.is-not-for-me .explore-detail {{color:#73514d}}
.explore-badges {{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.15rem}}
.explore-badge {{display:inline-flex;align-items:center;gap:.25rem;font-size:.75rem;
  font-weight:600;border-radius:999px;padding:.15rem .5rem}}
.badge-favorite {{background:#d8eddf;color:#1d5934}}
.badge-not-for-me {{background:#f7dad6;color:#7c2e25}}
@media (prefers-reduced-motion:reduce) {{a.explore-card,button.explore-card {{transition:none}}}}
</style></head><body><main>{body}</main>
<script>document.querySelectorAll('time[datetime]').forEach(function(el) {{
  el.textContent = new Date(el.dateTime).toLocaleString();
}});</script></body></html>""", status_code=status_code)


def _form(action: str, label: str, fields: dict | None = None, kind: str = "secondary") -> str:
    inputs = "".join(f'<input type="hidden" name="{escape(str(key), quote=True)}" '
                     f'value="{escape(str(value), quote=True)}">' for key, value in (fields or {}).items())
    return f'<form method="post" action="{escape(action, quote=True)}">{inputs}<button class="{kind}">{escape(label)}</button></form>'


def _link(url: str, label: str) -> str:
    return f'<a class="button secondary" href="{escape(url, quote=True)}">{escape(label)}</a>'


def _error(request: Request, code: int, message: str):
    if _html(request):
        return _page("Unable to continue", f'<h1>Unable to continue</h1><p>{escape(message)}</p>'
                     + _link("/", "Home") + _link("/explore", "Explore")
                     + _form("/library/refresh", "Refresh Library"), code)
    return JSONResponse({"detail": message}, status_code=code)


@app.exception_handler(KeyError)
async def missing_record(request: Request, error: KeyError):
    return _error(request, 404, str(error.args[0]))


@app.exception_handler(ValueError)
async def invalid_value(request: Request, error: ValueError):
    return _error(request, 409 if isinstance(error, Conflict) else 400, str(error))


@app.exception_handler(OSError)
@app.exception_handler(sqlite3.Error)
async def unavailable_storage(request: Request, error: Exception):
    logger.error("Storage operation failed: %s", error)
    return _error(request, 503, str(error))


def _remember_player(request: Request, player: str | None) -> str:
    if player is not None:
        if player not in {"vlc", "mpv"}:
            raise ValueError("Player must be vlc or mpv")
        request.state.browser = _db(request).update_browser(request.state.browser_id, player=player)
    return request.state.browser["player"]


def build_vlc_url(stream_url: str) -> str:
    return "vlc-x-callback://x-callback-url/stream?" + urlencode({"url": stream_url})


def _launch_mpv(settings: Settings, path: Path) -> None:
    subprocess.Popen([settings.mpv_path, str(path)])


def _launch(request: Request, encounter: dict) -> dict:
    database = _db(request)
    result = {"encounter_id": encounter["id"], "file_name": Path(encounter["relative_path"]).name,
              "player": encounter["context"]["player"], "launched": False}
    try:
        path = database.verify_media(encounter["media_id"])
        if database.blocked(encounter["media_id"]):
            raise ValueError("This file has outstanding Something Else feedback")
        if result["player"] == "mpv":
            _launch_mpv(_settings(request), path)
            result["launched"] = True
        else:
            token = state.create_stream_token(path)
            result["stream_url"] = f"{_settings(request).public_base_url}/stream/{token}"
            result["player_url"] = build_vlc_url(result["stream_url"])
        database.mark_launch(encounter["id"], request.state.browser_id)
    except (OSError, ValueError) as error:
        message = str(error)
        if isinstance(error, FileNotFoundError) and result["player"] == "mpv":
            message = "mpv was not found. Set MPV_PATH in .env to its full executable path."
        database.mark_launch(encounter["id"], request.state.browser_id, message)
        result["launch_error"] = message
        logger.warning("Playback request failed: %s", message)
    return result


def _encounter(request: Request, media: dict, session_id: str, source: str,
               return_path: str, folders: list[str] | None = None) -> dict:
    return _db(request).create_encounter(media["id"], request.state.browser_id, session_id, source,
        {"player": request.state.browser["player"], "folders": folders or [], "return_path": return_path})


def _recommend(request: Request, folders: list[str] | None = None) -> dict:
    database = _db(request)
    session_id = database.begin_session(request.state.browser_id)
    candidates = database.candidates(session_id)
    if folders is not None:
        chosen = set(folders)
        candidates = [row for row in candidates
                      if "/" in row["relative_path"] and row["relative_path"].split("/", 1)[0] in chosen]
    while candidates:
        selected = recommender.pick_weighted(candidates)
        try:
            database.verify_media(selected["id"])
        except ValueError:
            candidates.remove(selected)
            continue
        return _encounter(request, selected, session_id, "selected" if folders is not None else "recommend",
                          "/select" if folders is not None else "/", folders)
    raise ValueError("No eligible media files were found. Use Explore to restore clip preferences, "
                     "or Refresh Library after adding or changing files.")


def _feedback_page(request: Request, encounter: dict, launch: dict | None = None) -> HTMLResponse:
    identifier = encounter["id"]
    if encounter["resolution"] != "open":
        return _page("Feedback saved", "<h1>Feedback saved</h1><p>This encounter is already closed.</p>"
                     + _link(encounter["context"]["return_path"], "Continue") + _link("/", "Home"))
    media = _db(request).media(encounter["media_id"])
    favorite = media["favorite"]
    body = f'<h1>Feedback</h1><p>{escape(Path(encounter["relative_path"]).name)}</p>'
    error = launch.get("launch_error") if launch else encounter["launch_error"]
    if error:
        body += f'<p class="error">{escape(error)}</p>'
    if launch and launch.get("player_url"):
        url = json.dumps(launch["player_url"]).replace("<", "\\u003c")
        body += f'<script>setTimeout(function() {{window.location.href={url};}},100);</script>'
    body += _form(f"/encounters/{identifier}/play", "Open Player")
    if not media["not_for_me"]:
        body += _form("/feedback/favorite", "★ Favorite — remove" if favorite else "☆ Mark as Favorite",
                      {"encounter_id": identifier, "favorite": "0" if favorite else "1"})
    else:
        body += '<p>Allow recommendations again before marking this clip as Favorite.</p>'
    for preference, label in (("less_often", "Restore normal frequency"),
                              ("not_for_me", "Allow recommendations again")):
        if media[preference]:
            body += _form("/feedback/restore", label,
                          {"encounter_id": identifier, "preference": preference})
    body += '<div class="stack">'
    for value, label in FEEDBACK.items():
        body += _form("/feedback/" + value, label, {"encounter_id": identifier}, "")
    body += _form("/feedback/no_feedback", "No feedback", {"encounter_id": identifier})
    if not _db(request).blocked(encounter["media_id"]):
        body += _form("/feedback/other", "Something Else", {"encounter_id": identifier})
    body += '</div>' + _link("/", "Home")
    return _page("Feedback", body)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/pocket-manager-icon.png")
@app.get("/favicon.ico")
def app_icon() -> FileResponse:
    return FileResponse(APP_ICON_PATH, media_type="image/png")


@app.get("/manifest.webmanifest")
def web_manifest() -> dict:
    return {"name": "Pocket Media Manager", "short_name": "Pocket Media", "start_url": "/",
            "display": "standalone", "theme_color": "#f7f7f4",
            "icons": [{"src": "/pocket-manager-icon.png", "sizes": "128x128", "type": "image/png"}]}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    player = request.state.browser["player"]
    body = '<h1>Pocket Media Manager</h1><div class="player-toggle">'
    for value, label in (("vlc", "VLC (Phone)"), ("mpv", "mpv (PC)")):
        body += f'<button type="button" class="secondary{" active" if player == value else ""}" data-player="{value}">{label}</button>'
    body += '</div><div class="stack">'
    pending = _db(request).latest_encounter(request.state.browser_id, only_open=True)
    if pending:
        body += '<div class="notice"><p>Unfinished feedback: ' + escape(Path(pending["relative_path"]).name) + '</p>'
        body += _link("/feedback/" + pending["id"], "Resume Feedback")
        body += _form("/feedback/no_feedback", "Close without feedback", {"encounter_id": pending["id"]})
        body += _link("/encounters/open", "All Unfinished Feedback") + '</div>'
    body += _form("/recommend", "Recommend", {"player": player}, "")
    for url, label in (("/select", "Recommend with Selections"), ("/explore", "Explore"), ("/stream", "Stream")):
        body += f'<form method="get" action="{url}"><input type="hidden" name="player" value="{player}"><button class="secondary">{label}</button></form>'
    for url, label in (("/feedback/addressed", "Address Other Feedback"), ("/cleanup", "Clean Up"), ("/reset", "Reset Preferences")):
        body += _link(url, label)
    body += _form("/library/refresh", "Refresh Library") + _form("/session/new", "Start New Session")
    body += '</div><script>document.querySelectorAll("[data-player]").forEach(function(btn) {'
    body += '''btn.addEventListener("click",function() {
      var body=new URLSearchParams(); body.set("player",btn.dataset.player);
      fetch("/settings/player",{method:"POST",body:body}).then(function(response) {
        if(!response.ok) throw new Error("Player could not be saved");
        document.querySelectorAll("[data-player]").forEach(function(b){b.classList.toggle("active",b===btn);});
        document.querySelectorAll('input[name="player"]').forEach(function(input){input.value=btn.dataset.player;});
      }).catch(function(error){alert(error.message);});
    });});</script>'''
    return _page("Pocket Media Manager", body)


@app.post("/settings/player")
def remember_player(request: Request, player: str = Form(...)) -> dict:
    return {"player": _remember_player(request, player)}


@app.post("/session/new")
def new_session(request: Request):
    session_id = _db(request).begin_session(request.state.browser_id, force=True)
    return RedirectResponse("/", 303) if _html(request) else {"session_id": session_id}


@app.post("/library/refresh")
def refresh_library(request: Request):
    count = _db(request).refresh_catalog()
    _db(request).backup(daily=True)
    return RedirectResponse("/", 303) if _html(request) else {"media_count": count}


@app.post("/recommend", response_class=HTMLResponse)
def recommend(request: Request, player: str | None = Form(None)):
    _remember_player(request, player)
    encounter = _recommend(request)
    return _feedback_page(request, encounter, _launch(request, encounter))


@app.get("/next")
def next_video(request: Request, player: str | None = None) -> dict:
    _remember_player(request, player)
    encounter = _recommend(request)
    result = _launch(request, encounter)
    base = _settings(request).public_base_url
    result["feedback"] = {key: f"{base}/feedback/{key}" for key in (*FEEDBACK, "no_feedback", "other", "favorite")}
    result["feedback_page"] = f"{base}/feedback/{encounter['id']}"
    return result


@app.get("/select", response_class=HTMLResponse)
def select_folders(request: Request, player: str | None = None):
    _remember_player(request, player)
    rows = _db(request).media_rows()
    folders = sorted({row["relative_path"].split("/", 1)[0] for row in rows if "/" in row["relative_path"]}, key=str.lower)
    selected = set(request.state.browser["folders"])
    body = '<h1>Recommend with Selections</h1><div class="toolbar row">'
    body += '<button form="selection-form">Recommend</button>' + _link("/", "Home") + '</div>'
    body += f'<p>Folders in <em>{escape(str(_settings(request).media_root))}</em></p>'
    body += '<div class="row"><button class="secondary" type="button" data-select="all">Select All</button><button class="secondary" type="button" data-select="none">Deselect All</button></div>'
    body += '<form id="selection-form" method="post" action="/recommend/selected" class="stack">'
    for folder in folders:
        checked = " checked" if folder in selected else ""
        body += f'<label class="check"><input type="checkbox" name="folders" value="{escape(folder, quote=True)}"{checked}>{escape(folder)}</label>'
    body += '</form>' + _form("/select/cancel", "Cancel and Clear Selections")
    if not folders:
        body += '<p>No eligible folders found. Refresh Library after adding media.</p>'
    body += _select_script("folders")
    return _page("Select Folders", body)


def _select_script(name: str) -> str:
    return '<script>document.querySelectorAll("[data-select]").forEach(function(btn){btn.addEventListener("click",function(){document.querySelectorAll(\'input[name="' + name + '"]\').forEach(function(input){input.checked=btn.dataset.select==="all";});});});</script>'


@app.post("/select/cancel")
def cancel_selections(request: Request):
    _db(request).update_browser(request.state.browser_id, folders=[])
    return RedirectResponse("/", 303)


@app.post("/recommend/selected", response_class=HTMLResponse)
def recommend_selected(request: Request, folders: list[str] = Form(default=[])):
    if not folders:
        raise ValueError("Select at least one folder")
    allowed = {row["relative_path"].split("/", 1)[0] for row in _db(request).media_rows()
               if "/" in row["relative_path"]}
    folders = list(dict.fromkeys(folder for folder in folders if folder in allowed))
    if not folders:
        raise ValueError("Select at least one available folder")
    _db(request).update_browser(request.state.browser_id, folders=folders)
    encounter = _recommend(request, folders)
    return _feedback_page(request, encounter, _launch(request, encounter))


def _relative_folder(request: Request, value: str | None, stream: bool = False) -> str:
    settings = _settings(request)
    root = (settings.media_root / settings.stream_folder).resolve() if stream else settings.media_root
    target = (root / (value or "")).resolve()
    if not target.is_relative_to(root) or target.is_relative_to(settings.metadata_root):
        raise ValueError("Folder path is not allowed")
    if not stream and target.is_relative_to((settings.media_root / settings.stream_folder).resolve()):
        raise ValueError("Use Stream for this folder")
    return target.relative_to(root).as_posix() if target != root else ""


def _browse_url(mode: str, path: str) -> str:
    return "/" + mode + ("?" + urlencode({"path": path}) if path else "")


def _explore_folder(name: str, path: str) -> str:
    url = escape(_browse_url("explore", path), quote=True)
    return f'''<a class="button explore-card explore-folder" href="{url}">
<span class="explore-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"
stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
focusable="false"><path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9H3Z"/></svg></span>
<span class="explore-content"><span class="explore-name">{escape(name)}</span>
<span class="explore-detail">Open folder</span></span>
<span class="explore-chevron" aria-hidden="true">›</span></a>'''


def _explore_file(media: dict) -> str:
    tone = "is-not-for-me" if media["not_for_me"] else "is-favorite" if media["favorite"] else ""
    badges = ""
    if media["favorite"] and not media["not_for_me"]:
        badges += '<span class="explore-badge badge-favorite"><span aria-hidden="true">★</span>Favorite</span>'
    if media["not_for_me"]:
        badges += '<span class="explore-badge badge-not-for-me">Not for me</span>'
    if badges:
        badges = f'<span class="explore-badges">{badges}</span>'
    return f'''<form method="post" action="/explore/play">
<input type="hidden" name="media_id" value="{media['id']}">
<button type="submit" class="explore-card explore-file {tone}">
<span class="explore-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"
focusable="false"><path d="M8 5v14l11-7Z"/></svg></span>
<span class="explore-content"><span class="explore-name">{escape(Path(media['relative_path']).name)}</span>
<span class="explore-detail">Play clip</span>{badges}</span></button></form>'''


@app.get("/explore", response_class=HTMLResponse)
def explore(request: Request, path: str | None = None, player: str | None = None):
    _remember_player(request, player)
    relative = _relative_folder(request, path)
    prefix = relative + "/" if relative else ""
    folders, files = set(), []
    for row in _db(request).media_rows():
        if not row["relative_path"].startswith(prefix):
            continue
        remainder = row["relative_path"][len(prefix):]
        if "/" in remainder:
            folders.add(remainder.split("/", 1)[0])
        else:
            files.append(row)
    body = '<h1>Explore</h1><div class="toolbar row">' + _link("/", "Home")
    if relative:
        body += _link(_browse_url("explore", relative.rsplit("/", 1)[0] if "/" in relative else ""), "Back")
    body += '</div><p>Browsing <em>' + escape(relative or str(_settings(request).media_root)) + '</em></p>'
    body += '<p>Open a folder to browse. Playing a clip opens your player and feedback.</p>'
    if folders:
        body += f'<section class="explore-section" aria-labelledby="explore-folders"><h2 class="explore-heading" id="explore-folders">Folders <span class="explore-count">({len(folders)})</span></h2>'
        for folder in sorted(folders, key=str.lower):
            body += _explore_folder(folder, prefix + folder)
        body += '</section>'
    if files:
        body += f'<section class="explore-section" aria-labelledby="explore-clips"><h2 class="explore-heading" id="explore-clips">Clips <span class="explore-count">({len(files)})</span></h2>'
        for row in files:
            body += _explore_file(row)
        body += '</section>'
    if not folders and not files:
        body += '<p>No eligible media here. Refresh Library after adding files.</p>'
    return _page("Explore", body)


@app.post("/explore/play", response_class=HTMLResponse)
def play_explored(request: Request, media_id: int = Form(...)):
    database = _db(request)
    media = database.media(media_id)
    database.verify_media(media_id)
    if database.blocked(media_id):
        raise ValueError("This file has outstanding Something Else feedback")
    session_id = database.begin_session(request.state.browser_id)
    parent = media["relative_path"].rsplit("/", 1)[0] if "/" in media["relative_path"] else ""
    encounter = _encounter(request, media, session_id, "explore", _browse_url("explore", parent))
    return _feedback_page(request, encounter, _launch(request, encounter))


def _stream_files(request: Request, root: Path) -> list[Path]:
    files = []
    for directory, folders, names in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if path.suffix.lower() in _settings(request).supported_extensions and not path.is_symlink() and path.is_file():
                files.append(path)
    return files


@app.get("/stream", response_class=HTMLResponse)
def stream_browse(request: Request, path: str | None = None, player: str | None = None):
    _remember_player(request, player)
    relative = _relative_folder(request, path, stream=True)
    root = (_settings(request).media_root / _settings(request).stream_folder).resolve()
    current = root / relative
    body = '<h1>Stream</h1><div class="toolbar row">' + _link("/", "Home")
    if relative:
        body += _link(_browse_url("stream", relative.rsplit("/", 1)[0] if "/" in relative else ""), "Back")
    body += '</div><p>Browsing <em>' + escape(relative or str(root)) + '</em></p><p id="stream-status" aria-live="polite"></p><div class="stack">'
    if current.is_dir():
        descendants = _stream_files(request, current)
        folders = sorted({item.relative_to(current).parts[0] for item in descendants if item.parent != current}, key=str.lower)
        for folder in folders:
            body += _link(_browse_url("stream", (relative + "/" if relative else "") + folder), folder)
        for item in sorted((item for item in descendants if item.parent == current), key=lambda p: p.name.lower()):
            value = escape(item.relative_to(root).as_posix(), quote=True)
            body += f'<button class="secondary" type="button" data-stream-path="{value}">{escape(item.name)}</button>'
    else:
        body += '<p>Create the configured Stream folder and put direct-play media there.</p>'
    body += '''</div><script>document.querySelectorAll("[data-stream-path]").forEach(function(btn){
      btn.addEventListener("click",function(){var status=document.getElementById("stream-status");
      status.textContent="Opening player…";var body=new URLSearchParams();body.set("path",btn.dataset.streamPath);
      fetch("/stream/play",{method:"POST",body:body}).then(function(response){
        if(!response.ok) throw new Error("Could not open this file");return response.json();
      }).then(function(payload){if(payload.player_url)window.location.href=payload.player_url;
        status.textContent=payload.launched?"Opened in mpv.":"";
      }).catch(function(error){status.textContent=error.message;});});});</script>'''
    return _page("Stream", body)


@app.post("/stream/play")
def stream_play(request: Request, path: str = Form(...)) -> dict:
    relative = _relative_folder(request, path, stream=True)
    target = (_settings(request).media_root / _settings(request).stream_folder / relative).resolve()
    if not target.is_file() or target.suffix.lower() not in _settings(request).supported_extensions:
        raise ValueError("Stream file not found or unsupported")
    if request.state.browser["player"] == "mpv":
        _launch_mpv(_settings(request), target)
        return {"launched": True}
    token = state.create_stream_token(target)
    return {"player_url": build_vlc_url(f"{_settings(request).public_base_url}/stream/{token}")}


@app.get("/stream/{token}")
@app.head("/stream/{token}")
def stream_media(request: Request, token: str) -> FileResponse:
    path = state.get_stream_path(token)
    settings = _settings(request)
    if (path is None or not path.resolve().is_relative_to(settings.media_root)
            or path.resolve().is_relative_to(settings.metadata_root) or not path.is_file()):
        raise HTTPException(404, "Playback link expired or file unavailable")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/encounters/open", response_class=HTMLResponse)
def open_encounters(request: Request):
    with _db(request).connect() as conn:
        rows = list(conn.execute("""SELECT e.id,e.selected_at,m.relative_path FROM encounters e
            JOIN media m ON m.id=e.media_id WHERE e.browser_id=? AND e.resolution='open'
            ORDER BY selected_at DESC""", (request.state.browser_id,)))
    body = '<h1>Unfinished Feedback</h1>' + _link("/", "Home")
    for row in rows:
        body += '<div class="notice"><p>' + escape(row["relative_path"]) + '</p>'
        body += f'<p><time datetime="{row["selected_at"]}"></time></p>'
        body += _link("/feedback/" + row["id"], "Resume")
        body += _form("/feedback/no_feedback", "No feedback", {"encounter_id": row["id"]}) + '</div>'
    if not rows:
        body += '<p>No unfinished feedback.</p>'
    return _page("Unfinished Feedback", body)


@app.post("/encounters/{encounter_id}/play", response_class=HTMLResponse)
def replay_encounter(request: Request, encounter_id: str):
    encounter = _db(request).encounter(encounter_id, request.state.browser_id)
    if encounter["resolution"] != "open":
        raise Conflict("This encounter is already closed; choose the clip again through Explore")
    return _feedback_page(request, encounter, _launch(request, encounter))


@app.post("/feedback/favorite")
def favorite_feedback(request: Request, encounter_id: str = Form(...), favorite: bool = Form(...)):
    _db(request).preference_for_encounter(encounter_id, request.state.browser_id, "favorite", favorite)
    return RedirectResponse("/feedback/" + encounter_id, 303) if _html(request) else {"favorite": favorite}


@app.post("/feedback/restore")
def restore_preference(request: Request, encounter_id: str = Form(...), preference: str = Form(...)):
    if preference not in {"less_often", "not_for_me"}:
        raise ValueError("Only frequency or recommendation eligibility can be restored here")
    _db(request).preference_for_encounter(encounter_id, request.state.browser_id, preference, False)
    return (RedirectResponse("/feedback/" + encounter_id, 303) if _html(request)
            else {"preference": preference, "enabled": False})


@app.post("/feedback/other")
def begin_other(request: Request, encounter_id: str = Form(...)):
    encounter = _db(request).encounter(encounter_id, request.state.browser_id)
    if encounter["resolution"] != "open" or _db(request).blocked(encounter["media_id"]):
        raise Conflict("This encounter is closed or already has Something Else feedback")
    return RedirectResponse("/feedback/other?" + urlencode({"encounter_id": encounter_id}), 303)


@app.get("/feedback/other", response_class=HTMLResponse)
def other_page(request: Request, encounter_id: str):
    encounter = _db(request).encounter(encounter_id, request.state.browser_id)
    if encounter["resolution"] != "open":
        raise Conflict("This encounter is already closed")
    body = '<h1>Describe Change Required</h1><p>' + escape(encounter["relative_path"]) + '</p><div class="stack">'
    for value, label in OTHER_FEEDBACK.items():
        body += _form("/feedback/other/save", label, {"encounter_id": encounter_id, "feedback_type": value}, "")
    body += '</div>' + _link("/feedback/" + encounter_id, "Cancel")
    return _page("Describe Change Required", body)


def _feedback_response(request: Request, encounter: dict) -> dict | RedirectResponse:
    if _html(request):
        return RedirectResponse(encounter["context"]["return_path"], 303)
    return {"ok": True, "encounter_id": encounter["id"], "feedback": encounter["feedback"],
            "resolution": encounter["resolution"]}


@app.post("/feedback/other/save")
def save_other(request: Request, encounter_id: str = Form(...), feedback_type: str = Form(...)):
    return _feedback_response(request, _db(request).submit_other(encounter_id, request.state.browser_id, feedback_type))


@app.get("/feedback/addressed", response_class=HTMLResponse)
def addressed_page(request: Request):
    tasks = _db(request).tasks()
    body = '<h1>Address Other Feedback</h1>' + _link("/", "Home")
    body += '<form method="post" action="/feedback/addressed" class="stack">'
    for value, label in OTHER_FEEDBACK.items():
        group = [task for task in tasks if task["type"] == value]
        if not group:
            continue
        body += f'<h2>{label}</h2>'
        for task in group:
            path = task["relative_path"]
            folder = path.split("/", 1)[0] if "/" in path else "MEDIA_ROOT"
            body += f'<label class="check"><input type="checkbox" name="task_ids" value="{task["id"]}"><span><span class="tag">{escape(folder)}</span> {escape(Path(path).name)}</span></label>'
    body += '<button>Save Addressed Items</button></form>' if tasks else '</form><p>No outstanding tasks.</p>'
    return _page("Address Other Feedback", body)


@app.post("/feedback/addressed")
def save_addressed(request: Request, task_ids: list[int] = Form(default=[])):
    _db(request).resolve_tasks(task_ids)
    return RedirectResponse("/", 303) if _html(request) else {"ok": True}


@app.get("/feedback/{encounter_id}", response_class=HTMLResponse)
def resume_feedback(request: Request, encounter_id: str):
    return _feedback_page(request, _db(request).encounter(encounter_id, request.state.browser_id))


@app.post("/feedback/{feedback}")
def submit_feedback(request: Request, feedback: str, encounter_id: str = Form(...)):
    return _feedback_response(request, _db(request).submit_feedback(encounter_id, request.state.browser_id, feedback))


@app.get("/cleanup", response_class=HTMLResponse)
def cleanup_page(request: Request):
    rows = _db(request).media_rows(unavailable=True)
    body = '<h1>Clean Up</h1><p>Remove selected unavailable records and their encounter history. Media files are never deleted. Outstanding maintenance tasks are kept.</p>'
    body += _link("/", "Cancel") + '<form method="post" action="/cleanup" class="stack">'
    for row in rows:
        body += f'<label class="check"><input type="checkbox" name="media_ids" value="{row["id"]}">{escape(row["relative_path"])}</label>'
    body += '<button>Clean Selected Records</button></form>' if rows else '</form><p>No unavailable records. Refresh Library to check for deleted files.</p>'
    return _page("Clean Up", body)


@app.post("/cleanup")
def cleanup_records(request: Request, media_ids: list[int] = Form(default=[])):
    _db(request).cleanup(media_ids)
    return RedirectResponse("/", 303) if _html(request) else {"ok": True}


@app.get("/reset", response_class=HTMLResponse)
def reset_page():
    return _page("Reset Preferences", '<h1>Reset Preferences?</h1><p>This clears all Favorites, frequency preferences, disinterest, and encounters. The media catalog and Something Else tasks remain. A database backup is saved first.</p>'
                 + _form("/reset", "Reset Preferences", kind="danger") + _link("/", "Cancel"))


@app.post("/reset")
def reset_preferences(request: Request):
    _db(request).reset_preferences()
    state.clear_state()
    return RedirectResponse("/", 303) if _html(request) else {"ok": True}


@app.get("/last")
def last(request: Request):
    encounter = _db(request).latest_encounter(request.state.browser_id)
    if not encounter:
        raise KeyError("No encounter has been made in this browser")
    return {"encounter": encounter, "media": _db(request).media(encounter["media_id"])}
