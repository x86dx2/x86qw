#!/usr/bin/env python3
"""Compare public shell/PowerShell bootstraps with the approved candidate."""

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
MAX_BOOTSTRAP_BYTES = 16 * 1024 * 1024


class PublicBootstrapError(RuntimeError):
    """The public bootstrap differs from the approved candidate."""


def _read(path: Path, label: str) -> bytes:
    try:
        return read_bounded_regular_file(path, maximum_size=MAX_BOOTSTRAP_BYTES)
    except OSError as error:
        raise PublicBootstrapError(f"{label} ausente ou inseguro: {path}") from error


def verify_public_bootstraps(*, base_url: str, candidate: Path) -> dict[str, object]:
    try:
        parsed = validate_https_url(base_url, "base pública")
    except DownloadError as error:
        raise PublicBootstrapError("base pública não é HTTPS válida") from error
    if parsed.query or parsed.fragment or not parsed.path.endswith("/"):
        raise PublicBootstrapError("base pública deve terminar em / sem query ou fragmento")
    candidate = Path(candidate)
    files = {
        "install.sh": _read(candidate / "site/public/install.sh", "bootstrap candidato"),
        "install.ps1": _read(candidate / "site/public/install.ps1", "bootstrap candidato"),
    }
    for name, expected in files.items():
        result = download(BoundedMetadata(
            url=f"{base_url}{name}",
            maximum_size=MAX_BOOTSTRAP_BYTES,
            deadline_seconds=30,
            retry=RetryPolicy(attempts=2),
            headers={"User-Agent": "x86qw-public-bootstrap-gate/1"},
            label=f"bootstrap público {name}",
        ))
        actual = result.data
        if actual != expected:
            raise PublicBootstrapError(f"bootstrap público diverge: {name}")
    return {
        "format": 1,
        "project": "x86qw",
        "status": "verified-public-bootstraps",
        "files": {
            name: {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in files.items()
        },
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--candidate", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = verify_public_bootstraps(
            base_url=options.base_url,
            candidate=options.candidate,
        )
    except (OSError, PublicBootstrapError, DownloadError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
