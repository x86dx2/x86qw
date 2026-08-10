from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_073_SHA256 = "41ecb4d82d41c6d4733c6990c5baf40a9062f85ce9faf098d8e8822ad66784d6"
EXPECTED_073_SIZE = 286137
EXPECTED_074_SHA256 = "37f1372d2252a72ebdacb489ac15aacb45d45cebc5ee537ef158e43ed4e23e7f"
EXPECTED_074_SIZE = 286223


class Release074Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        cls.product = json.loads(
            (ROOT / "site/public/api/v1/product.json").read_text(encoding="utf-8")
        )
        cls.runtimes = {
            runtime["id"]: runtime
            for runtime in cls.product["runtimes"]
        }

    def test_historical_release_identity_and_previous_bundles_are_immutable(self):
        installers = [
            package for package in self.catalog["packages"]
            if package.get("package") == "x86qw-installer"
        ]
        release = next(package for package in installers if package["version"] == "0.7.4")
        self.assertFalse(release["current"])
        self.assertEqual(EXPECTED_074_SHA256, release["sha256"])
        self.assertEqual(EXPECTED_074_SIZE, release["size"])
        previous = next(package for package in installers if package["version"] == "0.7.3")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_073_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_073_SIZE, previous["size"])

    def test_public_release_notes_and_site_identify_the_corrective_contract(self):
        notes = (ROOT / "docs/releases/0.7.4.md").read_text(encoding="utf-8")
        self.assertIn("# x86QW 0.7.4", notes)
        self.assertIn("portable-contract", notes)

    def test_platform_truth_does_not_claim_native_support(self):
        stable = {
            platform["variant"]: platform
            for platform in self.runtimes["ezquake-stable"]["platforms"]
        }
        self.assertEqual("conditional", stable["macos-universal"]["support"])
        self.assertEqual("portable-contract", stable["macos-universal"]["validation"])
        self.assertEqual("preview", stable["linux-x86_64"]["support"])
        self.assertEqual("preview", stable["windows-x64"]["support"])
        self.assertEqual(
            {"macos-arm64", "macos-x64"},
            {target["variant"] for target in stable["macos-universal"]["support_targets"]},
        )
        for runtime in self.runtimes.values():
            for platform in runtime["platforms"]:
                with self.subTest(runtime=runtime["id"], variant=platform["variant"]):
                    self.assertEqual("portable-contract", platform["validation"])


if __name__ == "__main__":
    unittest.main()
