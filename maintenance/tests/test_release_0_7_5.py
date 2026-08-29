from __future__ import annotations

import hashlib
import io
import json
import re
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_074_SHA256 = "37f1372d2252a72ebdacb489ac15aacb45d45cebc5ee537ef158e43ed4e23e7f"
EXPECTED_074_SIZE = 286223
EXPECTED_075_SHA256 = "2af9a14729f0aa4dfd3ae8397fa025bc92c7b354ad5df8f1b741406e69414cf6"
EXPECTED_075_SIZE = 577437
EXPECTED_076_SHA256 = "ee3ce227fd1e6b604d56cf1c7559b57fe843fd5377fa592c075ba7210ed740c0"
EXPECTED_076_SIZE = 577554
EXPECTED_077_SHA256 = "7a3b0f2551f267e5d9a03ce9e3ac0b1a55b8be1bfc47f075500415624bfa637f"
EXPECTED_077_SIZE = 577558
EXPECTED_078_SHA256 = "a594700b634be563c941d99272e017d8da2434417fa82c412666ac1e45db818c"
EXPECTED_078_SIZE = 577617
EXPECTED_079_SHA256 = "fb08bc987641d4bea84c463d8f58b9c93b62595ff42ea0f764d4123814be7e33"
EXPECTED_079_SIZE = 581134
EXPECTED_0710_SHA256 = "284ea22d82945d0d1b9e06fa41e130813755cdc63d24a34ae334d9a627837b61"
EXPECTED_0710_SIZE = 581288
EXPECTED_0711_SHA256 = "3ce8e3c31c76d040249119c6dee37fc0829c14ff4d973f58ccdd72598f1bba53"
EXPECTED_0711_SIZE = 581352
EXPECTED_0712_SHA256 = "aa6fb98383c16c907a620b7041da03e50faec52fa5033fd66b9ee824bdf7dbb7"
EXPECTED_0712_SIZE = 581623


class Release0713Tests(unittest.TestCase):
    def test_release_identity_root_and_previous_bundle_are_consistent(self) -> None:
        catalog = json.loads(
            (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        product = json.loads(
            (ROOT / "site/public/api/v1/product.json").read_text(encoding="utf-8")
        )
        installers = [
            package for package in catalog["packages"]
            if package.get("package") == "x86qw-installer"
        ]
        current = [package for package in installers if package.get("current") is True]
        self.assertEqual("1.0.8", (ROOT / "dist/installer/VERSION").read_text().strip())
        self.assertEqual("1.0.8", (ROOT / "dist/installer/packages/latest").resolve().name)
        self.assertEqual(["1.0.8"], [package["version"] for package in current])
        self.assertEqual("1.0.8", product["version"])

        previous = next(package for package in installers if package["version"] == "0.7.4")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_074_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_074_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.5")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_075_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_075_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.6")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_076_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_076_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.7")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_077_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_077_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.8")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_078_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_078_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.9")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_079_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_079_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.10")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_0710_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_0710_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.11")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_0711_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_0711_SIZE, previous["size"])

        previous = next(package for package in installers if package["version"] == "0.7.12")
        self.assertFalse(previous["current"])
        self.assertEqual(EXPECTED_0712_SHA256, previous["sha256"])
        self.assertEqual(EXPECTED_0712_SIZE, previous["size"])

        historical = next(package for package in installers if package["version"] == "0.7.13")
        self.assertFalse(historical["current"])
        historical_bundle = ROOT / "dist/installer/packages/0.7.13/x86qw-installer-0.7.13.zip"
        self.assertEqual(historical["sha256"], hashlib.sha256(historical_bundle.read_bytes()).hexdigest())
        self.assertEqual(historical["size"], historical_bundle.stat().st_size)

        previous_101 = next(package for package in installers if package["version"] == "1.0.1")
        self.assertFalse(previous_101["current"])
        previous_102 = next(package for package in installers if package["version"] == "1.0.2")
        self.assertFalse(previous_102["current"])
        previous_103 = next(package for package in installers if package["version"] == "1.0.3")
        self.assertFalse(previous_103["current"])
        previous_104 = next(package for package in installers if package["version"] == "1.0.4")
        self.assertFalse(previous_104["current"])
        previous_105 = next(package for package in installers if package["version"] == "1.0.5")
        self.assertFalse(previous_105["current"])

        bundle = ROOT / "dist/installer/packages/1.0.8/x86qw-installer-1.0.8.zip"
        self.assertEqual(current[0]["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(current[0]["size"], bundle.stat().st_size)
        with zipfile.ZipFile(bundle) as outer:
            application = outer.read("x86qw-installer-1.0.8/x86qw.pyz")
        with zipfile.ZipFile(io.BytesIO(application)) as inner:
            self.assertEqual(
                (ROOT / "maintenance/trust/root.json").read_bytes(),
                inner.read("_x86qw/trust/root.json"),
            )
            self.assertFalse(any(name.endswith((".pem", ".key")) for name in inner.namelist()))

        self.assertIn('INSTALLER_VERSION="1.0.8"', (ROOT / "site/public/install.sh").read_text())
        self.assertIn('$InstallerVersion = "1.0.8"', (ROOT / "site/public/install.ps1").read_text())

    def test_public_trust_repository_authenticates_the_final_catalog(self) -> None:
        trust = ROOT / "site/public/api/v1/trust"
        catalog = (ROOT / "site/public/api/v1/catalog.json").read_bytes()
        digest = hashlib.sha256(catalog).hexdigest()
        timestamp = json.loads(
            (trust / "metadata/timestamp.json").read_text(encoding="utf-8")
        )
        snapshot_meta = timestamp["signed"]["meta"]["snapshot.json"]
        version = snapshot_meta["version"]
        current_metadata = {
            "metadata/1.root.json",
            f"metadata/{version}.targets.json",
            f"metadata/{version}.snapshot.json",
            "metadata/timestamp.json",
        }
        actual = {
            path.relative_to(trust).as_posix()
            for path in trust.rglob("*")
            if path.is_file()
        }
        self.assertTrue(current_metadata <= actual)
        self.assertIn(f"targets/catalog/{digest}.catalog.json", actual)
        self.assertTrue(
            all(
                path == "metadata/1.root.json"
                or path == "metadata/timestamp.json"
                or path.startswith("metadata/")
                and path.endswith((".targets.json", ".snapshot.json"))
                or path.startswith("targets/catalog/")
                and path.endswith(".catalog.json")
                for path in actual
            )
        )
        self.assertEqual(catalog, (trust / f"targets/catalog/{digest}.catalog.json").read_bytes())

    def test_public_text_identifies_the_installation_baseline_hotfix(self) -> None:
        notes = (ROOT / "docs/releases/0.7.13.md").read_text(encoding="utf-8")
        self.assertIn("# x86QW 0.7.13", notes)
        self.assertIn("directory-preferences", notes)
        self.assertIn("CFPreferences", notes)
        index = (ROOT / "site/public/index.html").read_text(encoding="utf-8")
        self.assertNotIn("0.7.13 histórica", index)
        visible_index = re.sub(r"<[^>]+>", "", index)
        self.assertIn("x86QW 1.0.8", visible_index)
        self.assertIn("29 pacotes", visible_index)


if __name__ == "__main__":
    unittest.main()
