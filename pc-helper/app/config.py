from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEDIA_ROOT = Path(r"E:\Hobby Disk")
DEFAULT_SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
DEFAULT_STREAM_FOLDER = "_stream"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(APP_ROOT / ".env")


def _parse_extensions(raw_value: str | None) -> set[str]:
    if not raw_value:
        return set(DEFAULT_SUPPORTED_EXTENSIONS)
    extensions = set()
    for item in raw_value.split(","):
        ext = item.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        extensions.add(ext)
    return extensions or set(DEFAULT_SUPPORTED_EXTENSIONS)


def _path_from_env(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if not raw_value:
        return default
    path = Path(raw_value).expanduser()
    if path.is_absolute():
        return path
    return (APP_ROOT / path).resolve()


def _stream_folder_from_env() -> str:
    stream_folder = os.getenv("STREAM_FOLDER", DEFAULT_STREAM_FOLDER).strip().strip("\\/")
    return stream_folder or DEFAULT_STREAM_FOLDER


@dataclass(frozen=True, slots=True)
class Settings:
    media_root: Path
    prefs_file: Path
    supported_extensions: set[str]
    server_host: str
    server_port: int
    public_base_url: str
    stream_folder: str
    mpv_path: str


def get_settings() -> Settings:
    _load_dotenv()
    server_port = int(os.getenv("SERVER_PORT", "8787"))
    public_base_url = os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{server_port}").rstrip("/")
    mpv_path = os.getenv("MPV_PATH", "mpv").strip()
    media_root = _path_from_env("MEDIA_ROOT", DEFAULT_MEDIA_ROOT)
    return Settings(
        media_root=media_root,
        prefs_file=media_root / "_mpv_prefs.json",
        supported_extensions=_parse_extensions(os.getenv("SUPPORTED_EXTENSIONS")),
        server_host=os.getenv("SERVER_HOST", "0.0.0.0"),
        server_port=server_port,
        public_base_url=public_base_url,
        stream_folder=_stream_folder_from_env(),
        mpv_path=mpv_path or "mpv",
    )
