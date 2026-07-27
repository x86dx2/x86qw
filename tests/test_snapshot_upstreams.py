from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from snapshot_upstreams import (  # noqa: E402
    component_owned_path,
    load_manifest,
    migrate_archive_layout,
    safe_filename,
    verify_archive,
    write_manifest,
)


class SnapshotTests(unittest.TestCase):
    def test_component_owned_layout_migration_is_complete_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_files = {
                "releases/ezquake/3.6.9/ezQuake-macOS-universal.zip": b"release",
                "nightly/macos-universal/20260701-120000_abcdef0_ezQuake-macOS-universal.zip": b"nightly",
                "maps/all/dm6.bsp": b"map",
                "maps/indexes/all.html": b"index",
                "maps/locs/dm6.loc": b"loc",
                "gfx/packages/1-example.zip": b"opaque",
                "dependencies/microsoft-vcpkg/snapshots/66c0373dc7fca549e5803087b9487edfe3aca0a1.tar.gz": b"vcpkg",
                "dependencies/qw-group-qwprot/snapshots/d508a7a4425e2dcdfab151cd188f8720907e5bbd.tar.gz": b"qwprot",
            }
            for relative, payload in legacy_files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            repository = root / "dependencies/qw-group-qwprot/repository.git"
            repository.mkdir(parents=True)
            (repository / "HEAD").write_text("ref: refs/heads/main\n")
            manifest = {
                "files": {relative: {"size": len(payload)} for relative, payload in legacy_files.items()},
                "repositories": {"qwprot": {}},
            }
            self.assertEqual((8, 1), migrate_archive_layout(root, manifest))
            self.assertEqual((0, 0), migrate_archive_layout(root, manifest))
            self.assertEqual("component-owned-v1", manifest["layout"])
            self.assertTrue((root / "components/ezquake/dependencies/qwprot/git/repository.git/HEAD").is_file())
            for relative in legacy_files:
                destination = component_owned_path(relative)
                self.assertTrue((root / destination).is_file(), destination)
                self.assertIn(destination, manifest["files"])
            self.assertFalse((root / "dependencies").exists())

    def test_safe_names_and_manifest_integrity(self) -> None:
        self.assertEqual("aerowalk#2020.ent", safe_filename("aerowalk%232020.ent"))
        self.assertIsNone(safe_filename("../"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "content/maps/all/dm6.bsp"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"map")
            manifest = {
                "format": 1, "project": "x86qw", "captured_at": None,
                "layout": "component-owned-v1",
                "files": {"content/maps/all/dm6.bsp": {
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
