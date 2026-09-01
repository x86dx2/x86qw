#!/usr/bin/env python3
"""Bind a signed TUF handoff to the exact release projection timestamp."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


MAX_TIMESTAMP_BYTES = 64 * 1024


class ReleaseSourceError(RuntimeError):
    """The handoff is not the exact TUF source declared by the release."""


def _read_timestamp(repository: Path, label: str) -> tuple[bytes, dict[str, object]]:
    path = Path(repository) / "metadata/timestamp.json"
    if path.is_symlink() or not path.is_file():
        raise ReleaseSourceError(f"{label} timestamp ausente ou inseguro: {path}")
    payload = path.read_bytes()
    if not payload or len(payload) > MAX_TIMESTAMP_BYTES:
        raise ReleaseSourceError(f"{label} timestamp excede o limite")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseSourceError(f"{label} timestamp não é JSON válido") from error
    signed = document.get("signed") if isinstance(document, dict) else None
    version = signed.get("version") if isinstance(signed, dict) else None
    if type(version) is not int or version <= 0:
        raise ReleaseSourceError(f"{label} timestamp não possui versão válida")
    return payload, document


def _signed_identity(document: dict[str, object]) -> str:
    signed = document["signed"]
    canonical = json.dumps(
        signed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_release_source(
    *, source_repository: Path, release_projection: Path,
) -> dict[str, object]:
    source_bytes, source = _read_timestamp(source_repository, "handoff")
    projection_bytes, projection = _read_timestamp(release_projection, "projeção")
    source_version = source["signed"]["version"]
    projection_version = projection["signed"]["version"]
    source_signed_sha256 = _signed_identity(source)
    projection_signed_sha256 = _signed_identity(projection)
    if source_version == projection_version and source_signed_sha256 != projection_signed_sha256:
        raise ReleaseSourceError(
            "equivocação TUF: timestamp da versão "
            f"{source_version} possui bytes assinados diferentes entre handoff e projeção"
        )
    if source_bytes != projection_bytes:
        raise ReleaseSourceError(
            "handoff TUF não corresponde byte a byte ao timestamp da projeção de release "
            f"(handoff v{source_version}, projeção v{projection_version})"
        )
    return {
        "format": 1,
        "project": "x86qw",
        "status": "bound-release-source",
        "timestamp_version": source_version,
        "timestamp_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "signed_sha256": source_signed_sha256,
    }


def verify_monotonic_promotion(
    *,
    source_repository: Path,
    release_projection: Path,
    public_repository: Path,
    renewed_repository: Path,
) -> dict[str, object]:
    binding = verify_release_source(
        source_repository=source_repository,
        release_projection=release_projection,
    )
    source_bytes, source = _read_timestamp(source_repository, "handoff")
    public_bytes, public = _read_timestamp(public_repository, "produção")
    _renewed_bytes, renewed = _read_timestamp(renewed_repository, "renovação")
    source_version = source["signed"]["version"]
    public_version = public["signed"]["version"]
    renewed_version = renewed["signed"]["version"]
    if public_version == source_version and _signed_identity(public) != _signed_identity(source):
        raise ReleaseSourceError(
            "equivocação TUF em produção: timestamp da versão "
            f"{public_version} diverge do handoff de origem"
        )
    if public_version == source_version and public_bytes != source_bytes:
        raise ReleaseSourceError(
            "timestamp TUF em produção não corresponde byte a byte ao handoff da mesma versão"
        )
    if public_version > source_version:
        if public_version != renewed_version:
            raise ReleaseSourceError(
                "handoff TUF faria rollback da produção "
                f"(produção v{public_version}, handoff v{source_version}, "
                f"renovação v{renewed_version})"
            )
        if _signed_identity(public) != _signed_identity(renewed):
            raise ReleaseSourceError(
                "equivocação TUF em produção: timestamp da versão "
                f"{public_version} diverge da renovação"
            )
        if public_bytes != _renewed_bytes:
            raise ReleaseSourceError(
                "timestamp TUF em produção não corresponde byte a byte à "
                "renovação da mesma versão"
            )
        return {
            "format": 1,
            "project": "x86qw",
            "status": "safe-converged-redeployment",
            "public_timestamp_version": public_version,
            "source_timestamp_version": source_version,
            "renewed_timestamp_version": renewed_version,
            "source_timestamp_sha256": binding["timestamp_sha256"],
        }
    if renewed_version != source_version + 1:
        raise ReleaseSourceError(
            "renovação TUF precisa ser exatamente N+1 "
            f"(handoff v{source_version}, renovação v{renewed_version})"
        )
    if renewed_version <= public_version:
        raise ReleaseSourceError(
            "renovação TUF não avança a versão pública "
            f"(produção v{public_version}, renovação v{renewed_version})"
        )
    return {
        "format": 1,
        "project": "x86qw",
        "status": "safe-monotonic-promotion",
        "public_timestamp_version": public_version,
        "source_timestamp_version": source_version,
        "renewed_timestamp_version": renewed_version,
        "source_timestamp_sha256": binding["timestamp_sha256"],
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--release-projection", type=Path, required=True)
    parser.add_argument("--public-repository", type=Path)
    parser.add_argument("--renewed-repository", type=Path)
    options = parser.parse_args(arguments)
    try:
        promotion_arguments = (options.public_repository, options.renewed_repository)
        if all(argument is None for argument in promotion_arguments):
            result = verify_release_source(
                source_repository=options.source_repository,
                release_projection=options.release_projection,
            )
        elif any(argument is None for argument in promotion_arguments):
            raise ReleaseSourceError(
                "public-repository e renewed-repository precisam ser fornecidos juntos"
            )
        else:
            result = verify_monotonic_promotion(
                source_repository=options.source_repository,
                release_projection=options.release_projection,
                public_repository=options.public_repository,
                renewed_repository=options.renewed_repository,
            )
    except (OSError, ReleaseSourceError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
