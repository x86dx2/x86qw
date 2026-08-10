from __future__ import annotations

import hashlib
import io
import json
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


class Release079Tests(unittest.TestCase):
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
        self.assertEqual("0.7.9", (ROOT / "dist/installer/VERSION").read_text().strip())
        self.assertEqual("0.7.9", (ROOT / "dist/installer/packages/latest").resolve().name)
        self.assertEqual(["0.7.9"], [package["version"] for package in current])
        self.assertEqual("0.7.9", product["version"])

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

        bundle = ROOT / "dist/installer/packages/0.7.9/x86qw-installer-0.7.9.zip"
        self.assertEqual(current[0]["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(current[0]["size"], bundle.stat().st_size)
        with zipfile.ZipFile(bundle) as outer:
            application = outer.read("x86qw-installer-0.7.9/x86qw.pyz")
        with zipfile.ZipFile(io.BytesIO(application)) as inner:
            self.assertEqual(
                (ROOT / "maintenance/trust/root.json").read_bytes(),
                inner.read("_x86qw/trust/root.json"),
            )
            self.assertFalse(any(name.endswith((".pem", ".key")) for name in inner.namelist()))

        self.assertIn('INSTALLER_VERSION="0.7.9"', (ROOT / "site/public/install.sh").read_text())
        self.assertIn('$InstallerVersion = "0.7.9"', (ROOT / "site/public/install.ps1").read_text())

    def test_public_trust_repository_authenticates_the_final_catalog(self) -> None:
        trust = ROOT / "site/public/api/v1/trust"
        catalog = (ROOT / "site/public/api/v1/catalog.json").read_bytes()
        digest = hashlib.sha256(catalog).hexdigest()
        expected = {
            "metadata/1.root.json",
            "metadata/5.targets.json",
            "metadata/5.snapshot.json",
            "metadata/timestamp.json",
            f"targets/catalog/{digest}.catalog.json",
        }
        self.assertEqual(
            expected,
            {
                path.relative_to(trust).as_posix()
                for path in trust.rglob("*")
                if path.is_file()
            },
        )
        self.assertEqual(catalog, (trust / f"targets/catalog/{digest}.catalog.json").read_bytes())

    def test_public_text_identifies_the_installation_baseline_hotfix(self) -> None:
        notes = (ROOT / "docs/releases/0.7.9.md").read_text(encoding="utf-8")
        self.assertIn("# x86QW 0.7.9", notes)
        self.assertIn("baseline", notes)
        self.assertIn("x86qw changes", notes)
        index = (ROOT / "site/public/index.html").read_text(encoding="utf-8")
        self.assertIn("Distribuição 0.7.9 pública e verificável", index)
        self.assertIn("versão 0.7.9, 69 pacotes", index)


if __name__ == "__main__":
    unittest.main()
