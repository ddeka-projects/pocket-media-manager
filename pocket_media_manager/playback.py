"""Player launch and temporary VLC links."""

import logging
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from fastapi import Request
from . import state
from .config import Settings
from .service import _db, _settings

logger = logging.getLogger(__name__)


def build_vlc_url(stream_url: str) -> str:
    return "vlc-x-callback://x-callback-url/stream?" + urlencode({"url": stream_url})


def _launch_mpv(settings: Settings, path: Path) -> None:
    subprocess.Popen([settings.mpv_path, str(path)])


def _launch(request: Request, encounter: dict) -> dict:
    database = _db(request)
    result = {
        "encounter_id": encounter["id"],
        "file_name": Path(encounter["relative_path"]).name,
        "player": encounter["context"]["player"],
        "launched": False,
    }
    preparation_error = None
    try:
        path = database.verify_media(encounter["media_id"])
        if database.blocked(encounter["media_id"]):
            raise ValueError("This file has outstanding Something Else feedback")
    except (OSError, ValueError) as error:
        preparation_error = error
    with database.playback(encounter["id"]) as conn:
        try:
            if preparation_error:
                raise preparation_error
            if result["player"] == "mpv":
                _launch_mpv(_settings(request), path)
                result["launched"] = True
            else:
                token = state.create_stream_token(path)
                result["stream_url"] = f"{_settings(request).public_base_url}/stream/{token}"
                result["player_url"] = build_vlc_url(result["stream_url"])
        except (OSError, ValueError) as error:
            message = str(error)
            if (
                isinstance(error, FileNotFoundError)
                and result["player"] == "mpv"
                and preparation_error is None
            ):
                message = "mpv was not found. Set POCKET_MEDIA_MANAGER_MPV_PATH in .env to its full executable path."
            result["launch_error"] = message
            logger.warning("Playback request failed: %s", message)
        database.mark_launch(conn, encounter["id"], result.get("launch_error"))
    return result
