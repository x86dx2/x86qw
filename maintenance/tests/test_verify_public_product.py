from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools.verify_public_product import (
    PublicProductError,
    verify_public_product,
)


class VerifyPublicProductTests(unittest.TestCase):
    def _candidate(self, root: Path, product: bytes, site_product: bytes | None = None) -> Path:
        candidate = root / "candidate"
        (candidate / "site/public/api/v1").mkdir(parents=True)
        (candidate / "product.json").write_bytes(product)
        (candidate / "site/public/api/v1/product.json").write_bytes(
            product if site_product is None else site_product
        )
        return candidate

    def test_public_product_matches_exact_candidate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            product = b'{"project":"x86qw","version":"1.0.0-rc.1"}\n'
            candidate = self._candidate(Path(temporary), product)

            with mock.patch(
                "maintenance.tools.verify_public_product.download",
                return_value=mock.Mock(data=product),
            ):
                result = verify_public_product(
                    base_url="https://public.invalid/",
                    candidate=candidate,
                )

            self.assertEqual("verified-public-product", result["status"])
            self.assertEqual(len(product), result["size"])

    def test_public_product_divergence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(
                Path(temporary),
                b'{"version":"1.0.0-rc.1"}\n',
            )
            with mock.patch(
                "maintenance.tools.verify_public_product.download",
                return_value=mock.Mock(data=b'{"version":"0.7.13"}\n'),
            ):
                with self.assertRaisesRegex(PublicProductError, "diverge"):
                    verify_public_product(
                        base_url="https://public.invalid/",
                        candidate=candidate,
                    )

    def test_candidate_product_projection_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(
                Path(temporary),
                b'{"version":"1.0.0-rc.1"}\n',
                site_product=b'{"version":"different"}\n',
            )
            with self.assertRaisesRegex(PublicProductError, "candidato"):
                verify_public_product(
                    base_url="https://public.invalid/",
                    candidate=candidate,
                )


if __name__ == "__main__":
    unittest.main()
