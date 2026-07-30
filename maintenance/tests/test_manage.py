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
    ezquake_source_revision,
    github_release_coordinates,
    parser,
    publish_github,
    preserve_profile_fingerprints,
    reference_content_changed,
    summarize_delta,
    update_inventory_lines,
    update_ezquake_catalog,
    update_reference_releases,
)


class DistributionManagerTests(unittest.TestCase):
    def test_registered_nightly_revision_does_not_require_github_lookup(self) -> None:
        asset = Asset(
            "ezquake",
            "https://example.invalid/ezquake.zip",
            "clients/ezquake/nightly/20260616-101233_a86996a/macos-universal/ezQuake.zip",
            1,
        )

        with mock.patch("maintenance.manage.github_commit_revision") as lookup:
            revision = ezquake_source_revision(asset)

        self.assertEqual("a86996a3d33dc1bc3fb15bfe7bcadd662b822557", revision)
        lookup.assert_not_called()

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
        new = "b" * 40

        changed = update_reference_releases(releases, new)

        self.assertTrue(changed)
        self.assertEqual(releases["reference"]["revision"], new)
        self.assertEqual(
            releases["components"]["final-arena"]["version"],
            "1.20+nquake.bbbbbbbbbbbb+x86qw.1",
        )
        self.assertEqual(releases["components"]["pro-x"]["version"], "1.1+x86qw.2")
        self.assertIn("nquake.bbbbbbbbbbbb", releases["components"]["team-fortress"]["version"])
        self.assertEqual("1.47+x86qw.2", releases["components"]["ktx"]["version"])
        self.assertEqual("ktx-1.47-x86qw.2", releases["components"]["ktx"]["distribution_tag"])

    def test_reference_advance_without_consumed_byte_changes_is_ignored(self) -> None:
        payload = b"same product bytes"
        digest = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = f"distributions/nquake/{'a' * 40}/non-gpl/qw/autoexec.cfg"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            manifest = {"files": {relative: {
                "url": "https://example.invalid/old",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "package": "nquake-bootstrap",
            }}}
            assets = [Asset(
                "nquake", "https://example.invalid/new", f"distributions/nquake/{'b' * 40}/non-gpl/qw/autoexec.cfg",
                None, "nquake-bootstrap", None, digest,
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
            generated = json.loads(next(recipes.rglob("macos-universal.json")).read_text())
            self.assertIn("x86dx2/x86qw/releases", generated["package"]["urls"][0])

    def test_github_release_coordinates_support_current_and_legacy_repositories(self) -> None:
        filename = "x86qw-installer-0.1.0.zip"
        self.assertEqual(
            ("x86dx2/x86qw", "x86qw-installer-0.1.0"),
            github_release_coordinates(
                f"https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.1.0/{filename}",
                filename,
            ),
        )

    def test_component_release_is_created_without_taking_latest(self) -> None:
        package = {
            "component": "core",
            "package": "x86qw-core-id1",
            "version": "0.1.0",
            "filename": "x86qw-core-id1-0.1.0.zip",
            "size": 1,
            "sha256": "0" * 64,
            "urls": [
                "https://github.com/x86dx2/x86qw/releases/download/"
                "x86qw-content-core-0.1.0/x86qw-core-id1-0.1.0.zip"
            ],
            "mirror_title": "x86QW Content · Dados base 0.1.0",
            "mirror_notes": "Dados base.",
            "mirror_latest": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / package["filename"]
            artifact.write_bytes(b"x")
            with mock.patch("maintenance.manage.local_artifact", return_value=artifact):
                with mock.patch("maintenance.manage.github_release", return_value=None):
                    with mock.patch("maintenance.manage.github_latest_release_tag", return_value=None):
                        with mock.patch("maintenance.manage.run") as run:
                            publish_github({"packages": [package]}, dry_run=False)
        create = run.call_args_list[0].args[0]
        self.assertIn("--latest=false", create)
        self.assertIn("x86QW Content · Dados base 0.1.0", create)
        self.assertEqual(
            ("x86dx2/x86qw", "x86qw-content-test-0.1.0"),
            github_release_coordinates(
                "https://github.com/x86dx2/x86qw/releases/download/x86qw-content-test-0.1.0/"
                "x86qw-test-0.1.0.zip",
                "x86qw-test-0.1.0.zip",
            ),
        )

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
        self.assertIn("QRP alta resolução: e4cb23d40aa2+x86qw.1", output)
        self.assertIn("Final Arena: upstream 1.20; pacote x86QW 1.20+nquake.e4cb23d40aa2+x86qw.1", output)
        self.assertIn("Pro-X: upstream 1.1; pacote x86QW 1.1+x86qw.2", output)
        self.assertIn("Team Fortress: upstream 2.9; pacote x86QW 2.9+nquake.e4cb23d40aa2+x86qw.3", output)
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
