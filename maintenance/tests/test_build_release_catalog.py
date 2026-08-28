from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.build_release_catalog import (
    ReleaseCatalogError,
    build_candidate_product,
    build_candidate_catalog,
)
from maintenance.tools.validate_catalog import published_package_count, validate_catalog


ROOT = Path(__file__).resolve().parents[2]


class BuildReleaseCatalogTests(unittest.TestCase):
    def test_script_entrypoint_imports_from_repository_root(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "maintenance/tools/build_release_catalog.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_candidate_catalog_is_reproducible_when_generated_at_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / "installer.zip"
            installer.write_bytes(b"candidate bytes\n")
            first = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=root / "first.json",
                version="1.0.0-rc.1",
                generated_at="2026-08-11T12:00:00Z",
            )
            second = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=root / "second.json",
                version="1.0.0-rc.1",
                generated_at="2026-08-11T12:00:00Z",
            )
            self.assertEqual("2026-08-11T12:00:00Z", first["generated_at"])
            self.assertEqual(first, second)
            self.assertEqual(
                (root / "first.json").read_bytes(),
                (root / "second.json").read_bytes(),
            )

    def test_candidate_catalog_binds_exact_installer_and_retires_old_current(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / "x86qw-installer-1.0.0-rc.1.zip"
            payload = b"candidate bytes\n"
            installer.write_bytes(payload)
            output = root / "catalog.json"
            catalog = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=output,
                version="1.0.0-rc.1",
            )
            self.assertEqual(len(catalog["packages"]), validate_catalog(catalog))
            installers = [
                item for item in catalog["packages"]
                if item["component"] == "installer"
            ]
            current = [item for item in installers if item["current"]]
            self.assertEqual(1, len(current))
            self.assertEqual("1.0.0-rc.1", current[0]["version"])
            self.assertEqual(len(payload), current[0]["size"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), current[0]["sha256"])
            self.assertTrue(all(url.endswith("x86qw-installer-1.0.0-rc.1.zip") for url in current[0]["urls"]))
            self.assertEqual(catalog, json.loads(output.read_text(encoding="utf-8")))
            product_source = ROOT / "site/public/api/v1/product.json"
            product_output = root / "site/api/v1/product.json"
            product = build_candidate_product(
                source=product_source, catalog=catalog, output=product_output,
            )
            self.assertEqual("1.0.0-rc.1", product["version"])
            self.assertEqual(published_package_count(catalog), product["package_count"])
            self.assertEqual(current[0]["sha256"], product["installer"]["sha256"])

    def test_candidate_catalog_binds_release_title_and_notes_from_the_release_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / "installer.zip"
            installer.write_bytes(b"candidate")
            catalog = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=root / "catalog.json",
                version="1.0.0-rc.1",
                release_title="x86QW RC 1",
                release_notes="Notas aprovadas do candidato.",
                generated_at="2026-08-11T12:00:00Z",
            )
            current = next(
                item for item in catalog["packages"]
                if item["component"] == "installer" and item["current"]
            )
            self.assertEqual("x86QW RC 1", current["release_title"])
            self.assertEqual("Notas aprovadas do candidato.", current["release_notes"])
            self.assertEqual("x86QW RC 1", current["mirror_title"])

    def test_cli_reuses_exact_current_release_without_changing_catalog_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            catalog = json.loads(
                (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
            )
            current = next(
                item for item in catalog["packages"]
                if item["component"] == "installer" and item["current"]
            )
            installer = root / current["filename"]
            payload = b"exact reconstructed installer bytes"
            installer.write_bytes(payload)
            current["size"] = len(payload)
            current["sha256"] = hashlib.sha256(payload).hexdigest()
            source.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            output = root / "catalog.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "maintenance/tools/build_release_catalog.py"),
                    "--source", str(source),
                    "--installer", str(installer),
                    "--output", str(output),
                    "--version", current["version"],
                    "--release-title", current["release_title"],
                    "--release-notes", current["release_notes"],
                    "--generated-at", "2030-01-01T00:00:00Z",
                    "--reuse-existing-current",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(source.read_bytes(), output.read_bytes())

    def test_reuse_existing_current_rejects_different_installer_bytes(self) -> None:
        source = ROOT / "site/public/api/v1/catalog.json"
        catalog = json.loads(source.read_text(encoding="utf-8"))
        current = next(
            item for item in catalog["packages"]
            if item["component"] == "installer" and item["current"]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / current["filename"]
            installer.write_bytes(b"different installer bytes")
            with self.assertRaisesRegex(ReleaseCatalogError, "diverge"):
                build_candidate_catalog(
                    source=source,
                    installer=installer,
                    output=root / "catalog.json",
                    version=current["version"],
                    release_title=current["release_title"],
                    release_notes=current["release_notes"],
                    reuse_existing_current=True,
                )

    def test_reuse_existing_current_rejects_different_release_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            catalog = json.loads(
                (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
            )
            current = next(
                item for item in catalog["packages"]
                if item["component"] == "installer" and item["current"]
            )
            installer = root / current["filename"]
            payload = b"exact reconstructed installer bytes"
            installer.write_bytes(payload)
            current["size"] = len(payload)
            current["sha256"] = hashlib.sha256(payload).hexdigest()
            source.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseCatalogError, "metadados"):
                build_candidate_catalog(
                    source=source,
                    installer=installer,
                    output=root / "catalog.json",
                    version=current["version"],
                    release_title="Título divergente",
                    release_notes=current["release_notes"],
                    reuse_existing_current=True,
                )

    def test_existing_output_and_existing_version_fail_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / "installer.zip"
            installer.write_bytes(b"candidate")
            output = root / "catalog.json"
            output.write_text("sentinel\n", encoding="utf-8")
            with self.assertRaises(ReleaseCatalogError):
                build_candidate_catalog(
                    source=ROOT / "site/public/api/v1/catalog.json",
                    installer=installer,
                    output=output,
                    version="1.0.0-rc.1",
                )


if __name__ == "__main__":
    unittest.main()
