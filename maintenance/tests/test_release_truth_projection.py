from __future__ import annotations

import json
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
        self.assertEqual("1.0.3", live["product_version"])
        self.assertEqual("1.0.3", live["catalog_current_installer"])
        self.assertEqual("CONVERGED_SOURCE_1_0_3", live["state"])

    def test_product_points_to_the_release_truth_projection(self) -> None:
        product = json.loads(
            (ROOT / "site/public/api/v1/product.json").read_text(encoding="utf-8")
        )
        self.assertEqual("/api/v1/release-truth.json", product["release_truth_url"])
        self.assertEqual("owner-only", product["release_audience"])
        self.assertFalse(product["external_public"])

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


if __name__ == "__main__":
    unittest.main()
