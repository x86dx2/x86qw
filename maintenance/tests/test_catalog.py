from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

from add_package import register_package  # noqa: E402
from downloader import MAX_ARTIFACT_BYTES  # noqa: E402
from validate_catalog import validate_catalog  # noqa: E402
from publish_gitlab_packages import artifact_url, main as publish_gitlab_main, upload  # noqa: E402
from build_component_packages import component_package_metadata, register_packages  # noqa: E402
from build_core_package import build_core_package  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_gitlab_publish_verifies_existing_remote_without_local_build(self) -> None:
        package = copy.deepcopy(json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )["packages"][0])
        package.update({
            "package": "x86qw-core-id1",
            "version": "0.1.0",
            "filename": "x86qw-core-id1-0.1.0.zip",
            "size": 1,
            "sha256": "a" * 64,
            "urls": ["https://example.invalid/x86qw-core-id1-0.1.0.zip"],
            "origin_url": "https://example.invalid/x86qw-core-id1-0.1.0.zip",
            "source_urls": ["https://example.invalid/source"],
        })
        catalog = {"format": 1, "project": "x86qw", "generated_at": "2026-08-04T00:00:00Z", "packages": [package]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with patch("publish_gitlab_packages.remote_sha256", return_value=(1, "a" * 64)), patch(
                "publish_gitlab_packages.local_artifact", side_effect=AssertionError("local build required"),
            ), patch.object(sys, "argv", [
                "publish_gitlab_packages.py", "--catalog", str(catalog_path),
                "--dist", str(root / "missing-dist"), "--builds", str(root / "missing-builds"),
            ]):
                self.assertEqual(0, publish_gitlab_main())

    @patch("publish_gitlab_packages.subprocess.run")
    def test_gitlab_upload_uses_documented_file_put_without_token_in_argv(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "201"
        package = {
            "component": "installer", "package": "x86qw-installer",
            "version": "0.2.2", "filename": "x86qw-installer-0.2.2.zip",
        }
        with patch.dict(os.environ, {"GLAB_TOKEN": "private-token"}, clear=False):
            upload(Path("bundle.zip"), package)
        arguments = run.call_args.args[0]
        self.assertEqual(["curl", "--disable"], arguments[:2])
        self.assertIn("--upload-file", arguments)
        self.assertIn("--header", arguments)
        self.assertIn("--proto", arguments)
        self.assertIn("--proto-redir", arguments)
        self.assertIn("--max-time", arguments)
        self.assertIn("--max-redirs", arguments)
        self.assertIn("--output", arguments)
        self.assertIn("--write-out", arguments)
        self.assertNotIn("--location", arguments)
        self.assertIn("@-", arguments)
        self.assertNotIn("private-token", repr(arguments))
        self.assertEqual("PRIVATE-TOKEN: private-token\n", run.call_args.kwargs["input"])
        self.assertIs(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    @patch("publish_gitlab_packages.subprocess.run")
    def test_gitlab_upload_rejects_redirect_without_capturing_response_body(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "302"
        package = {
            "component": "installer", "package": "x86qw-installer",
            "version": "0.2.2", "filename": "x86qw-installer-0.2.2.zip",
        }
        with patch.dict(os.environ, {"GLAB_TOKEN": "private-token"}, clear=False):
            with self.assertRaisesRegex(ValueError, "HTTP status 302"):
                upload(Path("bundle.zip"), package)
        arguments = run.call_args.args[0]
        self.assertEqual(os.devnull, arguments[arguments.index("--output") + 1])

    def test_catalog_rejects_artifacts_above_the_global_download_limit(self) -> None:
        catalog = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        catalog["packages"][0]["size"] = MAX_ARTIFACT_BYTES + 1
        with self.assertRaisesRegex(ValueError, "supported download limit"):
            validate_catalog(catalog)

    def test_catalog_rejects_boolean_sizes_and_format(self) -> None:
        source = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        invalid_size = copy.deepcopy(source)
        invalid_size["packages"][0]["size"] = True
        with self.assertRaises(ValueError):
            validate_catalog(invalid_size)
        invalid_format = copy.deepcopy(source)
        invalid_format["format"] = True
        with self.assertRaises(ValueError):
            validate_catalog(invalid_format)

    def test_catalog_json_duplicate_top_level_field_is_rejected(self) -> None:
        from validate_catalog import _reject_duplicate_pairs

        with self.assertRaises(ValueError):
            json.loads(
                '{"format":1,"format":1,"project":"x86qw","packages":[]}',
                object_pairs_hook=_reject_duplicate_pairs,
            )

    def test_catalog_rejects_credential_fragment_and_control_urls_without_secret(self) -> None:
        source = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        package = source["packages"][0]
        filename = package["filename"]
        secret = "never-print-catalog-secret"
        unsafe_urls = (
            f"https://reviewer:{secret}@example.invalid/{filename}",
            f"https://example.invalid/{filename}#{secret}",
            f"https://example.invalid/{filename}?token={secret}",
            f"https://example.invalid/{filename}?token={secret}\nforged",
        )

        for url in unsafe_urls:
            catalog = {**source, "packages": [{**package, "urls": [url]}]}
            with self.subTest(url=url), self.assertRaises(ValueError) as raised:
                validate_catalog(catalog)
            self.assertNotIn(secret, str(raised.exception))

    def test_ezquake_versions_and_distribution_coordinates_are_exact(self) -> None:
        current = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        clients = [
            package for package in current["packages"]
            if package["component"] == "ezquake"
        ]
        self.assertEqual(
            {
                ("stable", "linux", "x86_64"),
                ("stable", "macos", "universal"),
                ("stable", "windows", "x64"),
                ("nightly", "linux", "x86_64"),
                ("nightly", "macos", "universal"),
                ("nightly", "windows", "x64"),
            },
            {
                (package["channel"], package["platform"], package["architecture"])
                for package in clients
            },
        )
        for source in clients:
            package = copy.deepcopy(source)
            package["version"] = (
                "9.9.9"
                if package["channel"] == "stable"
                else "20990101-010203_abcdef0"
            )
            package["distribution_path"] = (
                f"clients/ezquake/{package['channel']}/{package['version']}/"
                f"{package['platform']}-{package['architecture']}/{package['filename']}"
            )
            with self.subTest(
                channel=package["channel"],
                platform=package["platform"],
                architecture=package["architecture"],
            ):
                self.assertEqual(1, validate_catalog({
                    "format": 1,
                    "project": "x86qw",
                    "generated_at": None,
                    "packages": [package],
                }))

        source = next(
            package for package in clients
            if package["channel"] == "stable" and package["platform"] == "linux"
        )
        base = copy.deepcopy(source)
        base["version"] = "9.9.9"
        base["distribution_path"] = (
            f"clients/ezquake/stable/9.9.9/linux-x86_64/{base['filename']}"
        )
        invalid = {
            "channel": {
                **base,
                "channel": "nightly",
                "version": "20990101-010203_abcdef0",
            },
            "version": {**base, "version": "9.9.8"},
            "platform": {**base, "platform": "windows"},
            "architecture": {**base, "architecture": "arm128"},
            "filename": {
                **base,
                "distribution_path": str(base["distribution_path"]).replace(
                    str(base["filename"]), "other.zip",
                ),
            },
            "stable-version-shape": {**base, "version": "9.9.9-review"},
            "nightly-version-shape": {
                **base,
                "channel": "nightly",
                "version": "garbage",
            },
        }
        for label, package in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_catalog({
                    "format": 1,
                    "project": "x86qw",
                    "generated_at": None,
                    "packages": [package],
                })

    def test_repository_catalog_and_trust_boundary(self) -> None:
        catalog = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        self.assertEqual(validate_catalog(catalog), len(catalog["packages"]))
        self.assertEqual(6, sum(package["component"] == "ezquake" for package in catalog["packages"]))
        ktx = next(package for package in catalog["packages"] if package.get("package") == "ktx")
        self.assertEqual("1.47+x86qw.19", ktx["version"])
        self.assertEqual("1.47", ktx["upstream_version"])
        self.assertEqual("ktx", ktx["component"])
        self.assertTrue(all(package["urls"] for package in catalog["packages"]))
        self.assertTrue(all("github.com" in package["urls"][0] for package in catalog["packages"]))
        clients = [package for package in catalog["packages"] if package["component"] == "ezquake"]
        content = [package for package in catalog["packages"] if package["channel"] == "content"]
        self.assertTrue(all(package.get("distribution_path") for package in clients))
        self.assertTrue(all((ROOT / "dist" / package["distribution_path"]).is_file() for package in clients))
        derived_content = [package for package in content if package["component"] != "installer"]
        self.assertTrue(all("distribution_path" not in package for package in derived_content))
        self.assertEqual(ktx["origin_url"], ktx["urls"][0])
        self.assertTrue(set(ktx["urls"]) <= {ktx["origin_url"], artifact_url(ktx)})
        core = next(package for package in catalog["packages"] if package.get("package") == "x86qw-core-id1")
        self.assertEqual("core", core["component"])
        self.assertEqual("0.1.0", core["version"])
        self.assertEqual(64, len(core["source_revision"]))
        self.assertFalse(core["mirror_latest"])
        self.assertEqual(
            {package["package"] for package in catalog["packages"] if package["component"] == "nquake"},
            {
                "nquake-bootstrap", "nquake-visual-core",
                "nquake-player-skins", "nquake-crosshairs", "nquake-skyboxes",
                "nquake-models", "nquake-scoreboard-flags",
                "nquake-external-textures", "nquake-base-textures", "nquake-maps",
                "nquake-matchinfo", "nquake-documentation", "qrp-hires",
            },
        )
        product_bootstraps = [
            package for package in catalog["packages"]
            if package.get("package") == "x86qw-client-bootstrap"
        ]
        self.assertEqual(1, len(product_bootstraps))
        self.assertEqual("x86qw", product_bootstraps[0]["component"])
        self.assertTrue(
            product_bootstraps[0]["mirror_title"].startswith(
                "x86QW Content · Base e inicialização do cliente x86QW ",
            ),
        )
        td2 = next(package for package in catalog["packages"] if package.get("package") == "total-destruction-2")
        self.assertEqual("2.22+x86qw.5", td2["version"])
        self.assertEqual("td2", td2["component"])
        self.assertEqual(64, len(td2["source_revision"]))
        self.assertEqual("2.22", td2["upstream_version"])
        final_arena = next(package for package in catalog["packages"] if package.get("package") == "final-arena")
        pro_x = next(package for package in catalog["packages"] if package.get("package") == "pro-x")
        self.assertEqual("1.20+nquake.e4cb23d40aa2+x86qw.4", final_arena["version"])
        self.assertEqual("final-arena", final_arena["component"])
        self.assertEqual("1.1+x86qw.5", pro_x["version"])
        self.assertEqual("pro-x", pro_x["component"])
        team_fortress = next(
            package for package in catalog["packages"] if package.get("package") == "team-fortress"
        )
        self.assertEqual("2.9+nquake.e4cb23d40aa2+x86qw.6", team_fortress["version"])
        self.assertEqual("team-fortress", team_fortress["component"])
        installers = [package for package in catalog["packages"] if package["component"] == "installer"]
        current_version = (ROOT / "dist/installer/VERSION").read_text(encoding="utf-8").strip()
        package_versions = {
            path.parent.name
            for path in (ROOT / "dist/installer/packages").glob("*/*.zip")
            if not path.parent.is_symlink()
        }
        self.assertEqual(package_versions, {package["version"] for package in installers})
        self.assertEqual([current_version], [
            package["version"] for package in installers if package.get("current") is True
        ])
        latest = [package for package in catalog["packages"] if package.get("mirror_latest") is True]
        self.assertEqual([("x86qw-installer", current_version)], [
            (package.get("package"), package["version"]) for package in latest
        ])
        self.assertTrue(all(
            str(package.get("mirror_title", "")).startswith("x86QW Content · ")
            for package in catalog["packages"] if package["component"] != "installer"
        ))

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

    def test_internal_component_metadata_carries_customized_catalog_version(self) -> None:
        metadata = component_package_metadata(
            "x86qw-client-bootstrap",
            "e4cb23d40aa2+x86qw.1",
            "reference-snapshot",
            "e4cb23d40aa202335b5dafe4e8f1e8d424caac0d",
            [],
        )
        self.assertEqual("e4cb23d40aa2+x86qw.1", metadata["version"])
        self.assertEqual(
            "e4cb23d40aa202335b5dafe4e8f1e8d424caac0d",
            metadata["source_commit"],
        )

    def test_core_game_data_is_built_as_a_separate_deterministic_package(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = root / "dist"
            id1 = distribution / "game-data/id1"
            id1.mkdir(parents=True)
            (id1 / "pak0.pak").write_bytes(b"PACKzero")
            (id1 / "pak1.pak").write_bytes(b"PACKone")
            first = build_core_package(distribution, root / "one")
            second = build_core_package(distribution, root / "two")
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(first["size"], second["size"])
            self.assertEqual("x86qw-core-id1", first["package"])
            self.assertEqual(
                "https://github.com/x86dx2/x86qw/blob/x86qw-content-core-0.1.0/LICENSE",
                first["license_url"],
            )
            self.assertEqual(2, len(first["urls"]))
            self.assertIn("gitlab.com/api/v4/projects/84813414", first["urls"][1])
            archive = next((root / "one").rglob(str(first["filename"])))
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(
                    {
                        "_x86qw/component.json",
                        "payload/id1/pak0.pak",
                        "payload/id1/pak1.pak",
                    },
                    set(package.namelist()),
                )

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
            self.assertEqual(
                1, validate_catalog(json.loads(catalog_path.read_text(encoding="utf-8")))
            )
            with self.assertRaises(ValueError):
                register_package(catalog_path, artifact, **arguments)

    def test_component_registration_reclassifies_origin_and_updates_publication_mirrors(self) -> None:
        import tempfile

        package = {
            "component": "ktx", "package": "ktx", "version": "1.47",
            "channel": "content", "platform": "any", "architecture": "any",
            "filename": "ktx-1.47.zip", "size": 1, "sha256": "0" * 64,
            "origin_url": "https://github.com/x86dx2/x86qw/releases/download/ktx-1.47/ktx-1.47.zip",
            "license": "GPL-2.0", "license_url": "https://github.com/example/LICENSE",
            "source_urls": ["https://github.com/example/source.tar.gz"],
            "redistribution_reviewed": True,
            "urls": ["https://github.com/x86dx2/x86qw/releases/download/ktx-1.47/ktx-1.47.zip"],
            "source_commit": "a" * 40,
        }
        fallback = "https://gitlab.com/example/ktx-1.47.zip"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            legacy = "https://github.com/example/legacy/releases/download/ktx-1.47/ktx-1.47.zip"
            catalog_package = dict(
                package, component="nquake", origin_url=legacy, urls=[legacy, fallback],
            )
            path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "generated_at": None,
                "packages": [catalog_package],
            }))
            register_packages(path, {"packages": [package]})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(package["urls"], saved["packages"][0]["urls"])
            self.assertEqual(package["origin_url"], saved["packages"][0]["origin_url"])
            self.assertEqual("ktx", saved["packages"][0]["component"])


if __name__ == "__main__":
    unittest.main()
