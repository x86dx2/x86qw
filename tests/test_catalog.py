from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from add_package import register_package  # noqa: E402
from validate_catalog import validate_catalog  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_repository_catalog_and_trust_boundary(self) -> None:
        catalog = json.loads((ROOT / "catalog/v1/index.json").read_text())
        self.assertEqual(validate_catalog(catalog), 0)

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


if __name__ == "__main__":
    unittest.main()
