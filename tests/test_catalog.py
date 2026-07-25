from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

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


if __name__ == "__main__":
    unittest.main()
