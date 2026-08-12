from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tests.trust_support import (
    METADATA_URL,
    TARGET_URL,
    MappingFetcher,
    build_repository,
    new_keyset,
)
from maintenance.tools.verify_public_tuf import (
    PublicTufVerificationError,
    verify_public_catalog,
)


class VerifyPublicTufTests(unittest.TestCase):
    def _files(self, root: Path, repository, catalog: bytes) -> tuple[Path, Path]:
        root_file = root / "root.json"
        root_file.write_bytes(repository.bootstrap_root)
        catalog_file = root / "catalog.json"
        catalog_file.write_bytes(catalog)
        return root_file, catalog_file

    def test_public_target_must_match_approved_bytes(self) -> None:
        catalog = b'{"format":1,"project":"x86qw","packages":[]}'
        repository = build_repository(new_keyset(), version=1, catalog=catalog)
        with tempfile.TemporaryDirectory() as temporary:
            root_file, catalog_file = self._files(Path(temporary), repository, catalog)
            result = verify_public_catalog(
                base_url="https://public.invalid/trust/",
                root=root_file,
                catalog=catalog_file,
                fetcher=MappingFetcher(repository.files),
                metadata_base_url=METADATA_URL,
                target_base_url=TARGET_URL,
            )
            self.assertEqual("verified-public-tuf", result["status"])
            self.assertEqual(0, result["package_count"])

    def test_signed_but_different_target_is_rejected(self) -> None:
        expected = b'{"format":1,"project":"x86qw","packages":[]}'
        served = b'{"format":1,"project":"x86qw","packages":[{"new":true}]}'
        repository = build_repository(new_keyset(), version=1, catalog=served)
        with tempfile.TemporaryDirectory() as temporary:
            root_file, catalog_file = self._files(Path(temporary), repository, expected)
            with self.assertRaisesRegex(PublicTufVerificationError, "diverge"):
                verify_public_catalog(
                    base_url="https://public.invalid/trust/",
                    root=root_file,
                    catalog=catalog_file,
                    fetcher=MappingFetcher(repository.files),
                    metadata_base_url=METADATA_URL,
                    target_base_url=TARGET_URL,
                )

    def test_invalid_base_url_is_rejected_before_fetch(self) -> None:
        catalog = b'{"format":1,"project":"x86qw","packages":[]}'
        repository = build_repository(new_keyset(), version=1, catalog=catalog)
        with tempfile.TemporaryDirectory() as temporary:
            root_file, catalog_file = self._files(Path(temporary), repository, catalog)
            with self.assertRaises(PublicTufVerificationError):
                verify_public_catalog(
                    base_url="http://public.invalid/trust/",
                    root=root_file,
                    catalog=catalog_file,
                    fetcher=MappingFetcher(repository.files),
                )


if __name__ == "__main__":
    unittest.main()
