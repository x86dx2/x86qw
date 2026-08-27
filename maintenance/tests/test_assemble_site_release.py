from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from maintenance.tools.assemble_site_release import (
    SiteAssemblyError,
    _published_root_version,
    assemble_site_release,
)
from maintenance.tools.publish_tuf_metadata import stage_tuf_repository
from maintenance.tests.trust_support import build_repository, new_keyset, signed_root


ROOT = Path(__file__).resolve().parents[2]


class AssembleSiteReleaseTests(unittest.TestCase):
    def test_published_root_versions_are_ordered_numerically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary) / "metadata"
            metadata.mkdir()
            for version in range(1, 11):
                (metadata / f"{version}.root.json").write_text(
                    json.dumps({"signed": {"version": version}}), encoding="utf-8"
                )
            self.assertEqual(10, _published_root_version(metadata.parent))

    def test_assembles_catalog_product_and_verified_tuf_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keys = new_keyset()
            root_v2 = signed_root(keys, version=2, previous_root=keys["root"])
            catalog = (ROOT / "site/public/api/v1/catalog.json").read_bytes()
            repository = build_repository(
                keys, version=1, catalog=catalog, root_updates=(root_v2,),
            )
            signed_repository = root / "signed"
            for url, payload in repository.files.items():
                parsed = urlsplit(url)
                destination_root = (
                    signed_repository / "targets"
                    if parsed.netloc == "targets.invalid"
                    else signed_repository / "metadata"
                )
                destination = destination_root / Path(parsed.path.lstrip("/")).name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            (signed_repository / "metadata/1.root.json").write_bytes(repository.bootstrap_root)
            (signed_repository / "metadata/2.root.json").write_bytes(root_v2)
            anchor = root / "root.json"
            anchor.write_bytes(repository.bootstrap_root)
            staged_trust = root / "trust"
            stage_tuf_repository(
                signed_repository=signed_repository,
                root=anchor,
                catalog=ROOT / "site/public/api/v1/catalog.json",
                output=staged_trust,
            )
            output = root / "site"
            result = assemble_site_release(
                site_source=ROOT / "site/public",
                catalog=ROOT / "site/public/api/v1/catalog.json",
                product=ROOT / "site/public/api/v1/product.json",
                trust_repository=staged_trust,
                output=output,
            )
            self.assertEqual("assembled", result["status"])
            self.assertEqual(
                (ROOT / "site/public/api/v1/catalog.json").read_bytes(),
                (output / "api/v1/catalog.json").read_bytes(),
            )
            self.assertEqual(
                json.loads((ROOT / "site/public/api/v1/product.json").read_text()),
                json.loads((output / "api/v1/product.json").read_text()),
            )
            self.assertTrue((output / "api/v1/trust/metadata/timestamp.json").is_file())
            release_truth = json.loads(
                (output / "api/v1/release-truth.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                2,
                release_truth["authorities"]["deployment"]["tuf"]["root_version"],
            )

    def test_refuses_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "site"
            output.mkdir()
            with self.assertRaises(SiteAssemblyError):
                assemble_site_release(
                    site_source=ROOT / "site/public",
                    catalog=ROOT / "site/public/api/v1/catalog.json",
                    product=ROOT / "site/public/api/v1/product.json",
                    trust_repository=ROOT / "site/public/api/v1/trust",
                    output=output,
                )


if __name__ == "__main__":
    unittest.main()
