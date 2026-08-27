from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.project_release_truth import (
    ReleaseTruthProjectionError,
    project_release_truth,
)


class ProjectReleaseTruthTests(unittest.TestCase):
    def _fixture(self, root: Path, *, candidate_sha: str = "candidate") -> tuple[Path, Path, Path, Path]:
        source = root / "release-truth.json"
        source.write_text(
            json.dumps(
                {
                    "schema": "x86qw-release-truth-v1",
                    "observed_at_utc": "2026-08-20T00:00:00Z",
                    "snapshot_commit": "0" * 40,
                    "status": {
                        "main": "GREEN",
                        "tuf": "HEALTHY",
                        "owner_only_release": "VALID_FOR_SINGLE_USER_M3",
                        "external_public": "NO-GO",
                        "feature_work": "ALLOWED",
                    },
                    "authorities": {
                        "source": {"baseline": "1.0.4"},
                        "candidate_release": {
                            "tag": "x86qw-installer-1.0.0",
                            "version": "1.0.0",
                            "target_commit": "1" * 40,
                            "audience": "owner-only",
                            "external_public_authorized": False,
                            "installer_size_bytes": 10,
                            "installer_sha256": "a" * 64,
                            "candidate_sha256": candidate_sha,
                            "exact_native_evidence": {"level": "E3", "cases": 25},
                        },
                        "deployment": {
                            "live_observation": {
                                "product_version": "1.0.4",
                                "catalog_current_installer": "1.0.4",
                            },
                            "tuf": {},
                        },
                        "development": {},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        candidate = root / "candidate"
        (candidate / "site/public").mkdir(parents=True)
        (candidate / "site/public/index.html").write_text(
            '<p class="kicker">x86QW <span>1.0.0</span> · owner-only</p>',
            encoding="utf-8",
        )
        manifest = {
            "format": 1,
            "project": "x86qw",
            "version": "1.0.0",
            "commit": "1" * 40,
            "artifacts": {
                "installer/x86qw-installer-1.0.0.zip": {
                    "size": 10,
                    "sha256": "a" * 64,
                },
            },
        }
        (candidate / "candidate.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        (candidate / "catalog.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "project": "x86qw",
                    "packages": [
                        {
                            "package": "x86qw-installer",
                            "component": "installer",
                            "version": "1.0.0",
                            "current": True,
                            "size": 10,
                            "sha256": "a" * 64,
                        },
                    ],
                },
            ),
            encoding="utf-8",
        )
        (candidate / "product.json").write_text(
            json.dumps(
                {
                    "project": "x86qw",
                    "version": "1.0.0",
                    "release_audience": "owner-only",
                    "external_public": False,
                },
            ),
            encoding="utf-8",
        )
        trust = root / "trust/metadata"
        trust.mkdir(parents=True)
        (trust / "1.root.json").write_text(
            json.dumps({"signed": {"version": 1}}), encoding="utf-8"
        )
        for name, version in (("27.snapshot.json", 27), ("27.targets.json", 27)):
            (trust / name).write_text(
                json.dumps({"signed": {"version": version, "expires": "2026-11-25T00:00:00Z"}}),
                encoding="utf-8",
            )
        (trust / "timestamp.json").write_text(
            json.dumps({"signed": {"version": 28, "expires": "2026-09-03T00:00:00Z"}}),
            encoding="utf-8",
        )
        return source, candidate, root / "trust", root / "projection.json"

    def test_projects_exact_candidate_and_live_tuf_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, candidate, trust, output = self._fixture(Path(temporary))
            candidate_sha = hashlib.sha256((candidate / "candidate.json").read_bytes()).hexdigest()
            source.write_text(
                source.read_text(encoding="utf-8").replace('"candidate_sha256": "candidate"', f'"candidate_sha256": "{candidate_sha}"'),
                encoding="utf-8",
            )
            result = project_release_truth(
                source=source,
                candidate=candidate,
                trust_repository=trust,
                site_source=candidate / "site/public",
                release_code_commit="2" * 40,
                development_validate_run=123,
                observed_at="2026-08-27T18:00:00Z",
                output=output,
            )
            self.assertEqual("2" * 40, result["snapshot_commit"])
            live = result["authorities"]["deployment"]["live_observation"]
            self.assertEqual("1.0.0", live["product_version"])
            self.assertEqual("1.0.0", live["catalog_current_installer"])
            self.assertEqual("CONVERGED_CANDIDATE_DEPLOYMENT", live["state"])
            self.assertEqual(28, result["authorities"]["deployment"]["tuf"]["timestamp_version"])
            self.assertEqual(1, result["authorities"]["deployment"]["tuf"]["packages_observed"])
            self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))
            self.assertNotEqual(source.read_bytes(), output.read_bytes())

    def test_refuses_candidate_that_would_reuse_other_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, candidate, trust, output = self._fixture(Path(temporary))
            with self.assertRaises(ReleaseTruthProjectionError):
                project_release_truth(
                    source=source,
                    candidate=candidate,
                    trust_repository=trust,
                    site_source=candidate / "site/public",
                    release_code_commit="2" * 40,
                    development_validate_run=123,
                    observed_at="2026-08-27T18:00:00Z",
                    output=output,
                )


if __name__ == "__main__":
    unittest.main()
