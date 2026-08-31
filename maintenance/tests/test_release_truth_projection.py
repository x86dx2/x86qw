from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReleaseTruthProjectionTests(unittest.TestCase):
    def test_public_projection_matches_explicit_offline_seed(self) -> None:
        authority = ROOT / "docs/post-1.0/release-truth-projection-seed.json"
        projection = ROOT / "site/public/api/v1/release-truth.json"
        self.assertEqual(authority.read_bytes(), projection.read_bytes())
        document = json.loads(authority.read_text(encoding="utf-8"))
        self.assertEqual("GREEN", document["status"]["main"])
        self.assertEqual("HEALTHY", document["status"]["tuf"])
        self.assertEqual(
            "962fb2b2cc27560e982c2255d9299a55f16acdd1",
            document["snapshot_commit"],
        )
        self.assertEqual("owner-only", document["authorities"]["candidate_release"]["audience"])
        self.assertFalse(document["authorities"]["candidate_release"]["external_public_authorized"])
        self.assertEqual("NO-GO", document["status"]["external_public"])
        self.assertIn("0B-sustainable-custody-backup-RTO", document["open_gates"])
        live = document["authorities"]["deployment"]["live_observation"]
        self.assertEqual("200", live["release_truth_endpoint"])
        self.assertEqual("1.0.0", live["product_version"])
        self.assertEqual("1.0.0", live["catalog_current_installer"])
        self.assertEqual("CONVERGED_CANDIDATE_DEPLOYMENT", live["state"])
        self.assertEqual(
            "x86QW 1.0.0 · validado no Apple M3",
            live["root_site_hero"],
        )
        self.assertNotIn("0.7.13", live["root_site_hero"])
        self.assertEqual(
            30,
            document["authorities"]["deployment"]["tuf"]["timestamp_version"],
        )
        self.assertEqual(
            29,
            document["authorities"]["deployment"]["tuf"]["snapshot_version"],
        )
        self.assertEqual(
            33136179763,
            document["authorities"]["deployment"]["tuf"]["publication_run_id"],
        )
        self.assertEqual(
            33135314707,
            document["authorities"]["deployment"]["tuf"]["renewal_run_id"],
        )
        self.assertEqual(
            "962fb2b2cc27560e982c2255d9299a55f16acdd1",
            document["authorities"]["development"]["head"],
        )
        self.assertEqual(33135951867, document["authorities"]["development"]["validate_run"])
        evidence = document["evidence"]
        self.assertEqual(33116886265, evidence["main_green"]["run_id"])
        self.assertEqual(
            "9ec18b6355790ef9f797783ebe3ab86036a36cd8",
            evidence["main_green"]["commit"],
        )
        self.assertEqual(33107505069, evidence["tuf_renewal"]["run_id"])
        self.assertEqual("27->28", evidence["tuf_renewal"]["timestamp_version"])
        self.assertEqual(33115777739, evidence["deployment_projection"]["run_id"])
        self.assertEqual(
            "a03a8b0e3dcd97a66d338891dacd6ca80befdbee907ed9b83007a538bb97646a",
            evidence["deployment_projection"]["catalog_sha256"],
        )
        home = (ROOT / "site/public/index.html").read_text(encoding="utf-8")
        visible_home = re.sub(r"<[^>]+>", "", home)
        self.assertIn("owner-only", visible_home)
        self.assertIn("external-public", visible_home)
        self.assertIn("release-product-version", home)
        self.assertIn("release-package-count", home)

    def test_current_release_truth_is_a_live_authority_pointer(self) -> None:
        pointer = json.loads(
            (ROOT / "docs/post-1.0/release-truth-current.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("x86qw-live-release-truth-pointer-v1", pointer["schema"])
        self.assertEqual(
            "https://qw.x86.com.br/api/v1/release-truth.json",
            pointer["authority_url"],
        )
        self.assertNotIn("alias_url", pointer)
        self.assertEqual(
            "release-truth-projection-seed.json", pointer["projection_seed"]
        )
        self.assertEqual("owner-only", pointer["required"]["release_audience"])
        self.assertEqual("NO-GO", pointer["required"]["external_public"])

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

    def test_current_status_surfaces_match_the_verified_snapshot(self) -> None:
        status = (ROOT / "docs/PROJECT-STATUS.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        release_note = (ROOT / "docs/releases/1.0.10.md").read_text(encoding="utf-8")
        current_truth = (ROOT / "docs/post-1.0/RELEASE-TRUTH-CURRENT.md").read_text(
            encoding="utf-8"
        )

        for path, text in (
            (ROOT / "docs/PROJECT-STATUS.md", status),
            (ROOT / "docs/ROADMAP.md", roadmap),
            (ROOT / "README.md", readme),
            (ROOT / "docs/releases/1.0.10.md", release_note),
            (ROOT / "docs/post-1.0/RELEASE-TRUTH-CURRENT.md", current_truth),
        ):
            with self.subTest(path=path):
                self.assertIn("1.0.10", text)
                self.assertIn("owner-only", text)
                self.assertIn("external-public", text)

        for marker in (
            "https://qw.x86.com.br/api/v1/release-truth.json",
            "verify_live_release_truth.py",
            "release-truth-projection-seed.json",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, status)
                self.assertIn(marker, current_truth)

        operational_records = {
            ROOT / "docs/post-1.0/CI-HEALTH.md": ("MAIN=GREEN", "33135951867"),
            ROOT / "docs/post-1.0/TUF-SLO-AND-RECOVERY.md": (
                "timestamp v30",
                "snapshot/targets v29",
                "external-public=NO-GO",
            ),
            ROOT / "docs/post-1.0/RELEASE-AUDIENCE.md": (
                "VALID_FOR_SINGLE_USER_M3",
                "external-public",
            ),
            ROOT / "docs/post-1.0/EXTERNAL-PUBLIC-READINESS.md": (
                "33136179763",
                "NO-GO",
            ),
        }
        for path, markers in operational_records.items():
            text = path.read_text(encoding="utf-8")
            for marker in markers:
                with self.subTest(path=path, marker=marker):
                    self.assertIn(marker, text)

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
