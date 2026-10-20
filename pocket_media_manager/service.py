"""Application operations and shared request context."""

from pathlib import Path
import os
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import urlencode
from . import recommender
from .config import Settings
from .storage import Database, FeedbackRequired, FEEDBACK


def _db(request: Request) -> Database:
    return request.app.state.database


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def _feedback_request(request: Request, encounter_id: str) -> bool:
    path = request.url.path
    if request.method == "GET":
        return path in {f"/feedback/{encounter_id}", "/last"} or (
            path == "/feedback/other" and request.query_params.get("encounter_id") == encounter_id
        )
    if request.method == "POST":
        return path == f"/encounters/{encounter_id}/play" or path in {
            "/feedback/" + action
            for action in (*FEEDBACK, "no_feedback", "favorite", "restore", "other", "other/save")
        }
    return False


def _pending_response(request: Request, encounter_id: str):
    path = "/feedback/" + encounter_id
    if _html(request):
        return RedirectResponse(path, 303, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {
            "ok": False,
            "error": {
                "code": "feedback_required",
                "message": str(FeedbackRequired(encounter_id)),
                "encounter_id": encounter_id,
                "feedback_page": _settings(request).public_base_url + path,
            },
        },
        status_code=409,
        headers={"Cache-Control": "no-store"},
    )


def _remember_player(request: Request, player: str | None) -> str:
    if player is not None:
        if player not in {"vlc", "mpv"}:
            raise ValueError("Player must be vlc or mpv")
        request.state.browser = _db(request).update_browser(request.state.browser_id, player=player)
    return request.state.browser["player"]


def _encounter(
    request: Request, media: dict, source: str, return_path: str, folders: list[str] | None = None
) -> dict:
    return _db(request).create_encounter(
        media["id"],
        source,
        {
            "player": request.state.browser["player"],
            "folders": folders or [],
            "return_path": return_path,
        },
    )


def _recommend(request: Request, folders: list[str] | None = None) -> dict:
    database = _db(request)
    candidates = database.candidates()
    if folders is not None:
        chosen = set(folders)
        candidates = [
            row
            for row in candidates
            if "/" in row["relative_path"] and row["relative_path"].split("/", 1)[0] in chosen
        ]
    while candidates:
        selected = recommender.pick_weighted(candidates)
        try:
            database.verify_media(selected["id"])
        except ValueError:
            candidates.remove(selected)
            continue
        return _encounter(
            request,
            selected,
            "selected" if folders is not None else "recommend",
            "/select" if folders is not None else "/",
            folders,
        )
    raise ValueError(
        "No eligible media files were found. Use Explore to restore clip preferences, "
        "or Refresh Library after adding or changing files."
    )


def _relative_folder(request: Request, value: str | None, stream: bool = False) -> str:
    settings = _settings(request)
    root = (
        (settings.media_root / settings.stream_folder).resolve() if stream else settings.media_root
    )
    target = (root / (value or "")).resolve()
    if not target.is_relative_to(root) or target.is_relative_to(settings.metadata_root):
        raise ValueError("Folder path is not allowed")
    if not stream and target.is_relative_to(
        (settings.media_root / settings.stream_folder).resolve()
    ):
        raise ValueError("Use Stream for this folder")
    return target.relative_to(root).as_posix() if target != root else ""


def _browse_url(mode: str, path: str) -> str:
    return "/" + mode + ("?" + urlencode({"path": path}) if path else "")


def _stream_files(request: Request, root: Path) -> list[Path]:
    files = []
    for directory, folders, names in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if (
                path.suffix.lower() in _settings(request).supported_extensions
                and not path.is_symlink()
                and path.is_file()
            ):
                files.append(path)
    return files
