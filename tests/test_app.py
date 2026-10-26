"""Behavioral coverage for ownership, durable feedback, request safety, and playback."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import errno
import os
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from pocket_media_manager.app import create_app
from pocket_media_manager.config import Settings
from pocket_media_manager.recommender import pick_weighted
from pocket_media_manager.storage import Database, FeedbackRequired

ORIGIN = "http://127.0.0.1:8787"


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "folder").mkdir()
        (self.root / "folder" / "clip.mp4").write_bytes(b"test video bytes")
        (self.root / "_stream").mkdir()
        (self.root / "_stream" / "episode.mp4").write_bytes(b"episode bytes")
        self.settings = Settings(self.root, public_base_url=ORIGIN)

    @contextmanager
    def client(self):
        application = create_app(self.settings)
        with TestClient(
            application, base_url=ORIGIN, client=("127.0.0.1", 50000), headers={"Origin": ORIGIN}
        ) as client:
            yield client

    def claim(self, client, page="page-one", takeover=False):
        response = client.post(
            "/api/controller/takeover" if takeover else "/api/controller/claim",
            json={"page_id": page},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-Page-Id": page, "X-Controller-Token": response.json()["token"]}

    def test_initial_document_contains_no_library_and_each_page_needs_control(self):
        with self.client() as client:
            initial = client.get("/explore", headers={"Accept": "text/html"})
            self.assertNotIn("clip.mp4", initial.text)
            self.assertIn("Connecting", initial.text)
            first = self.claim(client)
            self.assertIn("Explore", client.get("/explore", headers=first).text)
            self.assertEqual(client.get("/last").status_code, 423)
            second = client.post("/api/controller/claim", json={"page_id": "page-two"})
            self.assertEqual(second.status_code, 423)
            self.assertEqual(second.json()["error"]["code"], "controller_occupied")
            next_page = self.claim(client, "page-two", takeover=True)
            self.assertEqual(
                client.get("/", headers=first).json()["error"]["code"], "controller_transferred"
            )
            self.assertEqual(client.get("/", headers=next_page).status_code, 200)
            release = client.post(
                "/api/controller/release",
                json={"page_id": "page-two", "token": next_page["X-Controller-Token"]},
            )
            self.assertEqual(release.status_code, 200)
            self.assertEqual(
                client.get("/", headers=next_page).json()["error"]["code"], "controller_required"
            )

    def test_feedback_and_preferences_survive_takeover_and_restart(self):
        with self.client() as client:
            first = self.claim(client)
            encounter = client.post("/next", headers=first).json()
            encounter_id = encounter["encounter_id"]
            stream_url = encounter["stream_url"]
            self.assertEqual(
                client.post(
                    "/feedback/favorite",
                    headers=first,
                    data={"encounter_id": encounter_id, "favorite": "true"},
                ).status_code,
                200,
            )
            second = self.claim(client, "page-two", takeover=True)
            self.assertEqual(
                client.post(
                    "/feedback/enjoyed", headers=first, data={"encounter_id": encounter_id}
                ).status_code,
                423,
            )
            pending = client.get("/explore", headers=second)
            self.assertEqual(pending.status_code, 409)
            self.assertEqual(pending.json()["error"]["encounter_id"], encounter_id)
            # Existing VLC playback does not require browser control.
            self.assertEqual(client.get(stream_url).content, b"test video bytes")
            self.assertEqual(client.head(stream_url).status_code, 200)
            self.assertEqual(
                client.get(stream_url, headers={"Range": "bytes=0-3"}).content, b"test"
            )
        with self.client() as client:
            fresh = self.claim(client, "fresh-page")
            pending = client.get("/last", headers=fresh).json()
            self.assertEqual(pending["encounter"]["id"], encounter_id)
            self.assertTrue(pending["media"]["favorite"])
            self.assertEqual(client.get(stream_url).status_code, 404)
            self.assertEqual(
                client.post(
                    "/feedback/too_soon", headers=fresh, data={"encounter_id": encounter_id}
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/feedback/too_soon", headers=fresh, data={"encounter_id": encounter_id}
                ).status_code,
                200,
            )
            conflict = client.post(
                "/feedback/enjoyed", headers=fresh, data={"encounter_id": encounter_id}
            )
            self.assertEqual(conflict.status_code, 409)
            database = client.app.state.database
            self.assertTrue(database.media(1)["favorite"])
            with self.assertRaisesRegex(ValueError, "No clips fit"):
                pick_weighted(database.candidates())
        with self.client() as client:
            self.assertTrue(client.app.state.database.media(1)["favorite"])
            with self.assertRaises(ValueError):
                pick_weighted(client.app.state.database.candidates())

    def test_takeover_waits_for_authorized_playback_operation(self):
        started = threading.Event()
        finish = threading.Event()

        def launch(_settings, _path):
            started.set()
            self.assertTrue(finish.wait(5))

        with self.client() as client:
            first = self.claim(client)
            with (
                patch("pocket_media_manager.playback._launch_mpv", launch),
                ThreadPoolExecutor(2) as executor,
            ):
                playback = executor.submit(
                    client.post, "/next", headers=first, data={"player": "mpv"}
                )
                self.assertTrue(started.wait(5))
                takeover = executor.submit(
                    client.post, "/api/controller/takeover", json={"page_id": "second-page"}
                )
                time.sleep(0.05)
                self.assertFalse(takeover.done())
                finish.set()
                self.assertEqual(playback.result().status_code, 200)
                self.assertEqual(takeover.result().status_code, 200)
            self.assertEqual(client.get("/last", headers=first).status_code, 423)

    def test_request_protections_and_validation_use_structured_errors(self):
        with self.client() as client:
            health = client.get("/health").json()
            self.assertTrue(health["ok"])
            self.assertEqual(health["app"], "pocket_media_manager")
            self.assertTrue(health["instance_id"])
            cross_site = client.post(
                "/api/controller/takeover",
                json={"page_id": "evil"},
                headers={"Origin": "https://evil.example"},
            )
            self.assertEqual(cross_site.status_code, 403)
            self.assertEqual(cross_site.json()["error"]["code"], "origin_rejected")
            self.assertEqual(
                client.get("/health", headers={"Host": "evil.example:8787"}).status_code, 403
            )
            headers = self.claim(client)
            validation = client.post("/feedback/favorite", headers=headers)
            self.assertEqual(validation.status_code, 422)
            self.assertIn("error", validation.json())
            self.assertEqual(client.get("/next", headers=headers).status_code, 405)
            missing_origin = {**headers, "Origin": "null"}
            self.assertEqual(client.post("/next", headers=missing_origin).status_code, 403)
            bad_path = client.get("/explore?path=../../", headers=headers)
            self.assertEqual(bad_path.status_code, 400)
            invalid_json = client.post(
                "/api/controller/takeover",
                content="[]",
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(invalid_json.status_code, 400)
        with TestClient(
            create_app(self.settings), base_url=ORIGIN, client=("8.8.8.8", 50000)
        ) as client:
            self.assertEqual(client.get("/health").status_code, 403)

    def test_duplicate_servers_rejected_without_altering_persistent_data(self):
        with self.client() as first:
            headers = self.claim(first)
            encounter_id = first.post("/next", headers=headers).json()["encounter_id"]
            with self.assertRaisesRegex(RuntimeError, "Another server"):
                with self.client():
                    pass
            self.assertEqual(first.app.state.database.pending_encounter()["id"], encounter_id)

    def test_maintenance_and_scan_preserve_existing_feedback(self):
        with self.client() as client:
            headers = self.claim(client)
            identifier = client.post("/next", headers=headers).json()["encounter_id"]
            response = client.post(
                "/feedback/other/save",
                headers=headers,
                data={"encounter_id": identifier, "feedback_type": "fix"},
            )
            self.assertEqual(response.status_code, 200)
            database = client.app.state.database
            self.assertEqual(database.candidates(), [])
            task = database.tasks()[0]
            self.assertEqual(
                client.post(
                    "/feedback/addressed", headers=headers, data={"task_ids": task["id"]}
                ).status_code,
                200,
            )
            self.assertEqual(len(database.candidates()), 1)
            database.refresh_catalog()
            self.assertEqual(database.encounter(identifier)["resolution"], "other_feedback")

    def test_catalog_refresh_fails_without_marking_library_unavailable(self):
        with self.client() as client:
            database = client.app.state.database
            with patch(
                "pocket_media_manager.storage.os.walk", side_effect=OSError("unreadable directory")
            ):
                with self.assertRaises(OSError):
                    database.refresh_catalog()
            self.assertEqual(len(database.media_rows()), 1)

    def test_selection_page_lists_folder_cards_with_clip_counts(self):
        (self.root / "series").mkdir()
        (self.root / "series" / "one.mp4").write_bytes(b"one")
        (self.root / "series" / "two.mp4").write_bytes(b"two")
        with self.client() as client:
            page = client.get("/select", headers=self.claim(client)).text
        self.assertEqual(page.count('class="explore-card select-card"'), 2)
        self.assertIn('name="folders" value="folder"', page)
        self.assertIn("1 clip<", page)
        self.assertIn("2 clips<", page)
        self.assertNotIn("_stream", page)

    def test_cleanup_lists_folder_tag_and_file_name(self):
        (self.root / "series" / "season").mkdir(parents=True)
        gone = self.root / "series" / "season" / "episode.mp4"
        gone.write_bytes(b"episode")
        with self.client() as client:
            gone.unlink()
            client.app.state.database.refresh_catalog()
            page = client.get("/cleanup", headers=self.claim(client)).text
        self.assertIn('<span class="tag">series</span> episode.mp4', page)
        self.assertIn('data-select="all"', page)
        self.assertIn('<button class="primary" form="cleanup-form">', page)
        self.assertNotIn("series/season", page)

    def test_backup_is_valid_and_restore_preserves_feedback(self):
        with self.client() as client:
            database = client.app.state.database
            media = database.media_rows()[0]
            encounter = database.create_encounter(
                media["id"], "explore", {"return_path": "/", "player": "vlc"}
            )
            database.preference_for_encounter(encounter["id"], "favorite", True)
            database.submit_feedback(encounter["id"], "enjoyed")
            backup = database.backup()
            with closing(sqlite3.connect(backup)) as snapshot:
                self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(snapshot.execute("SELECT favorite FROM media").fetchone()[0], 1)
                self.assertEqual(
                    snapshot.execute("SELECT feedback FROM encounters").fetchone()[0], "enjoyed"
                )
            self.assertFalse(list(backup.parent.glob("*.tmp")))

    @unittest.skipIf(os.name == "nt", "Windows enforces this natively")
    def test_backup_flushes_snapshot_through_a_writable_handle(self):
        import fcntl

        # Emulate Windows, where fsync on a read-only handle fails with EBADF.
        real_fsync = os.fsync

        def windows_fsync(descriptor):
            if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
                raise OSError(errno.EBADF, "Bad file descriptor")
            real_fsync(descriptor)

        database = Database(self.settings)
        database.initialize()
        with patch("pocket_media_manager.storage.os.fsync", side_effect=windows_fsync):
            backup = database.backup()
        self.assertTrue(backup.is_file())

    def test_simultaneous_database_encounters_have_one_winner(self):
        database = Database(self.settings)
        database.initialize()
        database.refresh_catalog()
        barrier = threading.Barrier(2)

        def create():
            barrier.wait()
            try:
                return database.create_encounter(
                    1, "explore", {"return_path": "/", "player": "vlc"}
                )["id"]
            except FeedbackRequired:
                return None

        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: create(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)


if __name__ == "__main__":
    unittest.main()
