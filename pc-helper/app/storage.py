from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
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
SESSION_TIMEOUT = timedelta(minutes=30)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Conflict(ValueError):
    pass


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
            if version not in (0, 1):
                raise ValueError(f"Unsupported database version: {version}")
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
                    browser_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
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
                CREATE INDEX IF NOT EXISTS encounter_browser ON encounters(browser_id,selected_at);
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
                PRAGMA user_version=1;
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
                    value = {"player": "vlc", "folders": [], "session_id": None,
                             "last_activity_at": None}
                    self._save_setting(conn, key, value)
        return value

    def update_browser(self, browser_id: str, **changes: Any) -> dict[str, Any]:
        with self.connect(write=True) as conn:
            value = self._setting(conn, "browser:" + browser_id)
            value.update(changes)
            self._save_setting(conn, "browser:" + browser_id, value)
        return value

    def begin_session(self, browser_id: str, force: bool = False,
                      now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        with self.connect(write=True) as conn:
            value = self._setting(conn, "browser:" + browser_id)
            last = value["last_activity_at"]
            if (force or not value["session_id"] or not last
                    or now - datetime.fromisoformat(last) >= SESSION_TIMEOUT):
                value["session_id"] = uuid.uuid4().hex
            value["last_activity_at"] = now.isoformat(timespec="microseconds")
            self._save_setting(conn, "browser:" + browser_id, value)
            return value["session_id"]

    def _touch_encounter_session(self, conn: sqlite3.Connection, encounter: sqlite3.Row) -> None:
        key = "browser:" + encounter["browser_id"]
        value = self._setting(conn, key)
        now = datetime.now(timezone.utc)
        if (value and value["session_id"] == encounter["session_id"]
                and value["last_activity_at"]
                and now - datetime.fromisoformat(value["last_activity_at"]) < SESSION_TIMEOUT):
            value["last_activity_at"] = now.isoformat(timespec="microseconds")
            self._save_setting(conn, key, value)

    def refresh_catalog(self) -> int:
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

    def candidates(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT m.*,
                    COALESCE(h.enjoyed_count,0) AS enjoyed_count,
                    h.last_enjoyed_at, h.last_offered_at, h.last_too_soon_at,
                    COALESCE(h.declined_this_session,0) AS declined_this_session
                FROM media m LEFT JOIN (
                    SELECT media_id,
                        SUM(feedback='enjoyed') AS enjoyed_count,
                        MAX(CASE WHEN feedback='enjoyed' THEN closed_at END) AS last_enjoyed_at,
                        MAX(CASE WHEN launch_status='requested' THEN selected_at END) AS last_offered_at,
                        MAX(CASE WHEN feedback='too_soon' THEN closed_at END) AS last_too_soon_at,
                        MAX(CASE WHEN session_id=? AND feedback='not_in_mood' THEN 1 ELSE 0 END)
                            AS declined_this_session
                    FROM encounters WHERE source!='test' GROUP BY media_id
                ) h ON h.media_id=m.id
                WHERE m.available=1 AND m.not_for_me=0
                    AND NOT EXISTS (SELECT 1 FROM other_feedback o
                                    WHERE o.media_id=m.id AND o.resolved_at IS NULL)
                ORDER BY m.relative_path COLLATE NOCASE
            """, (session_id,))
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

    def create_encounter(self, media_id: int, browser_id: str, session_id: str,
                         source: str, context: dict[str, Any]) -> dict[str, Any]:
        encounter_id = uuid.uuid4().hex
        with self.connect(write=True) as conn:
            conn.execute("""INSERT INTO encounters
                (id,media_id,browser_id,session_id,selected_at,source,context)
                VALUES (?,?,?,?,?,?,?)""",
                (encounter_id, media_id, browser_id, session_id, now_iso(), source, json.dumps(context)))
        return self.encounter(encounter_id, browser_id)

    def encounter(self, encounter_id: str, browser_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("""SELECT e.*,m.relative_path,m.favorite FROM encounters e
                JOIN media m ON m.id=e.media_id WHERE e.id=? AND e.browser_id=?""",
                (encounter_id, browser_id)).fetchone()
            if not row:
                raise KeyError("Encounter not found in this browser")
            result = dict(row)
            result["context"] = json.loads(result["context"])
            return result

    def latest_encounter(self, browser_id: str, only_open: bool = False) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM encounters WHERE browser_id=?"
                               + (" AND resolution='open'" if only_open else "")
                               + " ORDER BY selected_at DESC LIMIT 1", (browser_id,)).fetchone()
        return self.encounter(row[0], browser_id) if row else None

    def mark_launch(self, encounter_id: str, browser_id: str, error: str | None = None) -> None:
        with self.connect(write=True) as conn:
            conn.execute("""UPDATE encounters SET playback_requested_at=?,launch_status=?,launch_error=?
                WHERE id=? AND browser_id=?""",
                (now_iso(), "failed" if error else "requested", error, encounter_id, browser_id))

    def set_preferences(self, media_id: int, **changes: bool) -> None:
        if not changes or not changes.keys() <= {"favorite", "less_often", "not_for_me"}:
            raise ValueError("Invalid preference fields")
        with self.connect(write=True) as conn:
            result = conn.execute("UPDATE media SET " + ",".join(f"{name}=?" for name in changes)
                                  + " WHERE id=?", (*[int(value) for value in changes.values()], media_id))
            if not result.rowcount:
                raise KeyError("Media record not found")

    def submit_feedback(self, encounter_id: str, browser_id: str, feedback: str) -> dict[str, Any]:
        if feedback not in FEEDBACK and feedback != "no_feedback":
            raise ValueError("Unknown feedback")
        resolution = "no_feedback" if feedback == "no_feedback" else "answered"
        response = None if feedback == "no_feedback" else feedback
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM encounters WHERE id=? AND browser_id=?",
                               (encounter_id, browser_id)).fetchone()
            if not row:
                raise KeyError("Encounter not found in this browser")
            if row["resolution"] != "open":
                if row["resolution"] != resolution or row["feedback"] != response:
                    raise Conflict("This encounter already has a different response")
            else:
                conn.execute("UPDATE encounters SET feedback=?,resolution=?,closed_at=? WHERE id=?",
                             (response, resolution, now_iso(), encounter_id))
                if feedback in ("less_often", "not_for_me"):
                    conn.execute(f"UPDATE media SET {feedback}=1 WHERE id=?", (row["media_id"],))
                self._touch_encounter_session(conn, row)
        return self.encounter(encounter_id, browser_id)

    def favorite_for_encounter(self, encounter_id: str, browser_id: str, favorite: bool) -> None:
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM encounters WHERE id=? AND browser_id=?",
                               (encounter_id, browser_id)).fetchone()
            if not row:
                raise KeyError("Encounter not found in this browser")
            if row["resolution"] != "open":
                raise Conflict("This encounter is already closed; edit Clip Preferences instead")
            conn.execute("UPDATE media SET favorite=? WHERE id=?", (int(favorite), row["media_id"]))
            self._touch_encounter_session(conn, row)

    def submit_other(self, encounter_id: str, browser_id: str, feedback_type: str) -> dict[str, Any]:
        if feedback_type not in OTHER_FEEDBACK:
            raise ValueError("Unknown maintenance type")
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT * FROM encounters WHERE id=? AND browser_id=?",
                               (encounter_id, browser_id)).fetchone()
            if not row:
                raise KeyError("Encounter not found in this browser")
            if row["resolution"] == "other_feedback":
                if json.loads(row["context"]).get("other_feedback_type") != feedback_type:
                    raise Conflict("This encounter already has a different maintenance response")
            elif row["resolution"] != "open":
                raise Conflict("This encounter is already closed")
            else:
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
                self._touch_encounter_session(conn, row)
        return self.encounter(encounter_id, browser_id)

    def tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("""SELECT o.*,m.relative_path
                FROM other_feedback o JOIN media m ON m.id=o.media_id
                WHERE resolved_at IS NULL ORDER BY m.relative_path COLLATE NOCASE""")]

    def resolve_tasks(self, task_ids: list[int]) -> None:
        with self.connect(write=True) as conn:
            conn.executemany("UPDATE other_feedback SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
                             [(now_iso(), task_id) for task_id in task_ids])

    def cleanup(self, media_ids: list[int]) -> None:
        self.backup()
        with self.connect(write=True) as conn:
            conn.executemany("""DELETE FROM media WHERE id=? AND available=0
                AND NOT EXISTS (SELECT 1 FROM other_feedback
                    WHERE media_id=media.id AND resolved_at IS NULL)""", [(item,) for item in media_ids])

    def reset_preferences(self) -> None:
        self.backup()
        with self.connect(write=True) as conn:
            conn.execute("DELETE FROM encounters")
            conn.execute("UPDATE media SET favorite=0,less_often=0,not_for_me=0")
            rows = list(conn.execute("SELECT key,value FROM settings WHERE key LIKE 'browser:%'"))
            for row in rows:
                value = json.loads(row["value"])
                value.update(session_id=None, last_activity_at=None)
                self._save_setting(conn, row["key"], value)

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
