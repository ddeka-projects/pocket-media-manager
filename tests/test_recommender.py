"""Frequency penalties and feedback timing preserve their distinct meanings."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pocket_media_manager.config import Settings
from pocket_media_manager.recommender import compute_weight, pick_weighted
from pocket_media_manager.storage import Database


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

    def candidate(self, **changes):
        media = {"favorite": False, "not_for_me": False, "less_often_count": 0}
        media.update(changes)
        return media

    def test_repeated_less_often_halves_weight_beyond_previous_minimum(self):
        previous = 2.0
        for count in range(13):
            with self.subTest(count=count):
                weight = compute_weight(self.candidate(less_often_count=count), self.now)
                self.assertEqual(weight, 0.5**count)
                self.assertLess(weight, previous)
                previous = weight
        self.assertLess(previous, 0.01)

    def test_even_extreme_less_often_count_can_be_the_only_recommendation(self):
        media = self.candidate(
            less_often_count=100_000,
            last_offered_at=self.now.isoformat(),
            last_enjoyed_at=self.now.isoformat(),
        )
        self.assertGreater(compute_weight(media, self.now), 0)
        self.assertLess(compute_weight(media, self.now), 1e-100)
        self.assertIs(pick_weighted([media], self.now), media)

    def test_recent_offer_penalty_wears_off_over_five_minutes(self):
        for minutes, expected in ((0, 0.1), (2.5, 0.5), (5, 1.0), (6, 1.0)):
            with self.subTest(minutes=minutes):
                media = self.candidate(
                    last_offered_at=(self.now - timedelta(minutes=minutes)).isoformat()
                )
                self.assertAlmostEqual(compute_weight(media, self.now), expected)

    def test_recent_enjoyment_reduction_wears_off_over_twenty_four_hours(self):
        for hours, expected in ((0, 0.08), (12, 0.5), (24, 1.0), (25, 1.0)):
            with self.subTest(hours=hours):
                media = self.candidate(
                    last_enjoyed_at=(self.now - timedelta(hours=hours)).isoformat()
                )
                self.assertAlmostEqual(compute_weight(media, self.now), expected)

    def test_explicit_cooldowns_expire_at_their_response_time_boundaries(self):
        for field, hours in (("last_too_soon_at", 72), ("last_not_in_mood_at", 48)):
            with self.subTest(field=field):
                media = self.candidate(**{field: self.now.isoformat()})
                expires = self.now + timedelta(hours=hours)
                with self.assertRaises(ValueError):
                    pick_weighted([media], expires - timedelta(microseconds=1))
                self.assertIs(pick_weighted([media], expires), media)
        with self.assertRaises(ValueError):
            pick_weighted([self.candidate(not_for_me=True)], self.now)

    def test_stored_selection_and_response_times_drive_the_correct_calculations(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clip.mp4").write_bytes(b"media")
            database = Database(Settings(root))
            database.initialize()
            database.scan_catalog()
            selected = self.now - timedelta(hours=96)
            enjoyed = self.now - timedelta(hours=12)
            with patch("pocket_media_manager.storage.now_iso", return_value=selected.isoformat()):
                encounter = database.create_encounter("clip.mp4", "/")
            with database.playback(encounter["id"]) as conn:
                database.mark_launch(conn, encounter["id"])
            with patch("pocket_media_manager.storage.now_iso", return_value=enjoyed.isoformat()):
                database.submit_feedback(encounter["id"], "enjoyed")
            media = database.candidates()[0]
            self.assertEqual(media["last_offered_at"], selected.isoformat())
            self.assertEqual(media["last_enjoyed_at"], enjoyed.isoformat())
            self.assertEqual(media["enjoyed_count"], 1)
            self.assertAlmostEqual(compute_weight(media, self.now), 1.25 * 0.5)

            selected = self.now - timedelta(hours=4)
            answered = self.now - timedelta(hours=1)
            with patch("pocket_media_manager.storage.now_iso", return_value=selected.isoformat()):
                encounter = database.create_encounter("clip.mp4", "/")
            with database.playback(encounter["id"]) as conn:
                database.mark_launch(conn, encounter["id"])
            with patch("pocket_media_manager.storage.now_iso", return_value=answered.isoformat()):
                database.submit_feedback(encounter["id"], "too_soon")
            media = database.candidates()[0]
            self.assertEqual(media["last_offered_at"], selected.isoformat())
            self.assertEqual(media["last_too_soon_at"], answered.isoformat())
            with self.assertRaises(ValueError):
                pick_weighted([media], selected + timedelta(hours=72))
            self.assertIs(pick_weighted([media], answered + timedelta(hours=72)), media)


if __name__ == "__main__":
    unittest.main()
