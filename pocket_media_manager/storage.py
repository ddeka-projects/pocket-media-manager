from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
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
OTHER_FEEDBACK = {"remake": "Remake", "fix": "Fix", "trim": "Trim", "hold": "Hold"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Conflict(ValueError):
    pass


class FeedbackRequired(Conflict):
    def __init__(self, encounter_id: str):
        super().__init__("Close the current clip's feedback before continuing. No feedback is fine.")
        self.encounter_id = encounter_id


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.database_file
        self.scan_lock = RLock()

    def initialize(self) -> None:
        if not self.settings.media_root.is_dir():
            raise OSError(f"MEDIA_ROOT is not an available directory: {self.settings.media_root}")
        self.settings.metadata_root.mkdir(exist_ok=True)
        for name in ("backups", "logs"):
            (self.settings.metadata_root / name).mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 2):
                raise ValueError(
                    f"Database version {version} is not supported by this build. "
                    f"Stop the helper, move {self.settings.metadata_root} aside, and restart "
                    "to initialize fresh metadata. Media files are not changed."
                )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                BEGIN;
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relative_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    discovered_at TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)),
                    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
                    less_often INTEGER NOT NULL DEFAULT 0 CHECK(less_often IN (0,1)),
                    not_for_me INTEGER NOT NULL DEFAULT 0 CHECK(not_for_me IN (0,1))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS media_current_path
                    ON media(relative_path) WHERE available=1;
                CREATE TABLE IF NOT EXISTS encounters (
                    id TEXT PRIMARY KEY,
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    selected_at TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('recommend','selected','explore','test')),
                    context TEXT NOT NULL,
                    playback_requested_at TEXT,
                    launch_status TEXT NOT NULL DEFAULT 'not_requested'
                        CHECK(launch_status IN ('not_requested','requested','failed')),
                    launch_error TEXT,
                    feedback TEXT CHECK(feedback IN
                        ('enjoyed','too_soon','not_in_mood','less_often','not_for_me')),
                    resolution TEXT NOT NULL DEFAULT 'open'
                        CHECK(resolution IN ('open','answered','no_feedback','other_feedback')),
                    closed_at TEXT,
                    CHECK((resolution='open') = (closed_at IS NULL)),
                    CHECK((resolution='answered') = (feedback IS NOT NULL))
                );
                CREATE INDEX IF NOT EXISTS encounter_media ON encounters(media_id,selected_at);
                CREATE INDEX IF NOT EXISTS encounter_open ON encounters(selected_at,id)
                    WHERE resolution='open';
                CREATE TABLE IF NOT EXISTS other_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    type TEXT NOT NULL CHECK(type IN ('remake','fix','trim','hold')),
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unresolved_task
                    ON other_feedback(media_id) WHERE resolved_at IS NULL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                UPDATE media SET favorite=0 WHERE not_for_me=1 AND favorite=1;
                PRAGMA user_version=2;
                COMMIT;
            """)

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        # mode=rw avoids silently creating a replacement database if the drive disappears.
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                               timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _save_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))

    def browser(self, browser_id: str) -> dict[str, Any]:
        key = "browser:" + browser_id
        with self.connect() as conn:
            value = self._setting(conn, key)
        if value is None:
            with self.connect(write=True) as conn:
                value = self._setting(conn, key)
                if value is None:
                    value = {"player": "vlc", "folders": []}
                    self._save_setting(conn, key, value)
        return value

    def update_browser(self, browser_id: str, **changes: Any) -> dict[str, Any]:
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            value = self._setting(conn, "browser:" + browser_id)
            value.update(changes)
            self._save_setting(conn, "browser:" + browser_id, value)
        return value

    def refresh_catalog(self, *, allow_pending: bool = False) -> int:
        with self.scan_lock:
            root = self.settings.media_root
            if not root.is_dir():
                raise OSError("MEDIA_ROOT is unavailable; the previous catalog was preserved")
            stream_root = (root / self.settings.stream_folder).resolve()
            found: dict[str, tuple[int, int]] = {}

            def scan_error(error: OSError) -> None:
                raise error

            # Complete the scan before changing availability; a partial/failed scan changes nothing.
            for directory, folders, filenames in os.walk(root, followlinks=False, onerror=scan_error):
                parent = Path(directory)
                folders[:] = [name for name in folders
                              if not (parent / name).is_symlink()
                              and (parent / name) != self.settings.metadata_root
                              and (parent / name) != stream_root]
                for name in filenames:
                    path = parent / name
                    if path.suffix.lower() not in self.settings.supported_extensions or path.is_symlink():
                        continue
                    stat = path.stat()
                    if path.is_file():
                        found[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
            timestamp = now_iso()
            with self.connect(write=True) as conn:
                if not allow_pending:
                    self.require_idle(conn)
                previous = {row["relative_path"]: row for row in
                            conn.execute("SELECT * FROM media ORDER BY id")}
                conn.execute("UPDATE media SET available=0 WHERE available=1")
                for path, (size, modified) in found.items():
                    old = previous.get(path)
                    if old and old["file_size"] == size and old["modified_ns"] == modified:
                        conn.execute("UPDATE media SET available=1 WHERE id=?", (old["id"],))
                    else:
                        conn.execute("""INSERT INTO media
                            (relative_path,file_size,modified_ns,discovered_at) VALUES (?,?,?,?)""",
                            (path, size, modified, timestamp))
                self._save_setting(conn, "last_catalog_refresh", timestamp)
            return len(found)

    def media_rows(self, include_blocked: bool = False, unavailable: bool = False) -> list[dict[str, Any]]:
        where = "m.available=0" if unavailable else "m.available=1"
        if not include_blocked:
            where += " AND NOT EXISTS (SELECT 1 FROM other_feedback o WHERE o.media_id=m.id AND o.resolved_at IS NULL)"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                f"SELECT m.* FROM media m WHERE {where} ORDER BY m.relative_path COLLATE NOCASE")]

    def candidates(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT m.*,
                    COALESCE(h.enjoyed_count,0) AS enjoyed_count,
                    h.last_enjoyed_at, h.last_offered_at, h.last_too_soon_at,
                    h.last_not_in_mood_at
                FROM media m LEFT JOIN (
                    SELECT media_id,
                        SUM(feedback='enjoyed') AS enjoyed_count,
                        MAX(CASE WHEN feedback='enjoyed' THEN closed_at END) AS last_enjoyed_at,
                        MAX(CASE WHEN launch_status='requested' THEN selected_at END) AS last_offered_at,
                        MAX(CASE WHEN feedback='too_soon' THEN closed_at END) AS last_too_soon_at,
                        MAX(CASE WHEN feedback='not_in_mood' THEN closed_at END) AS last_not_in_mood_at
                    FROM encounters WHERE source!='test' GROUP BY media_id
                ) h ON h.media_id=m.id
                WHERE m.available=1 AND m.not_for_me=0
                    AND NOT EXISTS (SELECT 1 FROM other_feedback o
                                    WHERE o.media_id=m.id AND o.resolved_at IS NULL)
                ORDER BY m.relative_path COLLATE NOCASE
            """)
            return [dict(row) for row in rows]

    def media(self, media_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
            if not row:
                raise KeyError("Media record not found")
            return dict(row)

    def blocked(self, media_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM other_feedback WHERE media_id=? AND resolved_at IS NULL",
                                (media_id,)).fetchone() is not None

    def verify_media(self, media_id: int) -> Path:
        media = self.media(media_id)
        root = self.settings.media_root
        path = (root / media["relative_path"]).resolve()
        if (not path.is_relative_to(root) or path.is_relative_to(self.settings.metadata_root)
                or path.is_relative_to((root / self.settings.stream_folder).resolve())):
            raise ValueError("Media path is not allowed")
        try:
            stat = path.stat()
        except FileNotFoundError:
            with self.connect(write=True) as conn:
                conn.execute("UPDATE media SET available=0 WHERE id=?", (media_id,))
            raise ValueError("File was removed; refresh the library") from None
        if (not media["available"] or not path.is_file()
                or stat.st_size != media["file_size"] or stat.st_mtime_ns != media["modified_ns"]
                or path.suffix.lower() not in self.settings.supported_extensions):
            with self.connect(write=True) as conn:
                conn.execute("UPDATE media SET available=0 WHERE id=?", (media_id,))
            raise ValueError("File changed; refresh the library to catalogue its new record")
        return path

    def create_encounter(self, media_id: int, source: str, context: dict[str, Any]) -> dict[str, Any]:
        if source not in {"recommend", "selected", "explore"}:
            raise ValueError("Unknown encounter source")
        encounter_id = uuid.uuid4().hex
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.execute("""INSERT INTO encounters
                (id,media_id,selected_at,source,context) VALUES (?,?,?,?,?)""",
                (encounter_id, media_id, now_iso(), source, json.dumps(context)))
        return self.encounter(encounter_id)

    @staticmethod
    def _pending_id(conn: sqlite3.Connection) -> str | None:
        row = conn.execute("""SELECT id FROM encounters WHERE resolution='open'
            ORDER BY selected_at DESC,id DESC LIMIT 1""").fetchone()
        return row[0] if row else None

    def require_idle(self, conn: sqlite3.Connection) -> None:
        pending = self._pending_id(conn)
        if pending:
            raise FeedbackRequired(pending)

    @contextmanager
    def idle(self) -> Iterator[sqlite3.Connection]:
        # Keep the check and the operation together, including new direct-stream launches.
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            yield conn

    def pending_encounter(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("""SELECT e.*,m.relative_path,m.favorite FROM encounters e
                JOIN media m ON m.id=e.media_id WHERE e.resolution='open'
                ORDER BY e.selected_at DESC,e.id DESC LIMIT 1""").fetchone()
            return self._encounter_dict(row) if row else None

    @staticmethod
    def _encounter_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["context"] = json.loads(result["context"])
        return result

    def encounter(self, encounter_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("""SELECT e.*,m.relative_path,m.favorite FROM encounters e
                JOIN media m ON m.id=e.media_id WHERE e.id=?""", (encounter_id,)).fetchone()
            if not row:
                raise KeyError("Encounter not found")
            return self._encounter_dict(row)

    def latest_encounter(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM encounters ORDER BY selected_at DESC,id DESC LIMIT 1").fetchone()
        return self.encounter(row[0]) if row else None

    def _require_open(self, conn: sqlite3.Connection, encounter_id: str) -> None:
        row = conn.execute("SELECT resolution FROM encounters WHERE id=?", (encounter_id,)).fetchone()
        if not row:
            raise KeyError("Encounter not found")
        if row[0] != "open":
            raise Conflict("This encounter is already closed; open the clip again through Explore")
        pending = self._pending_id(conn)
        if pending and pending != encounter_id:
            raise FeedbackRequired(pending)

    @contextmanager
    def playback(self, encounter_id: str) -> Iterator[sqlite3.Connection]:
        # Closing feedback must wait until its player launch has finished, and vice versa.
        with self.connect(write=True) as conn:
            self._require_open(conn, encounter_id)
            yield conn

    @staticmethod
    def mark_launch(conn: sqlite3.Connection, encounter_id: str, error: str | None = None) -> None:
        conn.execute("""UPDATE encounters SET playback_requested_at=?,launch_status=?,launch_error=?
            WHERE id=?""", (now_iso(), "failed" if error else "requested", error, encounter_id))

    def submit_feedback(self, encounter_id: str, feedback: str) -> dict[str, Any]:
        if feedback not in FEEDBACK and feedback != "no_feedback":
            raise ValueError("Unknown feedback")
        resolution = "no_feedback" if feedback == "no_feedback" else "answered"
        response = None if feedback == "no_feedback" else feedback
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
            if not row:
                raise KeyError("Encounter not found")
            if row["resolution"] != "open":
                if row["resolution"] != resolution or row["feedback"] != response:
                    raise Conflict("This encounter already has a different response")
            else:
                self._require_open(conn, encounter_id)
                conn.execute("UPDATE encounters SET feedback=?,resolution=?,closed_at=? WHERE id=?",
                             (response, resolution, now_iso(), encounter_id))
                if feedback == "not_for_me":
                    conn.execute("UPDATE media SET not_for_me=1,favorite=0 WHERE id=?", (row["media_id"],))
                elif feedback == "less_often":
                    conn.execute("UPDATE media SET less_often=1 WHERE id=?", (row["media_id"],))
        return self.encounter(encounter_id)

    def preference_for_encounter(self, encounter_id: str, preference: str, enabled: bool) -> None:
        if preference not in {"favorite", "less_often", "not_for_me"}:
            raise ValueError("Unknown preference")
        with self.connect(write=True) as conn:
            self._require_open(conn, encounter_id)
            row = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
            if preference == "favorite" and enabled:
                media = conn.execute("SELECT not_for_me FROM media WHERE id=?", (row["media_id"],)).fetchone()
                if media["not_for_me"]:
                    raise Conflict("Allow recommendations again before marking this clip as Favorite")
            if preference == "not_for_me" and enabled:
                conn.execute("UPDATE media SET not_for_me=1,favorite=0 WHERE id=?", (row["media_id"],))
            else:
                conn.execute(f"UPDATE media SET {preference}=? WHERE id=?", (int(enabled), row["media_id"]))

    def submit_other(self, encounter_id: str, feedback_type: str) -> dict[str, Any]:
        if feedback_type not in OTHER_FEEDBACK:
            raise ValueError("Unknown maintenance type")
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM encounters WHERE id=?", (encounter_id,)).fetchone()
            if not row:
                raise KeyError("Encounter not found")
            if row["resolution"] == "other_feedback":
                if json.loads(row["context"]).get("other_feedback_type") != feedback_type:
                    raise Conflict("This encounter already has a different maintenance response")
            elif row["resolution"] != "open":
                raise Conflict("This encounter is already closed")
            else:
                self._require_open(conn, encounter_id)
                if conn.execute("SELECT 1 FROM other_feedback WHERE media_id=? AND resolved_at IS NULL",
                                (row["media_id"],)).fetchone():
                    raise Conflict("This clip already has outstanding Something Else feedback")
                timestamp = now_iso()
                conn.execute("INSERT INTO other_feedback (media_id,type,created_at) VALUES (?,?,?)",
                             (row["media_id"], feedback_type, timestamp))
                context = json.loads(row["context"])
                context["other_feedback_type"] = feedback_type
                conn.execute("UPDATE encounters SET resolution='other_feedback',closed_at=?,context=? WHERE id=?",
                             (timestamp, json.dumps(context), encounter_id))
        return self.encounter(encounter_id)

    def tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("""SELECT o.*,m.relative_path
                FROM other_feedback o JOIN media m ON m.id=o.media_id
                WHERE resolved_at IS NULL ORDER BY m.relative_path COLLATE NOCASE""")]

    def resolve_tasks(self, task_ids: list[int]) -> None:
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.executemany("UPDATE other_feedback SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
                             [(now_iso(), task_id) for task_id in task_ids])

    def cleanup(self, media_ids: list[int]) -> None:
        self.backup()
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.executemany("""DELETE FROM media WHERE id=? AND available=0
                AND NOT EXISTS (SELECT 1 FROM other_feedback
                    WHERE media_id=media.id AND resolved_at IS NULL)""", [(item,) for item in media_ids])

    def reset_preferences(self) -> None:
        self.backup()
        with self.connect(write=True) as conn:
            self.require_idle(conn)
            conn.execute("DELETE FROM encounters")
            conn.execute("UPDATE media SET favorite=0,less_often=0,not_for_me=0")

    def backup(self, daily: bool = False) -> Path | None:
        with self.scan_lock:
            today = datetime.now(timezone.utc).date().isoformat()
            with self.connect() as conn:
                if daily and self._setting(conn, "last_backup_date") == today:
                    return None
                folder = self.settings.metadata_root / "backups"
                target = folder / ("backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
                with closing(sqlite3.connect(target)) as destination:
                    conn.backup(destination)
            with self.connect(write=True) as conn:
                self._save_setting(conn, "last_backup_date", today)
            # Only rotate snapshots created by this helper, inside its dedicated folder.
            for old in sorted(folder.glob("backup-*.sqlite3"))[:-7]:
                if old.is_file() and not old.is_symlink():
                    old.unlink()
            return target
