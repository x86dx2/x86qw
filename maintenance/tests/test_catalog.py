from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

from add_package import register_package  # noqa: E402
from validate_catalog import validate_catalog  # noqa: E402
from publish_gitlab_packages import artifact_url  # noqa: E402
from build_component_packages import register_packages  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_repository_catalog_and_trust_boundary(self) -> None:
        catalog = json.loads((ROOT / "site/public/api/v1/catalog.json").read_text())
        self.assertEqual(validate_catalog(catalog), 25)
        self.assertEqual(6, sum(package["component"] == "ezquake" for package in catalog["packages"]))
        ktx = next(package for package in catalog["packages"] if package.get("package") == "nquake-ktx")
        self.assertEqual("1.47+nquake.e4cb23d40aa2+x86qw.2", ktx["version"])
        self.assertEqual("1.47", ktx["upstream_version"])
        self.assertEqual("ktx", ktx["component"])
        self.assertTrue(all(package["urls"] for package in catalog["packages"]))
        self.assertTrue(all("github.com" in package["urls"][0] for package in catalog["packages"]))
        clients = [package for package in catalog["packages"] if package["component"] == "ezquake"]
        content = [package for package in catalog["packages"] if package["channel"] == "content"]
        self.assertTrue(all(package.get("distribution_path") for package in clients))
        self.assertTrue(all((ROOT / "dist" / package["distribution_path"]).is_file() for package in clients))
        self.assertTrue(all("distribution_path" not in package for package in content))
        self.assertIn(artifact_url(ktx), ktx["urls"])
        self.assertEqual(
            {package["package"] for package in catalog["packages"] if package["component"] == "nquake"},
            {
                "nquake-bootstrap", "nquake-visual-core",
                "nquake-player-skins", "nquake-crosshairs", "nquake-skyboxes",
                "nquake-models", "nquake-scoreboard-flags", "nquake-sounds",
                "nquake-external-textures", "nquake-base-textures", "nquake-maps",
                "nquake-matchinfo", "nquake-documentation", "qrp-hires",
                "final-arena", "pro-x", "team-fortress",
            },
        )
        td2 = next(package for package in catalog["packages"] if package.get("package") == "total-destruction-2")
        self.assertEqual("2.22+x86qw.2", td2["version"])
        self.assertEqual("td2", td2["component"])
        self.assertEqual(64, len(td2["source_revision"]))
        self.assertEqual("2.22", td2["upstream_version"])
        final_arena = next(package for package in catalog["packages"] if package.get("package") == "final-arena")
        pro_x = next(package for package in catalog["packages"] if package.get("package") == "pro-x")
        self.assertEqual("e4cb23d40aa2+x86qw.2", final_arena["version"])
        self.assertEqual("e4cb23d40aa2+x86qw.2", pro_x["version"])

        package = {
            "component": "ezquake",
            "version": "3.6.9",
            "channel": "stable",
            "platform": "macos",
            "architecture": "universal",
            "filename": "ezquake.zip",
            "size": 1,
            "sha256": "0" * 64,
            "origin_url": "https://example.invalid/original/ezquake.zip",
            "license": "GPL-2.0-or-later",
            "license_url": "https://example.invalid/LICENSE",
            "source_urls": ["https://example.invalid/source.tar.gz"],
            "redistribution_reviewed": True,
            "urls": ["https://downloads.x86.com.br/x86qw/ezquake.zip"],
        }
        catalog["packages"] = [package]
        self.assertEqual(validate_catalog(catalog), 1)

        package["filename"] = "..\\ezquake.zip"
        with self.assertRaises(ValueError):
            validate_catalog(catalog)

        package["filename"] = "ezquake.zip"
        package["urls"] = ["http://example.invalid/ezquake.zip"]
        with self.assertRaises(ValueError):
            validate_catalog(catalog)

        package["urls"] = ["https://downloads.x86.com.br/x86qw/ezquake.zip"]
        package["redistribution_reviewed"] = False
        with self.assertRaises(ValueError):
            validate_catalog(catalog)

    def test_reviewed_artifact_registration_is_atomic_and_immutable(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "ezQuake-macOS-universal.zip"
            artifact.write_bytes(b"reviewed artifact")
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "generated_at": None, "packages": [],
            }))
            arguments = {
                "component": "ezquake", "version": "3.6.9", "channel": "stable",
                "platform": "macos", "architecture": "universal",
                "origin_url": f"https://example.invalid/original/{artifact.name}",
                "license_name": "GPL-2.0", "license_url": "https://example.invalid/LICENSE",
                "source_urls": ["https://example.invalid/source.tar.gz"],
                "mirror_urls": [f"https://downloads.x86.com.br/x86qw/{artifact.name}"],
                "redistribution_reviewed": True,
            }
            package = register_package(catalog_path, artifact, **arguments)
            self.assertEqual(artifact.stat().st_size, package["size"])
            self.assertEqual(1, validate_catalog(json.loads(catalog_path.read_text())))
            with self.assertRaises(ValueError):
                register_package(catalog_path, artifact, **arguments)

    def test_component_registration_reclassifies_origin_and_preserves_fallback_mirrors(self) -> None:
        import tempfile

        package = {
            "component": "ktx", "package": "nquake-ktx", "version": "1.47",
            "channel": "content", "platform": "any", "architecture": "any",
            "filename": "nquake-ktx-1.47.zip", "size": 1, "sha256": "0" * 64,
            "origin_url": "https://github.com/example/nquake-ktx-1.47.zip",
            "license": "GPL-2.0", "license_url": "https://github.com/example/LICENSE",
            "source_urls": ["https://github.com/example/source.tar.gz"],
            "redistribution_reviewed": True,
            "urls": ["https://github.com/example/nquake-ktx-1.47.zip"],
            "source_commit": "a" * 40,
        }
        fallback = "https://gitlab.com/example/nquake-ktx-1.47.zip"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            catalog_package = dict(package, component="nquake", urls=[*package["urls"], fallback])
            path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "generated_at": None,
                "packages": [catalog_package],
            }))
            register_packages(path, {"packages": [package]})
            saved = json.loads(path.read_text())
            self.assertEqual([*package["urls"], fallback], saved["packages"][0]["urls"])
            self.assertEqual("ktx", saved["packages"][0]["component"])


if __name__ == "__main__":
    unittest.main()
