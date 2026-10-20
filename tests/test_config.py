from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pocket_media_manager.config import load_settings


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = self.root / "settings.env"
        self.env.write_text(
            f'POCKET_MEDIA_MANAGER_MEDIA_ROOT="{self.root}"\nPOCKET_MEDIA_MANAGER_PORT=8787\n',
            encoding="utf-8",
        )

    def test_real_environment_overrides_file_without_mutating_environment(self):
        settings = load_settings(self.env, environ={"POCKET_MEDIA_MANAGER_PORT": "8899"})
        self.assertEqual(settings.port, 8899)
        self.assertEqual(settings.public_base_url, "http://127.0.0.1:8899")
        self.assertEqual(settings.media_root, self.root.resolve())
        with self.assertRaises(AttributeError):
            settings.port = 1
        self.assertIsInstance(settings.supported_extensions, frozenset)

    def test_literal_windows_paths_are_not_unescaped(self):
        self.env.write_text(
            self.env.read_text() + 'POCKET_MEDIA_MANAGER_MPV_PATH="C:\\new\\tools\\mpv.exe"\n'
        )
        settings = load_settings(self.env, environ={})
        self.assertEqual(settings.mpv_path, r"C:\new\tools\mpv.exe")

    def test_invalid_values_are_rejected(self):
        for name, value in (
            ("PORT", "65536"),
            ("PORT", "no"),
            ("PUBLIC_BASE_URL", "http://example/path"),
            ("PUBLIC_BASE_URL", "http://example:99999"),
            ("STREAM_FOLDER", ".."),
            ("STREAM_FOLDER", ".pocket-media-manager"),
            ("SUPPORTED_EXTENSIONS", ".mp4,../"),
            ("HOST", ""),
            ("MEDIA_ROOT", "relative/path"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                load_settings(self.env, environ={"POCKET_MEDIA_MANAGER_" + name: value})

    def test_missing_media_root_is_actionable(self):
        with self.assertRaisesRegex(ValueError, "POCKET_MEDIA_MANAGER_MEDIA_ROOT"):
            load_settings(self.root / "missing.env", environ={})


if __name__ == "__main__":
    unittest.main()
