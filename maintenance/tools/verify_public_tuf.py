#!/usr/bin/env python3
"""Verify the public catalog through the production TUF trust boundary.

This is a read-only post-publish gate.  It authenticates the catalog served by
the public metadata and target endpoints, then compares the exact target bytes
with the approved candidate catalog.  It never signs, writes public metadata,
or falls back to the legacy release-evidence verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

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
from x86qw_runtime.trust import (  # noqa: E402
    CATALOG_MAX_BYTES,
    BoundedTufFetcher,
    TrustError,
    load_trusted_catalog,
)


DEFAULT_BASE_URL = "https://qw.x86.com.br/api/v1/trust/"
USER_AGENT = "x86qw-public-tuf-gate/1"
MAX_PUBLIC_RESPONSE_BYTES = 2 * 1024 * 1024


class PublicTufVerificationError(RuntimeError):
    """The public TUF repository did not match the approved candidate."""


def _regular_file(path: Path, label: str, *, maximum_size: int) -> bytes:
    try:
        return read_bounded_regular_file(path, maximum_size=maximum_size)
    except OSError as error:
        raise PublicTufVerificationError(f"{label} ausente ou inseguro: {path}") from error


def _json_file(path: Path, label: str) -> tuple[bytes, dict[str, object]]:
    payload = _regular_file(path, label, maximum_size=CATALOG_MAX_BYTES)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicTufVerificationError(f"{label} não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise PublicTufVerificationError(f"{label} precisa ser um objeto JSON")
    return payload, value


def _base_urls(base_url: str) -> tuple[str, str]:
    try:
        parsed = validate_https_url(base_url, "base pública de trust")
    except DownloadError as error:
        raise PublicTufVerificationError("base pública de trust não é HTTPS válida") from error
    if parsed.query or parsed.fragment or not parsed.path.endswith("/"):
        raise PublicTufVerificationError(
            "base pública de trust deve terminar em / e não conter query ou fragmento"
        )
    normalized = base_url
    return f"{normalized}metadata/", f"{normalized}targets/"


def _network_fetcher():
    def get(
        url: str,
        *,
        maximum_size: int,
        timeout: float,
        attempts: int,
    ) -> bytes:
        result = download(BoundedMetadata(
            url=url,
            maximum_size=min(maximum_size, MAX_PUBLIC_RESPONSE_BYTES),
            deadline_seconds=timeout,
            retry=RetryPolicy(attempts=max(1, min(attempts, 3))),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            label="metadata TUF pública",
        ))
        payload = result.data
        if payload is None:
            raise PublicTufVerificationError("download TUF não retornou bytes")
        return payload

    return BoundedTufFetcher(get)


def _target_bytes(target_dir: Path) -> bytes:
    candidates = [
        path for path in target_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise PublicTufVerificationError(
            f"TUF retornou {len(candidates)} targets locais; esperado exatamente 1"
        )
    return _regular_file(
        candidates[0], "target TUF autenticado", maximum_size=CATALOG_MAX_BYTES,
    )


def verify_public_catalog(
    *,
    base_url: str,
    root: Path,
    catalog: Path,
    fetcher=None,
    metadata_base_url: str | None = None,
    target_base_url: str | None = None,
) -> dict[str, object]:
    """Authenticate and byte-compare one public catalog.

    ``fetcher`` is injectable only for deterministic tests.  Production CLI
    calls leave it unset and therefore use the bounded HTTPS transport.
    """

    expected_bytes, expected_catalog = _json_file(Path(catalog), "catálogo aprovado")
    metadata_url, target_url = _base_urls(base_url)
    if metadata_base_url is not None:
        metadata_url = metadata_base_url
    if target_base_url is not None:
        target_url = target_base_url
    trusted_fetcher = fetcher if fetcher is not None else _network_fetcher()
    root_bytes = _regular_file(Path(root), "root TUF incorporada", maximum_size=512 * 1024)
    with tempfile.TemporaryDirectory(prefix="x86qw-public-tuf-") as temporary:
        temporary_root = Path(temporary)
        try:
            authenticated = load_trusted_catalog(
                bootstrap_root=root_bytes,
                metadata_dir=temporary_root / "metadata",
                target_dir=temporary_root / "targets",
                metadata_base_url=metadata_url,
                target_base_url=target_url,
                fetcher=trusted_fetcher,
            )
            actual_bytes = _target_bytes(temporary_root / "targets")
        except (OSError, TrustError, ValueError, TypeError) as error:
            raise PublicTufVerificationError(
                f"metadata pública não autenticou o catálogo: {error}"
            ) from error
    if actual_bytes != expected_bytes:
        raise PublicTufVerificationError(
            "target TUF público diverge byte a byte do catálogo aprovado"
        )
    if authenticated != expected_catalog:
        raise PublicTufVerificationError(
            "catálogo TUF público diverge semanticamente do catálogo aprovado"
        )
    return {
        "format": 1,
        "project": "x86qw",
        "status": "verified-public-tuf",
        "catalog_sha256": hashlib.sha256(actual_bytes).hexdigest(),
        "catalog_size": len(actual_bytes),
        "package_count": len(authenticated.get("packages", [])),
        "metadata_base_url": metadata_url,
        "target_base_url": target_url,
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = verify_public_catalog(
            base_url=options.base_url,
            root=options.root,
            catalog=options.catalog,
        )
    except (OSError, PublicTufVerificationError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
