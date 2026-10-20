"""Validated local configuration with literal Windows paths."""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .env import load_env

REPOSITORY = Path(__file__).resolve().parents[1]
PREFIX = "POCKET_MEDIA_MANAGER_"
DEFAULT_SUPPORTED_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts"})
METADATA_FOLDER = ".pocket-media-manager"


@dataclass(frozen=True, slots=True)
class Settings:
    media_root: Path
    host: str = "0.0.0.0"
    port: int = 8787
    public_base_url: str = "http://127.0.0.1:8787"
    stream_folder: str = "_stream"
    supported_extensions: frozenset[str] = DEFAULT_SUPPORTED_EXTENSIONS
    mpv_path: str = "mpv"

    def __post_init__(self) -> None:
        object.__setattr__(self, "media_root", Path(self.media_root).resolve())
        object.__setattr__(self, "supported_extensions", frozenset(self.supported_extensions))

    @property
    def metadata_root(self) -> Path:
        return self.media_root / METADATA_FOLDER

    @property
    def database_file(self) -> Path:
        return self.metadata_root / "pocket-media.sqlite3"


def load_settings(
    env_file: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> Settings:
    values = load_env(REPOSITORY / ".env" if env_file is None else Path(env_file), environ=environ)

    def value(name: str, default: str = "") -> str:
        return values.get(PREFIX + name, default).strip()

    raw_root = value("MEDIA_ROOT")
    if not raw_root:
        raise ValueError(
            "Set POCKET_MEDIA_MANAGER_MEDIA_ROOT to your existing absolute media directory."
        )
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        raise ValueError("POCKET_MEDIA_MANAGER_MEDIA_ROOT must be an absolute path.")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"POCKET_MEDIA_MANAGER_MEDIA_ROOT is not an available directory: {root}")
    if root == REPOSITORY or root.is_relative_to(REPOSITORY) or REPOSITORY.is_relative_to(root):
        raise ValueError("Keep the media directory and repository in separate directories.")
    host = value("HOST", "0.0.0.0")
    if not host or any(character.isspace() or character in "/\\?#@" for character in host):
        raise ValueError("POCKET_MEDIA_MANAGER_HOST must be a host name or IP address.")
    try:
        port = int(value("PORT", "8787"))
    except ValueError as error:
        raise ValueError(
            "POCKET_MEDIA_MANAGER_PORT must be an integer between 1 and 65535."
        ) from error
    if not 1 <= port <= 65535:
        raise ValueError("POCKET_MEDIA_MANAGER_PORT must be between 1 and 65535.")
    public_url = value("PUBLIC_BASE_URL", f"http://127.0.0.1:{port}").rstrip("/")
    try:
        parsed = urlsplit(public_url)
        valid_url = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.port != 0
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError(
            "POCKET_MEDIA_MANAGER_PUBLIC_BASE_URL must be an http(s) origin without a path."
        )
    stream_folder = value("STREAM_FOLDER", "_stream").replace("\\", "/")
    stream_root = (root / stream_folder).resolve()
    if not stream_folder or not stream_root.is_relative_to(root) or stream_root == root:
        raise ValueError(
            "POCKET_MEDIA_MANAGER_STREAM_FOLDER must name a folder inside the media directory."
        )
    if stream_root.is_relative_to(root / METADATA_FOLDER):
        raise ValueError("POCKET_MEDIA_MANAGER_STREAM_FOLDER cannot be the metadata folder.")
    raw_extensions = value("SUPPORTED_EXTENSIONS", ",".join(sorted(DEFAULT_SUPPORTED_EXTENSIONS)))
    extensions = frozenset(
        "." + item.strip().lower().lstrip(".") for item in raw_extensions.split(",") if item.strip()
    )
    if not extensions or any(not extension[1:].isalnum() for extension in extensions):
        raise ValueError(
            "POCKET_MEDIA_MANAGER_SUPPORTED_EXTENSIONS must list extensions such as .mp4,.mkv."
        )
    return Settings(
        root, host, port, public_url, stream_folder, extensions, value("MPV_PATH", "mpv") or "mpv"
    )
