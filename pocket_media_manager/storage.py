from __future__ import annotations

import json
import os
import sqlite3
import stat
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from threading import RLock
from typing import Any, Iterator

from .config import Settings


FEEDBACK = {
    "enjoyed": "Enjoyed it",
    "too_soon": "Too soon",
    "not_in_mood": "Not in the mood",
    "less_often": "Less often",
    "not_for_me": "Not for me",
}
OTHER_FEEDBACK = {"remake": "Remake", "fix": "Fix", "trim": "Trim"}
SCHEMA_VERSION = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_relative_path(value: str) -> str:
    """Use slash separators without changing valid filename spelling."""
    path = PureWindowsPath(value)
    if not value or path.drive or path.root or ".." in path.parts or not path.parts:
        raise ValueError("A relative media path is required")
    return path.as_posix()


def _path_key(value: str) -> str:
    # Windows normcase matches Windows path casing; other platforms retain theirs.
    return os.path.normcase(normalize_relative_path(value))


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


class Conflict(ValueError):
    pass


class FeedbackRequired(Conflict):
    def __init__(self, encounter_id: str):
        super().__init__(
            "Close the current clip's feedback before continuing. No feedback is fine."
        )
        self.encounter_id = encounter_id


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.database_file
        self.scan_lock = RLock()

    def initialize(self) -> None:
        self._require_root()
        self.settings.metadata_root.mkdir(exist_ok=True)
        (self.settings.metadata_root / "backups").mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(
                    f"Database version {version} is not supported by this build. "
                    "This build requires a fresh database; no migration is provided. "
                    f"Stop the server and move {self.settings.metadata_root} aside, "
                    "then start again and use Scan Library. Media files are unchanged."
                )
            if (
                version == 0
                and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()
            ):
                raise ValueError(
                    "An unversioned database already contains tables; use a fresh database"
                )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                BEGIN;
                CREATE TABLE IF NOT EXISTS media (
                    relative_path TEXT PRIMARY KEY NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)),
                    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
                    less_often_count INTEGER NOT NULL DEFAULT 0 CHECK(less_often_count >= 0),
                    not_for_me INTEGER NOT NULL DEFAULT 0 CHECK(not_for_me IN (0,1)),
                    other_feedback TEXT CHECK(other_feedback IN ('remake','fix','trim'))
                );
                CREATE TABLE IF NOT EXISTS encounters (
                    id TEXT PRIMARY KEY NOT NULL,
                    media_path TEXT NOT NULL REFERENCES media(relative_path) ON DELETE CASCADE,
                    selected_at TEXT NOT NULL,
                    feedback TEXT CHECK(feedback IN
                        ('enjoyed','too_soon','not_in_mood','less_often','not_for_me',
                         'no_feedback','remake','fix','trim')),
                    closed_at TEXT,
                    return_path TEXT NOT NULL,
                    launch_status TEXT NOT NULL DEFAULT 'not_requested'
                        CHECK(launch_status IN ('not_requested','requested','failed')),
                    CHECK((feedback IS NULL) = (closed_at IS NULL))
                );
                CREATE INDEX IF NOT EXISTS encounter_media ON encounters(media_path,selected_at);
                CREATE UNIQUE INDEX IF NOT EXISTS encounter_open ON encounters((1))
                    WHERE closed_at IS NULL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=3;
                COMMIT;
                """
            )

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        # mode=rw avoids silently replacing a database when its drive disappears.
        with closing(
            sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None
            )
        ) as conn:
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
                if write:
                    conn.commit()
            except BaseException:
                if write:
                    conn.rollback()
                raise

    @staticmethod
    def _setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _save_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def preferences(self) -> dict[str, Any]:
        defaults = {"player": "vlc", "folders": []}
        with self.connect() as conn:
            result = {key: self._setting(conn, key) for key in defaults}
        if any(value is None for value in result.values()):
            with self.connect(write=True) as conn:
                for key, default in defaults.items():
                    # Recheck under the write lock before creating a missing default.
                    value = self._setting(conn, key)
                    if value is None:
                        value = default
                        self._save_setting(conn, key, value)
                    result[key] = value
        return result

    def update_preferences(self, **changes: Any) -> dict[str, Any]:
        if changes.keys() - {"player", "folders"}:
            raise ValueError("Unknown preference")
        if "player" in changes and changes["player"] not in {"vlc", "mpv"}:
            raise ValueError("Player must be vlc or mpv")
        if "folders" in changes:
            if not isinstance(changes["folders"], list) or not all(
                isinstance(folder, str) for folder in changes["folders"]
            ):
                raise ValueError("Folders must be a list of relative paths")
            changes["folders"] = list(
                dict.fromkeys(normalize_relative_path(folder) for folder in changes["folders"])
            )
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            for key, value in changes.items():
                self._save_setting(conn, key, value)
        return self.preferences()

    def library_state(self) -> dict[str, bool]:
        """Only table emptiness is needed to choose Home actions."""
        with self.connect() as conn:
            return {
                "has_media": conn.execute("SELECT 1 FROM media LIMIT 1").fetchone() is not None,
                "has_encounters": conn.execute("SELECT 1 FROM encounters LIMIT 1").fetchone()
                is not None,
            }

    def _require_root(self) -> None:
        info = self.settings.media_root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("The media directory is unavailable; the previous catalog was preserved")

    def scan_catalog(self) -> int:
        with self.scan_lock:
            self._require_root()
            root = self.settings.media_root
            stream_root = (root / self.settings.stream_folder).resolve()
            found: list[str] = []

            def scan_error(error: OSError) -> None:
                raise error

            # Complete every filesystem check before changing the stored catalog.
            for directory, folders, filenames in os.walk(
                root, followlinks=False, onerror=scan_error
            ):
                parent = Path(directory)
                folders[:] = [
                    name
                    for name in folders
                    if not _is_link((parent / name).lstat())
                    and (parent / name) != self.settings.metadata_root
                    and (parent / name) != stream_root
                ]
                for name in filenames:
                    path = parent / name
                    if path.suffix.lower() not in self.settings.supported_extensions:
                        continue
                    info = path.lstat()
                    if not _is_link(info) and stat.S_ISREG(info.st_mode):
                        found.append(path.relative_to(root).as_posix())
            self._require_root()
            with self.connect(write=True) as conn:
                self.require_idle(conn)
                previous = {
                    _path_key(row["relative_path"]): row["relative_path"]
                    for row in conn.execute("SELECT relative_path FROM media")
                }
                conn.execute("UPDATE media SET available=0 WHERE available=1")
                for path in found:
                    # Reuse the first catalogued spelling when Windows casing changes.
                    path = previous.get(_path_key(path), path)
                    conn.execute(
                        """INSERT INTO media (relative_path) VALUES (?)
                        ON CONFLICT(relative_path) DO UPDATE SET available=1""",
                        (path,),
                    )
            return len(found)

    def media_rows(
        self, include_blocked: bool = False, unavailable: bool = False
    ) -> list[dict[str, Any]]:
        where = "available=0" if unavailable else "available=1"
        if not include_blocked:
            where += " AND other_feedback IS NULL"
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM media WHERE {where} ORDER BY relative_path COLLATE NOCASE"
                )
            ]

    def candidates(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*,
                    COALESCE(h.enjoyed_count,0) AS enjoyed_count,
                    h.last_enjoyed_at, h.last_offered_at, h.last_too_soon_at,
                    h.last_not_in_mood_at
                FROM media m LEFT JOIN (
                    SELECT media_path,
                        SUM(feedback='enjoyed') AS enjoyed_count,
                        MAX(CASE WHEN feedback='enjoyed' THEN closed_at END) AS last_enjoyed_at,
                        MAX(CASE WHEN launch_status='requested' THEN selected_at END) AS last_offered_at,
                        MAX(CASE WHEN feedback='too_soon' THEN closed_at END) AS last_too_soon_at,
                        MAX(CASE WHEN feedback='not_in_mood' THEN closed_at END) AS last_not_in_mood_at
                    FROM encounters GROUP BY media_path
                ) h ON h.media_path=m.relative_path
                WHERE m.available=1 AND m.not_for_me=0 AND m.other_feedback IS NULL
                ORDER BY m.relative_path COLLATE NOCASE
                """
            )
            return [dict(row) for row in rows]

    def media(self, media_path: str) -> dict[str, Any]:
        media_path = normalize_relative_path(media_path)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE relative_path=?", (media_path,)
            ).fetchone()
            if row is None and os.name == "nt":
                key = _path_key(media_path)
                row = next(
                    (
                        item
                        for item in conn.execute("SELECT * FROM media")
                        if _path_key(item["relative_path"]) == key
                    ),
                    None,
                )
            if row is None:
                raise KeyError("Media record not found")
            return dict(row)

    def blocked(self, media_path: str) -> bool:
        return self.media(media_path)["other_feedback"] is not None

    def _catalog_path(self, media_path: str) -> Path:
        root = self.settings.media_root
        path = root / normalize_relative_path(media_path)
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(root)
            or resolved.is_relative_to(self.settings.metadata_root)
            or resolved.is_relative_to((root / self.settings.stream_folder).resolve())
        ):
            raise ValueError("Media path is not allowed")
        # Do not start following links or Windows junctions after cataloguing.
        for component in (*reversed(path.relative_to(root).parents), path.relative_to(root)):
            if _is_link((root / component).lstat()):
                raise ValueError("Linked media is not allowed")
        return path

    def verify_media(self, media_path: str) -> Path:
        media = self.media(media_path)
        if not media["available"]:
            raise ValueError("File is unavailable; use Scan Library")
        self._require_root()
        try:
            path = self._catalog_path(media["relative_path"])
            info = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            with self.connect(write=True) as conn:
                conn.execute(
                    "UPDATE media SET available=0 WHERE relative_path=?", (media["relative_path"],)
                )
            raise ValueError("File was removed; use Scan Library") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or path.suffix.lower() not in self.settings.supported_extensions
        ):
            with self.connect(write=True) as conn:
                conn.execute(
                    "UPDATE media SET available=0 WHERE relative_path=?", (media["relative_path"],)
                )
            raise ValueError("File is unavailable; use Scan Library")
        return path

    def check_available_paths(self) -> None:
        """Check catalogued paths only; discover nothing and commit no partial check."""
        with self.scan_lock:
            self._require_root()
            with self.connect() as conn:
                self.require_idle(conn)
                paths = [
                    row[0]
                    for row in conn.execute("SELECT relative_path FROM media WHERE available=1")
                ]
            missing = []
            for media_path in paths:
                try:
                    info = self._catalog_path(media_path).stat()
                    if not stat.S_ISREG(info.st_mode):
                        missing.append(media_path)
                except (FileNotFoundError, NotADirectoryError, ValueError):
                    missing.append(media_path)
            self._require_root()
            with self.connect(write=True) as conn:
                self.require_idle(conn)
                conn.executemany(
                    "UPDATE media SET available=0 WHERE relative_path=?",
                    [(path,) for path in missing],
                )

    def create_encounter(self, media_path: str, return_path: str) -> dict[str, Any]:
        media_path = self.media(media_path)["relative_path"]
        encounter_id = uuid.uuid4().hex
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.execute(
                """INSERT INTO encounters (id,media_path,selected_at,return_path)
                VALUES (?,?,?,?)""",
                (encounter_id, media_path, now_iso(), return_path),
            )
        return self.encounter(encounter_id)

    @staticmethod
    def _pending_id(conn: sqlite3.Connection) -> str | None:
        row = conn.execute("SELECT id FROM encounters WHERE closed_at IS NULL LIMIT 1").fetchone()
        return row[0] if row else None

    def require_idle(self, conn: sqlite3.Connection) -> None:
        pending = self._pending_id(conn)
        if pending:
            raise FeedbackRequired(pending)

    @contextmanager
    def idle(self) -> Iterator[sqlite3.Connection]:
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            yield conn

    def pending_encounter(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            identifier = self._pending_id(conn)
        return self.encounter(identifier) if identifier else None

    def encounter(self, encounter_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT e.*,m.relative_path,m.favorite FROM encounters e
                JOIN media m ON m.relative_path=e.media_path WHERE e.id=?""",
                (encounter_id,),
            ).fetchone()
            if not row:
                raise KeyError("Encounter not found")
            return dict(row)

    def latest_encounter(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM encounters ORDER BY selected_at DESC,id DESC LIMIT 1"
            ).fetchone()
        return self.encounter(row[0]) if row else None

    def _require_open(self, conn: sqlite3.Connection, encounter_id: str) -> None:
        row = conn.execute(
            "SELECT closed_at FROM encounters WHERE id=?", (encounter_id,)
        ).fetchone()
        if not row:
            raise KeyError("Encounter not found")
        if row[0] is not None:
            raise Conflict("This encounter is already closed; open the clip again through Explore")

    @contextmanager
    def playback(self, encounter_id: str) -> Iterator[sqlite3.Connection]:
        with self.connect(write=True) as conn:
            self._require_open(conn, encounter_id)
            yield conn

    @staticmethod
    def mark_launch(conn: sqlite3.Connection, encounter_id: str, error: str | None = None) -> None:
        conn.execute(
            "UPDATE encounters SET launch_status=? WHERE id=?",
            ("failed" if error else "requested", encounter_id),
        )

    def submit_feedback(self, encounter_id: str, feedback: str) -> dict[str, Any]:
        if feedback not in FEEDBACK and feedback != "no_feedback":
            raise ValueError("Unknown feedback")
        return self._close_encounter(encounter_id, feedback)

    def _close_encounter(self, encounter_id: str, feedback: str) -> dict[str, Any]:
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
            if not row:
                raise KeyError("Encounter not found")
            if row["closed_at"] is not None:
                if row["feedback"] != feedback:
                    raise Conflict("This encounter already has a different response")
            else:
                if feedback in OTHER_FEEDBACK:
                    media = conn.execute(
                        "SELECT other_feedback FROM media WHERE relative_path=?",
                        (row["media_path"],),
                    ).fetchone()
                    if media[0] is not None:
                        raise Conflict("This clip already has outstanding Something Else feedback")
                    conn.execute(
                        "UPDATE media SET other_feedback=? WHERE relative_path=?",
                        (feedback, row["media_path"]),
                    )
                elif feedback == "not_for_me":
                    conn.execute(
                        "UPDATE media SET not_for_me=1,favorite=0 WHERE relative_path=?",
                        (row["media_path"],),
                    )
                elif feedback == "less_often":
                    conn.execute(
                        "UPDATE media SET less_often_count=less_often_count+1 WHERE relative_path=?",
                        (row["media_path"],),
                    )
                elif feedback == "enjoyed":
                    conn.execute(
                        "UPDATE media SET less_often_count=MAX(0,less_often_count-1) WHERE relative_path=?",
                        (row["media_path"],),
                    )
                conn.execute(
                    "UPDATE encounters SET feedback=?,closed_at=? WHERE id=?",
                    (feedback, now_iso(), encounter_id),
                )
        return self.encounter(encounter_id)

    def preference_for_encounter(self, encounter_id: str, preference: str, enabled: bool) -> None:
        if preference not in {"favorite", "less_often", "not_for_me"}:
            raise ValueError("Unknown preference")
        with self.connect(write=True) as conn:
            self._require_open(conn, encounter_id)
            row = conn.execute(
                "SELECT media_path FROM encounters WHERE id=?", (encounter_id,)
            ).fetchone()
            if preference == "favorite" and enabled:
                media = conn.execute(
                    "SELECT not_for_me FROM media WHERE relative_path=?", (row["media_path"],)
                ).fetchone()
                if media[0]:
                    raise Conflict(
                        "Allow recommendations again before marking this clip as Favorite"
                    )
            if preference == "less_often":
                if enabled:
                    raise ValueError("Use Less often feedback to increase the frequency penalty")
                conn.execute(
                    "UPDATE media SET less_often_count=0 WHERE relative_path=?",
                    (row["media_path"],),
                )
            elif preference == "not_for_me" and enabled:
                conn.execute(
                    "UPDATE media SET not_for_me=1,favorite=0 WHERE relative_path=?",
                    (row["media_path"],),
                )
            else:
                conn.execute(
                    f"UPDATE media SET {preference}=? WHERE relative_path=?",
                    (int(enabled), row["media_path"]),
                )

    def submit_other(self, encounter_id: str, feedback_type: str) -> dict[str, Any]:
        if feedback_type not in OTHER_FEEDBACK:
            raise ValueError("Unknown maintenance type")
        return self._close_encounter(encounter_id, feedback_type)

    def tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM media WHERE other_feedback IS NOT NULL ORDER BY relative_path COLLATE NOCASE"
                )
            ]

    def resolve_tasks(self, media_paths: list[str]) -> None:
        paths = [self.media(path)["relative_path"] for path in media_paths]
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.executemany(
                "UPDATE media SET other_feedback=NULL WHERE relative_path=?",
                [(path,) for path in paths],
            )

    def cleanup(self, media_paths: list[str]) -> None:
        paths = [self.media(path)["relative_path"] for path in media_paths]
        self.backup()
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.executemany(
                "DELETE FROM media WHERE relative_path=? AND available=0",
                [(path,) for path in paths],
            )

    def reset_library(self) -> None:
        self.backup()
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.execute("DELETE FROM encounters")
            conn.execute("DELETE FROM media")
            conn.execute("DELETE FROM settings")

    def backup(self, daily: bool = False) -> Path | None:
        with self.scan_lock:
            today = datetime.now(timezone.utc).date().isoformat()
            with self.connect() as conn:
                if daily and self._setting(conn, "last_backup_date") == today:
                    return None
                folder = self.settings.metadata_root / "backups"
                target = folder / (
                    "backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3"
                )
                temporary = target.with_suffix(".sqlite3.tmp")
                try:
                    with closing(sqlite3.connect(temporary)) as destination:
                        conn.backup(destination)
                        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise sqlite3.DatabaseError(
                                "The database snapshot failed its integrity check"
                            )
                    with temporary.open("r+b") as snapshot:
                        os.fsync(snapshot.fileno())
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            with self.connect(write=True) as conn:
                self._save_setting(conn, "last_backup_date", today)
            for old in sorted(folder.glob("backup-*.sqlite3"))[:-7]:
                if old.is_file() and not old.is_symlink():
                    old.unlink()
            return target
