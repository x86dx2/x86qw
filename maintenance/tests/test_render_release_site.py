from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.build_release_catalog import build_candidate_catalog, build_candidate_product
from maintenance.tools.render_release_site import ReleaseSiteError, render_release_site
from maintenance.tools.validate_catalog import published_package_count


ROOT = Path(__file__).resolve().parents[2]


class RenderReleaseSiteTests(unittest.TestCase):
    def test_script_entrypoint_is_runnable_from_repository_root(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "maintenance/tools/render_release_site.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("render_release_site.py", result.stdout)

    def test_candidate_catalog_and_product_are_rendered_into_fresh_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "api/v1").mkdir(parents=True)
            (source / "index.html").write_text(
                '<span data-product-version>0.7.3</span>'
                '<span data-package-count>64</span>'
                '<span data-component-count>21</span>',
                encoding="utf-8",
            )
            installer = root / "installer.zip"
            installer.write_bytes(b"candidate")
            catalog = root / "catalog.json"
            catalog_value = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=catalog,
                version="1.0.0-rc.1",
            )
            product = root / "product.json"
            build_candidate_product(
                source=ROOT / "site/public/api/v1/product.json",
                catalog=catalog_value,
                output=product,
            )
            output = root / "rendered"
            result = render_release_site(
                source=source, catalog=catalog, product=product, output=output,
            )
            self.assertEqual("1.0.0-rc.1", result["version"])
            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("1.0.0-rc.1", rendered)
            self.assertIn(f">{published_package_count(catalog_value)}<", rendered)
            self.assertEqual(catalog.read_bytes(), (output / "api/v1/catalog.json").read_bytes())
            self.assertEqual(product.read_bytes(), (output / "api/v1/product.json").read_bytes())

    def test_current_public_source_is_a_renderable_release_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer = root / "installer.zip"
            installer.write_bytes(b"candidate")
            catalog = root / "catalog.json"
            catalog_value = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=catalog,
                version="1.0.0-rc.1",
            )
            product = root / "product.json"
            build_candidate_product(
                source=ROOT / "site/public/api/v1/product.json",
                catalog=catalog_value,
                output=product,
            )
            product_value = json.loads(product.read_text(encoding="utf-8"))
            output = root / "rendered"
            result = render_release_site(
                source=ROOT / "site/public",
                catalog=catalog,
                product=product,
                output=output,
            )
            self.assertEqual("1.0.0-rc.1", result["version"])
            rendered = (output / "index.html").read_text(encoding="utf-8")
            self.assertRegex(
                rendered,
                r'x86QW\s+<span class="release-product-version">1\.0\.0-rc\.1</span>',
            )
            self.assertIn(
                f'<span class="release-package-count">{published_package_count(catalog_value)}</span> pacotes',
                rendered,
            )
            self.assertIn(
                f'<span class="release-component-count">{product_value["component_count"]}</span> componentes',
                rendered,
            )

    def test_bootstraps_are_bound_to_candidate_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "api/v1").mkdir(parents=True)
            (source / "index.html").write_text(
                '<span data-product-version>0.7.3</span>'
                '<span data-package-count>64</span>'
                '<span data-component-count>21</span>',
                encoding="utf-8",
            )
            (source / "install.sh").write_text(
                'INSTALLER_VERSION="0.7.3"\n'
                'INSTALLER_SHA256="old"\nINSTALLER_SIZE="1"\n',
                encoding="utf-8",
            )
            (source / "install.ps1").write_text(
                '$InstallerVersion = "0.7.3"\n'
                '$InstallerSha256 = "old"\n$InstallerSize = "1"\n',
                encoding="utf-8",
            )
            installer = root / "installer.zip"
            installer.write_bytes(b"candidate")
            catalog = root / "catalog.json"
            catalog_value = build_candidate_catalog(
                source=ROOT / "site/public/api/v1/catalog.json",
                installer=installer,
                output=catalog,
                version="1.0.0-rc.1",
            )
            product = root / "product.json"
            build_candidate_product(
                source=ROOT / "site/public/api/v1/product.json",
                catalog=catalog_value,
                output=product,
            )
            output = root / "rendered"
            render_release_site(
                source=source,
                catalog=catalog,
                product=product,
                output=output,
                bootstrap_source=source,
            )
            self.assertIn(
                'INSTALLER_VERSION="1.0.0-rc.1"',
                (output / "install.sh").read_text(),
            )
            self.assertIn(
                '$InstallerVersion = "1.0.0-rc.1"',
                (output / "install.ps1").read_text(),
            )
            self.assertIn('INSTALLER_SIZE="9"', (output / "install.sh").read_text())

    def test_existing_destination_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "rendered"
            output.mkdir()
            with self.assertRaises(ReleaseSiteError):
                render_release_site(
                    source=ROOT / "site/public",
                    catalog=ROOT / "site/public/api/v1/catalog.json",
                    product=ROOT / "site/public/api/v1/product.json",
                    output=output,
                )


if __name__ == "__main__":
    unittest.main()
