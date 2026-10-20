"""Streaming owns the validated file and bounds Safari byte-range responses."""

import asyncio
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from pocket_media_manager.app import create_app
from pocket_media_manager.config import Settings
from pocket_media_manager.state import create_stream_token
from pocket_media_manager.streaming import media_response


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "sample.mp4"
        self.path.write_bytes(b"0123456789")
        self.settings = Settings(self.root)
        self.client = TestClient(
            create_app(self.settings), base_url="http://127.0.0.1:8787", client=("127.0.0.1", 50000)
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.url = "/stream/" + create_stream_token(self.path)

    def test_ranges_suffixes_and_head(self):
        cases = (
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
            ("bytes=8-999", b"89", "bytes 8-9/10"),
        )
        for value, content, content_range in cases:
            with self.subTest(range=value):
                response = self.client.get(self.url, headers={"Range": value})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, content)
                self.assertEqual(response.headers["content-range"], content_range)
                self.assertEqual(response.headers["content-length"], str(len(content)))
        head = self.client.head(self.url, headers={"Range": "bytes=2-5"})
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers["content-length"], "10")
        self.assertEqual(head.headers["accept-ranges"], "bytes")

    def test_if_range_matches_or_falls_back_to_whole_file(self):
        whole = self.client.get(self.url)
        for validator in (whole.headers["etag"], whole.headers["last-modified"]):
            response = self.client.get(
                self.url, headers={"Range": "bytes=0-1", "If-Range": validator}
            )
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"01")
        stale = self.client.get(
            self.url, headers={"Range": "bytes=0-1", "If-Range": '"stale-version"'}
        )
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.content, b"0123456789")
        self.assertNotIn("content-range", stale.headers)

    def test_invalid_ranges_are_bounded_and_multi_ranges_are_ignored(self):
        for value in (
            "bytes=10-",
            "bytes=4-3",
            "bytes=-0",
            "bytes=-",
            "bytes=999999999999999999999999999999-",
        ):
            with self.subTest(range=value):
                response = self.client.get(self.url, headers={"Range": value})
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.content, b"")
                self.assertEqual(response.headers["content-range"], "bytes */10")
        for value in ("bytes=0-1,8-9", "items=0-1"):
            response = self.client.get(self.url, headers={"Range": value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"0123456789")

    def test_empty_file_has_zero_length_and_rejects_byte_ranges(self):
        empty = self.root / "empty.mp4"
        empty.touch()
        url = "/stream/" + create_stream_token(empty)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["content-length"], "0")
        self.assertEqual(self.client.head(url).headers["content-length"], "0")
        invalid = self.client.get(url, headers={"Range": "bytes=0-"})
        self.assertEqual(invalid.status_code, 416)
        self.assertEqual(invalid.headers["content-range"], "bytes */0")

    def test_symlink_tokens_and_linked_parent_directories_are_rejected(self):
        alias = self.root / "alias.mp4"
        try:
            alias.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks requires local Windows permission")
        self.assertEqual(self.client.get("/stream/" + create_stream_token(alias)).status_code, 404)
        folder = self.root / "real"
        folder.mkdir()
        (folder / "clip.mp4").write_bytes(b"secret")
        linked = self.root / "linked"
        linked.symlink_to(folder, target_is_directory=True)
        self.assertEqual(
            self.client.get("/stream/" + create_stream_token(linked / "clip.mp4")).status_code, 404
        )
        metadata = self.settings.database_file
        self.assertEqual(
            self.client.get("/stream/" + create_stream_token(metadata)).status_code, 404
        )

    def test_replacement_before_open_never_serves_new_unvalidated_bytes(self):
        real_open = os.open
        replacement = self.root / "replacement.mp4"
        replacement.write_bytes(b"unvalidated replacement")

        def swap_then_open(path, flags, *args, **kwargs):
            if Path(path) == self.path:
                os.replace(replacement, self.path)
            return real_open(path, flags, *args, **kwargs)

        with patch("pocket_media_manager.streaming.os.open", side_effect=swap_then_open):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"unvalidated", response.content)

    def test_replacement_after_open_keeps_owned_original_file(self):
        real_open = os.open
        replacement = self.root / "replacement.mp4"
        replacement.write_bytes(b"unvalidated replacement")

        def open_then_swap(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if Path(path) == self.path:
                try:
                    os.replace(replacement, self.path)
                except PermissionError:
                    os.close(descriptor)
                    self.skipTest("Windows denies replacing an open media file")
            return descriptor

        with patch("pocket_media_manager.streaming.os.open", side_effect=open_then_swap):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"0123456789")

    def test_file_closes_on_head_range_failure_disconnect_and_read_error(self):
        info = self.path.stat()
        for method, headers, expected in (
            ("HEAD", [], 200),
            ("GET", [(b"range", b"bytes=11-")], 416),
        ):
            stream = io.BytesIO(b"0123456789")
            request = Request({"type": "http", "method": method, "headers": headers})
            with patch("pocket_media_manager.streaming.open_media", return_value=(stream, info)):
                response = media_response(
                    self.root, self.settings.metadata_root, self.path, request
                )
            self.assertEqual(response.status_code, expected)
            self.assertTrue(stream.closed)

        async def deliver(response, disconnect):
            async def send(message):
                if disconnect and message["type"] == "http.response.body":
                    raise OSError("client disconnected")

            async def receive():
                return {"type": "http.disconnect"}

            await response(
                {"type": "http", "method": "GET", "asgi": {"spec_version": "2.4"}}, receive, send
            )

        for content, disconnect in ((b"0123456789", True), (b"truncated", False)):
            stream = io.BytesIO(content)
            request = Request({"type": "http", "method": "GET", "headers": []})
            with patch("pocket_media_manager.streaming.open_media", return_value=(stream, info)):
                response = media_response(
                    self.root, self.settings.metadata_root, self.path, request
                )
            with self.assertRaises(Exception):
                asyncio.run(deliver(response, disconnect))
            self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
