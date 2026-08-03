from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROLS = ROOT / "docs" / "controls"
PROFILES = ("ktx", "final-arena", "pro-x", "team-fortress", "td2")
LAYOUTS = ("windows-ansi", "macos-en-us", "keychron-k3-v3")
VIEWPORTS = ((1366, 768), (1024, 768))
HIGHLIGHT_GROUPS = {"movement", "weapons", "team", "match", "mode", "bots"}


class HighlightedKeyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.highlighted = 0
        self.missing_descriptions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if "key" not in classes or not classes.intersection(HIGHLIGHT_GROUPS):
            return
        self.highlighted += 1
        description = (values.get("data-bind-descriptions") or "").strip()
        aria_label = (values.get("aria-label") or "").strip()
        if not description or description != aria_label:
            self.missing_descriptions.append(values.get("data-key") or "<sem-id>")


def chrome_executable() -> str | None:
    configured = os.environ.get("CHROME")
    candidates = [
        configured,
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def canonical_text_bytes(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


class ControlMapTests(unittest.TestCase):
    def test_all_keyboard_variants_are_selectable_and_rendered(self) -> None:
        source = (CONTROLS / "index.html").read_text(encoding="utf-8")
        render_script = (CONTROLS / "render.sh").read_text(encoding="utf-8")
        for layout in LAYOUTS:
            with self.subTest(layout=layout):
                self.assertIn(f'data-layout="{layout}"', source)
                self.assertIn(layout, render_script)

    def test_every_highlighted_bind_requires_a_description(self) -> None:
        source = (CONTROLS / "index.html").read_text(encoding="utf-8")
        self.assertIn("profile.groups[group].filter(id => !described.has(id))", source)
        self.assertIn("Bind destacado sem descrição", source)
        self.assertIn('dataset.controlMapValid = "true"', source)
        self.assertIn('data-control-map-valid="true"', (CONTROLS / "render.sh").read_text(encoding="utf-8"))

    def test_keyboard_geometry_uses_physical_key_units(self) -> None:
        source = (CONTROLS / "index.html").read_text(encoding="utf-8")
        self.assertIn("--main-width: 923px", source)
        self.assertIn("--main-width: 892px", source)
        self.assertIn("--main-width: 985px", source)
        self.assertIn('.key.w275 { width: 163.5px; }', source)
        self.assertIn('["SHIFT_R","Shift","w275"]', source)
        self.assertIn('["SHIFT_R","Shift","w175"],["UP","↑"],["END","End"]', source)
        self.assertIn('dataset.controlMapGeometryValid = "true"', source)

    def test_each_profile_and_layout_passes_the_rendered_contract(self) -> None:
        chrome = chrome_executable()
        if not chrome:
            self.skipTest("Google Chrome/Chromium não está disponível para validar geometria")
        viewports = ((1920, 1080), *VIEWPORTS)
        with tempfile.TemporaryDirectory(prefix="x86qw-control-maps-") as browser_data:
            for width, height in viewports:
                with self.subTest(viewport=f"{width}x{height}"):
                    user_data = Path(browser_data) / f"all-{width}x{height}"
                    command = [
                        chrome,
                        "--headless=new",
                        "--hide-scrollbars",
                        "--disable-background-mode",
                        "--disable-background-networking",
                        "--disable-extensions",
                        "--disable-gpu",
                        "--no-first-run",
                        "--force-device-scale-factor=1",
                        f"--window-size={width},{height}",
                        f"--user-data-dir={user_data}",
                        "--dump-dom",
                        f"{(CONTROLS / 'index.html').as_uri()}?profile=ktx&layout=windows-ansi&validate=all",
                    ]
                    try:
                        result = subprocess.run(
                            command,
                            capture_output=True,
                            timeout=12,
                            check=False,
                        )
                        stdout = result.stdout
                        stderr = result.stderr
                        self.assertEqual(0, result.returncode, stderr.decode("utf-8", "replace"))
                    except subprocess.TimeoutExpired as error:
                        stdout = error.stdout or b""
                        stderr = error.stderr or b""
                    rendered = stdout.decode("utf-8", "replace")
                    self.assertIn('data-control-map-valid="true"', rendered, stderr.decode("utf-8", "replace"))
                    self.assertIn('data-control-map-geometry-valid="true"', rendered)
                    self.assertIn('data-control-map-descriptions-valid="true"', rendered)
                    self.assertIn('data-control-map-all-profiles-valid="true"', rendered)
                    parser = HighlightedKeyParser()
                    parser.feed(rendered)
                    self.assertGreater(parser.highlighted, 0)
                    self.assertEqual([], parser.missing_descriptions)
                    self.assertEqual(2, rendered.count('aria-current="page"'))

    def test_generated_maps_match_the_canonical_source(self) -> None:
        manifest = json.loads((CONTROLS / "generated" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["format"])
        self.assertEqual(
            hashlib.sha256(canonical_text_bytes(CONTROLS / "index.html")).hexdigest(),
            manifest["source_sha256"],
        )
        expected = {
            f"{profile}.png" if layout == "windows-ansi" else f"{profile}--{layout}.png"
            for profile in PROFILES
            for layout in LAYOUTS
        }
        self.assertEqual(expected, set(manifest["images"]))
        for name, expected_hash in manifest["images"].items():
            with self.subTest(image=name):
                payload = (CONTROLS / "generated" / name).read_bytes()
                self.assertEqual(expected_hash, hashlib.sha256(payload).hexdigest())
                self.assertEqual(b"\x89PNG\r\n\x1a\n", payload[:8])
                self.assertEqual((1920, 1080), struct.unpack(">II", payload[16:24]))

    def test_source_hash_is_independent_from_checkout_line_endings(self) -> None:
        source = canonical_text_bytes(CONTROLS / "index.html")
        with tempfile.TemporaryDirectory() as temporary:
            windows_checkout = Path(temporary) / "index.html"
            windows_checkout.write_bytes(source.replace(b"\n", b"\r\n"))
            self.assertEqual(source, canonical_text_bytes(windows_checkout))

    def test_keychron_insert_is_documented_as_a_remap(self) -> None:
        readme = (CONTROLS / "README.md").read_text(encoding="utf-8")
        self.assertIn("Fn+Del → Insert", readme)
        self.assertIn("remapeamento x86QW recomendado", readme)

    def test_renderer_checks_geometry_for_every_profile_and_layout(self) -> None:
        render_script = (CONTROLS / "render.sh").read_text(encoding="utf-8")
        self.assertIn('data-control-map-geometry-valid="true"', render_script)
        self.assertIn('data-control-map-all-profiles-valid="true"', render_script)
        self.assertIn("Geometria do mapa de controles inválida", render_script)


if __name__ == "__main__":
    unittest.main()
