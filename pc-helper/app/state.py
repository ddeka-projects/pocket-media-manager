from __future__ import annotations

import secrets
from pathlib import Path
from threading import RLock


LAST_RECOMMENDED_PATH: Path | None = None
MAX_STREAM_TOKENS = 30
STREAM_TOKENS: dict[str, Path] = {}
STREAM_TOKENS_LOCK = RLock()
AWAITING_FEEDBACK = False
AWAITING_OTHER_FEEDBACK = False
SELECTED_FOLDER_NAMES: list[str] = []
FEEDBACK_RETURN_PATH = "/"
SUPPORTED_PLAYERS = {"vlc", "mpv"}
ACTIVE_PLAYER = "vlc"
ACTIVE_PROFILE = "balanced"


def set_last_recommended(file_path: Path) -> None:
    global LAST_RECOMMENDED_PATH
    LAST_RECOMMENDED_PATH = file_path


def set_awaiting_feedback(value: bool) -> None:
    global AWAITING_FEEDBACK
    AWAITING_FEEDBACK = value


def is_awaiting_feedback() -> bool:
    return AWAITING_FEEDBACK


def set_awaiting_other_feedback(value: bool) -> None:
    global AWAITING_OTHER_FEEDBACK
    AWAITING_OTHER_FEEDBACK = value


def is_awaiting_other_feedback() -> bool:
    return AWAITING_OTHER_FEEDBACK


def get_last_recommended() -> Path | None:
    return LAST_RECOMMENDED_PATH


def set_selected_folder_names(folder_names: list[str]) -> None:
    global SELECTED_FOLDER_NAMES
    SELECTED_FOLDER_NAMES = list(folder_names)


def get_selected_folder_names() -> list[str]:
    return list(SELECTED_FOLDER_NAMES)


def clear_selected_folder_names() -> None:
    SELECTED_FOLDER_NAMES.clear()


def set_feedback_return_path(path: str) -> None:
    global FEEDBACK_RETURN_PATH
    FEEDBACK_RETURN_PATH = path


def consume_feedback_return_path() -> str:
    global FEEDBACK_RETURN_PATH
    path = FEEDBACK_RETURN_PATH
    FEEDBACK_RETURN_PATH = "/"
    return path


def create_stream_token(file_path: Path) -> str:
    token = secrets.token_urlsafe(16)
    with STREAM_TOKENS_LOCK:
        STREAM_TOKENS[token] = file_path
        while len(STREAM_TOKENS) > MAX_STREAM_TOKENS:
            oldest_token = next(iter(STREAM_TOKENS))
            STREAM_TOKENS.pop(oldest_token, None)
    return token


def get_stream_path(token: str) -> Path | None:
    with STREAM_TOKENS_LOCK:
        return STREAM_TOKENS.get(token)


def set_active_player(player: str) -> None:
    global ACTIVE_PLAYER
    ACTIVE_PLAYER = player if player in SUPPORTED_PLAYERS else "vlc"


def get_active_player() -> str:
    return ACTIVE_PLAYER


def set_active_profile(profile: str) -> None:
    global ACTIVE_PROFILE
    from .recommender import PROFILES
    ACTIVE_PROFILE = profile if profile in PROFILES else "balanced"


def get_active_profile() -> str:
    return ACTIVE_PROFILE


def clear_state() -> None:
    global LAST_RECOMMENDED_PATH, AWAITING_FEEDBACK, AWAITING_OTHER_FEEDBACK, FEEDBACK_RETURN_PATH
    LAST_RECOMMENDED_PATH = None
    AWAITING_FEEDBACK = False
    AWAITING_OTHER_FEEDBACK = False
    FEEDBACK_RETURN_PATH = "/"
    clear_selected_folder_names()
    with STREAM_TOKENS_LOCK:
        STREAM_TOKENS.clear()
