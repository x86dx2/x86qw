from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

from component_policy import load_component_policy, require_component  # noqa: E402
from components import (  # noqa: E402
    component_for_source,
    load_catalog as load_component_catalog,
    source_roots,
    validate_tree_partition,
)
from sync_distribution import (  # noqa: E402
    Asset,
    collapse_case_duplicates,
    consumed_component,
    download_asset,
    load_manifest,
    prune_unconsumed,
    safe_filename,
    verify_distribution,
    write_manifest,
)

SPEC = importlib.util.spec_from_file_location("install_qw_policy", ROOT / "install-qw.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class DistributionTests(unittest.TestCase):
    def test_policy_matches_installer_and_rejects_undeclared_components(self) -> None:
        components = load_component_policy()
        catalog = load_component_catalog(ROOT / "maintenance/inventory/components.json")
        self.assertGreater(len(source_roots(catalog)), 10)
        for component in ("installer", "ezquake", "nquake", "ktx", "pro-x", "team-fortress", "td2"):
            require_component(components, component)
        for component in ("classicq", "unezquake", "gfx", "maps", "locs"):
            with self.subTest(component=component):
                with self.assertRaisesRegex(ValueError, "not consumed"):
                    require_component(components, component)

    def test_nquake_snapshot_is_partitioned_without_unused_overlays(self) -> None:
        catalog = load_component_catalog(ROOT / "maintenance/inventory/components.json")
        snapshots = list((ROOT / "dist/nquake").iterdir())
        self.assertEqual(1, len(snapshots))
        paths = sorted(path.relative_to(snapshots[0]).as_posix() for path in snapshots[0].rglob("*") if path.is_file())
        partition = validate_tree_partition(catalog, paths)
        self.assertEqual(535, sum(map(len, partition.values())))
        self.assertEqual("nquake-ktx", component_for_source(catalog, "gpl/qw/ktx.pk3"))
        self.assertIsNone(component_for_source(catalog, "gpl/qw/skins/player_orange.png"))

    def test_only_runtime_assets_have_consumers(self) -> None:
        self.assertEqual("ezquake", consumed_component(
            "ezquake/3.6.9/stable/macos-universal/ezQuake-macOS-universal.zip"
        ))
        self.assertIsNone(consumed_component("content/maps/all/dm6.bsp"))
        self.assertIsNone(consumed_component("content/locs/dm6.loc"))
        self.assertIsNone(consumed_component("content/gfx/packages/1-theme.download"))
        self.assertIsNone(consumed_component("ezquake/3.6.9/stable/source/source.tar.gz"))
        self.assertIsNone(consumed_component("ezquake/3.6.9/stable/metadata/checksums.txt"))
        self.assertIsNone(consumed_component("ezquake/build/nightly/linux-x86_64/build.AppImage.md5"))
        self.assertEqual("ezquake", consumed_component(
            "ezquake/3.6.9/source/ezquake-source-3.6.9.tar.gz"
        ))
        self.assertEqual("ktx", consumed_component("mods/ktx/1.47/source/ktx-1.47.tar.gz"))
        self.assertEqual("team-fortress", consumed_component(
            "mods/team-fortress/2.9/source/tf_29src.zip"
        ))
        self.assertEqual("installer", consumed_component(
            "installer/1.0.0/x86qw-installer-1.0.0.zip"
        ))
        self.assertEqual("ktx", consumed_component(
            "mods/ktx/1.47/qwprogs-qvm.zip"
        ))
        self.assertEqual("td2", consumed_component(
            "mods/td2/2.22/quakeworld-TD2.22QW-server_PTBR.tar.gz"
        ))

    def test_policy_prunes_unconsumed_files_and_legacy_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kept = "ezquake/3.6.9/stable/macos-universal/ezQuake-macOS-universal.zip"
            removed = "content/gfx/packages/theme.download"
            for relative, payload in ((kept, b"zip"), (removed, b"gfx")):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            repository_file = root / "components/ezquake/git/repository.git/HEAD"
            repository_file.parent.mkdir(parents=True)
            repository_file.write_text("legacy\n")
            manifest = {
                "format": 1, "project": "x86qw", "captured_at": None,
                "layout": "component-owned-v1", "repositories": {"ezquake": {}},
                "files": {
                    kept: {"size": 3, "sha256": hashlib.sha256(b"zip").hexdigest()},
                    removed: {"size": 3, "sha256": hashlib.sha256(b"gfx").hexdigest()},
                },
            }
            self.assertEqual((1, 2), prune_unconsumed(root, manifest))
            self.assertTrue((root / kept).is_file())
            self.assertFalse((root / removed).exists())
            self.assertFalse(repository_file.exists())
            self.assertEqual({}, manifest["repositories"])
            self.assertEqual("distribution-v1", manifest["layout"])

    def test_case_collisions_are_collapsed_only_for_identical_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "content/maps/all/testmapB.bsp"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"same")
            digest = hashlib.sha256(b"same").hexdigest()
            files = {
                "content/maps/all/testmapB.bsp": {"size": 4, "sha256": digest},
                "content/maps/all/testmapb.bsp": {"size": 4, "sha256": digest},
            }
            self.assertEqual(1, collapse_case_duplicates(root, files))
            self.assertEqual(["content/maps/all/testmapB.bsp"], list(files))

            files["content/maps/all/testmapb.bsp"] = {"size": 5, "sha256": "0" * 64}
            with self.assertRaisesRegex(ValueError, "different content"):
                collapse_case_duplicates(root, files)

    def test_safe_names_and_manifest_integrity(self) -> None:
        self.assertEqual("aerowalk#2020.ent", safe_filename("aerowalk%232020.ent"))
        self.assertIsNone(safe_filename("../"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "ezquake/3.6.9/stable/macos-universal/ezQuake-macOS-universal.zip"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"zip")
            manifest = {
                "format": 1, "project": "x86qw", "captured_at": None,
                "layout": "distribution-v1", "repositories": {},
                "files": {"ezquake/3.6.9/stable/macos-universal/ezQuake-macOS-universal.zip": {
                    "component": "ezquake", "consumer": "install:ezquake",
                    "url": "https://example.invalid/ezQuake-macOS-universal.zip", "size": 3,
                    "sha256": hashlib.sha256(b"zip").hexdigest(),
                }},
            }
            path = root / "manifest.json"
            write_manifest(path, manifest)
            loaded = load_manifest(path)
            self.assertEqual(1, verify_distribution(root, loaded))
            extra = root / "unused.bin"
            extra.write_bytes(b"unused")
            with self.assertRaisesRegex(ValueError, "without an explicit consumer"):
                verify_distribution(root, loaded)
            extra.unlink()
            payload.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify_distribution(root, loaded)

    def test_download_does_not_reuse_stale_metadata_for_a_pinned_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "mods/ktx/test/qwprogs-qvm.zip"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old")
            expected = b"new"
            git_identity = hashlib.sha1(f"blob {len(expected)}\0".encode() + expected).hexdigest()
            asset = Asset(
                "ktx",
                "https://example.invalid/qwprogs-qvm.zip",
                target.relative_to(root).as_posix(),
                None,
                "nquake-ktx",
                hashlib.sha256(expected).hexdigest(),
                git_identity,
            )
            known = {
                "component": "ktx",
                "consumer": "install:nquake-ktx",
                "url": "https://example.invalid/old.zip",
                "size": len(expected),
                "sha256": hashlib.sha256(b"old").hexdigest(),
            }
            with mock.patch("sync_distribution.urllib.request.urlopen", return_value=io.BytesIO(expected)):
                _, metadata, reused = download_asset(root, asset, known)
            self.assertFalse(reused)
            self.assertEqual(expected, target.read_bytes())
            self.assertEqual(hashlib.sha256(expected).hexdigest(), metadata["sha256"])


if __name__ == "__main__":
    unittest.main()
