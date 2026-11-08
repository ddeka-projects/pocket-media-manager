"""Path identity and atomic catalog/feedback operations on the fresh schema."""

from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import ntpath
import sqlite3
import unittest
from unittest.mock import patch

from pocket_media_manager.config import Settings
from pocket_media_manager.storage import (
    Conflict,
    Database,
    FeedbackRequired,
    normalize_relative_path,
)


class StorageModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.database = Database(Settings(self.root))
        self.database.initialize()

    def add_file(self, name="Anime/日本語 #50% 'edit'.mp4"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"media")
        return name

    def open_clip(self, name):
        return self.database.create_encounter(name, "/explore")

    def respond(self, name, response):
        encounter = self.open_clip(name)
        return self.database.submit_feedback(encounter["id"], response)

    def test_initialization_is_empty_and_requires_supported_fresh_schema(self):
        self.add_file()
        self.assertEqual(
            {"has_media": False, "has_encounters": False}, self.database.library_state()
        )
        with self.database.connect() as conn:
            self.assertEqual(3, conn.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertEqual({"media", "encounters", "settings"}, tables)
        with self.database.connect(write=True) as conn:
            conn.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(ValueError, "fresh database"):
            self.database.initialize()
        with self.database.connect() as conn:
            self.assertEqual(2, conn.execute("PRAGMA user_version").fetchone()[0])

    def test_same_path_edits_preserve_preferences_encounters_and_current_work(self):
        name = self.add_file()
        self.assertEqual(1, self.database.scan_catalog())
        encounter = self.open_clip(name)
        self.database.preference_for_encounter(encounter["id"], "favorite", True)
        self.database.submit_feedback(encounter["id"], "less_often")
        task_encounter = self.open_clip(name)
        self.database.submit_other(task_encounter["id"], "trim")
        original = self.database.media(name)
        (self.root / name).write_bytes(b"replacement with different size")
        self.assertEqual(self.root / name, self.database.verify_media(name))
        self.database.scan_catalog()
        self.assertEqual(original, self.database.media(name))
        self.assertEqual("trim", self.database.encounter(task_encounter["id"])["feedback"])
        self.assertEqual([], self.database.candidates())
        self.assertEqual([], self.database.media_rows())
        self.database.resolve_tasks([name])
        self.assertEqual(1, len(self.database.candidates()))
        # A retry after resolution must not recreate completed maintenance work.
        self.database.submit_other(task_encounter["id"], "trim")
        self.assertIsNone(self.database.media(name)["other_feedback"])

    def test_move_creates_new_identity_and_return_restores_old_record(self):
        name = self.add_file("old.mp4")
        self.database.scan_catalog()
        self.respond(name, "less_often")
        (self.root / name).rename(self.root / "new.mp4")
        self.database.scan_catalog()
        self.assertEqual(0, self.database.media(name)["available"])
        self.assertEqual(1, self.database.media(name)["less_often_count"])
        self.assertEqual(0, self.database.media("new.mp4")["less_often_count"])
        (self.root / "new.mp4").rename(self.root / name)
        self.database.scan_catalog()
        self.assertEqual(1, self.database.media(name)["available"])
        self.assertEqual(1, self.database.media(name)["less_often_count"])

    def test_windows_case_changes_reuse_original_spelling_and_history(self):
        name = self.add_file("Anime/Épisode.MP4")
        self.database.scan_catalog()
        encounter = self.respond(name, "less_often")
        (self.root / name).rename(self.root / "Anime/épisode.mp4")
        with patch(
            "pocket_media_manager.storage._path_key",
            side_effect=lambda value: ntpath.normcase(normalize_relative_path(value)),
        ):
            self.database.scan_catalog()
        self.assertEqual([name], [row["relative_path"] for row in self.database.media_rows()])
        self.assertEqual(1, self.database.media(name)["less_often_count"])
        self.assertEqual(name, self.database.encounter(encounter["id"])["media_path"])

    def test_less_often_and_enjoyment_apply_once_and_restoration_clears_count(self):
        name = self.add_file()
        self.database.scan_catalog()
        for expected in (1, 2):
            encounter = self.respond(name, "less_often")
            self.database.submit_feedback(encounter["id"], "less_often")
            self.assertEqual(expected, self.database.media(name)["less_often_count"])
        for expected in (1, 0, 0):
            encounter = self.respond(name, "enjoyed")
            self.database.submit_feedback(encounter["id"], "enjoyed")
            self.assertEqual(expected, self.database.media(name)["less_often_count"])
        self.respond(name, "less_often")
        encounter = self.open_clip(name)
        self.database.preference_for_encounter(encounter["id"], "less_often", False)
        self.assertEqual(0, self.database.media(name)["less_often_count"])
        self.assertIsNotNone(self.database.pending_encounter())
        self.database.submit_feedback(encounter["id"], "no_feedback")
        candidate = self.database.candidates()[0]
        self.assertEqual(3, candidate["enjoyed_count"])
        with self.assertRaises(Conflict):
            self.database.submit_feedback(encounter["id"], "enjoyed")

    def test_pending_feedback_and_global_preferences_survive_database_reopening(self):
        name = self.add_file()
        self.database.scan_catalog()
        self.assertEqual({"player": "vlc", "folders": []}, self.database.preferences())
        self.database.update_preferences(player="mpv", folders=["Anime"])
        encounter = self.open_clip(name)
        reopened = Database(self.database.settings)
        reopened.initialize()
        self.assertEqual(encounter["id"], reopened.pending_encounter()["id"])
        self.assertEqual({"player": "mpv", "folders": ["Anime"]}, reopened.preferences())
        with self.assertRaises(FeedbackRequired):
            reopened.create_encounter(name, "/")
        with self.assertRaises(FeedbackRequired):
            reopened.update_preferences(player="vlc")
        with self.database.connect(write=True) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO encounters(id,media_path,selected_at,return_path) VALUES ('second',?,'now','/')",
                    (name,),
                )
        reopened.submit_feedback(encounter["id"], "no_feedback")
        self.assertIsNone(self.database.pending_encounter())

    def test_scan_and_cleanup_check_preserve_catalog_on_partial_failures(self):
        first, second = self.add_file("first.mp4"), self.add_file("second.mp4")
        self.database.scan_catalog()
        (self.root / first).unlink()

        def partial_walk(*args, **kwargs):
            yield str(self.root), [], [second]
            kwargs["onerror"](PermissionError("Folder is unreadable"))

        with patch("pocket_media_manager.storage.os.walk", side_effect=partial_walk):
            with self.assertRaises(PermissionError):
                self.database.scan_catalog()
        self.assertEqual(1, self.database.media(first)["available"])
        original = self.database._catalog_path

        def partial_check(name):
            if name == second:
                raise PermissionError("File is unreadable")
            return original(name)

        with patch.object(self.database, "_catalog_path", side_effect=partial_check):
            with self.assertRaises(PermissionError):
                self.database.check_available_paths()
        self.assertEqual(1, self.database.media(first)["available"])
        self.assertEqual(1, self.database.media(second)["available"])

    def test_cleanup_checks_known_paths_and_deletes_history_including_maintenance(self):
        name = self.add_file("known.mp4")
        self.database.scan_catalog()
        encounter = self.open_clip(name)
        self.database.submit_other(encounter["id"], "fix")
        self.add_file("not-scanned.mp4")
        (self.root / name).unlink()
        with patch(
            "pocket_media_manager.storage.os.walk", side_effect=AssertionError("No directory scan")
        ):
            self.database.check_available_paths()
        self.assertEqual(0, self.database.media(name)["available"])
        with self.assertRaises(KeyError):
            self.database.media("not-scanned.mp4")
        self.database.cleanup([name])
        self.assertEqual(
            {"has_media": False, "has_encounters": False}, self.database.library_state()
        )
        self.assertEqual([], self.database.tasks())
        with self.assertRaises(KeyError):
            self.database.encounter(encounter["id"])
        self.assertTrue((self.root / "not-scanned.mp4").is_file())

    def test_reset_clears_all_app_data_but_backups_and_media_survive(self):
        name = self.add_file()
        self.database.scan_catalog()
        self.database.update_preferences(player="mpv", folders=["Anime"])
        self.respond(name, "less_often")
        self.database.reset_library()
        with self.database.connect() as conn:
            for table in ("media", "encounters", "settings"):
                self.assertEqual(0, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        backups = list((self.database.settings.metadata_root / "backups").glob("*.sqlite3"))
        self.assertEqual(1, len(backups))
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(1, conn.execute("SELECT less_often_count FROM media").fetchone()[0])
            self.assertEqual(
                '"mpv"', conn.execute("SELECT value FROM settings WHERE key='player'").fetchone()[0]
            )
        self.assertTrue((self.root / name).is_file())
        self.assertEqual({"player": "vlc", "folders": []}, self.database.preferences())
        self.assertEqual([], self.database.media_rows())

    def test_paths_preserve_valid_windows_names_and_reject_outside_paths(self):
        self.assertEqual(
            "Anime/日本語 #50% 'edit'.mp4", normalize_relative_path("Anime\\日本語 #50% 'edit'.mp4")
        )
        for value in (
            "",
            ".",
            "..",
            "../outside.mp4",
            "Anime/../outside.mp4",
            "/absolute.mp4",
            r"C:\file.mp4",
            "C:file.mp4",
            r"\\server\share\file.mp4",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_relative_path(value)


if __name__ == "__main__":
    unittest.main()
