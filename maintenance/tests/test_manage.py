from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.manage import (
    PROJECT_ROOT,
    Asset,
    distribution_delta,
    parser,
    preserve_profile_fingerprints,
    reference_content_changed,
    summarize_delta,
    update_inventory_lines,
    update_ezquake_catalog,
    update_reference_releases,
)


class DistributionManagerTests(unittest.TestCase):
    def test_profile_history_preserves_old_and_new_distribution_shapes(self) -> None:
        catalog = {
            "profiles": {"essential": ["base"], "recommended": ["base"], "complete": ["base"]},
            "profile_history": {"essential": [], "recommended": [], "complete": []},
        }
        preserve_profile_fingerprints(catalog)
        old = catalog["profile_history"]["recommended"][0]
        catalog["profiles"]["recommended"].append("feature")
        preserve_profile_fingerprints(catalog)
        self.assertEqual(2, len(catalog["profile_history"]["recommended"]))
        self.assertEqual(old, catalog["profile_history"]["recommended"][0])

    def test_distribution_delta_reports_new_and_obsolete_managed_files(self) -> None:
        manifest = {
            "files": {
                "clients/ezquake/old.zip": {"url": "https://example.invalid/old.zip"},
                "mods/kept.zip": {"url": "https://example.invalid/kept.zip", "size": 10, "sha256": "a" * 64},
            }
        }
        assets = [
            Asset("td2", "https://example.invalid/kept.zip", "mods/kept.zip", 10, expected_sha256="a" * 64),
            Asset("td2", "https://example.invalid/new.zip", "mods/new.zip", 20),
        ]

        delta = distribution_delta(assets, manifest)

        self.assertEqual(
            [item["path"] for item in delta],
            ["clients/ezquake/old.zip", "mods/new.zip"],
        )
        self.assertEqual({item["status"] for item in delta}, {"obsolete", "update-available"})

    def test_nquake_delta_is_summarized_as_one_snapshot_change(self) -> None:
        delta = [
            {"path": f"distributions/nquake/{'a' * 40}/one", "status": "obsolete", "reason": "old"},
            {"path": f"distributions/nquake/{'b' * 40}/one", "status": "update-available", "reason": "new"},
            {"path": f"distributions/nquake/{'b' * 40}/two", "status": "update-available", "reason": "new"},
        ]

        summary = summarize_delta(delta)

        self.assertEqual(len(summary), 1)
        self.assertIn("aaaaaaaaaaaa (1 removidos)", summary[0])
        self.assertIn("bbbbbbbbbbbb (2 novos)", summary[0])

    def test_reference_update_preserves_x86qw_suffixes_and_overlay_version(self) -> None:
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        old = str(releases["reference"]["revision"])
        new = "b" * 40

        changed = update_reference_releases(releases, new)

        self.assertTrue(changed)
        self.assertEqual(releases["reference"]["revision"], new)
        self.assertEqual(releases["components"]["final-arena"]["version"], "bbbbbbbbbbbb+x86qw.6")
        self.assertEqual(releases["components"]["pro-x"]["version"], "1.1+x86qw.1")
        self.assertIn("nquake.bbbbbbbbbbbb", releases["components"]["team-fortress"]["version"])
        self.assertIn("nquake.bbbbbbbbbbbb", releases["components"]["nquake-ktx"]["version"])
        self.assertNotIn(old[:12], releases["components"]["nquake-ktx"]["distribution_tag"])

    def test_reference_advance_without_consumed_byte_changes_is_ignored(self) -> None:
        payload = b"same product bytes"
        digest = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = f"distributions/nquake/{'a' * 40}/gpl/qw/ktx.pk3"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            manifest = {"files": {relative: {
                "url": "https://example.invalid/old",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "package": "nquake-ktx",
            }}}
            assets = [Asset(
                "nquake", "https://example.invalid/new", f"distributions/nquake/{'b' * 40}/gpl/qw/ktx.pk3",
                None, "nquake-ktx", None, digest,
            )]

            self.assertFalse(reference_content_changed(assets, manifest, root=root))

    def test_ezquake_catalog_and_recipes_are_rebuilt_from_the_same_assets(self) -> None:
        catalog = json.loads((PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8"))
        manifest = json.loads((PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8"))
        assets = []
        for package in catalog["packages"]:
            if package["component"] == "ezquake":
                assets.append(Asset(
                    "ezquake", package["origin_url"], package["distribution_path"], package["size"],
                ))
        assets.append(Asset(
            "ezquake",
            "https://example.invalid/ezquake-source.tar.gz",
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz",
            1,
            "ezquake-stable",
        ))
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "maintenance.manage.ezquake_source_revision", return_value="c" * 40,
        ):
            recipes = Path(temporary) / "recipes"

            stable, nightly = update_ezquake_catalog(copy.deepcopy(catalog), assets, manifest, recipes)

            self.assertEqual(stable, "3.6.9")
            self.assertEqual(nightly, "20260616-101233_a86996a")
            self.assertEqual(len(list(recipes.rglob("*.json"))), 3)

    def test_public_parser_exposes_the_complete_lifecycle(self) -> None:
        choices = parser()._subparsers._group_actions[0].choices
        self.assertEqual(set(choices), {"check", "update", "add", "verify", "build", "publish", "commit"})

    def test_update_summary_exposes_clients_ktx_td2_and_their_sources(self) -> None:
        catalog = json.loads((PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8"))
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        assets = [
            Asset("ezquake", package["origin_url"], package["distribution_path"], package["size"])
            for package in catalog["packages"]
            if package["component"] == "ezquake"
        ]
        assets.append(Asset(
            "ezquake",
            "https://example.invalid/ezquake-source.tar.gz",
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz",
            1,
            "ezquake-stable",
        ))
        results = [
            {
                "component": identifier,
                "installed": str(release["version"]),
                "latest_source": str(release.get("upstream", {}).get("release", release["version"])),
                "status": "current",
                "strategy": str(release["strategy"]),
            }
            for identifier, release in releases["components"].items()
        ]

        output = "\n".join(update_inventory_lines(results, assets, releases, catalog))
        self.assertIn("ezQuake stable: 3.6.9 (3 plataformas)", output)
        self.assertIn("ezQuake nightly: 20260616-101233_a86996a (3 plataformas)", output)
        self.assertIn("Interface e recursos visuais nQuake: e4cb23d40aa2", output)
        self.assertIn("QRP alta resolução: e4cb23d40aa2+x86qw.2", output)
        self.assertIn("Final Arena: e4cb23d40aa2+x86qw.6", output)
        self.assertIn("Pro-X: upstream 1.1; pacote x86QW 1.1+x86qw.1", output)
        self.assertIn("Team Fortress: upstream 2.9; pacote x86QW 2.9+nquake.e4cb23d40aa2+x86qw.1", output)
        self.assertIn("KTX competitivo", output)
        self.assertIn("dist/mods/ktx/1.47/upstream/qwprogs-qvm.zip", output)
        self.assertIn("Total Destruction 2", output)
        self.assertIn("dist/mods/td2/2.22/source/", output)

    def test_contextual_layout_has_no_legacy_root_directories(self) -> None:
        for name in ("distribution", "installer", "inventory", "recipes", "tools", "tests"):
            self.assertFalse((PROJECT_ROOT / name).exists(), name)
        self.assertFalse((PROJECT_ROOT / "maintenance/inventory/upstream-current.json").exists())
        self.assertTrue((PROJECT_ROOT / "site/wrangler.jsonc").is_file())


if __name__ == "__main__":
    unittest.main()
