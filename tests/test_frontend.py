"""Execute persistent-shell and per-document ownership behavior without a browser."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendTests(unittest.TestCase):
    def test_shell_navigation_controller_isolation_and_late_responses(self):
        runtime = shutil.which("node")
        jsc = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")
        if runtime is None and jsc.is_file():
            runtime = str(jsc)
        if runtime is None:
            self.skipTest("Optional JavaScript behavior checks require Node.js or JavaScriptCore.")
        source = (ROOT / "pocket_media_manager" / "static" / "app.js").read_text(encoding="utf-8")
        before, marker, after = source.rpartition("  takeControl();")
        self.assertTrue(marker, "The test must replace the initial connection call.")
        source = (
            before
            + "  globalThis.client = { takeControl, navigate, waiting, readState: () => ({ token, busy, pageId }) };"
            + after
        )
        script = "\n".join(
            (
                (ROOT / "tests" / "frontend_harness.js").read_text(encoding="utf-8"),
                "const CLIENT_SOURCE = " + json.dumps(source) + ";",
                source,
                (ROOT / "tests" / "frontend_checks.js").read_text(encoding="utf-8"),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checks.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [runtime, str(path)], capture_output=True, text=True, timeout=20
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Frontend behavior checks passed", result.stdout)
