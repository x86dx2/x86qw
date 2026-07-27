from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_package import build_package, verify_artifact  # noqa: E402
from validate_recipes import recipe_paths, validate_recipe  # noqa: E402


class RecipeTests(unittest.TestCase):
    def test_repository_recipes_are_valid_and_blocked_until_reviewed(self) -> None:
        paths = recipe_paths()
        self.assertEqual(3, len(paths))
        for path in paths:
            with self.subTest(path=path):
                recipe = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("blocked", validate_recipe(recipe, str(path)))
                with tempfile.TemporaryDirectory() as temporary:
                    with self.assertRaisesRegex(ValueError, "recipe is blocked"):
                        build_package(path, Path(temporary))

    def test_ready_recipe_builds_identical_mirror_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "ezQuake-test.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("ezquake.exe", b"binary")
            package = {
                "component": "ezquake", "version": "9.9.9", "channel": "stable",
                "platform": "windows", "architecture": "x64", "filename": artifact.name,
                "size": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "origin_url": f"https://example.invalid/origin/{artifact.name}",
                "license": "GPL-2.0-only", "license_url": "https://example.invalid/LICENSE",
                "source_urls": ["https://example.invalid/source.tar.gz"],
                "redistribution_reviewed": True,
                "urls": [f"https://downloads.example.invalid/{artifact.name}"],
                "artifact_format": "zip", "expected_members": ["ezquake.exe"],
            }
            recipe = {
                "format": 1, "project": "x86qw", "kind": "mirror", "package": package,
                "review": {"status": "ready", "notes": "Test fixture approved."},
            }
            recipe_path = root / "recipe.json"
            recipe_path.write_text(json.dumps(recipe), encoding="utf-8")

            target, manifest, public_package = build_package(
                recipe_path, root / "dist", artifact=artifact,
            )
            self.assertEqual(artifact.read_bytes(), target.read_bytes())
            self.assertEqual(package["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(public_package, json.loads(manifest.read_text())["package"])

            target.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                build_package(recipe_path, root / "dist", artifact=artifact)

    def test_archive_traversal_and_missing_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("../escape", b"bad")
            package = {
                "size": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "artifact_format": "zip",
                "expected_members": ["ezquake.exe"],
            }
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                verify_artifact(artifact, package)

    def test_recipe_output_segments_cannot_escape_dist(self) -> None:
        path = recipe_paths()[0]
        recipe = json.loads(path.read_text(encoding="utf-8"))
        recipe["package"]["version"] = "../../outside"
        with self.assertRaisesRegex(ValueError, "safe path segment"):
            validate_recipe(recipe)


if __name__ == "__main__":
    unittest.main()
