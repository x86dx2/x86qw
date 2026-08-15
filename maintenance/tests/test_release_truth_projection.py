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
        self.assertEqual("WARNING", document["status"]["tuf"])
        self.assertEqual("owner-only", document["authorities"]["candidate_release"]["audience"])
        self.assertFalse(document["authorities"]["candidate_release"]["external_public_authorized"])
        self.assertEqual("NO-GO", document["status"]["external_public"])
        self.assertIn("0B-warning-renewal-custody-recovery", document["open_gates"])

    def test_product_points_to_the_release_truth_projection(self) -> None:
        product = json.loads(
            (ROOT / "site/public/api/v1/product.json").read_text(encoding="utf-8")
        )
        self.assertEqual("/api/v1/release-truth.json", product["release_truth_url"])

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
