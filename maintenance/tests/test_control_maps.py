from __future__ import annotations

import hashlib
import json
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROLS = ROOT / "docs" / "controls"
PROFILES = ("ktx", "final-arena", "pro-x", "team-fortress", "td2")
LAYOUTS = ("windows-ansi", "macos-en-us", "keychron-k3-v3")


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

    def test_generated_maps_match_the_canonical_source(self) -> None:
        manifest = json.loads((CONTROLS / "generated" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["format"])
        self.assertEqual(
            hashlib.sha256((CONTROLS / "index.html").read_bytes()).hexdigest(),
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

    def test_keychron_insert_is_documented_as_a_remap(self) -> None:
        readme = (CONTROLS / "README.md").read_text(encoding="utf-8")
        self.assertIn("Fn+Del → Insert", readme)
        self.assertIn("remapeamento x86QW recomendado", readme)


if __name__ == "__main__":
    unittest.main()
