from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

import build_artifacts as build_artifacts_module  # noqa: E402
from build_package import build_package, verify_artifact  # noqa: E402
from validate_recipes import recipe_paths, validate_recipe  # noqa: E402


class RecipeTests(unittest.TestCase):
    @staticmethod
    def ready_recipe(root: Path) -> tuple[Path, Path, dict[str, object]]:
        artifact = root / "ezQuake-test.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("ezquake.exe", b"binary")
        package: dict[str, object] = {
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
        return recipe_path, artifact, package

    def test_zip_verification_uses_the_archive_plan_identity_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("runtime.exe", b"runtime")
            payload = artifact.read_bytes()
            package = {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "artifact_format": "zip",
                "expected_members": ["runtime.exe"],
            }
            with mock.patch(
                "build_package.file_sha256",
                side_effect=AssertionError("ZIP must not be reopened for hashing"),
            ):
                verify_artifact(artifact, package)

    def test_repository_recipes_are_ready_for_the_published_mirror(self) -> None:
        paths = recipe_paths()
        self.assertEqual(3, len(paths))
        for path in paths:
            with self.subTest(path=path):
                recipe = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("ready", validate_recipe(recipe, str(path)))
                self.assertIn("x86dx2/x86qw/releases", recipe["package"]["urls"][0])

    def test_ready_recipe_builds_identical_mirror_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, package = self.ready_recipe(root)

            target, manifest, public_package = build_package(
                recipe_path, root / "dist", artifact=artifact,
            )
            self.assertEqual(artifact.read_bytes(), target.read_bytes())
            self.assertEqual(package["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(public_package, json.loads(manifest.read_text())["package"])

            target.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                build_package(recipe_path, root / "dist", artifact=artifact)

    def test_build_never_writes_artifact_through_an_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, package = self.ready_recipe(root)
            victim = root / "personal-artifact.txt"
            victim.write_bytes(b"personal-artifact")
            target = (
                root / "dist/ezquake/9.9.9/stable/windows-x64"
                / str(package["filename"])
            )
            target.parent.mkdir(parents=True)
            try:
                target.symlink_to(victim)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            with self.assertRaises(ValueError):
                build_package(recipe_path, root / "dist", artifact=artifact)

            self.assertEqual(b"personal-artifact", victim.read_bytes())
            self.assertTrue(target.is_symlink())

    def test_build_never_writes_manifest_through_an_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, _package = self.ready_recipe(root)
            victim = root / "personal-manifest.txt"
            victim.write_bytes(b"personal-manifest")
            manifest = root / "dist/ezquake/9.9.9/stable/windows-x64/manifest.json"
            manifest.parent.mkdir(parents=True)
            try:
                manifest.symlink_to(victim)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            with self.assertRaises(ValueError):
                build_package(recipe_path, root / "dist", artifact=artifact)

            self.assertEqual(b"personal-manifest", victim.read_bytes())
            self.assertTrue(manifest.is_symlink())

    def test_build_never_overwrites_a_concurrent_artifact_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, package = self.ready_recipe(root)
            target = (
                root / "dist/ezquake/9.9.9/stable/windows-x64"
                / str(package["filename"])
            )
            personal = b"concurrent-personal-artifact"
            real_link = os.link

            def plant_then_link(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                Path(destination).write_bytes(personal)
                real_link(source, destination, **kwargs)

            with mock.patch.object(
                build_artifacts_module.os, "link", side_effect=plant_then_link,
            ):
                with self.assertRaises(ValueError):
                    build_package(recipe_path, root / "dist", artifact=artifact)

            self.assertEqual(personal, target.read_bytes())

    def test_build_revalidates_the_published_artifact_before_accepting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, package = self.ready_recipe(root)
            target = (
                root / "dist/ezquake/9.9.9/stable/windows-x64"
                / str(package["filename"])
            )
            replacement = b"replacement-after-publication"
            real_link = os.link

            def link_then_mutate(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                real_link(source, destination, **kwargs)
                if Path(destination) == target:
                    target.write_bytes(replacement)

            with mock.patch.object(
                build_artifacts_module.os, "link", side_effect=link_then_mutate,
            ):
                with self.assertRaisesRegex(ValueError, "preserved for inspection"):
                    build_package(recipe_path, root / "dist", artifact=artifact)

            self.assertEqual(replacement, target.read_bytes())
            self.assertFalse((target.parent / "manifest.json").exists())

    def test_build_preserves_a_different_target_replaced_after_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, package = self.ready_recipe(root)
            target = (
                root / "dist/ezquake/9.9.9/stable/windows-x64"
                / str(package["filename"])
            )
            personal = b"replacement-owned-by-another-writer"
            real_link = os.link

            def link_then_replace(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                real_link(source, destination, **kwargs)
                Path(destination).unlink()
                Path(destination).write_bytes(personal)

            with mock.patch.object(
                build_artifacts_module.os, "link", side_effect=link_then_replace,
            ):
                with self.assertRaises(ValueError):
                    build_package(recipe_path, root / "dist", artifact=artifact)

            self.assertEqual(personal, target.read_bytes())
            self.assertFalse((target.parent / "manifest.json").exists())

    def test_build_rejects_a_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe_path, artifact, _package = self.ready_recipe(root)
            output = root / "dist"
            output.mkdir()
            personal = root / "personal"
            personal.mkdir()
            component_root = output / "ezquake"
            try:
                component_root.symlink_to(personal, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            with self.assertRaisesRegex(ValueError, "link or reparse point"):
                build_package(recipe_path, output, artifact=artifact)

            self.assertEqual([], list(personal.iterdir()))

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
            with self.assertRaisesRegex(ValueError, "safe ZIP archive"):
                verify_artifact(artifact, package)

    def test_recipe_output_segments_cannot_escape_dist(self) -> None:
        path = recipe_paths()[0]
        recipe = json.loads(path.read_text(encoding="utf-8"))
        recipe["package"]["version"] = "../../outside"
        with self.assertRaisesRegex(ValueError, "safe path segment"):
            validate_recipe(recipe)


if __name__ == "__main__":
    unittest.main()
