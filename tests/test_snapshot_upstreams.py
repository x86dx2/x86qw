from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from snapshot_upstreams import (  # noqa: E402
    load_manifest,
    migrate_legacy_gfx_names,
    safe_filename,
    verify_archive,
    write_manifest,
)


class SnapshotTests(unittest.TestCase):
    def test_legacy_gfx_packages_are_treated_as_opaque_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "gfx/packages/1-example.zip"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"not necessarily a ZIP")
            manifest = {
                "files": {"gfx/packages/1-example.zip": {"size": legacy.stat().st_size}},
            }
            self.assertEqual(1, migrate_legacy_gfx_names(root, manifest))
            self.assertFalse(legacy.exists())
            self.assertTrue((root / "gfx/packages/1-example.download").is_file())
            self.assertIn("gfx/packages/1-example.download", manifest["files"])

    def test_safe_names_and_manifest_integrity(self) -> None:
        self.assertEqual("aerowalk#2020.ent", safe_filename("aerowalk%232020.ent"))
        self.assertIsNone(safe_filename("../"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "maps/all/dm6.bsp"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"map")
            manifest = {
                "format": 1, "project": "x86qw", "captured_at": None,
                "files": {"maps/all/dm6.bsp": {
                    "url": "https://example.invalid/dm6.bsp", "size": 3,
                    "sha256": "60be9861750facbfad8758254a2f76c0cfe78d54459a3bc187d49b1401fcd8e8",
                }},
                "repositories": {},
            }
            path = root / "manifest.json"
            write_manifest(path, manifest)
            loaded = load_manifest(path)
            self.assertEqual(1, verify_archive(root, loaded))
            payload.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify_archive(root, loaded)


if __name__ == "__main__":
    unittest.main()
