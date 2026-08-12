from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "install_qw_installed_trust",
    ROOT / "dist/installer/bin/manager.py",
)
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
import sys
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class InstalledTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        install_qw.console.configure(verbose=False, no_color=True)

    def test_online_catalog_uses_the_embedded_standard_tuf_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = install_qw.Installer(
                root / "bundle", root / "target", online_only=True,
                cache_root=root / "cache" / "x86qw",
            )
            catalog = {"format": 1, "project": "x86qw", "packages": []}
            with mock.patch.object(
                install_qw, "trusted_root_bytes", return_value=b"embedded-root",
            ) as root_bytes, mock.patch.object(
                install_qw, "load_trusted_catalog", return_value=catalog,
            ) as load:
                self.assertEqual(catalog, installer.public_catalog("remote"))

            root_bytes.assert_called_once_with()
            load.assert_called_once()
            kwargs = load.call_args.kwargs
            self.assertEqual(b"embedded-root", kwargs["bootstrap_root"])
            self.assertEqual(install_qw.TRUST_METADATA_URL, kwargs["metadata_base_url"])
            self.assertEqual(install_qw.TRUST_TARGET_URL, kwargs["target_base_url"])

    def test_catalog_url_environment_cannot_bypass_tuf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = install_qw.Installer(
                root / "bundle", root / "target", online_only=True,
                cache_root=root / "cache" / "x86qw",
            )
            with mock.patch.dict(
                os.environ, {"X86_QW_CATALOG_URL": "https://example.invalid/catalog.json"},
            ), mock.patch.object(install_qw, "load_trusted_catalog") as load:
                with self.assertRaisesRegex(install_qw.InstallerError, "contornar.*TUF"):
                    installer.public_catalog("remote")
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
