#!/usr/bin/env python3
"""Compare the public product projection with the approved candidate bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.downloader import (  # noqa: E402
    BoundedMetadata,
    DownloadError,
    RetryPolicy,
    download,
    validate_https_url,
)
from x86qw_runtime.io.metadata import read_bounded_regular_file  # noqa: E402


DEFAULT_BASE_URL = "https://qw.x86.com.br/"
MAX_PRODUCT_BYTES = 2 * 1024 * 1024


class PublicProductError(RuntimeError):
    """The public product projection differs from the approved candidate."""


def _read(path: Path, label: str) -> bytes:
    try:
        return read_bounded_regular_file(path, maximum_size=MAX_PRODUCT_BYTES)
    except OSError as error:
        raise PublicProductError(f"{label} ausente ou inseguro: {path}") from error


def verify_public_product(*, base_url: str, candidate: Path) -> dict[str, object]:
    """Verify the exact product bytes served by the public site."""

    try:
        parsed = validate_https_url(base_url, "base pública")
    except DownloadError as error:
        raise PublicProductError("base pública não é HTTPS válida") from error
    if parsed.query or parsed.fragment or not parsed.path.endswith("/"):
        raise PublicProductError("base pública deve terminar em / sem query ou fragmento")

    candidate = Path(candidate)
    expected = _read(candidate / "product.json", "produto candidato")
    projected = _read(
        candidate / "site/public/api/v1/product.json",
        "projeção de produto candidata",
    )
    if projected != expected:
        raise PublicProductError("projeção de produto do candidato diverge")

    try:
        result = download(BoundedMetadata(
            url=f"{base_url}api/v1/product.json",
            maximum_size=MAX_PRODUCT_BYTES,
            deadline_seconds=30,
            retry=RetryPolicy(attempts=2),
            headers={"User-Agent": "x86qw-public-product-gate/1"},
            label="produto público",
        ))
    except DownloadError as error:
        raise PublicProductError("não foi possível baixar o produto público") from error
    actual = result.data
    if actual is None:
        raise PublicProductError("download do produto público não retornou bytes")
    if actual != expected:
        raise PublicProductError("produto público diverge do candidato")

    return {
        "format": 1,
        "project": "x86qw",
        "status": "verified-public-product",
        "size": len(expected),
        "sha256": hashlib.sha256(expected).hexdigest(),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--candidate", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = verify_public_product(
            base_url=options.base_url,
            candidate=options.candidate,
        )
    except (OSError, PublicProductError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
