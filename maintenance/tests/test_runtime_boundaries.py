from __future__ import annotations

import importlib
import importlib.util
import io
import sys
import unittest
import zipfile
from pathlib import Path

from maintenance.tools.build_installer_bundle import zipapp_bytes


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_BIN = ROOT / "dist/installer/bin"
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))


class RuntimeDownloaderBoundaryTests(unittest.TestCase):
    def test_maintenance_downloader_is_the_runtime_compatibility_module(self) -> None:
        """A second downloader implementation must not survive under maintenance."""

        runtime_spec = importlib.util.find_spec("x86qw_runtime.io.downloader")
        self.assertIsNotNone(
            runtime_spec,
            "the bounded downloader must be owned by x86qw_runtime",
        )
        runtime_downloader = importlib.import_module("x86qw_runtime.io.downloader")
        maintenance_downloader = importlib.import_module(
            "maintenance.tools.downloader",
        )

        self.assertIs(maintenance_downloader.download, runtime_downloader.download)
        self.assertIs(
            maintenance_downloader.DownloadContract,
            runtime_downloader.DownloadContract,
        )
        self.assertEqual(
            maintenance_downloader.PinnedArtifact.__module__,
            "x86qw_runtime.io.downloader",
        )

    def test_installed_zipapp_carries_only_the_runtime_downloader(self) -> None:
        """The public CLI must not ship the maintenance downloader facade."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/io/downloader.py", names)
        self.assertNotIn("maintenance/tools/downloader.py", names)


class RuntimeErrorBoundaryTests(unittest.TestCase):
    def test_entrypoints_share_the_runtime_installer_error(self) -> None:
        """Entrypoints must not import manager merely to share an error type."""

        runtime_spec = importlib.util.find_spec("x86qw_runtime.errors")
        self.assertIsNotNone(
            runtime_spec,
            "typed CLI errors must be owned by x86qw_runtime",
        )
        runtime_errors = importlib.import_module("x86qw_runtime.errors")
        manager = importlib.import_module("manager")
        gameplay = importlib.import_module("gameplay")
        services = importlib.import_module("services")

        self.assertIs(manager.InstallerError, runtime_errors.InstallerError)
        self.assertIs(gameplay.InstallerError, runtime_errors.InstallerError)
        self.assertIs(services.InstallerError, runtime_errors.InstallerError)
        self.assertEqual(
            int(runtime_errors.InstallerError("falha").exit_code),
            1,
        )


if __name__ == "__main__":
    unittest.main()
