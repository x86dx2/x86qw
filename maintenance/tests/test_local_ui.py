from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from x86qw_runtime.library import LIBRARY_PATH, add_favorite
from x86qw_runtime.local_ui import render_local_ui, write_local_ui


CLOCK = datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "x86qw_local_ui_manager_test", ROOT / "dist/installer/bin/manager.py",
)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


class LocalUiTests(unittest.TestCase):
    def test_html_is_read_only_and_escapes_library_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            add_favorite(
                target,
                "quake.example:27500",
                title="<script>alert(1)</script>",
                now=CLOCK,
            )
            html = render_local_ui(target)
            self.assertIn("owner-only", html)
            self.assertIn("installation", html)
            self.assertIn("quake.example:27500", html)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertFalse((target / ".x86qw-ui.html").exists())

    def test_write_stays_outside_the_installation_and_creates_no_install_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "install"
            output = root / "panel.html"
            before = set(root.rglob("*"))
            written = write_local_ui(target, output)
            after = set(root.rglob("*"))
            self.assertEqual(output.resolve(), written)
            self.assertTrue(output.is_file())
            self.assertIn("owner-only", output.read_text(encoding="utf-8"))
            self.assertEqual({output.resolve()}, {path.resolve() for path in after} - {path.resolve() for path in before})
            self.assertFalse(target.exists())

    def test_cli_prints_the_html_path_and_does_not_mutate_the_target(self) -> None:
        manager.console.configure(verbose=False, no_color=True)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            target.mkdir()
            output = Path(temporary) / "ui.html"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                result = manager.main(["ui", "--output", str(output), str(target)])
            self.assertEqual(0, result)
            self.assertEqual(output.resolve().as_posix(), stdout.getvalue().strip())
            self.assertIn("owner-only", output.read_text(encoding="utf-8"))
            self.assertEqual(["install", "ui.html"], sorted(path.name for path in Path(temporary).iterdir()))


if __name__ == "__main__":
    unittest.main()
