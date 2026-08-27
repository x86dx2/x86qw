from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReleaseTruthProjectionTests(unittest.TestCase):
    def test_public_projection_matches_dated_authority(self) -> None:
        authority = ROOT / "docs/post-1.0/release-truth-current.json"
        projection = ROOT / "site/public/api/v1/release-truth.json"
        self.assertEqual(authority.read_bytes(), projection.read_bytes())
        document = json.loads(authority.read_text(encoding="utf-8"))
        self.assertEqual("GREEN", document["status"]["main"])
        self.assertEqual("HEALTHY", document["status"]["tuf"])
        self.assertEqual("owner-only", document["authorities"]["candidate_release"]["audience"])
        self.assertFalse(document["authorities"]["candidate_release"]["external_public_authorized"])
        self.assertEqual("NO-GO", document["status"]["external_public"])
        self.assertIn("0B-sustainable-custody-backup-RTO", document["open_gates"])
        live = document["authorities"]["deployment"]["live_observation"]
        self.assertEqual("200", live["release_truth_endpoint"])
        self.assertEqual("1.0.0", live["product_version"])
        self.assertEqual("1.0.0", live["catalog_current_installer"])
        self.assertEqual("CONVERGED_CANDIDATE_DEPLOYMENT", live["state"])
        home = (ROOT / "site/public/index.html").read_text(encoding="utf-8")
        visible_home = re.sub(r"<[^>]+>", "", home)
        self.assertIn("owner-only", visible_home)
        self.assertIn("external-public", visible_home)
        self.assertIn("release-product-version", home)
        self.assertIn("release-package-count", home)

    def test_product_points_to_the_release_truth_projection(self) -> None:
        product = json.loads(
            (ROOT / "site/public/api/v1/product.json").read_text(encoding="utf-8")
        )
        self.assertEqual("/api/v1/release-truth.json", product["release_truth_url"])
        self.assertEqual("owner-only", product["release_audience"])
        self.assertFalse(product["external_public"])

    def test_release_truth_reports_the_highest_published_root(self) -> None:
        projection = json.loads(
            (ROOT / "site/public/api/v1/release-truth.json").read_text(encoding="utf-8")
        )
        metadata = ROOT / "site/public/api/v1/trust/metadata"
        versions = []
        for path in metadata.glob("*.root.json"):
            version = int(path.name.removesuffix(".root.json"))
            signed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(version, signed["signed"]["version"])
            versions.append(version)
        self.assertTrue(versions)
        self.assertEqual(list(range(1, max(versions) + 1)), sorted(versions))
        self.assertEqual(
            max(versions),
            projection["authorities"]["deployment"]["tuf"]["root_version"],
        )

    def test_user_facing_surfaces_explain_owner_only(self) -> None:
        surfaces = (
            ROOT / "README.md",
            ROOT / "dist/installer/docs/installer.md",
            ROOT / "site/public/index.html",
        )
        for path in surfaces:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("owner-only", text)
                self.assertIn("external-public", text)

    def test_historical_root_version_claims_link_to_the_errata(self) -> None:
        errata = (ROOT / "docs/post-1.0/ERRATA-TUF-ROOT-VERSION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "660af63e52a033290adf8899d2078a779c04e04cf5d1fac465b4aa2e04937201",
            errata,
        )
        for path in (
            ROOT / "docs/post-1.0/AUDIT-BASELINE.md",
            ROOT / "docs/post-1.0/LAYER-MAP.md",
            ROOT / "docs/post-1.0/RELEASE-TRUTH-CURRENT.md",
            ROOT / "docs/post-1.0/TUF-SLO-AND-RECOVERY.md",
        ):
            with self.subTest(path=path):
                self.assertIn("ERRATA-TUF-ROOT-VERSION.md", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
