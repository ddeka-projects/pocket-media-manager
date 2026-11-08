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
from urllib.parse import parse_qs, quote, urlsplit

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
    def client(self, *, scan=False):
        application = create_app(self.settings)
        with TestClient(
            application, base_url=ORIGIN, client=("127.0.0.1", 50000), headers={"Origin": ORIGIN}
        ) as client:
            if scan and application.state.database.pending_encounter() is None:
                application.state.database.scan_catalog()
            yield client

    def claim(self, client, page="page-one", takeover=False):
        response = client.post(
            "/api/controller/takeover" if takeover else "/api/controller/claim",
            json={"page_id": page},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-Page-Id": page, "X-Controller-Token": response.json()["token"]}

    def test_initial_document_contains_no_library_and_each_page_needs_control(self):
        with self.client(scan=True) as client:
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

    def test_startup_and_home_only_check_catalog_emptiness(self):
        with patch("pocket_media_manager.storage.os.walk", side_effect=AssertionError("no scan")):
            with self.client() as client:
                owner = self.claim(client)
                database = client.app.state.database
                with (
                    patch.object(database, "candidates", side_effect=AssertionError("no weights")),
                    patch.object(database, "tasks", side_effect=AssertionError("no task counts")),
                ):
                    page = client.get("/", headers=owner)
                self.assertEqual(page.status_code, 200)
                self.assertIn("Scan Library", page.text)
                self.assertIn('href="/stream"', page.text)
                self.assertIn('action="/cleanup/check"', page.text)
                for target in (
                    'action="/recommend"',
                    'href="/select"',
                    'href="/explore"',
                    'href="/feedback/addressed"',
                    'href="/reset"',
                ):
                    self.assertNotIn(target, page.text)
                self.assertEqual(database.media_rows(), [])
                self.assertEqual(client.post("/next", headers=owner).status_code, 400)
                self.assertNotIn("set-cookie", page.headers)
                self.assertEqual(client.get("/media/_stream/episode.mp4").content, b"episode bytes")

    def test_home_keeps_actions_when_catalogued_media_is_ineligible(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            database = client.app.state.database
            encounter = database.create_encounter("folder/clip.mp4", "/")
            database.submit_feedback(encounter["id"], "not_for_me")
            with (
                patch.object(database, "candidates", side_effect=AssertionError("no weights")),
                patch.object(database, "tasks", side_effect=AssertionError("no task counts")),
            ):
                page = client.get("/", headers=owner).text
            for target in (
                'action="/recommend"',
                'href="/select"',
                'href="/explore"',
                'href="/feedback/addressed"',
                'href="/reset"',
            ):
                self.assertIn(target, page)
            self.assertIn("No outstanding", client.get("/feedback/addressed", headers=owner).text)

    def test_player_and_folder_choices_are_shared_without_cookies(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            self.assertEqual(client.app.state.database.preferences()["player"], "vlc")
            saved = client.post("/settings/player", headers=owner, data={"player": "mpv"})
            self.assertEqual(saved.status_code, 200)
            with patch("pocket_media_manager.playback._launch_mpv"):
                opened = client.post(
                    "/recommend/selected", headers=owner, data={"folders": "folder"}
                )
            self.assertEqual(opened.status_code, 200)
            pending = client.app.state.database.pending_encounter()
            client.post(
                "/feedback/no_feedback", headers=owner, data={"encounter_id": pending["id"]}
            )
            client.cookies.clear()
            second = self.claim(client, "second-device", takeover=True)
            home = client.get("/", headers=second)
            self.assertIn('class="active" data-player="mpv"', home.text)
            selections = client.get("/select", headers=second).text
            self.assertIn('name="folders" value="folder" checked', selections)
            self.assertNotIn("set-cookie", home.headers)
        with self.client() as client:
            owner = self.claim(client, "after-restart")
            self.assertEqual(
                client.app.state.database.preferences(), {"player": "mpv", "folders": ["folder"]}
            )
            self.assertIn('class="active" data-player="mpv"', client.get("/", headers=owner).text)

    def test_successful_replay_clears_failure_and_keeps_pending_encounter_after_takeover(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            with patch(
                "pocket_media_manager.playback._launch_mpv", side_effect=FileNotFoundError()
            ):
                failed = client.post("/recommend", headers=owner, data={"player": "mpv"})
            self.assertEqual(failed.status_code, 200)
            self.assertIn("mpv was not found", failed.text)
            database = client.app.state.database
            encounter = database.pending_encounter()
            self.assertEqual(encounter["launch_status"], "failed")
            second = self.claim(client, "retry-device", takeover=True)
            restored = client.get(f"/feedback/{encounter['id']}", headers=second)
            self.assertIn("last playback request failed", restored.text)
            self.assertIn("Open Player — mpv (PC)", restored.text)
            with patch("pocket_media_manager.playback._launch_mpv") as launch:
                replayed = client.post(f"/encounters/{encounter['id']}/play", headers=second)
            self.assertEqual(replayed.status_code, 200)
            launch.assert_called_once_with(
                self.settings, self.settings.media_root / "folder" / "clip.mp4"
            )
            self.assertNotIn('class="error"', replayed.text)
            self.assertNotIn("failed", replayed.text)
            pending = database.pending_encounter()
            self.assertEqual(pending["id"], encounter["id"])
            self.assertEqual(pending["launch_status"], "requested")
            self.assertEqual(database.preferences()["player"], "mpv")
            with database.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0], 1)

    def test_stable_urls_are_reused_by_replay_new_encounters_and_stream(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            first = client.post("/next", headers=owner).json()
            url = first["stream_url"]
            self.assertEqual(url, ORIGIN + "/media/folder/clip.mp4")
            self.assertEqual(parse_qs(urlsplit(first["player_url"]).query)["url"], [url])
            replay = client.post(f"/encounters/{first['encounter_id']}/play", headers=owner)
            self.assertEqual(replay.status_code, 200)
            with client.app.state.database.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0], 1)
            client.post(
                "/feedback/no_feedback", headers=owner, data={"encounter_id": first["encounter_id"]}
            )
            second = client.post("/next", headers=owner).json()
            self.assertNotEqual(first["encounter_id"], second["encounter_id"])
            self.assertEqual(second["stream_url"], url)
            client.post(
                "/feedback/no_feedback",
                headers=owner,
                data={"encounter_id": second["encounter_id"]},
            )
            stream_first = client.post(
                "/stream/play", headers=owner, data={"path": "episode.mp4"}
            ).json()
            stream_second = client.post(
                "/stream/play", headers=owner, data={"path": "episode.mp4"}
            ).json()
            self.assertEqual(stream_first["player_url"], stream_second["player_url"])
            stream_url = parse_qs(urlsplit(stream_first["player_url"]).query)["url"][0]
            self.assertEqual(stream_url, ORIGIN + "/media/_stream/episode.mp4")
            with client.app.state.database.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM media").fetchone()[0], 1)
        with self.client() as client:
            self.assertEqual(client.get(url).content, b"test video bytes")
            self.assertEqual(client.get(stream_url).content, b"episode bytes")

    def test_playback_preserves_unicode_spaces_and_url_punctuation(self):
        (self.root / "folder" / "clip.mp4").unlink()
        name = "雪 % + # & ' [cut];=.mp4"
        relative = "folder/" + name
        (self.root / relative).write_bytes(b"special filename")
        with self.client(scan=True) as client:
            owner = self.claim(client)
            opened = client.post("/next", headers=owner)
            self.assertEqual(opened.status_code, 200)
            result = opened.json()
            expected = ORIGIN + "/media/" + quote(relative, safe="/")
            self.assertEqual(result["stream_url"], expected)
            self.assertEqual(parse_qs(urlsplit(result["player_url"]).query)["url"], [expected])
            self.assertEqual(client.get(expected).content, b"special filename")
            self.assertEqual(client.app.state.database.media(relative)["relative_path"], relative)
            client.post(
                "/feedback/no_feedback",
                headers=owner,
                data={"encounter_id": result["encounter_id"]},
            )
            explored = client.post("/explore/play", headers=owner, data={"media_path": relative})
            self.assertEqual(explored.status_code, 200)
            self.assertEqual(client.app.state.database.pending_encounter()["media_path"], relative)

    def test_cleanup_checks_stored_paths_then_deletes_media_and_encounters(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            database = client.app.state.database
            encounter = database.create_encounter("folder/clip.mp4", "/")
            database.submit_other(encounter["id"], "fix")
            (self.root / "folder" / "clip.mp4").unlink()
            unseen = self.root / "unseen.mp4"
            unseen.write_bytes(b"not discovered by cleanup")
            client.get("/cleanup", headers=owner)
            self.assertTrue(database.media("folder/clip.mp4")["available"])
            with patch(
                "pocket_media_manager.storage.os.walk", side_effect=AssertionError("no scan")
            ):
                checked = client.post("/cleanup/check", headers=owner)
            self.assertEqual(checked.status_code, 200)
            self.assertFalse(database.media("folder/clip.mp4")["available"])
            self.assertIn("clip.mp4", client.get("/cleanup", headers=owner).text)
            with self.assertRaises(KeyError):
                database.media("unseen.mp4")
            removed = client.post(
                "/cleanup", headers=owner, data={"media_paths": "folder/clip.mp4"}
            )
            self.assertEqual(removed.status_code, 200)
            with database.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM media").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0], 0)
            self.assertTrue(unseen.exists())
            self.assertTrue(list((self.settings.metadata_root / "backups").glob("*.sqlite3")))

    def test_reset_library_clears_all_tables_and_restores_defaults_lazily(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            database = client.app.state.database
            database.update_preferences(player="mpv", folders=["folder"])
            encounter = database.create_encounter("folder/clip.mp4", "/")
            database.submit_other(encounter["id"], "trim")
            warning = client.get("/reset", headers=owner).text
            self.assertIn("Reset Library", warning)
            self.assertIn("settings", warning.lower())
            reset = client.post("/reset", headers=owner)
            self.assertEqual(reset.status_code, 200)
            with database.connect() as conn:
                for table in ("media", "encounters", "settings"):
                    self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertTrue((self.root / "folder" / "clip.mp4").exists())
            self.assertTrue((self.root / "_stream" / "episode.mp4").exists())
            self.assertTrue(list((self.settings.metadata_root / "backups").glob("*.sqlite3")))
            home = client.get("/", headers=owner).text
            self.assertEqual(database.preferences(), {"player": "vlc", "folders": []})
            self.assertNotIn('href="/reset"', home)
            self.assertNotIn('href="/explore"', home)
            self.assertEqual(client.post("/library/scan", headers=owner).status_code, 200)
            self.assertEqual(len(database.media_rows()), 1)

    def test_feedback_and_preferences_survive_takeover_and_restart(self):
        with self.client(scan=True) as client:
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
        with self.client(scan=True) as client:
            fresh = self.claim(client, "fresh-page")
            pending = client.get("/last", headers=fresh).json()
            self.assertEqual(pending["encounter"]["id"], encounter_id)
            self.assertTrue(pending["media"]["favorite"])
            self.assertEqual(client.get(stream_url).status_code, 200)
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
            self.assertTrue(database.media("folder/clip.mp4")["favorite"])
            with self.assertRaisesRegex(ValueError, "No clips fit"):
                pick_weighted(database.candidates())
        with self.client(scan=True) as client:
            self.assertTrue(client.app.state.database.media("folder/clip.mp4")["favorite"])
            with self.assertRaises(ValueError):
                pick_weighted(client.app.state.database.candidates())

    def test_takeover_waits_for_authorized_playback_operation(self):
        started = threading.Event()
        finish = threading.Event()

        def launch(_settings, _path):
            started.set()
            self.assertTrue(finish.wait(5))

        with self.client(scan=True) as client:
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
        with self.client(scan=True) as client:
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
        with self.client(scan=True) as first:
            headers = self.claim(first)
            encounter_id = first.post("/next", headers=headers).json()["encounter_id"]
            with self.assertRaisesRegex(RuntimeError, "Another server"):
                with self.client():
                    pass
            self.assertEqual(first.app.state.database.pending_encounter()["id"], encounter_id)

    def test_maintenance_and_scan_preserve_existing_feedback(self):
        with self.client(scan=True) as client:
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
                    "/feedback/addressed",
                    headers=headers,
                    data={"media_paths": task["relative_path"]},
                ).status_code,
                200,
            )
            self.assertEqual(len(database.candidates()), 1)
            database.scan_catalog()
            self.assertEqual(database.encounter(identifier)["feedback"], "fix")

    def test_catalog_refresh_fails_without_marking_library_unavailable(self):
        with self.client(scan=True) as client:
            database = client.app.state.database
            with patch(
                "pocket_media_manager.storage.os.walk", side_effect=OSError("unreadable directory")
            ):
                with self.assertRaises(OSError):
                    database.scan_catalog()
            self.assertEqual(len(database.media_rows()), 1)

    def test_selection_page_lists_folder_cards_with_clip_counts(self):
        (self.root / "series").mkdir()
        (self.root / "series" / "one.mp4").write_bytes(b"one")
        (self.root / "series" / "two.mp4").write_bytes(b"two")
        with self.client(scan=True) as client:
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
        with self.client(scan=True) as client:
            gone.unlink()
            client.app.state.database.scan_catalog()
            page = client.get("/cleanup", headers=self.claim(client)).text
        self.assertIn('<span class="tag">series</span> episode.mp4', page)
        self.assertIn('data-select="all"', page)
        self.assertIn('<button class="primary" form="cleanup-form">', page)
        self.assertIn('name="media_paths" value="series/season/episode.mp4"', page)
        self.assertNotIn(">series/season", page)

    def test_folder_pages_show_title_and_tappable_trail_instead_of_paths(self):
        (self.root / "series" / "season").mkdir(parents=True)
        (self.root / "series" / "season" / "episode.mp4").write_bytes(b"episode")
        with self.client(scan=True) as client:
            owner = self.claim(client)
            top = client.get("/explore", headers=owner).text
            inner = client.get("/explore?path=series/season", headers=owner).text
            stream = client.get("/stream", headers=owner).text
        self.assertIn("<h1>Explore</h1>", top)
        self.assertNotIn('class="crumbs"', top)
        self.assertIn("<h1>season</h1>", inner)
        self.assertIn('<a href="/explore">Explore</a>', inner)
        self.assertIn('<a href="/explore?path=series">series</a>', inner)
        self.assertNotIn('href="/explore?path=series%2Fseason"', inner)
        for page in (top, inner, stream):
            self.assertNotIn(str(self.root.resolve()), page)

    def test_explore_retains_catalog_spelling_after_windows_case_resolution(self):
        with self.client(scan=True) as client:
            owner = self.claim(client)
            original_resolve = Path.resolve
            folder = self.settings.media_root / "folder"

            def windows_resolve(path, *args, **kwargs):
                resolved = original_resolve(path, *args, **kwargs)
                # A Windows case-only folder rename can alter resolve()'s spelling.
                return folder.with_name("FOLDER") if resolved == folder else resolved

            with patch("pocket_media_manager.service.Path.resolve", windows_resolve):
                page = client.get("/explore?path=folder", headers=owner)
            self.assertEqual(page.status_code, 200)
            self.assertIn("clip.mp4", page.text)
            self.assertIn('name="media_path" value="folder/clip.mp4"', page.text)

    def test_home_lists_library_and_stream_locations(self):
        with self.client(scan=True) as client:
            page = client.get("/", headers=self.claim(client)).text
        self.assertIn(f"<dt>Library</dt><dd>{self.root.resolve()}</dd>", page)
        self.assertIn(f"<dt>Stream</dt><dd>{self.root.resolve() / '_stream'}</dd>", page)

    def test_backup_is_valid_and_restore_preserves_feedback(self):
        with self.client(scan=True) as client:
            database = client.app.state.database
            media = database.media_rows()[0]
            encounter = database.create_encounter(media["relative_path"], "/")
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
        database.scan_catalog()
        barrier = threading.Barrier(2)

        def create():
            barrier.wait()
            try:
                return database.create_encounter("folder/clip.mp4", "/")["id"]
            except FeedbackRequired:
                return None

        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: create(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)


if __name__ == "__main__":
    unittest.main()
