from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools.verify_public_bootstraps import (
    PublicBootstrapError,
    verify_public_bootstraps,
)


class VerifyPublicBootstrapsTests(unittest.TestCase):
    def test_candidate_bootstraps_are_compared_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "site/public").mkdir(parents=True)
            shell = b'INSTALLER_VERSION="1.0.0-rc.1"\n'
            powershell = b'$InstallerVersion = "1.0.0-rc.1"\n'
            (root / "site/public/install.sh").write_bytes(shell)
            (root / "site/public/install.ps1").write_bytes(powershell)

            def fake_download(contract):
                payload = shell if contract.url.endswith("install.sh") else powershell
                return mock.Mock(data=payload)

            with mock.patch(
                "maintenance.tools.verify_public_bootstraps.download",
                side_effect=fake_download,
            ):
                result = verify_public_bootstraps(
                    base_url="https://public.invalid/",
                    candidate=root,
                )
            self.assertEqual("verified-public-bootstraps", result["status"])

    def test_projected_bootstraps_are_compared_instead_of_the_candidate_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "site/public").mkdir(parents=True)
            projected = root / "current-site"
            projected.mkdir()
            (root / "site/public/install.sh").write_bytes(b"candidate-shell")
            (root / "site/public/install.ps1").write_bytes(b"candidate-powershell")
            (projected / "install.sh").write_bytes(b"projected-shell")
            (projected / "install.ps1").write_bytes(b"projected-powershell")

            def fake_download(contract):
                payload = (
                    b"projected-shell"
                    if contract.url.endswith("install.sh")
                    else b"projected-powershell"
                )
                return mock.Mock(data=payload)

            with mock.patch(
                "maintenance.tools.verify_public_bootstraps.download",
                side_effect=fake_download,
            ):
                result = verify_public_bootstraps(
                    base_url="https://public.invalid/",
                    candidate=root,
                    bootstrap_dir=projected,
                )
            self.assertEqual("verified-public-bootstraps", result["status"])
            self.assertEqual(
                "projected-shell",
                (projected / "install.sh").read_text(),
            )

    def test_one_public_bootstrap_divergence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "site/public").mkdir(parents=True)
            (root / "site/public/install.sh").write_bytes(b"candidate")
            (root / "site/public/install.ps1").write_bytes(b"candidate")
            with mock.patch(
                "maintenance.tools.verify_public_bootstraps.download",
                return_value=mock.Mock(data=b"different"),
            ):
                with self.assertRaisesRegex(PublicBootstrapError, "diverge"):
                    verify_public_bootstraps(
                        base_url="https://public.invalid/",
                        candidate=root,
                    )


if __name__ == "__main__":
    unittest.main()
