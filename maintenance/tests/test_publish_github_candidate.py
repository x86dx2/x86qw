from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.publish_github_candidate import (
    PublisherError,
    _asset_plan,
    _expected_assets,
    _github_latest,
    _release_create_command,
)


class PublishGithubCandidateTests(unittest.TestCase):
    def test_expected_assets_include_all_candidate_zips_and_bound_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            zip_path = candidate / "installer" / "x86qw-installer-1.0.0.zip"
            zip_path.parent.mkdir()
            zip_path.write_bytes(b"installer")
            for name in (
                "candidate.json",
                "checksums.txt",
                "ownership.json",
                "sbom.spdx.json",
                "provenance.json",
                "mirrors.json",
            ):
                (candidate / name).write_bytes(name.encode("utf-8"))
            manifest = {
                "artifacts": {
                    "installer/x86qw-installer-1.0.0.zip": {
                        "size": zip_path.stat().st_size,
                        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                    },
                    "runtime/native-smoke/macos-arm64/x86qw-native-smoke": {
                        "size": 1,
                        "sha256": "0" * 64,
                    },
                },
                "metadata": {
                    name: {
                        "size": len(name.encode("utf-8")),
                        "sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
                    }
                    for name in (
                        "checksums.txt",
                        "ownership.json",
                        "sbom.spdx.json",
                        "provenance.json",
                        "mirrors.json",
                    )
                },
            }
            expected = _expected_assets(candidate, manifest)
            self.assertEqual(
                {
                    "x86qw-installer-1.0.0.zip",
                    "candidate.json",
                    "checksums.txt",
                    "ownership.json",
                    "sbom.spdx.json",
                    "provenance.json",
                    "mirrors.json",
                },
                set(expected),
            )
            self.assertEqual(
                hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                expected["x86qw-installer-1.0.0.zip"]["digest"][len("sha256:"):],
            )

    def test_asset_plan_only_returns_missing_and_rejects_divergence_or_extras(self) -> None:
        expected = {
            "installer.zip": {"size": 3, "digest": "sha256:" + "a" * 64},
            "candidate.json": {"size": 4, "digest": "sha256:" + "b" * 64},
        }
        self.assertEqual(
            ["candidate.json"],
            _asset_plan(expected, {"installer.zip": expected["installer.zip"]}),
        )
        with self.assertRaises(PublisherError):
            _asset_plan(expected, {"installer.zip": {"size": 4, "digest": "sha256:" + "c" * 64}})
        with self.assertRaises(PublisherError):
            _asset_plan(expected, {**expected, "unexpected.zip": expected["installer.zip"]})

    def test_create_command_targets_commit_and_never_allows_overwrite(self) -> None:
        command = _release_create_command(
            repository="x86dx2/x86qw",
            tag="x86qw-installer-1.0.0",
            title="x86QW Installer 1.0.0",
            notes="# x86QW 1.0.0",
            commit="a" * 40,
            prerelease=False,
            latest=True,
        )
        self.assertEqual("release", command[0])
        self.assertIn("--target", command)
        self.assertIn("a" * 40, command)
        self.assertNotIn("--clobber", command)
        self.assertNotIn("--force", command)

    def test_prerelease_is_not_github_latest_even_when_site_is_current(self) -> None:
        self.assertFalse(_github_latest(mirror_latest=True, prerelease=True))
        command = _release_create_command(
            repository="x86dx2/x86qw",
            tag="x86qw-installer-1.0.0-rc.1",
            title="x86QW Installer 1.0.0-rc.1",
            notes="Release candidate.",
            commit="a" * 40,
            prerelease=True,
            latest=True,
        )
        self.assertIn("--prerelease", command)
        self.assertIn("--latest=false", command)


if __name__ == "__main__":
    unittest.main()
