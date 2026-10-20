"""Only temporary playback links live in memory; preferences live in SQLite."""

import secrets
from pathlib import Path
from threading import RLock


MAX_STREAM_TOKENS = 30
STREAM_TOKENS: dict[str, Path] = {}
STREAM_TOKENS_LOCK = RLock()


def create_stream_token(file_path: Path) -> str:
    token = secrets.token_urlsafe(16)
    with STREAM_TOKENS_LOCK:
        STREAM_TOKENS[token] = file_path
        while len(STREAM_TOKENS) > MAX_STREAM_TOKENS:
            STREAM_TOKENS.pop(next(iter(STREAM_TOKENS)))
    return token


def get_stream_path(token: str) -> Path | None:
    with STREAM_TOKENS_LOCK:
        return STREAM_TOKENS.get(token)


def clear_state() -> None:
    with STREAM_TOKENS_LOCK:
        STREAM_TOKENS.clear()
