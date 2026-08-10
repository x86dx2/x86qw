from __future__ import annotations

import hashlib
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
    NIGHTLIES,
    collapse_case_duplicates,
    consumed_component,
    discover_nquake,
    discover_nightlies,
    download_asset,
    load_manifest,
    pin_assets_from_manifest,
    prune_unconsumed,
    safe_filename,
    verify_distribution,
    write_manifest,
)
from downloader import MAX_ARTIFACT_BYTES  # noqa: E402
from public_upstreams import GitTreeEntry  # noqa: E402
from upstreams import validate_upstreams  # noqa: E402

SPEC = importlib.util.spec_from_file_location("install_qw_policy", ROOT / "dist/installer/bin/manager.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class DistributionTests(unittest.TestCase):
    def test_upstream_registry_rejects_preserved_source_above_global_limit(self) -> None:
        registry = json.loads(
            (ROOT / "maintenance/inventory/upstreams.json").read_text(encoding="utf-8"),
        )
        source = next(
            entry["source"]
            for entry in registry["upstreams"]
            if "size" in entry["source"]
        )
        source["size"] = MAX_ARTIFACT_BYTES + 1
        with self.assertRaisesRegex(ValueError, "invalid preserved source identity"):
            validate_upstreams(registry)

    def test_upstream_registry_rejects_unsafe_persistent_urls_without_disclosing_them(self) -> None:
        original = json.loads(
            (ROOT / "maintenance/inventory/upstreams.json").read_text(encoding="utf-8"),
        )
        sentinel = "X86QW_UPSTREAM_SECRET_SENTINEL"
        mutations = (
            ("release_url", f"https://reviewer:{sentinel}@example.invalid/release"),
            ("release_url", f"https://example.invalid/release?token={sentinel}"),
            ("release_url", "https://example.invalid/release#fragment"),
            ("source.url", f"https://example.invalid/source?token={sentinel}"),
            ("update.repository", f"https://reviewer:{sentinel}@github.com/nQuake/distfiles.git"),
        )
        for field, value in mutations:
            registry = json.loads(json.dumps(original))
            if field == "source.url":
                target = next(
                    entry for entry in registry["upstreams"]
                    if entry["source"].get("status") in {"complete", "partial"}
                )
                target["source"]["url"] = value
            elif field == "update.repository":
                target = next(
                    entry for entry in registry["upstreams"]
                    if entry["update"].get("strategy") == "git-ref"
                )
                target["update"]["repository"] = value
            else:
                registry["upstreams"][0][field] = value

            with self.subTest(field=field), self.assertRaises(ValueError) as raised:
                validate_upstreams(registry)
            self.assertNotIn(sentinel, str(raised.exception))

    def test_distribution_manifest_rejects_file_above_global_limit(self) -> None:
        manifest = {
            "format": 1,
            "project": "x86qw",
            "captured_at": None,
            "layout": "distribution-v1",
            "repositories": {},
            "files": {
                "mods/ktx/test/oversized.zip": {
                    "component": "ktx",
                    "consumer": "install:ktx",
                    "url": "https://example.invalid/oversized.zip",
                    "size": MAX_ARTIFACT_BYTES + 1,
                    "sha256": "0" * 64,
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid distribution file size"):
                load_manifest(path)

    def test_distribution_manifest_rejects_unsafe_urls_without_disclosing_them(self) -> None:
        sentinel = "X86QW_MANIFEST_SECRET_SENTINEL"
        for url in (
            f"https://reviewer:{sentinel}@example.invalid/payload.zip",
            f"https://example.invalid/payload.zip?token={sentinel}",
            "https://example.invalid/payload.zip#fragment",
            "http://example.invalid/payload.zip",
        ):
            manifest = {
                "format": 1,
                "project": "x86qw",
                "captured_at": None,
                "layout": "distribution-v1",
                "repositories": {},
                "files": {
                    "mods/ktx/test/payload.zip": {
                        "component": "ktx",
                        "consumer": "install:ktx",
                        "url": url,
                        "size": 1,
                        "sha256": "0" * 64,
                    },
                },
            }
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "manifest.json"
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(url=url), self.assertRaises(ValueError) as raised:
                    load_manifest(path)
            self.assertNotIn(sentinel, str(raised.exception))

    def test_top_level_taxonomy_separates_clients_distributions_and_game_data(self) -> None:
        stable = ROOT / "dist/clients/ezquake/stable/3.6.9"
        nightly = ROOT / "dist/clients/ezquake/nightly/20260616-101233_a86996a"
        self.assertTrue((stable / "source").is_dir())
        self.assertTrue((nightly / "source").is_dir())
        for root in (stable, nightly):
            self.assertTrue((root / "linux-x86_64").is_dir())
            self.assertTrue((root / "macos-universal").is_dir())
            self.assertTrue((root / "windows-x64").is_dir())
        self.assertTrue((ROOT / "dist/distributions/nquake").is_dir())
        self.assertTrue((ROOT / "dist/game-data/id1/pak0.pak").is_file())
        self.assertTrue((ROOT / "dist/game-data/id1/pak1.pak").is_file())
        for legacy in ("ezquake", "nquake", "id1"):
            self.assertFalse((ROOT / "dist" / legacy).exists())

    def test_versioned_mods_use_contextual_source_upstream_and_x86qw_directories(self) -> None:
        versions = {
            "final-arena": "1.20",
            "ktx": "1.47",
            "pro-x": "1.1",
            "td2": "2.22",
            "team-fortress": "2.9",
        }
        allowed = {"source", "upstream", "x86qw"}
        for mod, version in versions.items():
            with self.subTest(mod=mod):
                root = ROOT / "dist/mods" / mod / version
                self.assertTrue(root.is_dir())
                self.assertFalse([path.name for path in root.iterdir() if path.is_file()])
                self.assertLessEqual({path.name for path in root.iterdir()}, allowed)
                self.assertTrue((root / "x86qw").is_dir())

        self.assertTrue((ROOT / "dist/mods/td2/2.22/source").is_dir())
        self.assertTrue((ROOT / "dist/mods/ktx/1.47/source").is_dir())
        self.assertTrue((ROOT / "dist/mods/team-fortress/2.9/source").is_dir())
        self.assertFalse((ROOT / "dist/mods/clan-arena").exists())
        self.assertEqual(
            {"catalog", "config", "policy", "runtime", "source"},
            {
                path.name for path in (ROOT / "dist/mods/ktx/1.47/x86qw").iterdir()
                if path.is_dir()
            },
        )
        ktx_x86qw = ROOT / "dist/mods/ktx/1.47/x86qw"
        self.assertEqual(
            {"qwprogs.map", "qwprogs.qvm"},
            {path.name for path in (ktx_x86qw / "runtime").iterdir()},
        )
        self.assertEqual(
            {
                "0001-reproducible-build-date.patch",
                "0002-frogbot-identities.patch",
                "0003-frogbot-team-balance.patch",
                "0004-frogbot-role-names.patch",
                "0005-quad-coach.patch",
            },
            {path.name for path in (ktx_x86qw / "source").iterdir()},
        )
        self.assertEqual(
            {"names.json", "names.user.json.example"},
            {
                path.name
                for path in (ktx_x86qw / "catalog/frogbots").iterdir()
            },
        )

    def test_policy_matches_installer_and_rejects_undeclared_components(self) -> None:
        components = load_component_policy()
        catalog = load_component_catalog(ROOT / "maintenance/inventory/components.json")
        self.assertGreater(len(source_roots(catalog)), 10)
        for component in ("installer", "ezquake", "nquake", "ktx", "final-arena", "pro-x", "team-fortress", "td2"):
            require_component(components, component)
        for component in ("classicq", "unezquake", "gfx", "maps", "locs"):
            with self.subTest(component=component):
                with self.assertRaisesRegex(ValueError, "not consumed"):
                    require_component(components, component)

    def test_nquake_snapshot_is_partitioned_without_unused_overlays(self) -> None:
        catalog = load_component_catalog(ROOT / "maintenance/inventory/components.json")
        snapshots = list((ROOT / "dist/distributions/nquake").iterdir())
        self.assertEqual(1, len(snapshots))
        paths = sorted(path.relative_to(snapshots[0]).as_posix() for path in snapshots[0].rglob("*") if path.is_file())
        partition = validate_tree_partition(catalog, paths)
        self.assertEqual(523, sum(map(len, partition.values())))
        self.assertEqual("ktx", component_for_source(catalog, "gpl/qw/ktx.pk3"))
        self.assertTrue((snapshots[0] / "gpl/qw/ktx.pk3").is_file())
        self.assertIsNone(component_for_source(catalog, "gpl/qw/skins/player_orange.png"))

    def test_only_runtime_assets_have_consumers(self) -> None:
        self.assertEqual("ezquake", consumed_component(
            "clients/ezquake/stable/3.6.9/macos-universal/ezQuake-macOS-universal.zip"
        ))
        self.assertIsNone(consumed_component("content/maps/all/dm6.bsp"))
        self.assertIsNone(consumed_component("content/locs/dm6.loc"))
        self.assertIsNone(consumed_component("content/gfx/packages/1-theme.download"))
        self.assertIsNone(consumed_component("clients/ezquake/stable/3.6.9/source/source.tar.gz"))
        self.assertIsNone(consumed_component("clients/ezquake/stable/3.6.9/metadata/checksums.txt"))
        self.assertIsNone(consumed_component(
            "clients/ezquake/nightly/build/linux-x86_64/build.AppImage.md5"
        ))
        self.assertEqual("ezquake", consumed_component(
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz"
        ))
        self.assertEqual("ktx", consumed_component("mods/ktx/1.47/source/ktx-1.47.tar.gz"))
        self.assertEqual("team-fortress", consumed_component(
            "mods/team-fortress/2.9/source/tf_29src.zip"
        ))
        self.assertEqual("installer", consumed_component(
            "installer/packages/1.0.0/x86qw-installer-1.0.0.zip"
        ))
        self.assertEqual("ktx", consumed_component(
            "mods/ktx/1.47/upstream/qwprogs-qvm.zip"
        ))
        self.assertEqual("td2", consumed_component(
            "mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz"
        ))
        self.assertIsNone(consumed_component(
            "distributions/nquake/e4cb23d40aa202335b5dafe4e8f1e8d424caac0d/"
            "non-gpl/qw/sound/ca/sf1.wav"
        ))

    def test_policy_prunes_unconsumed_files_and_legacy_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kept = "clients/ezquake/stable/3.6.9/macos-universal/ezQuake-macOS-universal.zip"
            kept_nquake = (
                "distributions/nquake/e4cb23d40aa202335b5dafe4e8f1e8d424caac0d/"
                "gpl/qw/ktx.pk3"
            )
            historical_installer = (
                "installer/packages/0.9.0/x86qw-installer-0.9.0.zip"
            )
            current_installer = (
                "installer/packages/1.0.0/x86qw-installer-1.0.0.zip"
            )
            removed = "content/gfx/packages/theme.download"
            for relative, payload in (
                (kept, b"zip"), (kept_nquake, b"pk3"),
                (historical_installer, b"old"), (current_installer, b"new"),
                (removed, b"gfx"),
            ):
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
                    kept_nquake: {"size": 3, "sha256": hashlib.sha256(b"pk3").hexdigest()},
                    historical_installer: {
                        "size": 3, "sha256": hashlib.sha256(b"old").hexdigest(),
                    },
                    current_installer: {
                        "size": 3, "sha256": hashlib.sha256(b"new").hexdigest(),
                    },
                    removed: {"size": 3, "sha256": hashlib.sha256(b"gfx").hexdigest()},
                },
            }
            self.assertEqual((1, 2), prune_unconsumed(root, manifest))
            self.assertTrue((root / kept).is_file())
            self.assertFalse((root / removed).exists())
            self.assertFalse(repository_file.exists())
            self.assertEqual({}, manifest["repositories"])
            self.assertEqual("distribution-v1", manifest["layout"])
            self.assertEqual("ktx", manifest["files"][kept_nquake]["package"])
            self.assertEqual(
                "install:nquake-reference",
                manifest["files"][kept_nquake]["consumer"],
            )
            self.assertEqual(
                "archive:installer-history",
                manifest["files"][historical_installer]["consumer"],
            )
            self.assertEqual(
                "bootstrap:installer",
                manifest["files"][current_installer]["consumer"],
            )

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
            payload = root / "clients/ezquake/stable/3.6.9/macos-universal/ezQuake-macOS-universal.zip"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"zip")
            manifest = {
                "format": 1, "project": "x86qw", "captured_at": None,
                "layout": "distribution-v1", "repositories": {},
                "files": {"clients/ezquake/stable/3.6.9/macos-universal/ezQuake-macOS-universal.zip": {
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
                len(expected),
                "ktx",
                hashlib.sha256(expected).hexdigest(),
                git_identity,
            )
            known = {
                "component": "ktx",
                "consumer": "install:ktx",
                "url": "https://example.invalid/old.zip",
                "size": len(expected),
                "sha256": hashlib.sha256(b"old").hexdigest(),
            }
            def transfer(contract):
                self.assertEqual(len(expected), contract.expected_size)
                contract.destination.write_bytes(expected)
                return mock.Mock(
                    size=len(expected), sha256=hashlib.sha256(expected).hexdigest(),
                )

            with mock.patch(
                "sync_distribution.download", side_effect=transfer,
            ):
                _, metadata, reused = download_asset(root, asset, known)
            self.assertFalse(reused)
            self.assertEqual(expected, target.read_bytes())
            self.assertEqual(hashlib.sha256(expected).hexdigest(), metadata["sha256"])

    def test_download_rejects_an_unpinned_asset_before_network_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = (
                Asset(
                    "ezquake",
                    "https://example.invalid/new.zip",
                    "clients/ezquake/nightly/new/windows-x64/new.exe",
                    123,
                ),
                Asset(
                    "ezquake",
                    "https://example.invalid/new.zip",
                    "clients/ezquake/nightly/new/windows-x64/new.exe",
                    None,
                    expected_sha256=hashlib.sha256(b"candidate").hexdigest(),
                ),
            )
            with mock.patch("sync_distribution.download") as transfer, mock.patch(
                "sync_distribution.remote_content_length",
            ) as content_length:
                for asset in assets:
                    with self.subTest(asset=asset), self.assertRaisesRegex(
                        ValueError, "reviewed size and SHA-256",
                    ):
                        download_asset(root, asset, None)

            transfer.assert_not_called()
            content_length.assert_not_called()
            self.assertFalse((root / "clients").exists())

    def test_manifest_pin_requires_exact_path_url_size_and_reviewed_digest(self) -> None:
        digest = hashlib.sha256(b"known").hexdigest()
        asset = Asset(
            "ezquake",
            "https://example.invalid/current.zip",
            "clients/ezquake/stable/current/linux-x86_64/current.zip",
            5,
        )
        manifest = {"files": {asset.path: {
            "url": asset.url,
            "size": 5,
            "sha256": digest,
        }}}

        pinned = pin_assets_from_manifest([asset], manifest)[0]

        self.assertEqual(5, pinned.expected_size)
        self.assertEqual(digest, pinned.expected_sha256)
        for changed in (
            {**manifest["files"][asset.path], "url": "https://example.invalid/other.zip"},
            {**manifest["files"][asset.path], "size": 6},
            {**manifest["files"][asset.path], "sha256": "not-a-digest"},
        ):
            with self.subTest(changed=changed):
                candidate = pin_assets_from_manifest(
                    [asset], {"files": {asset.path: changed}},
                )[0]
                self.assertIsNone(candidate.expected_sha256)

    def test_nightly_discovery_records_the_remote_size(self) -> None:
        names = {
            "macos-universal": "20260803-120000_abcdef0_ezQuake-macOS-universal.zip",
            "linux-x86_64": "20260803-120000_abcdef0_ezQuake-x86_64.AppImage",
            "windows-x64": "20260803-120000_abcdef0_ezquake.exe",
        }
        with mock.patch(
            "sync_distribution.links",
            side_effect=lambda root: [names[next(
                platform
                for platform, (candidate, _pattern) in NIGHTLIES.items()
                if candidate == root
            )]],
        ), mock.patch(
            "sync_distribution.remote_content_length", return_value=1234,
        ) as content_length:
            assets = discover_nightlies()
        self.assertEqual([1234, 1234, 1234], [asset.expected_size for asset in assets])
        self.assertEqual(3, content_length.call_count)

    def test_nquake_discovery_reuses_blob_size_when_github_api_provides_it(self) -> None:
        entry = GitTreeEntry("qw/file.cfg", "b" * 40, 321)
        with mock.patch(
            "sync_distribution.load_component_catalog", return_value={},
        ), mock.patch(
            "sync_distribution.source_roots", return_value=["qw"],
        ), mock.patch(
            "sync_distribution.github_recursive_tree", return_value=("a" * 40, [entry]),
        ), mock.patch(
            "sync_distribution.component_for_source", return_value="x86qw-client-bootstrap",
        ):
            assets = discover_nquake()

        self.assertEqual(1, len(assets))
        self.assertEqual(321, assets[0].expected_size)
        self.assertEqual("b" * 40, assets[0].expected_git_sha1)


if __name__ == "__main__":
    unittest.main()
