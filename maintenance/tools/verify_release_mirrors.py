#!/usr/bin/env python3
"""Verify every catalog mirror for one immutable release candidate.

The ordinary installer intentionally falls back between equivalent mirrors.
Release publication has a stricter contract: every URL declared by the signed
catalog must independently serve the exact pinned bytes before metadata can be
moved.  This command therefore never calls ``download_mirrors`` and never
silently accepts a partial mirror set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.release_candidate import CandidateError, verify_candidate  # noqa: E402
from x86qw_runtime.io.downloader import (  # noqa: E402
    DownloadError,
    DownloadResult,
    MAX_ARTIFACT_BYTES,
    PinnedArtifact,
    RetryPolicy,
    download,
    validate_https_url,
)
from x86qw_runtime.trust import MAX_METADATA_BYTES  # noqa: E402


MAX_MIRRORS = 16
MIRROR_SET_DEADLINE_SECONDS = 30 * 60.0
MIRROR_DEADLINE_SECONDS = 5 * 60.0
MIRROR_MAX_BYTES = MAX_ARTIFACT_BYTES
MIRROR_USER_AGENT = "x86qw-release-mirror-gate/1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class MirrorVerificationError(RuntimeError):
    """The complete declared mirror set did not satisfy the release pin."""


def _read_catalog(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MirrorVerificationError(f"catálogo de mirrors ausente ou inseguro: {path}")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_METADATA_BYTES + 1)
        if len(payload) > MAX_METADATA_BYTES:
            raise MirrorVerificationError("catálogo de mirrors excede o limite de metadata")
        value = json.loads(payload.decode("utf-8"))
    except MirrorVerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MirrorVerificationError("catálogo de mirrors não é JSON válido") from error
    if not isinstance(value, dict):
        raise MirrorVerificationError("catálogo de mirrors precisa ser um objeto JSON")
    return value


def _catalog_record(catalog: dict[str, Any], version: str) -> dict[str, Any]:
    if catalog.get("project") != "x86qw":
        raise MirrorVerificationError("catálogo de mirrors possui projeto inválido")
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise MirrorVerificationError("catálogo de mirrors não possui packages")
    records = [
        item for item in packages
        if isinstance(item, dict)
        and item.get("package") == "x86qw-installer"
        and item.get("version") == version
        and item.get("current") is True
    ]
    if len(records) != 1:
        raise MirrorVerificationError(
            f"catálogo não identifica exatamente um instalador current {version}"
        )
    return records[0]


def _validate_mirrors(urls: object) -> tuple[str, ...]:
    if not isinstance(urls, (list, tuple)) or not urls:
        raise MirrorVerificationError("o catálogo precisa declarar ao menos um mirror")
    if len(urls) > MAX_MIRRORS:
        raise MirrorVerificationError(f"o catálogo excede o limite de {MAX_MIRRORS} mirrors")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw_url in enumerate(urls, start=1):
        if not isinstance(raw_url, str):
            raise MirrorVerificationError(f"mirror {index} não é texto")
        try:
            parsed = validate_https_url(raw_url, f"mirror {index}")
        except DownloadError as error:
            raise MirrorVerificationError(f"mirror {index} não é HTTPS seguro") from error
        if parsed.query:
            raise MirrorVerificationError(f"mirror {index} não pode conter query string")
        canonical = parsed.geturl()
        if canonical != raw_url:
            raise MirrorVerificationError(f"mirror {index} possui forma não canônica")
        if canonical in seen:
            raise MirrorVerificationError(f"mirror duplicado: {canonical}")
        seen.add(canonical)
        result.append(canonical)
    return tuple(result)


def _validate_pin(expected_size: object, expected_sha256: object) -> tuple[int, str]:
    if type(expected_size) is not int or expected_size <= 0 or expected_size > MIRROR_MAX_BYTES:
        raise MirrorVerificationError("tamanho de mirror inválido")
    if not isinstance(expected_sha256, str) or HEX64.fullmatch(expected_sha256) is None:
        raise MirrorVerificationError("SHA-256 de mirror inválido")
    return expected_size, expected_sha256


def verify_mirrors(
    urls: Sequence[str],
    *,
    expected_size: int,
    expected_sha256: str,
    set_deadline_seconds: float = MIRROR_SET_DEADLINE_SECONDS,
) -> tuple[dict[str, object], ...]:
    """Download and verify every declared URL independently.

    All URL and pin validation occurs before the first network call.  Each
    mirror gets its own private temporary destination and a bounded contract;
    a successful earlier mirror never hides a later failure.
    """

    normalized = _validate_mirrors(urls)
    size, digest = _validate_pin(expected_size, expected_sha256)
    if (
        isinstance(set_deadline_seconds, bool)
        or not isinstance(set_deadline_seconds, (int, float))
        or set_deadline_seconds <= 0
    ):
        raise MirrorVerificationError("deadline agregado de mirrors inválido")
    deadline = time.monotonic() + float(set_deadline_seconds)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="x86qw-release-mirrors-") as temporary:
        directory = Path(temporary)
        for index, url in enumerate(normalized, start=1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MirrorVerificationError("deadline agregado dos mirrors expirou")
            contract = PinnedArtifact(
                url=url,
                destination=directory / f"mirror-{index}.artifact",
                expected_size=size,
                expected_sha256=digest,
                maximum_size=MIRROR_MAX_BYTES,
                deadline_seconds=min(MIRROR_DEADLINE_SECONDS, remaining),
                retry=RetryPolicy(attempts=3),
                headers={"User-Agent": MIRROR_USER_AGENT},
                label=f"mirror {index}",
            )
            try:
                result = download(contract)
            except DownloadError as error:
                raise MirrorVerificationError(
                    f"mirror {index} não respondeu com o artefato esperado"
                ) from error
            if not isinstance(result, DownloadResult):
                raise MirrorVerificationError(f"mirror {index} retornou resultado inválido")
            if result.size != size or result.sha256 != digest:
                raise MirrorVerificationError(f"mirror {index} diverge do pin de tamanho/SHA-256")
            results.append({"url": url, "size": result.size, "sha256": result.sha256})
    return tuple(results)


def verify_candidate_mirrors(
    *,
    candidate: Path,
    catalog: Path,
    expected_release: str | None = None,
) -> dict[str, object]:
    """Verify all URLs for the installer identified by a candidate manifest."""

    try:
        manifest = verify_candidate(Path(candidate))
    except CandidateError as error:
        raise MirrorVerificationError(f"candidato inválido: {error}") from error
    version = manifest.get("version")
    if not isinstance(version, str):
        raise MirrorVerificationError("candidato não possui versão")
    if expected_release is not None and version != expected_release:
        raise MirrorVerificationError(
            f"candidato {version} diverge da versão esperada {expected_release}"
        )
    record = _catalog_record(_read_catalog(Path(catalog)), version)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MirrorVerificationError("candidato não possui artifacts")
    installer = artifacts.get(f"installer/x86qw-installer-{version}.zip")
    if not isinstance(installer, dict):
        raise MirrorVerificationError("candidato não possui o bundle do instalador")
    if record.get("filename") != f"x86qw-installer-{version}.zip":
        raise MirrorVerificationError("nome do instalador diverge no catálogo")
    if (
        type(record.get("size")) is not int
        or not isinstance(record.get("sha256"), str)
        or record.get("size") != installer.get("size")
        or record.get("sha256") != installer.get("sha256")
    ):
        raise MirrorVerificationError("pin do catálogo diverge do candidato")
    results = verify_mirrors(
        record.get("urls"),
        expected_size=int(installer["size"]),
        expected_sha256=str(installer["sha256"]),
    )
    return {"format": 1, "project": "x86qw", "release": version, "mirrors": results, "status": "verified"}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="verifica todos os mirrors do candidato")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--expected-release")
    options = parser.parse_args(arguments)
    try:
        result = verify_candidate_mirrors(
            candidate=options.candidate,
            catalog=options.catalog,
            expected_release=options.expected_release,
        )
    except (MirrorVerificationError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
